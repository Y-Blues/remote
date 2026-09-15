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
