# ycappuccino-remote

Appel d'un `IExposedService` nommé sur une **autre** instance YCappuccino (un autre processus, un autre
conteneur), en HTTP, avec la même enveloppe `{"status","meta","data"}` que `http_server` définit déjà : un
« client de service distant » est un client HTTP qui parle le protocole que le framework se parle à
lui-même.

Conception : [docs/superpowers/specs/2026-09-15-remote-design.md](docs/superpowers/specs/2026-09-15-remote-design.md).

Prérequis : lire les README de [core](../core/README.md), [storage](../storage/README.md) et
[endpoints_service](../endpoints_service/README.md). Le pair appelé a besoin de
[http_server](../http_server/README.md) et `endpoints_service` pour exposer son service, mais l'instance
appelante n'en a pas besoin elle-même (voir « Développer remote »).

## Mise en place

```bash
uv add --editable ../remote
```

`conf/application.yml` :

```yaml
bundle_prefix:
  - ycappuccino.storage
  - ycappuccino.endpoints_service
  - ycappuccino.remote
  - myapp
layers:
  ycappuccino_storage_memory:
    active: true
```

`RemoteCall` (`name = "remote_call"`) est publié dès que le package est chargé.

## Enregistrer un pair

`RemoteServer` est un `@Item` normal (`secure_read=True, secure_write=True`) : `http_server` expose
`/api/crud/remote-servers` sans code supplémentaire, ou on l'enregistre directement via `IManager` :

```python
from ycappuccino.remote.models.remote_server import RemoteServer

server = RemoteServer()
server.id("eu-node-2")           # identifiant choisi, utilisé dans l'adressage d'un appel
server.host("eu-node-2.internal")
server.port(9000)
server.scheme("http")
server.secret("clé partagée")    # la même des deux côtés, voir "Authentification"
await manager.up_sert_model(server)
```

**Important** : `remote` ne fait aucune découverte de pairs (pas de heartbeat, pas de failover) — choix
délibéré, voir la conception. Un `RemoteServer` invalide (pair injoignable) ne se voit qu'à l'appel.

## Appeler un service sur le pair

```
POST /api/services/remote_call/<peer id>/<nom du service>[/<segment>...]
```

`<peer id>` est l'`id` du `RemoteServer` enregistré, `<nom du service>` le nom sous lequel le service est
publié **sur le pair**. La query string et le corps JSON sont transmis tels quels ; les segments
supplémentaires du chemin (`extra_path`) aussi.

```python
from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.api.endpoints_service import IServiceEndpoint


class Caller(YCappuccinoComponent):
    def __init__(self, endpoint: IServiceEndpoint):
        self._endpoint = endpoint

    async def start(self):
        result = await self._endpoint.call(
            "remote_call", "POST", ["eu-node-2", "greeting"], {}, {"name": "world"}, None
        )
        print(result.body)  # {"greeting": "hello world"}

    async def stop(self):
        pass
```

## Authentification

Deux instances s'authentifient par une signature HMAC-SHA256 de chaque requête, avec le `secret` de leur
`RemoteServer` respectif (le même secret enregistré des deux côtés). Rien de secret ne circule :

