# remote natif : design

Date : 2026-09-15. Sous-projet de la reprise des dépôts YCappuccino, après `core`, `api`, `storage`,
`endpoints_storage`, `http_server`, `endpoints_service` (et `permissions_app`, `scheduler`, `scripts`,
`swagger`, `hosts`, tous natifs).

## Objectif

`remote` permet à une instance YCappuccino d'appeler un `IExposedService` nommé sur une **autre**
instance (un autre processus, un autre conteneur) en HTTP, en réutilisant exactement l'enveloppe de
requête/réponse qu'`http_server`/`endpoints_service` définissent déjà pour l'appel local. Un « client de
service distant » n'est donc rien d'autre qu'un client HTTP qui parle le protocole que le framework se
parle déjà à lui-même.

## Ce que le legacy faisait, et pourquoi on ne le reproduit pas

Le legacy (`bundles/remote_server.py`, `remote_client.py`, `remote_client_factory.py`,
`remote_component_proxy.py`, `remote_storage_factory.py`, `remote_storage_mongo.py`) est un mécanisme de
**mirroring générique par réflexion** : un serveur JSON-RPC (`jsonrpclib`, pool de threads bespoke) expose
tout composant `YCappuccinoRemote`, et un client dynamique (`RemoteComponentProxy`) reconstruit un proxy
pour **chaque** composant distant détecté, en interrogeant ses propriétés à distance — pensé pour un
cluster Docker/Kubernetes/Swarm où toute l'API interne de chaque instance doit être mirroir des autres.

Ce besoin n'existe plus : `endpoints_service` donne déjà à tout composant natif qui le souhaite une
surface HTTP-appelable (`IExposedService`/`IServiceEndpoint`), gratuitement, dès qu'il est déclaré. Il
n'y a donc plus besoin de mirroring générique ni de proxy dynamique : il suffit qu'un appelant qui connaît
le nom d'un service sur une autre instance puisse l'appeler, exactement comme `IServiceEndpoint.call` le
ferait localement. C'est délibérément **beaucoup plus petit** que le legacy — voir « Hors périmètre ».

Seul `RemoteServer` (`models/remote_server.py`, host/port/scheme) survit du legacy, quasiment inchangé :
c'est le registre des pairs connus, et c'est naturellement un `@Item` (CRUD gratuit via `http_server`,
comme documenté dans `storage/README.md`).

## Décisions

| Sujet | Décision |
|---|---|
| Style | Composant natif, aucun décorateur |
| Modèle conservé | `RemoteServer` (`id`, `host`, `port`, `scheme`) — registre manuel des pairs |
| Mécanisme conservé du legacy | Aucun : ni JSON-RPC, ni proxy dynamique, ni serveur bespoke — voir ci-dessus |
| Transport | HTTP simple vers `/api/services/<nom>[...]` du pair, avec la même enveloppe `{"status","meta","data"}` qu'`http_server` documente déjà |
| Adressage d'un appel distant | Le **nom du pair** et le **nom du service cible** viennent d'`extra_path` (voir section 1) : `/api/services/remote_call/<peer_id>/<service>[/<segment>...]` |
| Bibliothèque HTTP | `urllib.request`/`urllib.error` (stdlib), déjà utilisée par les tests d'intégration d'`http_server` (`test_http_server_framework.py`) — aucune dépendance externe nouvelle (pas de `httpx`) ; l'« opener » (`urllib.request.urlopen` par défaut) est injectable au constructeur pour les tests unitaires, sans socket réel |
| Authentification vers le pair | **Non transportée dans cette première version** — voir section 3 |
| Sécurité du service `remote_call` | `secure = True` par défaut : traverser une frontière réseau vers un autre processus est une opération sensible, contrôlée comme n'importe quel service par `IAuthorization` (même mécanisme qu'`endpoints_service`) |
| Sécurité de `RemoteServer` | `secure_read=True, secure_write=True` : seul un appelant autorisé peut lister ou modifier le registre des pairs |
| Lecture du registre par `RemoteCall` | `subject=None` (lecture système), même convention que `permissions_app`/`scheduler` pour leurs propres lectures internes de configuration |
| Découverte de pairs | Aucune : `RemoteServer` est géré à la main via `/api/crud/remote-servers` (gratuit dès que le modèle est enregistré). Pas de heartbeat, pas de failover, pas de client-factory dynamique — coupure de périmètre assumée par rapport au legacy |
| Erreurs du pair | Le statut HTTP de la réponse du pair est retraduit dans la famille d'erreurs locale (`NotAuthenticated`/`Forbidden`/`NotFound`/`InvalidRequest`/générique), pour que l'appelant local voie exactement le même type d'erreur qu'un appel local aurait produit |
| Erreurs réseau | Non enveloppées : une erreur `urllib.error.URLError` (pair injoignable, DNS, timeout) remonte telle quelle et devient un `500` générique côté `http_server` local, comme toute exception non prévue — pas de retry, pas de circuit breaker (hors périmètre) |

## 1. Adressage d'un appel distant

`RemoteCall` est un `IExposedService` ordinaire, publié sous `name = "remote_call"`. Une requête cliente
prend donc la forme :

```
POST /api/services/remote_call/<peer_id>/<service>[/<segment>...]
```

`ApiServlet._route_services` (voir `http_server/README.md`) sépare déjà `name` (`"remote_call"`) du reste
du chemin ; `IServiceEndpoint.call` donne donc à `RemoteCall.call` :

- `extra_path[0]` : l'`id` du `RemoteServer` visé (registre local) ;
- `extra_path[1]` : le nom du service exposé **sur le pair** ;
- `extra_path[2:]` : le chemin supplémentaire transmis tel quel au pair (son propre `extra_path`) ;
- `params` : transmis tel quel en query string de la requête vers le pair ;
- `body` : transmis tel quel en corps JSON de la requête vers le pair (absent si `None`) ;
- `subject` : **non transmis** au pair (voir section 3), utilisé uniquement pour l'autorisation locale de
  `remote_call` lui-même.

`len(extra_path) < 2` lève `InvalidRequest` (pair ou service cible manquant). Un `RemoteServer` introuvable
lève `NotFound`, exactement comme `ServiceEndpoint._find` pour un service local inconnu.

