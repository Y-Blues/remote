# Transparent inter-instance service calls — design (sous-projet 1)

**Status: DÉCIDÉ, amendé le 2026-09-17 (section 11, qui prime sur les sections qu'elle modifie).** Ce
document remplace le checkpoint
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

## 11. Amendement du 2026-09-17 : `client` pair de premier rang, subject propagé, dispatch à deux niveaux

Décisions utilisateur du 2026-09-17, qui complètent (et sur les points signalés, modifient) les sections
précédentes.

### 11.1 `client` utilise le même mécanisme que `remote`

Le navigateur (`client`, vrai `Framework` sous Pyodide) est une instance YCappuccino comme les autres : le
code applicatif y dépend des interfaces backend (`ICrud`, `ILoginService`, ...) par injection, et ce sont
des **proxies locaux synthétisés par réflexion sur l'interface** qui appellent le backend en JSON-RPC via
`__remote_dispatch__` — même forme que `remote.remote_proxy.make_generic_proxy`, dupliquée dans `client`
(jamais importée, `client` ne dépend pas de `remote`). Cela **remplace** `client.remote_proxy.make_remote`
et `known_interfaces.KNOWN_INTERFACES` (inférence verbe/chemin REST depuis les noms de méthode, limitée à 4
interfaces). N'importe quelle interface backend devient proxyable. La découverte reste faite **avant**
`Framework().init()` (module généré, `client/discovery.py`) : côté navigateur, une dépendance fédérée peut
donc être requise, contrairement à la limitation de la section 5 qui ne concerne que `remote`.

Aucune classe « transport » n'est construite par le code applicatif : les ponts `ICrud`/`IServiceEndpoint`
→ `ui.transport.Transport` vivent dans `ui` (`ycappuccino.ui.ycappuccino_transport`), et le seul service
spécifique client que le code applicatif peut nommer est la session (le jeton porté par le transport).

### 11.2 Un subject pour tous les appels dispatchés

- `RemoteDispatch` transmet le subject authentifié de la requête (celui décodé par la chaîne
  `IAuthentication` de `http_server`) à toute méthode cible déclarant un paramètre `subject`. Un `subject`
  placé par l'appelant dans ses `kwargs` est ignoré (jamais d'usurpation par la charge utile).
- Navigateur : authentifié par le JWT utilisateur (`JwtAuthentication`), subject `{"sub", "tid"}`.
- Pair backend appelant **pour le compte d'un utilisateur** (modifie la section 4) : `call_peer` accepte
  `subject` et l'envoie dans un en-tête `X-YCappuccino-Subject` (JSON), **inclus dans le message signé**
  (`method\npath\ntimestamp\nsubject\n` + corps). `PeerHmacAuthentication` retourne alors le subject
  transmis enrichi de `"peer": <peer id>` ; sans en-tête subject, `{"peer": <peer id>}` comme prévu.
  `remote_proxy._dispatch` transmet le `subject` reçu au lieu de le jeter.

### 11.3 Deux niveaux d'accès sur `__remote_dispatch__` (modifie section 4, conséquence)

Le navigateur n'est pas un pair de confiance (code lisible, ne peut détenir aucun secret HMAC). L'accès est
donc décidé par `RemoteDispatch` lui-même, selon l'appelant :

| Appelant (subject) | Méthodes appelables |
|---|---|
| pair HMAC (`"peer"` présent) | toute méthode dispatchable (niveau interne, section 2) |
| utilisateur JWT ou anonyme | seulement les méthodes marquées `@rpc_method` **sur l'interface résolue** |

Pour une méthode `@rpc_method(secure=True)` (défaut) appelée hors pair : subject requis (sinon
`NotAuthenticated`), puis `IAuthorization.is_authorized(subject, "call", "<chemin qualifié>.<méthode>")`
(sinon `Forbidden`) — une `RolePermission` `call:<chemin>.*` l'accorde, `*:*` couvre le superadmin.
`secure=False` : l'appel passe, la méthode porte elle-même son contrôle (login ; `Crud`/`Drafts`/
`ItemCatalog` via `Access`, `ServiceEndpoint` via le `secure` de chaque service — même sémantique que
`IExposedService.secure` aujourd'hui). `RemoteDispatch` reste donc `secure=False` au niveau du service
(sinon un anonyme ne pourrait jamais se connecter) et **n'est pas** migré vers `secure=True` comme
l'annonçait la section 4.

`RemoteCapabilities` reste `secure=False` : un pair reçoit toute la liste, tout autre appelant seulement les
interfaces ayant au moins une méthode `@rpc_method` (la surface publique), ce qui suffit à la découverte du
navigateur avant connexion.