- **en sortie** (`_http.call_peer`, utilisé par tous les appels vers un pair) : si le `RemoteServer` a un
  `secret`, la requête reçoit `X-YCappuccino-Peer` (le `name` de l'`application.yml` local),
  `X-YCappuccino-Timestamp` et `X-YCappuccino-Signature`, signature de
  `méthode\nchemin\nhorodatage\nsujet\n` + corps. Sans `secret`, rien n'est signé : le pair voit une
  requête anonyme ;
- **en entrée** : `PeerHmacAuthentication` (une `IAuthentication`, essayée par `http_server` avec les
  autres, par exemple le JWT de `permissions_app`) retrouve le `RemoteServer` annoncé, vérifie la signature
  et refuse un horodatage à plus de 60 s. Le sujet obtenu est `{"peer": <id>}`.

**Appel pour le compte d'un utilisateur** : le sujet reçu par `remote_call`, `FederatedServiceEndpoint` ou
un proxy généré (paramètre `subject`) est transmis au pair dans `X-YCappuccino-Subject`, couvert par la
signature ; le pair obtient alors `{"sub": ..., "tid": ..., "peer": <id>}`. Un sujet ne passe jamais sans
signature. Un service `secure=True` du pair est donc atteignable, son `IAuthorization` décide.

Pour charger l'authentification seule, sans le reste de `remote`, lister les modules dans `bundle_prefix` :
`ycappuccino.remote.models` et `ycappuccino.remote.peer_authentication`.

## Erreurs

Le statut de la réponse du pair est retraduit dans la même famille d'erreurs que les appels locaux
(`NotAuthenticated`/`Forbidden`/`NotFound`/`InvalidRequest`/générique). Une erreur réseau (pair injoignable,
délai dépassé) n'est pas enveloppée : elle remonte telle quelle et devient un `500` générique côté
`http_server` local, comme toute exception imprévue.

## Appel transparent : découverte dynamique (`FederatedServiceEndpoint`)

Tout ce qui précède demande à l'appelant de connaître le pair (`peer_id`) au site d'appel. Une autre
utilisation de `remote` couvre le cas où l'on ne veut **pas** le savoir : « le conteneur A contient le
composant appelant, le conteneur B contient `service_b` », et le code de A doit appeler `service_b`
exactement comme s'il était local, sans jamais nommer B.

**Comment un conteneur apprend quel pair héberge quel service** : par **découverte dynamique**, pas
par déclaration manuelle. Chaque instance qui charge `ycappuccino.remote` publie automatiquement
`__remote_capabilities__` (`secure=False`, liste seulement des *noms* de services, jamais de
données — voir la conception, addendum, section A pour le compromis de sécurité assumé).
`ServiceDirectory` interroge ce service sur chaque `RemoteServer` connu, au démarrage puis à la
demande (`locate()`), et met le résultat en cache — un cache qui peut être périmé entre deux
découvertes (voir la conception, section B). `FederatedServiceEndpoint` s'appuie dessus : il essaie
d'abord un service **local** (même comportement que `endpoints_service.ServiceEndpoint`, y compris
l'autorisation), et seulement si absent localement, demande à `ServiceDirectory` où il vit, puis
relaie l'appel en HTTP directement vers ce pair.

**Règle opérationnelle importante** : charger `ycappuccino.remote` **à la place** de
`ycappuccino.endpoints_service`, jamais les deux ensemble, quand une instance doit jouer ce rôle
`IServiceEndpoint`. iPOPO départage plusieurs fournisseurs d'une même spécification par
`service.ranking` puis par ordre d'enregistrement, mais `http_server.ApiServlet` ne consulte de toute
façon **que le premier** `IServiceEndpoint` de sa liste pour **toutes** les requêtes
`/api/services/*` (voir `_route_services`) : charger les deux rend l'un des deux silencieusement mort
pour tout appel HTTP, au gré de l'ordre de scan de `bundle_prefix` — voir la conception (addendum,
section C) pour le détail complet.

`conf/application.yml` du conteneur qui appelle (A) :

```yaml
bundle_prefix:
  - ycappuccino.storage
  - ycappuccino.remote        # fournit FederatedServiceEndpoint, PAS ycappuccino.endpoints_service
  - myapp
layers:
  ycappuccino_storage_memory:
    active: true
```

Le composant appelant ne connaît que le nom du service, exactement comme un appel local :

```python
from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.api.endpoints_service import IServiceEndpoint


class Caller(YCappuccinoComponent):
    def __init__(self, endpoint: IServiceEndpoint):
        self._endpoint = endpoint

    async def start(self):
        result = await self._endpoint.call("service_b", "POST", [], {}, {"name": "A"}, None)
        print(result.body)  # {"greeting": "hello A from container B"} -- que service_b soit local ou distant

    async def stop(self):
        pass
```