**Pourquoi `extra_path` plutôt que le corps** : `extra_path` fonctionne identiquement pour toutes les
méthodes HTTP (`GET`, `POST`, `PUT`, `DELETE`), alors qu'un `GET`/`DELETE` n'a en général pas de corps
exploité par `endpoints_service`/`http_server` (voir `http_server/README.md`) ; mettre l'adressage dans le
chemin plutôt que dans le corps évite une différence de comportement selon la méthode.

## 2. Traduction de l'enveloppe

La réponse du pair est `{"status", "meta", "data"}` (voir `http_server/README.md`). `RemoteCall` lit le
statut HTTP réel de la réponse (`urllib` lève `HTTPError` pour tout statut ≥ 400, avec le même corps JSON
accessible via `error.read()`) et traduit :

| Statut du pair | Résultat local |
|---|---|
| 2xx | `ServiceResult(body=data["data"], headers=<en-têtes filtrés>)` |
| 401 | `NotAuthenticated(data["error"])` |
| 403 | `Forbidden(data["error"])` |
| 404 | `NotFound(data["error"])` |
| 400 | `InvalidRequest(data["error"])` |
| autre ≥ 400 | `RuntimeError(data["error"])`, devient un `500` générique localement, comme toute exception imprévue |

Les en-têtes de la réponse du pair sont propagés dans `ServiceResult.headers` (utile pour chaîner un
`login_cookie` distant, par exemple), à l'exception des en-têtes de transport (`content-length`,
`content-type`, `connection`, `transfer-encoding`, `date`, `server`).

## 3. Authentification vers le pair : décision et compromis

`IExposedService.call` reçoit un `subject` **déjà décodé** (le dict JWT), jamais les en-têtes bruts de la
requête HTTP entrante (`IAuthentication.authenticate` a déjà consommé l'`Authorization`/le cookie avant que
`ApiServlet` n'appelle `IServiceEndpoint.call`, voir `http_server/README.md`). `RemoteCall` n'a donc, par
construction, aucun jeton brut à retransmettre : reforwarder l'authentification demanderait soit de changer
la signature d'`IExposedService.call` pour lui donner accès aux en-têtes bruts (changement de contrat
partagé par tous les services, hors du périmètre autorisé pour ce sous-projet qui ne doit pas toucher
`api`/`endpoints_service`), soit de réencoder un nouveau jeton pour le pair (suppose que les deux instances
partagent la même clé JWT et le même format de sujet — un couplage fort, non demandé).

**Décision retenue** : cette première version ne transmet **aucune authentification** au pair. Un
`RemoteServer` ne peut donc appeler que des services `secure=False` sur le pair (typiquement un service de
démonstration, un webhook public, ou un service protégé autrement, par exemple par un réseau privé ou un
secret partagé applicatif que l'appelant met lui-même dans `body`/`params`). C'est documenté ici comme un
choix explicite, pas un oubli — voir « Hors périmètre » pour l'évolution possible.

## 4. `RemoteServer` (`models/remote_server.py`)

```python
@App(name="ycappuccino_remote")
@Item(collection="remote_servers", name="remoteServer", plural="remote-servers", secure_read=True, secure_write=True)
class RemoteServer(Model):
    def __init__(self, a_dict=None):
        super().__init__(a_dict)
        self._host = None
        self._port = None
        self._scheme = None

    @Property(name="host")
    def host(self, a_value):
        self._host = a_value

    @Property(name="port", type="integer", minimum=1, maximum=65535)
    def port(self, a_value):
        self._port = a_value

    @Property(name="scheme")
    def scheme(self, a_value):
        self._scheme = a_value
```

`id` du modèle est l'identifiant du pair utilisé dans l'adressage (`extra_path[0]`), au choix de
l'opérateur qui l'enregistre (ex. `"eu-node-2"`). Aucun champ n'a de valeur par défaut : un `RemoteServer`
sans `scheme` explicite est un document incomplet (erreur au moment de l'appel, pas un défaut silencieux
vers `"http"`) — cohérent avec le reste du framework qui préfère échouer bruyamment sur une configuration
incomplète plutôt que de deviner.

## 5. `RemoteCall` (`call.py`) — composant natif

```python
class RemoteCall(IExposedService):
    name = "remote_call"
    secure = True

    def __init__(self, manager: IManager, timeout: float = 5.0, opener=None):
        self._manager = manager
        self._timeout = timeout
        self._opener = opener if opener is not None else urllib.request.urlopen

    async def start(self): pass
    async def stop(self): pass

    async def call(self, method, extra_path, params, body, subject):
        ...  # section 1 et 2
```

`opener` est un paramètre non typé avec valeur par défaut : le framework l'expose comme une propriété
ordinaire (voir `core/README.md`), jamais surchargée en pratique via `application.yml` (ce n'est pas une
valeur sérialisable en YAML) mais injectable directement par un test unitaire qui construit `RemoteCall`
sans framework — c'est le point d'isolation demandé par le plan (pas de socket réel dans les tests
unitaires). `timeout` (secondes) est une propriété ordinaire, surchargeable comme `poll_interval` dans
`scheduler`.

## 6. Packaging et exemple

```
remote/
  pyproject.toml
  README.md
  example/
    conf/application.yml          # instance appelante (RemoteCall + un RemoteServer préenregistré)
    peer/conf/application.yml     # instance pair (http_server + endpoints_service + un service de démo)
    peer/demo/greeting.py
    demo/__init__.py
  src/main/python/ycappuccino/remote/
    __init__.py
    call.py                       # RemoteCall
    models/
      __init__.py
      remote_server.py            # RemoteServer
  src/unittest/python/
    remote_fixtures.py
    test_remote_server.py
    test_remote_call.py
    test_remote_framework.py      # intégration réelle, deux processus
    test_readme.py
```

- **`pyproject.toml`** (uv, `uv_build`) : projet `ycappuccino-remote`, module `ycappuccino.remote`, racine
  `src/main/python`. Dépendances : `ycappuccino-api`, `ycappuccino-core`, `ycappuccino-storage`. Pas
  d'`ycappuccino-endpoints-service` en dépendance runtime : `IExposedService`/`ServiceResult` vivent dans
  `ycappuccino.api.endpoints_service` (voir `api/src/main/python/ycappuccino/api/endpoints_service.py`),
  `endpoints_service` ne fournit que l'implémentation concrète de `IServiceEndpoint`
  (`ServiceEndpoint`), pas les types eux-mêmes. `ycappuccino-http-server` est nécessaire pour lancer le
  pair de test/exemple (il expose `/api/services/...`), ajoutée comme dépendance ordinaire — `remote` ne
  la code pas en dur mais un pair réel en a toujours besoin, y compris dans l'exemple.
- **Supprimés** : `build.py`, `setup.py`, `src/main/python/ycappuccino/remote/bundles/` (dossier complet :
  `remote_server.py`, `remote_client.py`, `remote_client_factory.py`, `remote_component_proxy.py`,
  `remote_manager.py`, `remote_storage_factory.py`, `remote_storage_mongo.py`), l'ancien
  `models/remote_server.py` (réécrit ci-dessus), `conf/config.yaml` (déclarait une couche
  `ycappuccino_service_comm` sans utilité ici), les stubs de test vides
  (`src/unittest/python/test_endpoints.py`, `test_endpoint_storage.py`, `test_jwt.py`,
  `test_service_path.py`, `test_utils_header.py`, `test_util_swagger.py`).
- **Exemple** : deux applications. `example/peer/` (couche mémoire, `http_server` actif sur un port fixe,
  un service `greeting` `secure=False`) joue le rôle du pair, lancée séparément
  (`cd example/peer && uv run --project ../.. ycappuccino`). `example/` (couche mémoire, `http_server`
  inactif, `remote` chargé) enregistre un `RemoteServer` pointant vers ce pair au démarrage et illustre un
  appel direct à `RemoteCall.call(...)`.

## 7. Tests

| Fichier | Contenu |
|---|---|
| `test_remote_server.py` | `RemoteServer` va-et-vient à travers un vrai `Manager` (mémoire), comme `test_scheduled_task.py` |
| `test_remote_call.py` | `RemoteCall` avec un faux `IManager` et un faux opener injecté (pas de socket réel) : URL construite (pair + service + extra_path + query), corps JSON transmis, pair introuvable → `NotFound`, `extra_path` incomplet → `InvalidRequest`, chaque statut d'erreur du pair (401/403/404/400/autre) retraduit dans la bonne exception locale, en-têtes de réponse propagés (hors ceux de transport), `secure`/`name` |
| `test_remote_framework.py` | **Un seul** test d'intégration réelle : le pair tourne dans un **vrai sous-processus** (`python -m ycappuccino.core.runner`, car un seul framework Pelix peut exister par processus — voir `core/README.md` — donc deux `Framework()` réels ne peuvent pas coexister dans le même test process ; un sous-processus est la façon correcte d'obtenir un second processus réel, cohérente avec le cadrage du domaine « une autre instance, un autre processus/conteneur »), avec `http_server` + `endpoints_service` + un service `echo`, sur un port de la plage 18160-18169 ; le framework appelant tourne dans le process de test, avec un `RemoteServer` enregistré vers ce pair, et appelle `RemoteCall` via le service `IServiceEndpoint` réel — vérifie qu'un appel HTTP réel traverse effectivement les deux processus |
| `test_readme.py` | les exemples du README restent exécutables |

