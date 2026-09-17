# Transparent inter-instance service calls — design (sous-projet 1)

**Status: DÉCIDÉ.** Ce document remplace le checkpoint
[`2026-09-16-transparent-rpc-checkpoint.md`](2026-09-16-transparent-rpc-checkpoint.md) (conservé pour
l'historique) : tous les points listés « Open » y sont ici tranchés. Prochaine étape : écrire le plan
d'implémentation tâche par tâche (TDD, un commit par tâche revue), comme pour
[`2026-09-15-remote.md`](../plans/2026-09-15-remote.md).

## Objectif

Un développeur qui dépend d'une interface de service (`ICrud`, ou tout `YCappuccinoComponent`) ne devrait
pas avoir à savoir si l'implémentation est locale ou vit sur un autre processus YCappuccino. Ce document ne
couvre que le **sous-projet 1** : enrichir `remote` (et les quelques points de `api`/`core`/`http_server`/
`swagger` qu'il touche) d'un catalogue de services typé et persisté, avec une couche interne
authentifiée. Les sous-projets 2 (UI shell dans `hosts`) et 3 (frontend `permissions_app`) restent hors
périmètre — décision utilisateur du 2026-09-16 : ils ne démarrent qu'une fois ce document implémenté.

## Décisions (reprises du checkpoint, inchangées)

1. **Modèle d'exposition à deux niveaux** : *public* (`IExposedService` → `endpoints_service`/
   `http_server`, inchangé dans son rôle, protégé par `IAuthorization`/`IAuthentication` existants) vs
   *interne* (nouveau, dans `remote` : appel composant-à-composant entre instances de confiance, protégé
   par l'authentification de pair définie section 4, pas par le système d'auth utilisateur).
2. **Dispatch typé par couche** : interne = opt-out (toute méthode publique d'un `YCappuccinoComponent`
   est éligible, sauf `start`/`stop`/`bind`/`un_bind` et toute méthode préfixée `_` — ce dernier point,
   laissé ouvert au checkpoint, est confirmé : convention Python standard, cohérente avec le reste du
   framework, aucune raison de s'en écarter) ; public = opt-in via `@rpc_method` (section 6). Introspection
   par `inspect.signature`/`typing.get_type_hints`, au moment de l'enregistrement du composant.
3. **Catalogue `ServiceDescriptor` persisté** (`@Item`, dans `remote`) : signatures de méthodes typées +
   référence au `RemoteServer` gateway. Chaque instance publie sa propre description au démarrage et garde
   une copie locale en cache de celle de ses pairs (pas de base partagée).

## 4. Authentification instance-à-instance : HMAC signé par requête

Décision utilisateur (2026-09-16) : bearer token partagé écarté au profit d'une signature HMAC par
requête (pas de secret qui transite sur le fil).

**`RemoteServer`** (`remote/models/remote_server.py`) gagne un champ `secret` (`@Property`, chaîne, non
vide) : la clé HMAC partagée pour ce pair, symétrique des deux côtés (comme aujourd'hui `host`/`port`/
`scheme`, déjà `secure_read=True, secure_write=True` — le secret n'est donc déjà lisible/écrivable que par
un appelant autorisé).

**Signature sortante** — point d'injection unique : `remote/_http.py:call_peer`, le seul helper HTTP
partagé par `RemoteCall`, `ServiceDirectory`, `ComponentDirectory`, `FederatedServiceEndpoint`. Avant
`opener(request, timeout=...)` :

```
timestamp = str(int(time.time()))
message = f"{method}\n{path_with_query}\n{timestamp}\n{body_bytes_or_empty}".encode()
signature = hmac.new(peer.secret.encode(), message, hashlib.sha256).hexdigest()
request.add_header("X-YCappuccino-Peer", <notre propre peer id local>)
request.add_header("X-YCappuccino-Timestamp", timestamp)
request.add_header("X-YCappuccino-Signature", signature)
```

**Vérification entrante** — nouveau `PeerHmacAuthentication(IAuthentication)` dans `remote`
(`remote/peer_authentication.py`) : relit les trois en-têtes, retrouve le `RemoteServer` correspondant
au `peer` annoncé via `IManager.get_one("remoteServer", peer_id, subject=None)`, recalcule la signature
avec son `secret`, compare en temps constant (`hmac.compare_digest`), rejette si `|now - timestamp| > 60s`
(anti-rejeu). Retourne `subject={"peer": peer_id}` en cas de succès, `None` sinon (laisse la chaîne
d'authentification suivante tenter).

**Blocage découvert et levé** : `http_server.ApiServlet._authenticate` (ligne 65) n'essaie aujourd'hui que
`authentications[0]` — un seul `IAuthentication` actif à la fois, occupé par `permissions_app.
JwtAuthentication`. Changement nécessaire, petit et localisé : itérer tous les `IAuthentication` bindés
jusqu'au premier succès (`for auth in authentications: subject = await auth.authenticate(headers); if
subject is not None: return subject`), pour que `JwtAuthentication` (utilisateurs) et
`PeerHmacAuthentication` (pairs) coexistent. Ceci fait partie de ce plan (touche `http_server`).

**Conséquence** : `RemoteCapabilities` (`__remote_capabilities__`) et `RemoteDispatch`
(`__remote_dispatch__`), documentés jusqu'ici comme volontairement `secure=False`, passent à
`secure=True` — un appel non signé ou mal signé est désormais rejeté avant d'atteindre le dispatch, ce qui
ferme la faille documentée dans le checkpoint (item 4 du checkpoint : « ceci referme cette lacune »).

## 5. Dépendance requise vs optionnelle : limitation acceptée, pas de correctif

Investigation menée : `core/component_factory.describe_component()` lit uniquement l'annotation Python du
paramètre au moment du scan (`Optional[...]`/`list[...]` ⇒ `optional=True`/`aggregate=True`), jamais un
registre runtime — il n'y a **aucun point d'ancrage existant** pour transformer une dépendance fédérée en
dépendance requise sans réordonner le chargement des bundles (charger `ComponentDirectory` et peupler le
catalogue *avant* que `load_bundles()` scanne les dépendants), ce qui est un changement d'ordre de
chargement, pas un changement de timing async. `component_directory.py` documente déjà ce choix comme
définitif. **Décision : limitation acceptée**, ce document ne rouvre pas ce chantier — une dépendance
satisfaite par `remote` reste `Optional[...]`/`list[...]`, jamais requise. À documenter dans le README de
`remote` comme limitation connue, pas comme TODO.

## 6. `@rpc_method` : nom et emplacement

Défini dans `api/decorators.py`, à côté de `@Item`/`@Property`, suivant exactement leur convention
existante — un attribut marqueur posé sur la fonction (`func._ycappuccino_rpc_method = {"summary": ...}`),
lu par une passe d'introspection au moment de l'enregistrement du composant (même mécanique que
`_register_class_properties` pour `@Property`, cohérent avec le préfixe `_ycappuccino_*` déjà utilisé
partout dans `component_factory.py`).

## 7. Routage `http_server` : une route par méthode typée

Aujourd'hui, `ApiServlet._route_services` (ligne 148) route tout `/api/services/<name>/...` vers un point
de dispatch générique unique, `service.call(name, method, extra_path, params, fields, subject)`. Le
précédent exact à suivre existe déjà dans le même fichier : `endpoints_storage.Crud`, qui **n'est pas** un
`IExposedService` (correction d'une erreur factuelle du checkpoint) mais un `ICrud` à méthodes typées
séparées (`get_many`, `create`, `get_one`, `update`, `delete_many`, `delete`), routées individuellement par
`ApiServlet._route_crud`.

Décision : `_route_services` est réécrit sur le même modèle. À l'enregistrement d'un `IExposedService`, la
table de routage est construite à partir des méthodes marquées `@rpc_method` (introspection décrite
section 2/6) au lieu d'un unique point d'entrée `call()`. `IExposedService.call(...)` (la méthode générique
actuelle) n'est plus le mécanisme de routage public — elle reste comme point d'entrée interne unique côté
composant si utile en transition, mais toute route HTTP publique correspond désormais à une méthode
`@rpc_method` précise.

## 8. `swagger` : schémas typés pour les routes de service

`swagger.build_openapi._service_paths` (ligne 109) ne produit aujourd'hui que `{"200": {"description":
route.summary}}` car `ServiceRoute(method, path, summary)` ne porte aucun type. Décision : la structure
retournée par l'introspection des méthodes `@rpc_method` (section 6 — nom, types de paramètres, type de
retour) remplace/enrichit `ServiceRoute`, et `_service_paths` en dérive un `requestBody`/`response` schema
JSON exactement comme `_item_paths` le fait déjà via `catalog.get_schema` pour les items CRUD — même
mécanique, deuxième application.

## 9. Migration des implémentations `IExposedService` existantes

Inventaire exhaustif (grep `IExposedService` sur les 14 repos, hors tests/fixtures/exemples) à migrer vers
des méthodes `@rpc_method` typées :

| Repo | Classe | `name` | Opération(s) actuelle(s) |
|---|---|---|---|
| `permissions_app` | `LoginService` | `login` | POST unique → un seul `@rpc_method` |
| `permissions_app` | `LoginCookieService` | `login_cookie` | POST unique → un seul `@rpc_method` |
| `permissions_app` | `ChangePasswordService` | `change_password` | POST unique → un seul `@rpc_method` |
| `scripts` | `ScriptService` | `scripts` | `POST /<scriptId>/execute` → un `@rpc_method(scriptId: str)` |
| `remote` | `RemoteCall` | `remote_call` | reste générique par nature (forward d'un `extra_path` arbitraire vers un pair) — **exclu de la migration**, cas structurellement différent, documenté comme exception |
| `remote` | `RemoteCapabilities` | `__remote_capabilities__` | GET unique → un `@rpc_method` |
| `remote` | `RemoteDispatch` | `__remote_dispatch__` | reste générique par nature (réception RPC générique `(méthode, kwargs)` pour la couche interne) — **exclu de la migration**, même raison que `RemoteCall` |

`endpoints_service.ServiceEndpoint` (le point de dispatch central lui-même) est mis à jour pour
router vers la nouvelle table par méthode (section 7) plutôt que d'appeler `service.call()` directement.

## 10. Nettoyage : suppression de `YCappuccinoRemote` et du code mort associé

Confirmé par grep exhaustif : zéro implémenteur concret dans les 14 repos. À supprimer dans le même lot de
commits que le reste de ce plan :

- `api/proxy.py` : `YCappuccinoRemote`.
- `api/permissions.py` : `ILoginService`, `ITenantTrigger`.
- `api/core.py` : `IService`.
- `api/scheduler.py` : `IScheduler(IService)`.
- `api/endpoints.py` : `IRightManager`, `IEndpoint`, `IHandlerEndpoint`.
- `api/scripts.py` : `IScriptInterpreter`.
- `api/storage.py` : `IRightSubject`, `IBootStrap(IRightSubject)`.
- `api/hosts.py` : `IClobReplaceService`, `IHost`, `IHostFactory`.
- `api/remote.py` : fichier entier (`IRemoteServer`, `IRemoteComponentProxy`, `IRemoteClient`,
  `IRemoteComponentProxyFactory`, `IRemoteClientFactory`).
- `core/bundles/list_components.py` : fichier entier (`ListComponent`, orphelin, `Framework.
  list_components()` — méthode différente déjà utilisée par `capabilities.py` — n'en dépend pas).
- `core/component_factory.py` : `_INTERFACE_ROOTS` passe de `(YCappuccinoComponent, YCappuccinoRemote)` à
  `(YCappuccinoComponent,)` seul.
- Tests à vérifier/adapter à l'implémentation : `core/src/unittest/python/test_component_factory.py`,
  `api/src/unittest/python/test_interfaces.py`.

## Hors périmètre (ce document)

- Sous-projets 2 (`hosts` UI shell) et 3 (frontend `permissions_app`) : non démarrés, décision utilisateur
  du 2026-09-16 de les séquencer après ce document.
- Rotation/révocation de la clé HMAC par pair : la clé est un champ statique de `RemoteServer` pour cette
  itération ; un mécanisme de rotation serait une évolution ultérieure, pas un blocage pour livrer ceci.
- mTLS ou toute alternative à HMAC : écarté par décision utilisateur.

## Prochaine étape

Invoquer l'équivalent de `writing-plans` : découper ce document en tâches TDD (un commit par tâche revue),
dans l'ordre — (a) `RemoteServer.secret` + `PeerHmacAuthentication` + fix `ApiServlet._authenticate`
(multi-auth), (b) `@rpc_method` dans `api/decorators.py` + introspection, (c) réécriture
`_route_services`/`ServiceEndpoint` sur le modèle CRUD, (d) migration des 4 services listés section 9,
(e) `ServiceDescriptor` + catalogue persisté, (f) `swagger._service_paths` typé, (g) nettoyage
`YCappuccinoRemote` et code mort (section 10, peut être fait tôt et indépendamment du reste).