### 11.4 `@rpc_method` (précise section 6)

`@rpc_method(summary: str = "", secure: bool = True)`, posé sur les méthodes **abstraites des interfaces**
d'`api` (pas sur les implémentations : une implémentation ne peut pas élargir la surface publique). Marqués
`secure=False` dans ce lot : toutes les méthodes de `ICrud`, `IDrafts`, `IItemCatalog`, `IServiceEndpoint`,
et `ILoginService.login`.

### 11.5 `ILoginService` typé

Nouvelle interface `api/permissions.py` : `ILoginService.login(login: str, password: str) -> str` (le
jeton), `@rpc_method(secure=False)`. `permissions_app.LoginService` l'implémente en plus de
`IExposedService` (la route REST `login` reste pour les clients non-framework, migrée en tâche 4).

### 11.6 Déploiement backend

Un backend servant le navigateur charge `ycappuccino.remote.dispatch`, `ycappuccino.remote.capabilities`
et `ycappuccino.remote.peer_authentication` en listant **ces modules** dans `bundle_prefix` (déjà supporté
par `Framework._module_names`), pas le paquet `ycappuccino.remote` entier : `FederatedServiceEndpoint` y
fournirait un second `IServiceEndpoint` en conflit avec `endpoints_service.ServiceEndpoint`.

### 11.7 Ordre d'implémentation (remplace « Prochaine étape »)

1. Tâche 1 du plan (HMAC, multi-auth, `IAuthentication` élargi), avec l'en-tête subject signé (11.2).
2. `@rpc_method(summary, secure)` + marquage des interfaces (11.4) + `ILoginService` (11.5).
3. Autorisation à deux niveaux dans `RemoteDispatch` et filtrage de `RemoteCapabilities` (11.3).
4. Réécriture de `client` (11.1).
5. Fin du nettoyage (section 10 : `YCappuccinoRemote`, `list_components.py`, `_INTERFACE_ROOTS`).
6. Tâches 3 à 6 du plan (routes REST par méthode, migration des services, `ServiceDescriptor`, swagger).
7. Navigation multi-écrans dans `ui_web`, bootstrap navigateur, `permissions_app` en mode web.

### 11.8 Étape 6 réalisée (2026-09-17), écarts au plan

- Routage REST : `IExposedService.call` n'est plus abstraite. Un service qui la redéfinit traite lui-même
  ses requêtes (`RemoteCall`, `RemoteDispatch`, services applicatifs existants) ; sinon
  `endpoints_service.endpoint.call_service` route vers le `@rpc_method` dont le verbe et le gabarit de
  chemin correspondent, avec le sujet injecté. `FederatedServiceEndpoint` utilise la même fonction.
- `LoginService` ne fournit plus `ILoginService` : `login()` ne peut pas rendre à la fois `str` (proxy) et
  `{"token"}` (REST). `PasswordLogin` implémente `ILoginService` (et porte la clé : `components.PasswordLogin.key`),
  `LoginService`/`LoginCookieService` en sont les faces HTTP.
- Catalogue : décrit des **interfaces qualifiées** (ce que les proxys consomment), pas des noms de service.
  `__remote_capabilities__` publie `"descriptors"` ; `ServiceDescriptor` ne stocke que les pairs (la vue locale
  est calculée) ; `ServiceCatalog.locate` renvoie les host:port.
- Swagger : `api.endpoints_service.service_routes` dérive les routes des `@rpc_method` quand `routes` est vide.

### 11.9 Étape 7 réalisée (2026-09-17)

- Navigateur réel (Chromium, Pyodide 0.28.3 standard) : aucun thread possible. `core.AsyncRunner` exécute
  alors les coroutines des composants sur le thread appelant, et ne crée plus de boucle hors de son thread
  (sous Pyodide, en créer une remplaçait celle de la page). Plus besoin de build pthread ni de COOP/COEP.
- `client.bootstrap.start_client` + page générique `client/static/index.html` pilotée par `ycappuccino.json`.
- `ui` : `ComponentTransport` (méthode d'une interface depuis un écran). `ui_web` : `Navigator`, `on_result`,
  erreurs du backend à l'écran, `ScreenView.set_value`, `IWebPage`/`PyodidePage`, `ycappuccino.ui_web.testing`.
- `permissions_app` : écrans partagés (`ycappuccino.permissions.screens`), connexion par `ILoginService` dans
  les deux consoles, `PermissionsWebApp` vérifiée dans Chromium face à un vrai backend (connexion, erreurs,
  organisation, création d'utilisateur puis connexion de cet utilisateur, déconnexion).
- Non vérifiés : Firefox/Safari, service de la page et des wheels par `hosts`.

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