## 8. Hors périmètre

- **Mirroring générique par réflexion** de tout composant `YCappuccinoRemote` (le cœur du legacy) :
  remplacé par un appel explicite, nommé, à un service exposé — voir l'objectif.
- **Découverte de pairs, heartbeat, failover, client-factory dynamique** : `RemoteServer` est un registre
  géré à la main via `/api/crud/remote-servers`.
- **Transmission de l'authentification au pair** : voir section 3 — nécessiterait un changement de contrat
  d'`IExposedService.call` (accès aux en-têtes bruts) hors du périmètre autorisé pour ce sous-projet, ou un
  couplage de clé JWT entre instances non demandé. Évolution possible : une nouvelle méthode
  `IExposedService.call_with_headers` ou un mécanisme de « jeton de service » partagé entre pairs de
  confiance — à spécifier séparément si le besoin se confirme.
- **Retry, circuit breaker, timeout adaptatif** : une erreur réseau devient un `500` générique, sans
  nouvelle tentative.
- **Stockage MongoDB dédié à `remote`** (`remote_storage_mongo.py` du legacy) : `RemoteServer` est un
  `@Item` normal, persisté par le backend de `storage` déjà en place (mémoire ou Mongo), sans code
  spécifique.

## Addendum (2026-09-16) : découverte dynamique et `FederatedServiceEndpoint`

**Demande** : l'appelant ne doit plus jamais nommer un pair au site d'appel. Un composant qui dépend
d'`IServiceEndpoint` et appelle `endpoint.call("service_b", ...)` doit atteindre `service_b`, qu'il
soit local ou exposé par un autre conteneur, sans savoir lequel — « comme si c'était dans le même
processus ». Deux options ont été soumises à l'utilisateur pour savoir comment un conteneur
apprendrait quel pair héberge quel service : (A) déclaration explicite sur `RemoteServer`, ou
(B) découverte dynamique en interrogeant les pairs. **L'utilisateur a choisi (B)** : `RemoteServer`
reste minimal (`host`/`port`/`scheme`), aucune liste de services n'y est ajoutée.

Ceci est une **addition** : `RemoteServer`, `RemoteCall` et l'adressage `remote_call/<peer_id>/<service>`
existants ne changent pas et restent utilisables tels quels par un appelant qui connaît déjà le pair
exact qu'il veut viser.

### A. `RemoteCapabilities` (`capabilities.py`)

Un `IExposedService` de plus, publié sous le nom réservé **`__remote_capabilities__`**, `secure = False`.
Il prend la même dépendance vivante `services: list[IExposedService]` que
`endpoints_service.ServiceEndpoint`/`FederatedServiceEndpoint`, et répond
`{"services": [s.name for s in self._services if s.name]}` — uniquement des noms, jamais de données.

Toute instance qui charge `ycappuccino.remote` dans son `bundle_prefix` expose donc automatiquement
ce qu'elle publie localement, sans code applicatif.

**Compromis de sécurité assumé, pas un oubli** : `secure=False` est nécessaire parce que
`RemoteCall`/`FederatedServiceEndpoint` ne transmettent jamais de sujet au pair (section 3
ci-dessus) — un `__remote_capabilities__` sécurisé ne pourrait jamais être interrogé par un autre
pair, la découverte serait impossible. Conséquence honnête : **tout appelant capable d'atteindre le
port HTTP de cette instance peut lister les noms de tous les `IExposedService` qu'elle publie
localement** (pas les appeler, pas voir de données — juste les noms). C'est une divulgation
d'information mineure, cohérente avec le périmètre déjà assumé de `remote` (« cluster interne de
confiance, pas de transmission d'authentification »). Ne jamais mettre d'information sensible dans
le *nom* d'un service.

