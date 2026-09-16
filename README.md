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

**Non transmise au pair dans cette version** : `IExposedService.call` reçoit un sujet déjà décodé, jamais
les en-têtes bruts de la requête entrante, donc `remote_call` n'a rien à reforwarder. Le service ciblé sur
le pair doit donc être `secure=False`. `remote_call` lui-même reste `secure=True` : seul un appelant local
autorisé peut l'utiliser. Voir la conception (section 3) pour le compromis et les évolutions possibles.

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

Le conteneur B, lui, expose `service_b` (`secure=False`, transmission d'authentification non
supportée, voir « Authentification » ci-dessus) et charge lui aussi `ycappuccino.remote` (jamais
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