Il faut tout de même enregistrer un `RemoteServer` pointant vers le conteneur B pour que la
découverte le trouve (une seule fois, typiquement au démarrage d'un composant `IManager`) :

```python
from ycappuccino.remote.models.remote_server import RemoteServer

server = RemoteServer()
server.id("b")
server.host("localhost")
server.port(18161)
server.scheme("http")
await manager.up_sert_model(server, subject=None)
```

Le conteneur B, lui, expose `service_b` (`secure=True` possible si A et B partagent un `secret`, voir
« Authentification » ci-dessus) et charge lui aussi `ycappuccino.remote` (jamais
`ycappuccino.endpoints_service` en même temps) pour publier `__remote_capabilities__` et dispatcher
ses propres appels HTTP via `FederatedServiceEndpoint` :

```yaml
bundle_prefix:
  - ycappuccino.storage
  - ycappuccino.endpoints_storage   # requis par http_server.ApiServlet (ICrud/IDrafts/IItemCatalog)
  - ycappuccino.remote
  - ycappuccino.http_server
  - myapp
config:
  http_server:
    active: true
    port: 18161
```

Voir `src/unittest/python/test_federated_framework.py` pour la version exécutable complète et testée
de ce scénario (deux processus réels, port 18161 de la plage réservée 18160-18169) : mêmes noms de
service et même structure de `bundle_prefix` que ci-dessus, avec les assertions qui prouvent l'appel
transparent de bout en bout.

## Découverte générique et proxys dynamiques (n'importe quelle spécification)

Tout ce qui précède (`RemoteCall`, `FederatedServiceEndpoint`) ne fédère qu'`IServiceEndpoint`. Une
troisième utilisation de `remote` couvre le cas où l'interface à fédérer n'est **pas**
`IServiceEndpoint` du tout, mais une interface définie par une application (`IInventoryService`,
disons) — que `remote` n'a jamais vue et ne connaît pas à l'avance. Choix de l'utilisateur, explicite
et sans ambiguïté : un mécanisme **totalement générique**, aucune liste d'interfaces codée en dur.

**Mise en place** : `ycappuccino.remote` publie automatiquement, en plus de `__remote_capabilities__`
(section précédente), une clé `"components"` dans sa réponse — la liste `Framework.list_components()`
de l'instance — et un nouveau service réservé `__remote_dispatch__` capable d'appeler n'importe quelle méthode de
n'importe quelle spécification publiée localement, pour un pair authentifié (voir « Sécurité » plus bas).
Les deux instances doivent partager un `secret` : sans lui, l'appel est anonyme et une interface interne
n'est pas atteignable.

Un composant natif dépendant de `IInventoryService`, définie et implémentée **seulement** sur un
pair, obtient un proxy créé à la volée (résolu depuis le pair, synthétisé par réflexion,
implémentant réellement l'interface, forwardant chaque appel en HTTP vers `__remote_dispatch__` du
pair) **sans jamais nommer ce pair** :

```python
from ycappuccino.api.core_base import YCappuccinoComponent


class InventoryConsumer(YCappuccinoComponent):
    # IMPORTANT (voir « Contrat opérationnel » ci-dessous) : dépendance agrégée, jamais requise
    def __init__(self, inventories: list[IInventoryService]):
        self._inventories = inventories

    async def start(self):
        pass

    async def stop(self):
        pass

    async def check(self, sku):
        if not self._inventories:
            return None  # le pair n'a pas encore été découvert -- voir le contrat ci-dessous
        return await self._inventories[0].check_stock(sku)
```

Il faut, comme pour la découverte de service, enregistrer un `RemoteServer` pointant vers le pair
(une seule fois, typiquement au démarrage d'un composant `IManager`) — c'est cet enregistrement qui
déclenche la (re)découverte et la création du proxy, voir le contrat opérationnel ci-dessous.

### Contrat opérationnel : piège de timing, à respecter absolument

**Un composant qui dépend d'une interface fédérée dynamiquement de cette façon doit la déclarer
comme dépendance optionnelle ou agrégée (`list[Interface]`), jamais comme paramètre de constructeur
requis au sens strict.** Le proxy dynamique est créé en arrière-plan (thread détaché au démarrage, ou
de façon synchrone mais **après coup** lors de l'enregistrement d'un pair) : rien ne garantit qu'il
existe déjà quand `load_bundles()` instancie, dans la même passe de scan de `bundle_prefix`, un autre
composant qui en dépendrait de façon stricte — voir `core/README.md`, « Piège de timing », et la
conception (addendum, partie 2, section D) pour l'analyse complète et pourquoi cette option a été
retenue plutôt que de séquencer la création du consommateur lui-même. Une dépendance agrégée est
toujours « disponible » (vide au pire) et se peuple automatiquement (`bind()` d'iPOPO) dès que le
proxy apparaît — c'est le mécanisme que `test_component_directory_framework.py` prouve de bout en
bout, avec deux processus réels.

### Sécurité : deux niveaux d'accès

`__remote_dispatch__` décide selon le sujet que `http_server` a authentifié pour la requête (conception,
section 11.3) :

| Appelant | Méthodes appelables |
|---|---|
| pair signé (`PeerHmacAuthentication`, sujet avec `"peer"`) | toute méthode, sauf `start`/`stop`/`bind`/`un_bind`/`_privée` |
| utilisateur (JWT) ou anonyme, par exemple un navigateur `ycappuccino-client` | seulement les méthodes `@rpc_method` de l'interface |

Une méthode `@rpc_method` sécurisée (par défaut) exige un sujet autorisé par la première
`IAuthorization` à l'action `call` sur `<chemin qualifié>.<méthode>` (une `RolePermission`
`call:ycappuccino.api.permissions.ILoginService.*`, par exemple) ; `@rpc_method(secure=False)` laisse la
méthode contrôler elle-même (`Crud` via `Access`, `ServiceEndpoint` via le `secure` de chaque service,
`ILoginService.login`). La méthode cible reçoit le sujet authentifié dans son paramètre `subject` si elle
en déclare un ; un `subject` dans la charge utile est ignoré.

`__remote_capabilities__` suit la même règle : un pair voit tous les composants, les autres seulement les
interfaces qui ont au moins une méthode `@rpc_method`.

Un backend qui sert des navigateurs sans utiliser `FederatedServiceEndpoint` liste les modules plutôt que
le paquet dans `bundle_prefix` : `ycappuccino.remote.dispatch`, `ycappuccino.remote.capabilities`,
`ycappuccino.remote.models`, `ycappuccino.remote.peer_authentication` (le paquet entier fournirait un
second `IServiceEndpoint`, en conflit avec `endpoints_service`).

### Limites acceptées

Arguments/retours : uniquement JSON-sérialisable (`str`/`int`/`float`/`bool`/`None`/`list`/`dict`).
`*args`/`**kwargs` d'une interface : non générés par le proxy (uniquement exploitables via un appel
`__remote_dispatch__` construit à la main). Une spécification résolvable mais dont l'instanciation du
proxy générique échoue (ex. `IHttpServlet`, qui exige une propriété `"path"`) est ignorée en warning,
jamais retentée. Voir la conception (addendum, partie 2, section E) pour la liste complète.

## Catalogue : quelles interfaces, quelles signatures, sur quel serveur

`__remote_capabilities__` ajoute une clé `"descriptors"` (omise si vide) : pour chaque interface listée
dans `"components"`, la signature des méthodes que l'appelant peut invoquer (`signatures.describe_interface`) :

```json
{"specification": "ycappuccino.api.permissions.ILoginService",
 "methods": [{"name": "login", "params": {"login": "str", "password": "str"}, "return_type": "str",
              "rpc": {"method": "POST", "path": "", "summary": "...", "secure": false}}]}
```

Un pair signé reçoit toutes les méthodes appelables (`"rpc": null` pour une méthode interne) ; les autres,
seulement les `@rpc_method`. Les types sont des noms (`"str"`, `"dict | None"`, `"module.Classe"`) ; le
paramètre `subject` n'apparaît jamais.

`ServiceCatalog` (publié avec `ycappuccino.remote`) garde ces descriptions dans l'item `ServiceDescriptor`
(collection `service_descriptors`, `/api/crud/service-descriptors`, lecture et écriture sécurisées) :
une entrée par couple (pair, interface), `peer_id` étant l'id du `RemoteServer` par lequel l'appeler.
Chaque instance garde les descriptions de ses pairs dans son propre stockage : rien n'est partagé.

```python
await catalog.refresh_peer("eu-node-2")   # relit ce pair et remplace ce qu'on savait de lui
await catalog.refresh_all()               # tous les RemoteServer ; un pair injoignable garde sa dernière description
await catalog.locate("ycappuccino.api.permissions.ILoginService")
# [{"peer_id": "", "methods": [...]},                                   <- cette instance, si elle la fournit
#  {"peer_id": "eu-node-2", "host": "eu-node-2.internal", "port": 9000, "scheme": "http", "methods": [...]}]
```

`start()` lance `refresh_all()` en arrière-plan. Un pair dont le secret est faux est traité en anonyme :
il ne décrit que ses interfaces publiques.

## Tester avec remote

`RemoteCall` s'instancie directement avec un faux `IManager` et un faux « opener » HTTP, sans socket réel :

```python
import json
import unittest

from ycappuccino.remote.call import RemoteCall
from ycappuccino.remote.models.remote_server import RemoteServer


class FakeManager:
    def __init__(self, peers):
        self._peers = peers

    async def get_one(self, item_id, id, params=None, subject=None):
        document = self._peers.get(id)
        if document is None:
            return None
        server = RemoteServer()
        server.id(id)
        server.host(document["host"])
        server.port(document["port"])
        server.scheme(document["scheme"])
        return server

    async def start(self):
        pass

    async def stop(self):
        pass


class FakeResponse:
    def __init__(self, payload):
        self.status = 200
        self.headers = {}
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self, request, timeout=None):
        return FakeResponse(self.payload)


class TestRemoteCall(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_to_the_peer(self):
        manager = FakeManager({"peer-a": {"host": "peer.example", "port": 9000, "scheme": "http"}})
        remote_call = RemoteCall(manager, opener=FakeOpener({"status": 200, "meta": {}, "data": {"ok": True}}))

        result = await remote_call.call("GET", ["peer-a", "echo"], {}, None, None)

        self.assertEqual(result.body, {"ok": True})
```

## Développer remote

```bash
uv sync
uv run python -m unittest discover -s src/unittest/python
```

L'exemple a deux applications : le pair (`example/peer/`, `http_server` actif) et l'appelant
(`example/`, `remote` chargé). Lancer le pair d'abord, puis l'appelant, dans deux terminaux :

```bash
cd example/peer && uv run --project ../.. ycappuccino
```

```bash
cd example && uv run --project .. ycappuccino
```

`ycappuccino-http-server` et `ycappuccino-endpoints-service` sont des dépendances de développement
(`uv sync` les installe) : elles ne sont nécessaires que pour lancer le pair de l'exemple et du test
d'intégration, pas pour le code de production de `remote` (le côté appelant), qui ne dépend que de
`ycappuccino-api`/`-core`/`-storage`.