### B. `ServiceDirectory` (`discovery.py`) : cache de découverte

Composant natif (`YCappuccinoComponent`), dépend d'`IManager` (registre `RemoteServer`, lecture
`subject=None`, même convention que `RemoteCall`) et, comme `RemoteCall`, d'un `opener` HTTP
injectable et d'un `timeout`.

- **`start()`** : interroge, au mieux, `__remote_capabilities__` de **chaque** `RemoteServer`
  actuellement enregistré (via le helper HTTP partagé, section « Helper HTTP partagé » ci-dessous) et
  peuple un cache `{nom_de_service: peer_id}`. Un pair injoignable est **loggé en warning et ignoré** —
  ne fait jamais échouer le démarrage.
- **`locate(service_name) -> Optional[str]`** : lit le cache d'abord (aucun appel HTTP en cas de
  succès). En cas d'échec (nom absent du cache), **réinterroge en direct tous les pairs actuellement
  enregistrés** (pas seulement ceux qui avaient échoué), puis relit le cache. Retourne `None` si le
  service n'est trouvé nulle part.
- Si deux pairs annoncent le même nom de service, le **premier découvert l'emporte** (ordre de
  `get_many()`, non garanti configurable) — un cas limite que l'opérateur doit éviter en n'exposant
  pas le même nom sur deux pairs interrogés par la même instance.

**Compromis de péremption (staleness), assumé et documenté, pas un bug** : le cache ne reflète que ce
que la découverte a vu. Entre deux découvertes, un pair qui commence à exposer un nouveau service,
arrête d'en exposer un, ou change d'hôte/port, est invisible pour une entrée déjà en cache — un
service déjà mis en cache vers le pair A y reste même si A ne le sert plus, jusqu'à ce que l'appel
distant réel échoue (pas détecté par `ServiceDirectory` lui-même). Il n'y a ni invalidation push, ni
heartbeat, ni TTL. Ceci prolonge, sans le contredire, le choix déjà assumé pour `RemoteServer`
lui-même (« Hors périmètre » : pas de heartbeat/failover) : la découverte ici est **best-effort et
non autoritaire**, jamais une source de vérité temps réel.

### C. `FederatedServiceEndpoint` (`federated_endpoint.py`) : précédence locale puis distante

Un `IServiceEndpoint` complet et indépendant (ne dépend pas de `ServiceEndpoint`, voir « Pourquoi »
ci-dessous). Dépendances : `services: list[IExposedService]`, `authorizations: list[IAuthorization]`
(dupliqué depuis `endpoints_service.endpoint.ServiceEndpoint`, comportement identique — sujet requis
si `secure`, `Forbidden` si aucune `IAuthorization` publiée, sinon délégation à
`authorizations[0].is_authorized(subject, CALL, name)`), plus `directory: ServiceDirectory` et
`manager: IManager` (pour résoudre `host`/`port`/`scheme` du pair une fois son `peer_id` connu, comme
`RemoteCall`).

`call(name, method, extra_path, params, body, subject)` :

1. Recherche `name` dans `services` (linéaire, dupliquée depuis `ServiceEndpoint._find`, voir
   « Pourquoi » — pas de dépendance à `ServiceEndpoint` lui-même).
2. **Trouvé localement** : vérifie son autorisation exactement comme `ServiceEndpoint`, puis l'appelle
   localement. **Aucune consultation de `ServiceDirectory` dans ce cas** — le local a toujours
   priorité, jamais de détour réseau pour un nom que l'instance sait déjà servir elle-même.
3. **Pas trouvé localement** : `directory.locate(name)`. `None` → `NotFound(name)`, exactement le
   comportement qu'un `ServiceEndpoint` purement local aurait pour un nom inconnu.
4. **Pair trouvé** : résout son document via `manager.get_one("remoteServer", peer_id, subject=None)`
   (`NotFound` si le pair a disparu du registre entre la découverte et l'appel) puis relaie l'appel
   **directement** vers `/api/services/<name>[...]` de ce pair, via le même helper HTTP que
   `RemoteCall` — **pas** via l'adressage `remote_call/<peer_id>/<service>` : le pair est déjà connu,
   `remote_call` n'apporterait rien ici. Aucun sujet n'est transmis (section 3) : le service ciblé sur
   le pair doit être `secure=False`, sinon l'appel distant se traduit en `NotAuthenticated`/`Forbidden`
   local, sans planter.

**Le site d'appel est donc indistinguable d'un appel local** : `endpoint.call("service_b", "POST",
[], {}, body, None)` ne contient ni identifiant de pair, ni marqueur « ceci est distant ».

#### Pourquoi dupliquer la logique locale plutôt que de dépendre de `ServiceEndpoint`

iPOPO/Pelix a une règle de départage déterministe pour plusieurs fournisseurs d'une même
spécification : `service.ranking` le plus haut gagne, égalité départagée par le plus petit
`service_id` (donc premier enregistré) — voir `pelix.internals.registry.ServiceReference.__compute_key`.
Ni `ServiceEndpoint` ni `FederatedServiceEndpoint` ne fixent de `service.ranking` explicite, donc à
égalité (0 partout), l'ordre suit l'ordre de scan de `bundle_prefix` (voir `core/README.md`).

Mais **c'est pire qu'une simple histoire de priorité** : `http_server.ApiServlet._route_services`
(http_server/src/main/python/ycappuccino/http_server/servlet.py) ne consulte **jamais que
`services[0]`** de sa liste vivante `list[IServiceEndpoint]`, pour **chaque** requête
`/api/services/*` — il n'y a aucun routage par service, aucun essai du suivant si le premier échoue.
Charger `ycappuccino.endpoints_service` **et** `ycappuccino.remote` ensemble rend donc l'un des deux
fournisseurs d'`IServiceEndpoint` **totalement mort pour tout appel HTTP**, silencieusement, au gré
d'un ordre de scan incident (pas une priorité documentée et stable sur laquelle concevoir quoi que ce
soit) : si `ServiceEndpoint` gagne, la découverte dynamique de `FederatedServiceEndpoint` ne se
déclenche jamais pour aucun appel HTTP ; si `FederatedServiceEndpoint` gagne, ça fonctionne, mais par
accident d'ordre de chargement, pas par conception. C'est cette raison précise — et non une simple
absence de mécanisme de priorité — qui interdit à `FederatedServiceEndpoint` de dépendre de
`ServiceEndpoint` et de lui déléguer son cas local : il doit être une implémentation complète et
indépendante, substituée à `ServiceEndpoint`, jamais ajoutée à côté.

