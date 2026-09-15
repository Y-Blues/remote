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