**Règle opérationnelle (documentée aussi dans le README)** : charger `ycappuccino.remote` **à la
place** d'`ycappuccino.endpoints_service` pour le rôle `IServiceEndpoint` d'une instance qui veut des
appels fédérés. Ne jamais charger les deux ensemble pour ce rôle.

### D. Helper HTTP partagé (`_http.py`)

`RemoteCall`, `ServiceDirectory` et `FederatedServiceEndpoint` partagent désormais un seul point
d'implémentation pour l'appel `urllib` et la traduction de statut (`call_peer`/`build_url`, extraits
de l'ancien `call.py`) : la logique de la section 2 ci-dessus n'est écrite qu'une fois. Comportement
inchangé, y compris la propagation non enveloppée des erreurs réseau (section 2/9).

### E. Tests ajoutés

| Fichier | Contenu |
|---|---|
| `test_capabilities.py` | `RemoteCapabilities` contre une liste de faux `IExposedService` |
| `test_discovery.py` | `ServiceDirectory` : découverte au démarrage, pair injoignable ne casse pas `start()`, cache hit sans requête, cache miss déclenche une requête live, service absent partout → `None` |
| `test_federated_endpoint.py` | `FederatedServiceEndpoint` : tous les cas locaux de `ServiceEndpoint` reproduits, priorité locale sur un nom aussi connu du répertoire, bascule distante, pair disparu, absent partout → `NotFound` |
| `test_federated_framework.py` | **Le test le plus important de cet addendum** : deux vrais processus (« conteneur A » en cours de test, « conteneur B » en sous-processus, port 18161), `FederatedServiceEndpoint` + `RemoteServer` seuls, aucun `peer_id`/nom de service au site d'appel — prouve la transparence de bout en bout |

## 9. Risques

- **Aucune authentification vers le pair** : un `RemoteServer` mal configuré (pointant vers un service
  interne non protégé) expose ce service à quiconque contrôle `remote_call` localement — atténué par
  `secure_read=True, secure_write=True` sur `RemoteServer` (seul un opérateur autorisé peut enregistrer un
  pair) et `secure=True` sur `remote_call` lui-même (seul un appelant autorisé peut l'utiliser). La
  responsabilité de n'exposer que des services publics sur le pair reste à l'opérateur.
- **Pas de pool de connexions ni de réutilisation de socket** : chaque appel ouvre une connexion HTTP via
  `urllib`, comme les tests d'intégration d'`http_server` — suffisant pour le volume visé (un appel
  explicite, pas un flux), à revisiter si `remote` devient un chemin chaud.
- **Sous-processus dans le test d'intégration** : plus lent et plus fragile qu'un test in-process
  (dépendance à `uv`/l'environnement Python du sous-processus, délai de démarrage attendu par polling) —
  accepté car c'est la seule façon d'obtenir deux `Framework` Pelix réels simultanés, et c'est la forme la
  plus fidèle du scénario réel (deux processus distincts).
- **(2026-09-16) `__remote_capabilities__` divulgue les noms de services locaux sans authentification** :
  tout appelant réseau peut lister ce qu'une instance publie, atténué uniquement par le fait que seuls des
  *noms* sont exposés (pas de données, pas d'appel possible via ce service) — voir addendum, section A.
  Assumé, pas corrigé, pour la même raison que l'absence d'authentification vers le pair (section 3) :
  corriger nécessiterait de transmettre un sujet, hors périmètre.
- **(2026-09-16) `ServiceDirectory` peut être périmé** : un service qui change de pair, disparaît ou
  apparaît entre deux découvertes n'est visible qu'à la prochaine découverte (miss de cache ou nouveau
  `start()`), jamais poussé — voir addendum, section B. Un appel vers une entrée périmée échoue au moment
  de l'appel réel (erreur réseau non enveloppée, ou 404 du pair), pas avant.
- **(2026-09-16) Charger `ycappuccino.endpoints_service` et `ycappuccino.remote` ensemble rend l'un des
  deux `IServiceEndpoint` totalement inopérant pour les appels HTTP**, silencieusement, selon l'ordre de
  scan de `bundle_prefix` — voir addendum, section C « Pourquoi ». Pas une simple ambiguïté de priorité :
  une régression silencieuse et totale pour l'un des deux mécanismes. Documenté comme règle opérationnelle
  dans le README, non applicable automatiquement par le framework (aucun garde-fou technique ajouté).

## Addendum (2026-09-16, partie 2) : découverte générique et proxys dynamiques pour N'IMPORTE QUELLE spécification

**Demande** (relayée par le coordinateur, après une troisième clarification) : chaque instance `core`
doit produire la liste de ses composants embarqués ; `remote` doit pouvoir l'interroger, par
processus/pair ; et les proxys distants doivent être créés **à la volée**, pour n'importe quelle
spécification, pas seulement `IServiceEndpoint` — y compris des interfaces définies par une
application, jamais vues par `remote`. Le coordinateur a soumis deux options à l'utilisateur : un
périmètre restreint (table d'interfaces connues, comme `client.remote_proxy.make_remote` le fait pour
`ICrud`/`IDrafts`/`IItemCatalog`/`IServiceEndpoint`) ou un mécanisme totalement générique (n'importe
quelle interface, y compris applicative, zéro liste d'interfaces codée en dur). **L'utilisateur a
choisi explicitement l'option totalement générique.**

Ce que `core` fournit déjà, sans qu'aucune tâche de cet addendum n'y touche (voir `core/README.md`) :
`component_factory.describe_component(klass).provides_qualified` (chemins qualifiés, alignés index à
index avec `provides`), `component_factory.resolve_class(chemin)` (résout un chemin qualifié en la
classe réelle), `Framework.list_components()` (liste de tous les composants natifs installés, un
dict `{"module","class","provides"}` par composant, `provides` déjà en chemins qualifiés), et
`Framework.instantiate_component()`/`destroy_component()` (déjà utilisés par `component-creator`).

### A. `RemoteCapabilities` élargi (`capabilities.py`)

Élargissement strictement additif, jamais un remplacement : la réponse `{"services": [...]}`
existante (section addendum partie A ci-dessus) reste identique caractère pour caractère quand
`Framework.get_framework().list_components()` est vide (aucun `Framework` réellement démarré, ou
zéro composant natif installé) — c'est exactement le cas de tous les tests unitaires existants de
`RemoteCapabilities`, qui restent verts sans aucune modification. Quand `list_components()` renvoie
quelque chose, une clé `"components"` est ajoutée à côté de `"services"` :
`{"services": [...], "components": [{"module": ..., "class": ..., "provides": [...]}, ...]}`.

`Framework.get_framework()` est appelé directement (jamais injecté) dans `call()`, exactement comme
`component-creator`'s `FrameworkComponentInstantiator` le fait déjà pour
`instantiate_component`/`destroy_component` — c'est l'idiome déjà établi pour accéder au framework
réel depuis un composant natif, `Framework` n'étant pas une spécification injectable par le DI de
`core` (ni `YCappuccinoComponent`, ni `YCappuccinoRemote`).

**Élargissement de sécurité, au-delà de celui déjà accepté en partie A** : `"components"` expose,
pour **chaque** composant natif installé localement, son module, sa classe, et le chemin qualifié de
**chaque** spécification qu'il fournit — pas seulement les noms des `IExposedService` volontairement
publiés. Un appelant réseau peut désormais énumérer la topologie interne complète d'une instance
(quelles classes existent, quelles interfaces internes — `IManager`, `ITrigger`, `IAuthorization`,
n'importe quelle interface applicative — sont implémentées où). C'est un prérequis structurel du
mécanisme générique de la partie C ci-dessous (une spécification ne peut être découverte que si elle
est listée), assumé pour la même raison que `secure=False` sur `__remote_capabilities__` lui-même
(partie A) : aucun sujet n'est jamais transmis à un pair, donc un `__remote_capabilities__` sécurisé
serait interrogeable par personne d'autre. Ne jamais exposer `ycappuccino.remote` en dehors d'un
réseau interne de confiance — vrai avant cet addendum, nettement plus conséquent après (voir aussi
partie C, « Sécurité »).

### B. `ComponentDirectory` (`component_directory.py`) : découverte + création à la volée

Composant natif séparé de `ServiceDirectory` (délibérément, pas une fusion) : `ServiceDirectory`
répond à une question plus étroite (« quel pair sert le service nommé X ») pour le seul besoin de
repli au moment de l'appel de `FederatedServiceEndpoint`, sans jamais rien créer.
`ComponentDirectory` répond à une question différente (« quelles spécifications existent n'importe
où, et ai-je besoin d'un proxy local pour l'une d'elles ») et a un effet de bord réel (installer un
composant). Les séparer suit le même principe que `RemoteCall`/`RemoteDispatch` ou
`ServiceDirectory`/`FederatedServiceEndpoint` : chaque composant a un seul « client », un seul
mécanisme de découverte.

**Découverte** : même discipline que `ServiceDirectory` — interroge, au mieux, chaque `RemoteServer`
enregistré (pair injoignable : loggé en warning, jamais fatal), construit un cache
`{chemin_qualifié: (peer_id, document)}`, premier pair découvert gagnant en cas de doublon.
`locate(chemin_qualifié)` relit le cache d'abord, réinterroge tous les pairs en direct sur un miss —
contrat identique à `ServiceDirectory.locate()`, même compromis de péremption (voir partie B
ci-dessus, non répété ici).

**Deux exclusions structurelles, pas une liste d'interfaces codée en dur** : (1) le propre nom de
classe concret d'un composant pair (toujours présent dans `provides` à côté de ses interfaces, voir
`component_factory._provided_specifications`) n'a aucune méthode abstraite à substituer — créer un
« proxy » dessus reviendrait à hériter silencieusement du **vrai comportement local** du pair via le
MRO, sans jamais traverser le réseau, potentiellement cassé par l'absence du vrai constructeur du
pair. `ComponentDirectory` ignore tout chemin résolu vers une classe non abstraite
(`inspect.isabstract`). (2) `"pelix.http.servlet"`, le marqueur documenté par `core` lui-même
(`ComponentDescription.provides_qualified`, voir son docstring) comme n'étant jamais une vraie classe
Python : ignoré sans même tenter `resolve_class`. Les deux s'appliquent uniformément à **toute**
spécification remplissant ces critères structurels, jamais à une interface nommée explicitement.

**Cas limite accepté, observé dans le test deux-processus** : une spécification résolvable mais dont
`describe_component`/`create_factory_module` a une exigence supplémentaire non satisfaite par le
proxy générique (ex. `IHttpServlet`, qui exige une propriété `"path"` que `make_generic_proxy` ne
fournit pas) échoue à l'instanciation — capturée par le même bloc `except Exception` que toute autre
erreur de création, loggée en warning, ignorée. Pas une régression : un proxy générique ne peut, par
construction, satisfaire des exigences de composant propres à une interface particulière (comme
`IHttpServlet` en a une) sans redevenir une table d'interfaces connues — hors périmètre pour cette
itération, voir « Hors périmètre » ci-dessous.

**Quand la découverte (et donc la création de proxy) s'exécute** — ce que `ServiceDirectory` n'a pas
à résoudre, puisque son unique consommateur (`FederatedServiceEndpoint`) la redéclenche
paresseusement à chaque appel : `ComponentDirectory` est aussi un `ITrigger` sur les upserts de
`remoteServer` (`item_id="remoteServer", actions=("upsert",), post=True`). Enregistrer un nouveau
pair (`manager.up_sert_model(RemoteServer(...))`) est donc ce qui déclenche, de façon proactive, la
(re)découverte et la création de proxy — rien d'autre ne le ferait, puisqu'aucun code ici n'est
appelé à chaque appel métier comme `ServiceDirectory.locate()` l'est. `core/README.md` énonce
explicitement qu'une réaction à un événement (un `ITrigger`, un service lié plus tard) n'est **pas**
le cas dangereux que son « Piège de timing » documente — seul un appel **synchrone depuis le
`start()`/`stop()` propre d'un composant** peut interbloquer — donc `execute()` appelle
`instantiate_component()` directement, synchrone, sans thread. `start()`, en revanche, **est** ce cas
dangereux pour les `RemoteServer` déjà enregistrés au démarrage : il délègue tout le travail
« découvrir l'existant, créer les proxys manquants » à un thread détaché et retourne immédiatement,
exactement comme `ycappuccino-component-creator`'s `ComponentActivator.start()` (voir son docstring).

### C. Proxy dynamique générique et `RemoteDispatch` : le mécanisme de bout en bout

**Pourquoi ni les conventions HTTP-shape de `client.remote_proxy.make_remote`, ni l'adressage
`remote_call`/`FederatedServiceEndpoint` ne s'appliquent** : les deux supposent une cible connue à
l'avance — un service nommé (`RemoteCall`/`FederatedServiceEndpoint`) ou une des quatre routes REST
fixes de `http_server` inférées du nom de méthode (`client`, spec §9, pour
`ICrud`/`IDrafts`/`IItemCatalog`/`IServiceEndpoint` uniquement). Une interface applicative arbitraire
(`IInventoryService.check_stock(sku)`, par exemple) n'a ni nom de service, ni route REST connue :
aucune convention de mise en forme HTTP n'existe pour elle. Le mécanisme retenu est donc un **RPC
générique** : chaque appel de méthode sérialise `(nom_de_méthode, args, kwargs)` en JSON, envoyé en
`POST` à un point d'entrée générique unique — jamais `/api/services/<nom>` (propre à un
`IExposedService` nommé), un nouveau point d'entrée réservé, `__remote_dispatch__`.

**`make_generic_proxy` (`remote_proxy.py`), côté appelant** : synthèse par réflexion, technique
identique à `client.remote_proxy.make_remote` (un vrai `__init__`/des vraies méthodes forgés via
`exec()`, la technique de `dataclasses`/`attrs`/`namedtuple`, pour que `describe_component` les
introspecte comme un composant écrit à la main — voir spec `client` §9.2), mais sans importer
`client` (aucune dépendance croisée dans ce sens : `remote` reste backend, `client` reste
navigateur). Chaque méthode abstraite de l'interface (hors `start`/`stop`) devient un appel
`self._dispatch(nom, {param: valeur, ...})`, qui poste `{"kwargs": {...}}` vers
`__remote_dispatch__/<chemin_qualifié>/<nom_de_méthode>` du pair et déballe `{"result": ...}`. Le
constructeur du proxy (`peer_host`, `peer_port`, `peer_scheme`, `timeout`, `opener`) suit la
convention déjà établie par `RemoteCall`/`ServiceDirectory` (paramètre non annoté avec valeur par
défaut = propriété de composant ordinaire) — pensé pour être créé via
`Framework.instantiate_component(classe_proxy, properties={...})`.

**`RemoteDispatch` (`dispatch.py`), côté pair, le NOUVEAU mécanisme de réception** — la pièce
réellement nouvelle de cet addendum, plus impliquée que tout ce qui a précédé puisqu'elle exige un
mécanisme de réception, pas seulement un mécanisme d'appel sortant. Un `IExposedService` de plus,
publié sous le nom réservé **`__remote_dispatch__`**, `secure=False` (même raison structurelle que
`__remote_capabilities__` : aucun sujet n'est jamais transmis à un pair, un service sécurisé ne
serait jamais interrogeable). Forme du fil :

```
POST /api/services/__remote_dispatch__/<chemin qualifié>/<nom de méthode>
body: {"args": [...], "kwargs": {...}}          (l'un ou l'autre, ou aucun -- défaut [] / {})

-> {"status": 200, "meta": {}, "data": {"result": <valeur JSON>}}
```

`<chemin qualifié>` est résolu en la classe réelle via `resolve_class` (déjà fourni par `core`) ;
`interface.__name__` (le nom court) sert ensuite à retrouver l'instance Pelix **réelle** actuellement
publiée sous cette spécification, via `Framework.get_framework().context.get_service_reference(...)`
/ `.get_service(...)` / `.unget_service(...)` (l'API `BundleContext` standard de Pelix, déjà
utilisée à l'identique par les tests d'intégration existants de `remote` pour retrouver un service
par son nom court) — **jamais** un second registre propre à `remote`. La méthode cible est appelée
avec les `args`/`kwargs` donnés, awaité si elle renvoie une coroutine (le cas normal pour une méthode
de composant natif), exactement comme un appel local. `resolve`/`locate_service`/`release_service`
sont injectables (même convention que `opener`), donc entièrement testable unitairement sans
`Framework` réel ni socket.

**Sécurité — un élargissement réel, signalé explicitement, pas un détail cosmétique.** Avant cet
addendum, un appelant réseau non authentifié ne pouvait atteindre qu'un `IExposedService`
**délibérément** publié sous un nom choisi par son auteur — qui pouvait encore le garder totalement
hors de portée d'un pair en le laissant `secure=True` (un service sécurisé appelé via
`RemoteCall`/`FederatedServiceEndpoint` devient `NotAuthenticated`/`Forbidden`, aucun sujet n'étant
jamais transmis). `__remote_dispatch__` n'a **aucune** frontière de ce type : **n'importe quelle**
méthode de **n'importe quelle** spécification que `describe_component()` a vue localement —
`IManager`, `ITrigger`, `IAuthorization`, une interface interne applicative jamais pensée pour être
appelée par le réseau — devient appelable ainsi, par quiconque atteint le port HTTP de l'instance,
sans contrôle d'autorisation propre au-delà de l'exclusion structurelle des méthodes de cycle de vie
(`start`/`stop`) et privées (préfixe `_`). C'est la conséquence directe et acceptée du choix explicite
de l'utilisateur (mécanisme totalement générique, zéro liste d'interfaces codée en dur) plutôt qu'un
périmètre plus restreint et sélectionné — pas un oubli, et ce document ne cherche pas à l'atténuer.
Ne jamais charger `ycappuccino.remote` sur une instance joignable en dehors d'un réseau interne de
confiance — vrai avant cet addendum, nettement plus lourd de conséquences après.

### D. Le piège de timing : résolution retenue

`core/README.md` (« Piège de timing ») énonce que rien ne garantit qu'un composant créé
dynamiquement par `instantiate_component()`, en arrière-plan depuis le `start()` d'un découvreur,
existe déjà quand `load_bundles()` instancie, dans la **même passe** de scan de `bundle_prefix`,
d'autres composants natifs — y compris via leurs services Pelix publiés, y compris `list_components()`.
Trois options étaient proposées : (a) séquencer la création du **consommateur** lui-même après la
découverte, (b) accepter la dépendance tardive (optionnelle/liste, jamais requise au sens strict),
(c) une autre approche justifiée.

**Retenu : (b).** L'option (a) exigerait une orchestration générique supplémentaire (« instancier ce
composant seulement après que telle spécification a été découverte ») — une fonctionnalité à part
entière, hors du périmètre resserré retenu pour cette itération (voir « Hors périmètre »). L'option
(b) est directement soutenue par un mécanisme déjà existant et déjà éprouvé ailleurs dans `core` :
une dépendance optionnelle ou agrégée (`list[Interface]`) est toujours « disponible » (vide au pire)
à la validation, et `bind()`/`un_bind()` d'iPOPO la met à jour automatiquement dès qu'un fournisseur
apparaît — aucun code de `remote` n'a à réinventer ce mécanisme, il n'a qu'à documenter que c'est le
contrat opérationnel attendu.

**Contrat opérationnel** : un composant qui dépend d'une interface potentiellement fédérée
dynamiquement doit la déclarer comme dépendance **optionnelle ou agrégée** (`list[Interface]`),
jamais comme paramètre de constructeur requis au sens strict, et réagir à son arrivée via `bind()`
(ou en la relisant à chaque usage, comme `list[Interface]` le permet nativement). Un composant qui a
réellement besoin d'un accès synchrone et garanti à une interface fédérée dynamiquement doit être
lui-même créé dynamiquement, séquencé après que `ComponentDirectory.locate()`/la découverte a résolu
cette spécification — option (a), non outillée génériquement ici.

**Preuve réelle, pas seulement affirmée** : `test_component_directory_framework.py` (voir « Tests »)
construit exactement ce scénario avec deux processus réels — un consommateur en conteneur A dont la
liste est vérifiée **vide juste après le démarrage du framework** (aucun `RemoteServer` encore
enregistré : le piège est réel, pas un épouvantail), puis enregistre un pair en conteneur B, et vérifie
que la liste devient non vide et que l'appel obtient le résultat **réellement calculé en conteneur B**.

### E. Limitations acceptées, honnêtement

- **Formes d'argument/retour supportées : uniquement JSON-sérialisable** (`str`/`int`/`float`/`bool`/
  `None`/`list`/`dict`) — comme le reste de l'enveloppe de `remote` l'exige déjà. `bytes`, des
  dataclasses/objets applicatifs, des générateurs asynchrones : non supportés, non interceptés
  spécifiquement (l'échec se produit à la sérialisation JSON habituelle, côté `http_server` ou côté
  `call_peer`).
- **`*args`/`**kwargs` d'une méthode d'interface** : fonctionnent seulement via la clé `"args"` du
  corps JSON (positionnel) — `make_generic_proxy` lui-même n'en génère jamais (les interfaces
  `YCappuccino` connues n'en utilisent pas), seul un appel construit à la main vers
  `__remote_dispatch__` pourrait les exploiter.
- **Une spécification résolvable dont l'instanciation du proxy générique échoue** (ex. `IHttpServlet`,
  qui exige une propriété `"path"` que le proxy générique ne fournit pas) : loggée en warning,
  ignorée, jamais retentée — cas limite observé et documenté, pas corrigé (corriger nécessiterait de
  redevenir une table d'interfaces connues, contraire au choix de l'utilisateur).
- **Aucune orchestration générique pour l'option (a)** du piège de timing (partie D) : un composant
  ayant besoin d'un accès synchrone garanti à une interface fédérée doit être créé et séquencé « à la
  main » via `instantiate_component`/`ComponentDirectory.locate()`, aucun outillage dédié fourni ici.
- **Péremption du cache** de `ComponentDirectory`, identique à `ServiceDirectory` (partie B ci-dessus) :
  un pair qui cesse de fournir une spécification déjà proxée localement n'est jamais détecté par
  `ComponentDirectory` lui-même — le proxy déjà créé continue d'exister et échoue au prochain appel
  réel (erreur réseau non enveloppée, ou 404 du pair), jamais désinstallé automatiquement.
- **Élargissement de sécurité** (partie A et C ci-dessus) : accepté explicitement comme la
  conséquence du choix de l'utilisateur pour un mécanisme totalement générique — voir partie C,
  « Sécurité », non répété ici.

### F. Hors périmètre (cet addendum)

- Toute orchestration générique pour l'option (a) du piège de timing (partie D).
- Un mécanisme d'annulation/désinstallation automatique d'un proxy dynamique dont le pair a cessé de
  fournir la spécification (péremption, partie E).
- Un contrôle d'autorisation propre à `__remote_dispatch__` au-delà de l'exclusion structurelle des
  méthodes de cycle de vie et privées (partie C, « Sécurité ») — transmettre un sujet au pair reste
  hors périmètre pour la même raison que documentée en section 3 du corps principal de cette
  conception.
- Support de formes d'argument/retour non JSON-sérialisables (partie E).

### G. Tests ajoutés

| Fichier | Contenu |
|---|---|
| `test_capabilities.py` (étendu) | `RemoteCapabilities` : `"components"` omis quand `list_components()` est vide (forme existante inchangée), ajouté quand des composants natifs sont installés |
| `test_dispatch.py` | `RemoteDispatch` contre des `resolve`/`locate_service`/`release_service` faux : dispatch kwargs/args, awaite une coroutine ou non, libère toujours la référence, `extra_path` incomplet, chemin non résolvable, aucune instance locale, méthode inconnue, `start`/`stop`/méthode privée jamais dispatchables |
| `test_remote_proxy.py` | `make_generic_proxy` contre un faux opener : RPC générique kwargs vers `__remote_dispatch__`, valeurs par défaut des paramètres omis, classe concrète, `start`/`stop` no-op |
| `test_component_directory.py` | `ComponentDirectory` contre des faux : découverte/`locate()` (même contrat que `ServiceDirectory`), création de proxy pour une spécification absente localement, jamais pour une déjà locale, jamais deux fois, jamais pour le nom de classe concret d'un pair, jamais pour le marqueur `pelix.http.servlet`, réaction `execute()` à un upsert de `remoteServer`, `start()` délègue à un thread détaché sans bloquer |
| `test_component_directory_framework.py` | **Le test le plus important de cet addendum** : deux processus réels (conteneur B, port **18162** de la plage réservée), une interface applicative (`IInventoryService`) jamais vue par `remote`, un consommateur à dépendance agrégée vide au démarrage puis peuplée après l'enregistrement du pair, un appel dont le résultat est réellement calculé en conteneur B ; plus deux vérifications directes du fil HTTP (`__remote_capabilities__` élargi, `__remote_dispatch__`) |
