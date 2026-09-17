# transparent RPC (remote + fondations) : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** implémenter `docs/superpowers/specs/2026-09-16-transparent-rpc-design.md` (sous-projet 1) :
authentification HMAC instance-à-instance, dispatch typé `@rpc_method`, une route HTTP par méthode,
catalogue `ServiceDescriptor` persisté, schémas `swagger` typés, migration des services existants,
suppression du code mort `YCappuccinoRemote`.

**Architecture — dépôts touchés (7, tous git séparés) :**

| Dépôt | Rôle dans ce plan | Fondation ? |
|---|---|---|
| `api` | `IAuthentication.authenticate` élargi, `@rpc_method`/introspection dans `decorators.py`, `ServiceRoute` enrichi | oui |
| `http_server` | `ApiServlet._authenticate` : chaîne multi-auth au lieu d'un seul provider | oui |
| `endpoints_service` | `ServiceEndpoint.call` : dispatch par méthode `@rpc_method` au lieu de `service.call()` | oui |
| `remote` | `RemoteServer.secret`, `PeerHmacAuthentication`, signature sortante (`_http.py`), `ServiceDescriptor` | non (mais déjà touché par les 2 plans précédents) |
| `permissions_app` | `JwtAuthentication` adapté à la nouvelle signature ; migration de 3 services vers `@rpc_method` | non |
| `scripts` | migration de `ScriptService` vers `@rpc_method` | non |
| `swagger` | `_service_paths` : schémas typés au lieu de `{"200": {"description": ...}}` | non |
| `core` | `_INTERFACE_ROOTS` réduit à `(YCappuccinoComponent,)`, suppression `bundles/list_components.py` | oui |

**Tech Stack:** Python ≥ 3.10, uv, Pelix/iPOPO 3, unittest (`IsolatedAsyncioTestCase`), stdlib uniquement
pour le nouveau code HMAC (`hmac`, `hashlib`, `time`) — aucune dépendance externe nouvelle nulle part.

## Global Constraints

- Racine du workspace : `/home/yaiba/Documents/yblues`. Chaque dépôt du tableau ci-dessus est un dépôt git
  séparé : **un commit par dépôt touché**, jamais un commit qui prétendrait couvrir plusieurs dépôts (c'est
  de toute façon impossible, `.git` est séparé). Une tâche de ce plan peut toucher plusieurs dépôts ; dans
  ce cas elle produit plusieurs commits, un par dépôt, **dans l'ordre où les dépôts apparaissent dans ses
  Steps** (les dépôts fondation d'abord, quand une tâche les touche).
- **Dépôts fondation** (`api`, `http_server`, `endpoints_service`, `core`) : ne les modifier que quand ce
  plan l'exige explicitement, garder leur propre suite de tests verte après chaque modification, ne jamais
  faire travailler deux sous-agents en parallèle sur le même dépôt fondation (les séquencer). Ceci reprend
  la règle déjà posée dans `MIGRATION_PLAN.md`.
- Commande de test, lancée depuis chaque dépôt : `uv run python -m unittest discover -s src/unittest/python`.
- `requires-python = ">=3.10"` partout.
- Commit, pour un dépôt `X` : `git -c user.name="Aurélien Pisu" -c user.email="aurelien.pisu@gmail.com"
  commit -m "X: <message court>"`, jamais de ligne d'attribution, jamais de push, jamais pendant qu'un
  sous-agent travaille encore dedans — seulement après revue de la tâche.
- Port du nouveau test d'intégration HMAC (Task 1) : **18163** (plage réservée `remote` 18160-18169 dans
  `MIGRATION_PLAN.md` ; 18160/18161/18162 déjà pris par les tests d'intégration existants de `remote`).
- Aucun décorateur sur les classes de composants (elles restent des classes natives ordinaires) —
  `@rpc_method` est un décorateur de **méthode**, jamais de classe, cohérent avec `@Property`.
- **Écart de conception découvert et tranché en écrivant ce plan** (la spec ne le détaillait pas) :
  `IAuthentication.authenticate(headers)` ne reçoit aujourd'hui QUE les en-têtes, jamais la méthode, le
  chemin ou le corps — un vrai obstacle pour un HMAC "signé par requête" qui doit couvrir ces trois choses
  pour tenir sa promesse (sinon ce n'est qu'un bearer token horodaté, pas une signature de requête). Décidé
  ici : élargir le port `IAuthentication.authenticate` à `(headers, method, path, body)` (Task 1, touche
  `api`) plutôt que dégrader le HMAC vers un simple bearer signé — c'est le sens même de la décision
  utilisateur du 2026-09-16 (HMAC signé par requête, explicitement préféré à un bearer token).
- **Deuxième écart tranché** : "notre propre peer id local" (mentionné par la spec pour l'en-tête
  `X-YCappuccino-Peer`) n'est l'objet d'aucun nouveau concept — c'est déjà `Framework.get_framework().
  get_app_name()` (le `name:` de tête d'`application.yml`), qui correspond déjà à la convention utilisée
  partout dans les tests/exemples existants pour choisir un `RemoteServer.id(...)` (`"peer"`,
  `"eu-node-2"`, ...). Pas de nouveau fichier de config.
- **Troisième écart, mineur** : le message signé couvre `method`, `path` (SANS la query string — la
  query string n'est reconstruite côté serveur qu'en `dict`, pas en chaîne canonique, un ordre de
  sérialisation différent romprait la vérification pour un gain de sécurité marginal dans un cluster déjà
  considéré de confiance) et `body`, pas `path_with_query` comme la spec l'esquissait.

## Structure des fichiers (nouveaux/modifiés, par dépôt)

| Dépôt | Fichier | Rôle |
|---|---|---|
| `api` | `src/main/python/ycappuccino/api/http_server.py` | `IAuthentication.authenticate` élargi |
| `api` | `src/main/python/ycappuccino/api/decorators.py` | `rpc_method`, `get_rpc_methods` |
| `api` | `src/main/python/ycappuccino/api/endpoints_service.py` | `ServiceRoute` enrichi (`params`, `return_type`) |
| `http_server` | `src/main/python/ycappuccino/http_server/servlet.py` | `_authenticate` multi-provider |
| `endpoints_service` | `src/main/python/ycappuccino/endpoints_service/endpoint.py` | dispatch par méthode |
| `remote` | `src/main/python/ycappuccino/remote/models/remote_server.py` | `+secret` |
| `remote` | `src/main/python/ycappuccino/remote/peer_authentication.py` | `PeerHmacAuthentication` (nouveau) |
| `remote` | `src/main/python/ycappuccino/remote/_http.py` | signature sortante |
| `remote` | `src/main/python/ycappuccino/remote/models/service_descriptor.py` | `ServiceDescriptor` (nouveau) |
| `remote` | `src/main/python/ycappuccino/remote/catalog.py` | publication + cache (nouveau) |
| `permissions_app` | `src/main/python/ycappuccino/permissions/authentication.py` | signature `authenticate` adaptée |
| `permissions_app` | `src/main/python/ycappuccino/permissions/services/login.py`, `change_password.py` | migration `@rpc_method` |
| `scripts` | `src/main/python/ycappuccino/scripts/service.py` | migration `@rpc_method` |
| `swagger` | `src/main/python/ycappuccino/swagger/generator.py` | `_service_paths` typé |
| `core` | `src/main/python/ycappuccino/core/component_factory.py` | `_INTERFACE_ROOTS` réduit |
| `core` | `src/main/python/ycappuccino/core/bundles/list_components.py` | supprimé |
| `api` | `proxy.py`, `permissions.py`, `core.py`, `scheduler.py`, `endpoints.py`, `scripts.py`, `storage.py`, `hosts.py`, `remote.py` | code mort supprimé |

---

### Task 1 : authentification HMAC instance-à-instance

**Files:**
- Modify (`api`): `src/main/python/ycappuccino/api/http_server.py`
- Test (`api`): `src/unittest/python/test_http_server_interfaces.py` (si un test de signature existe déjà
  pour `IAuthentication`, l'adapter ; sinon en créer un minimal vérifiant la nouvelle signature abstraite)
- Modify (`permissions_app`): `src/main/python/ycappuccino/permissions/authentication.py`
- Test (`permissions_app`): `src/unittest/python/test_authentication.py` (adapter les appels existants à
  `authenticate(headers, method, path, body)`)
- Modify (`remote`): `src/main/python/ycappuccino/remote/models/remote_server.py`,
  `src/main/python/ycappuccino/remote/_http.py`
- Create (`remote`): `src/main/python/ycappuccino/remote/peer_authentication.py`
- Test (`remote`): `src/unittest/python/test_remote_server.py` (étendre pour `secret`),
  `src/unittest/python/test_peer_authentication.py` (nouveau),
  `src/unittest/python/test_http_signing.py` (nouveau, teste `call_peer` signe bien)
- Modify (`http_server`): `src/main/python/ycappuccino/http_server/servlet.py`
- Test (`http_server`): `src/unittest/python/test_servlet.py` (adapter `_authenticate` : chaîne de
  providers, tests existants un seul provider doivent rester verts)
- Test (`remote`), intégration : `src/unittest/python/test_hmac_framework.py` (nouveau, port **18163**)

**Interfaces:**
- Produces (`api`): `IAuthentication.authenticate(self, headers: dict, method: str, path: str, body:
  bytes) -> Optional[dict]`.
- Produces (`remote`): `RemoteServer.secret(a_value: str)` (`@Property`) ; `PeerHmacAuthentication(manager:
  IManager, now=None, tolerance: float = 60.0)` ; `_http.call_peer(..., local_peer_id: str = None)`.

- [ ] **Step 1 (api) : élargir `IAuthentication`, écrire/adapter le test**

`api/src/main/python/ycappuccino/api/http_server.py` :

```python
class IAuthentication(YCappuccinoComponent, ABC):
    """decodes the subject of an HTTP request from its headers"""

    @abstractmethod
    async def authenticate(
        self, headers: dict, method: str, path: str, body: bytes
    ) -> Optional[dict]:
        """subject decoded from the request, or None when absent or invalid"""
```

Run (depuis `api`) : `uv run python -m unittest discover -s src/unittest/python`. Si un test existant
instancie un faux `IAuthentication` avec l'ancienne signature à un seul argument, l'adapter (source de
vérité : ce fichier). Commit `api`.

- [ ] **Step 2 (permissions_app) : adapter `JwtAuthentication` à la nouvelle signature**

`permissions_app/src/main/python/ycappuccino/permissions/authentication.py`, méthode `authenticate` :

```python
    async def authenticate(self, headers: dict, method: str, path: str, body: bytes) -> Optional[dict]:
        token = _token_from_headers(headers)
        if token is None:
            return None
        return jwt_codec.decode(token, self._key)
```

(seule la signature change ; `method`/`path`/`body` ne sont pas utilisés par un JWT). Adapter les appels
dans `src/unittest/python/test_authentication.py` (passer des valeurs quelconques, ex. `"GET", "/", b""`,
pour les trois nouveaux arguments). Run (depuis `permissions_app`) : `uv run python -m unittest discover -s
src/unittest/python`. Commit `permissions_app`.

- [ ] **Step 3 (remote) : `RemoteServer.secret`**

Ajouter à `remote/src/main/python/ycappuccino/remote/models/remote_server.py` :

```python
    @Property(name="secret")
    def secret(self, a_value):
        self._secret = a_value
```

(+ `self._secret = None` dans `__init__`). Étendre `test_remote_server.py` : round-trip d'un `secret` non
vide via le `Manager`, comme pour `host`/`port`/`scheme`. Run puis commit `remote` (message : "remote: add
RemoteServer.secret, the HMAC shared key for a peer").

- [ ] **Step 4 (remote) : écrire les tests de `PeerHmacAuthentication` avant l'implémentation**

`remote/src/unittest/python/test_peer_authentication.py` :

```python
import hashlib
import hmac
import time
import unittest

from ycappuccino.remote.models.remote_server import RemoteServer
from ycappuccino.remote.peer_authentication import PeerHmacAuthentication

PEERS = {"peer-a": {"host": "peer.example", "port": 9000, "scheme": "http", "secret": "s3cr3t"}}


class FakeManager:
    def __init__(self, peers):
        self._peers = peers

    async def get_one(self, item_id, id, params=None, subject=None):
        assert item_id == "remoteServer"
        assert subject is None
        document = self._peers.get(id)
        if document is None:
            return None
        server = RemoteServer()
        server.id(id)
        server.host(document["host"])
        server.port(document["port"])
        server.scheme(document["scheme"])
        server.secret(document["secret"])
        return server


def _sign(secret, method, path, timestamp, body):
    message = f"{method}\n{path}\n{timestamp}\n".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _headers(peer_id, secret, method="POST", path="/api/services/x", body=b"", timestamp=None):
    timestamp = timestamp if timestamp is not None else str(int(time.time()))
    return {
        "x-ycappuccino-peer": peer_id,
        "x-ycappuccino-timestamp": timestamp,
        "x-ycappuccino-signature": _sign(secret, method, path, timestamp, body),
    }


class TestPeerHmacAuthentication(unittest.IsolatedAsyncioTestCase):

    async def test_valid_signature_authenticates_as_the_peer(self):
        auth = PeerHmacAuthentication(FakeManager(PEERS))

        subject = await auth.authenticate(
            _headers("peer-a", "s3cr3t"), "POST", "/api/services/x", b""
        )

        self.assertEqual(subject, {"peer": "peer-a"})

    async def test_missing_headers_is_not_authenticated(self):
        auth = PeerHmacAuthentication(FakeManager(PEERS))

        self.assertIsNone(await auth.authenticate({}, "POST", "/api/services/x", b""))

    async def test_unknown_peer_is_not_authenticated(self):
        auth = PeerHmacAuthentication(FakeManager(PEERS))

        headers = _headers("unknown-peer", "irrelevant")
        self.assertIsNone(await auth.authenticate(headers, "POST", "/api/services/x", b""))

    async def test_wrong_signature_is_not_authenticated(self):
        auth = PeerHmacAuthentication(FakeManager(PEERS))

        headers = _headers("peer-a", "wrong-secret")
        self.assertIsNone(await auth.authenticate(headers, "POST", "/api/services/x", b""))

    async def test_signature_bound_to_method_and_path(self):
        auth = PeerHmacAuthentication(FakeManager(PEERS))

        headers = _headers("peer-a", "s3cr3t", method="POST", path="/api/services/x")
        self.assertIsNone(await auth.authenticate(headers, "GET", "/api/services/x", b""))
        self.assertIsNone(await auth.authenticate(headers, "POST", "/api/services/y", b""))

    async def test_stale_timestamp_is_not_authenticated(self):
        auth = PeerHmacAuthentication(FakeManager(PEERS), now=lambda: 1_000_000.0, tolerance=60.0)

        old_timestamp = str(int(1_000_000.0) - 61)
        headers = _headers("peer-a", "s3cr3t", timestamp=old_timestamp)
        self.assertIsNone(await auth.authenticate(headers, "POST", "/api/services/x", b""))

    async def test_timestamp_within_tolerance_authenticates(self):
        auth = PeerHmacAuthentication(FakeManager(PEERS), now=lambda: 1_000_000.0, tolerance=60.0)

        recent_timestamp = str(int(1_000_000.0) - 30)
        headers = _headers("peer-a", "s3cr3t", timestamp=recent_timestamp)
        subject = await auth.authenticate(headers, "POST", "/api/services/x", b"")
        self.assertEqual(subject, {"peer": "peer-a"})


if __name__ == "__main__":
    unittest.main()
```

Run : `ModuleNotFoundError` attendu.

- [ ] **Step 5 (remote) : implémenter `PeerHmacAuthentication`**

`remote/src/main/python/ycappuccino/remote/peer_authentication.py` :

```python
"""
PeerHmacAuthentication: verifies a peer-to-peer request signed by remote's own outgoing HTTP
helper (_http.py:call_peer). See spec 2026-09-16-transparent-rpc-design.md section 4.

The signed message is method + path (no query string) + timestamp + raw body, HMAC-SHA256 keyed by
the RemoteServer.secret registered for the announced peer id. A stale timestamp (see `tolerance`) is
rejected to bound replay. Returns subject={"peer": peer_id} on success, None otherwise -- this is one
provider in ApiServlet's authentication chain (see http_server), never the only one.
"""

import hashlib
import hmac
import time

from ycappuccino.api.http_server import IAuthentication
from ycappuccino.api.storage import IManager

_ITEM_ID = "remoteServer"
_PEER_HEADER = "x-ycappuccino-peer"
_TIMESTAMP_HEADER = "x-ycappuccino-timestamp"
_SIGNATURE_HEADER = "x-ycappuccino-signature"


class PeerHmacAuthentication(IAuthentication):

    def __init__(self, manager: IManager, now=None, tolerance: float = 60.0):
        self._manager = manager
        self._now = now if now is not None else time.time
        self._tolerance = tolerance

    async def start(self):
        pass

    async def stop(self):
        pass

    async def authenticate(self, headers: dict, method: str, path: str, body: bytes) -> dict | None:
        peer_id = headers.get(_PEER_HEADER)
        timestamp = headers.get(_TIMESTAMP_HEADER)
        signature = headers.get(_SIGNATURE_HEADER)
        if not peer_id or not timestamp or not signature:
            return None
        if abs(self._now() - float(timestamp)) > self._tolerance:
            return None

        peer = await self._manager.get_one(_ITEM_ID, peer_id, subject=None)
        if peer is None or not peer.get_storage_model().get("secret"):
            return None

        secret = peer.get_storage_model()["secret"]
        expected = _sign(secret, method, path, timestamp, body)
        if not hmac.compare_digest(expected, signature):
            return None
        return {"peer": peer_id}


def _sign(secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    message = f"{method}\n{path}\n{timestamp}\n".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
```

Run : `OK` attendu pour `test_peer_authentication.py`. Commit `remote` ("remote: add PeerHmacAuthentication,
the peer-to-peer IAuthentication provider").

- [ ] **Step 6 (remote) : signature sortante dans `_http.py`, tests d'abord**

`remote/src/unittest/python/test_http_signing.py` (nouveau) : vérifie que `call_peer`, quand `document`
contient une `secret` non vide, ajoute les trois en-têtes `X-YCappuccino-Peer`/`-Timestamp`/`-Signature` à
la requête envoyée à `opener`, avec une signature vérifiable par le `_sign` de `peer_authentication.py`
(importer et réutiliser cette fonction plutôt que la dupliquer) ; et qu'un `document` sans `secret` (ou
`secret` vide/absente) n'ajoute aucun de ces en-têtes (rétrocompatible avec un pair qui n'exige pas encore
d'auth). Utiliser le même `FakeOpener`/`FakeResponse` que `test_remote_call.py`.

Run : échec attendu (`call_peer` ne signe rien encore).

`remote/src/main/python/ycappuccino/remote/_http.py`, modifications :

```python
def call_peer(
    document, service, method, extra_path, params, body,
    timeout=DEFAULT_TIMEOUT, opener=None, local_peer_id=None,
):
    opener = opener if opener is not None else urllib.request.urlopen
    url = build_url(document, service, extra_path, params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}

    secret = document.get("secret")
    if secret:
        path = _path_only(url)
        timestamp = str(int(time.time()))
        headers["X-YCappuccino-Peer"] = local_peer_id or _local_peer_id()
        headers["X-YCappuccino-Timestamp"] = timestamp
        headers["X-YCappuccino-Signature"] = _sign(secret, method, path, timestamp, data or b"")

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    ...  # inchangé au-delà de ce point


def _path_only(url):
    return urllib.parse.urlsplit(url).path


def _local_peer_id():
    from ycappuccino.core.framework import Framework  # lazy: keeps unit tests framework-free

    return Framework.get_framework().get_app_name()
```

(importer `time`, `_sign` depuis `peer_authentication` en tête de fichier ; `_sign` y est déjà publique
pour cet usage). Run : `OK` attendu. Commit `remote` ("remote: sign outgoing peer calls with HMAC when the
peer has a secret").

- [ ] **Step 7 (http_server) : chaîne multi-auth dans `ApiServlet`, tests d'abord**

Étendre `http_server/src/unittest/python/test_servlet.py` : un test avec deux fausses `IAuthentication`
(la première retourne toujours `None`, la seconde retourne un sujet) vérifie que `handle()` utilise le
sujet de la seconde ; un test avec deux fausses `IAuthentication` retournant toutes deux `None` vérifie que
`subject` reste `None` (comportement actuel avec zéro provider inchangé). Adapter les fakes existantes à la
nouvelle signature `authenticate(headers, method, path, body)`.

Run : échec attendu (comportement actuel = seulement `authentications[0]`).

`http_server/src/main/python/ycappuccino/http_server/servlet.py`, méthode `_authenticate` :

```python
    async def _authenticate(self, request):
        for authentication in list(self._authentications):
            subject = await authentication.authenticate(
                request.headers, request.method, request.path, request.body
            )
            if subject is not None:
                return subject
        return None
```

Run : `OK` attendu, y compris les tests existants (un seul provider retournant un sujet doit toujours
authentifier — comportement inchangé dans ce cas). Commit `http_server` ("http_server: try every
IAuthentication in order instead of only the first").

- [ ] **Step 8 (remote) : test d'intégration HMAC deux processus, port 18163**

`remote/src/unittest/python/test_hmac_framework.py`, sur le modèle exact de
`test_component_directory_framework.py`/`test_federated_framework.py` (conteneur B en sous-processus,
port **18163**) : le pair (conteneur B) charge `ycappuccino.remote` en plus de `http_server`/
`endpoints_storage`/`endpoints_service` (pour avoir `PeerHmacAuthentication` dans sa propre chaîne
d'auth) et expose un service `secure=True` réel ; son `RemoteServer` vers le conteneur A (appelant, dans
le process de test) porte une `secret` ; le `RemoteServer` du conteneur A vers B (utilisé par
`RemoteCall`) porte la **même** `secret`. Deux cas :
1. Appel via `RemoteCall`/`remote_call` avec la `secret` correcte des deux côtés → succès, `result.body`
   correct (le service ciblé sur B passe de `secure=False` à `secure=True`, protégé par
   `PeerHmacAuthentication`, preuve que la boucle complète fonctionne).
2. Le `RemoteServer` du conteneur A vers B a une `secret` erronée → l'appel lève `NotAuthenticated` (le
   401 de B, retraduit par `_http.py:_translate`, comme le fait déjà `test_remote_call.py` pour un 401
   générique).

Run (depuis `remote`) : `OK` attendu. Commit `remote` ("remote: prove HMAC peer auth end-to-end across two
processes").

- [ ] **Step 9 : vérification finale du lot**

Run, dans chacun des 4 dépôts touchés (`api`, `permissions_app`, `remote`, `http_server`) :
`uv run python -m unittest discover -s src/unittest/python`. Expected : `OK` partout, aucune régression
sur les suites existantes.

---

### Task 2 : `@rpc_method`, introspection, `ServiceRoute` enrichi

**Files:**
- Modify (`api`): `src/main/python/ycappuccino/api/decorators.py`,
  `src/main/python/ycappuccino/api/endpoints_service.py`
- Test (`api`): `src/unittest/python/test_decorators.py` (étendre),
  `src/unittest/python/test_endpoints_service.py` (étendre pour `ServiceRoute` enrichi)

**Interfaces:**
- Produces: `rpc_method(method: str, path: str = "", summary: str = "")` (décorateur de méthode),
  `get_rpc_methods(klass) -> dict[str, dict]` (nom de méthode → métadonnées, y compris types).
  `ServiceRoute` gagne deux champs optionnels : `params: dict = field(default_factory=dict)` (nom →
  `type`, une chaîne JSON-Schema simple : `"string"`/`"integer"`/`"boolean"`/`"number"`), `return_type:
  Optional[str] = None`.

- [ ] **Step 1 : écrire les tests de `rpc_method`/`get_rpc_methods`**

`api/src/unittest/python/test_decorators.py`, ajouter (dans le style déjà présent, `unittest.TestCase`
simple, pas de framework) :

```python
class TestRpcMethod(unittest.TestCase):

    def test_marks_the_function_with_its_metadata(self):
        class Example:
            @rpc_method(method="POST", path="/{id}/execute", summary="run it")
            def execute(self, id: str, count: int = 1) -> dict:
                return {}

        metadata = Example.execute._ycappuccino_rpc_method
        self.assertEqual(metadata["method"], "POST")
        self.assertEqual(metadata["path"], "/{id}/execute")
        self.assertEqual(metadata["summary"], "run it")
        self.assertEqual(metadata["params"], {"id": str, "count": int})
        self.assertEqual(metadata["return_type"], dict)

    def test_get_rpc_methods_scans_the_class(self):
        class Example:
            @rpc_method(method="GET")
            def read(self) -> dict:
                return {}

            def untouched(self):
                pass

        methods = get_rpc_methods(Example)
        self.assertEqual(set(methods), {"read"})
        self.assertEqual(methods["read"]["method"], "GET")
```

Run : `ImportError` attendu (`rpc_method`/`get_rpc_methods` n'existent pas encore).

- [ ] **Step 2 : implémenter, dans `api/src/main/python/ycappuccino/api/decorators.py`**

```python
import inspect
import typing as t

def rpc_method(method: str, path: str = "", summary: str = ""):
    """marks a method of an IExposedService as a public RPC route; see get_rpc_methods"""

    def decorator(func):
        signature = inspect.signature(func)
        hints = t.get_type_hints(func)
        params = {
            name: hints[name]
            for name in signature.parameters
            if name != "self" and name in hints
        }
        func._ycappuccino_rpc_method = {
            "method": method,
            "path": path,
            "summary": summary,
            "params": params,
            "return_type": hints.get("return"),
        }
        return func

    return decorator


def get_rpc_methods(klass) -> dict:
    """name -> metadata for every @rpc_method of klass and its parents"""
    methods = {}
    for attribute_name in dir(klass):
        attribute = getattr(klass, attribute_name, None)
        metadata = getattr(attribute, "_ycappuccino_rpc_method", None)
        if metadata is not None:
            methods[attribute_name] = metadata
    return methods
```

(pas de `functools.wraps`/wrapper d'appel ici, à la différence de `@Property` : `@rpc_method` ne change
jamais le comportement d'appel de la méthode, seulement son introspection — appeler la méthode décorée
directement continue de fonctionner sans détour). Run : `OK` attendu pour `test_decorators.py`. Commit
`api` ("api: add @rpc_method, opt-in typed introspection for public service methods").

- [ ] **Step 3 : enrichir `ServiceRoute`, test d'abord**

Étendre `api/src/unittest/python/test_endpoints_service.py` (ou créer s'il n'existe pas) :
`ServiceRoute(method="POST", params={"id": str}, return_type=dict)` doit être construisible avec des
valeurs par défaut vides quand `params`/`return_type` sont omis (rétrocompatible avec les `ServiceRoute`
déjà écrits à la main dans `permissions_app`/`scripts`, non touchés avant Task 4).

`api/src/main/python/ycappuccino/api/endpoints_service.py` :

```python
@dataclass(frozen=True)
class ServiceRoute:
    """a request answered by a service, documented in the API descriptions (swagger)"""
    method: str
    path: str = ""
    summary: str = ""
    params: dict = field(default_factory=dict)
    return_type: Optional[type] = None
```

Run : `OK` attendu, y compris `endpoints_service`/`http_server`/`swagger`/`permissions_app`/`scripts` déjà
verts (champs additifs à défaut, aucun `ServiceRoute(...)` existant ne casse). Commit `api` ("api: add
params/return_type to ServiceRoute, additive").

- [ ] **Step 4 : vérification finale du lot**

Run, depuis `api` : `uv run python -m unittest discover -s src/unittest/python`. Expected : `OK`. Puis,
depuis chacun de `endpoints_service`, `http_server`, `swagger`, `permissions_app`, `scripts`, `remote` :
même commande, pour confirmer qu'aucun de ces dépôts n'est cassé par les champs additifs (ils dépendent
tous de `api` en éditable).

---

### Task 3 : dispatch par méthode dans `ServiceEndpoint`

**Files:**
- Modify (`endpoints_service`): `src/main/python/ycappuccino/endpoints_service/endpoint.py`
- Test (`endpoints_service`): `src/unittest/python/test_endpoint.py` (étendre)

**Interfaces:**
- Consumes (Task 2) : `api.decorators.get_rpc_methods`, `ServiceRoute` enrichi.
- `ServiceEndpoint.call` continue d'exposer exactement la même signature publique
  (`IServiceEndpoint.call(name, method, extra_path, params, body, subject)`, inchangée) — seul son
  **comportement interne** change : au lieu d'appeler `service.call(...)`, il route vers la méthode
  `@rpc_method` correspondante.

- [ ] **Step 1 : écrire les tests du nouveau dispatch**

`endpoints_service/src/unittest/python/test_endpoint.py`, ajouter des cas avec un faux service dont les
méthodes sont décorées `@rpc_method` (au lieu d'un `call()` générique) :

```python
class TypedFakeService(IExposedService):
    name = "typed"
    secure = False

    async def start(self):
        pass

    async def stop(self):
        pass

    @rpc_method(method="POST", path="/{item_id}/execute", summary="run")
    async def execute(self, item_id: str, count: int = 1) -> dict:
        return {"item_id": item_id, "count": count}


class TestServiceEndpointTypedDispatch(unittest.IsolatedAsyncioTestCase):

    async def test_routes_to_the_matching_rpc_method(self):
        endpoint = ServiceEndpoint([TypedFakeService()], [])

        result = await endpoint.call(
            "typed", "POST", ["abc", "execute"], {}, {"count": 3}, None
        )

        self.assertEqual(result.body, {"item_id": "abc", "count": 3})

    async def test_unmatched_method_or_path_is_not_found(self):
        endpoint = ServiceEndpoint([TypedFakeService()], [])

        with self.assertRaises(NotFound):
            await endpoint.call("typed", "GET", ["abc", "execute"], {}, {}, None)

    async def test_a_result_that_is_already_a_service_result_is_used_as_is(self):
        class CookieService(IExposedService):
            name = "cookie"
            secure = False

            async def start(self):
                pass

            async def stop(self):
                pass

            @rpc_method(method="POST")
            async def issue(self) -> ServiceResult:
                return ServiceResult(body={"ok": True}, headers={"set-cookie": "a=b"})

        endpoint = ServiceEndpoint([CookieService()], [])

        result = await endpoint.call("cookie", "POST", [], {}, {}, None)

        self.assertEqual(result.body, {"ok": True})
        self.assertEqual(result.headers, {"set-cookie": "a=b"})
```

Run : échec attendu (le dispatch actuel appelle toujours `service.call(...)`, que `TypedFakeService`
n'implémente pas puisqu'elle n'a plus de `call` — `TypeError`/`AttributeError`).

- [ ] **Step 2 : implémenter**

`endpoints_service/src/main/python/ycappuccino/endpoints_service/endpoint.py` :

```python
import re

from ycappuccino.api.decorators import get_rpc_methods
from ycappuccino.api.endpoints_service import CALL, IExposedService, IServiceEndpoint, ServiceResult
from ycappuccino.api.endpoints_storage import Forbidden, IAuthorization, NotAuthenticated, NotFound

_PATH_PARAM = re.compile(r"\{(\w+)\}")


class ServiceEndpoint(IServiceEndpoint):

    def __init__(self, services: list[IExposedService], authorizations: list[IAuthorization]):
        self._services = services
        self._authorizations = authorizations

    async def start(self):
        pass

    async def stop(self):
        pass

    async def call(self, name, method, extra_path, params, body, subject):
        service = self._find(name)
        await self._check(service, subject)
        target, path_params = _match(service, method, extra_path)
        kwargs = {**path_params, **(body or {})}
        result = await target(**kwargs)
        return result if isinstance(result, ServiceResult) else ServiceResult(body=result)

    def _find(self, name):
        for service in list(self._services):
            if service.name == name:
                return service
        raise NotFound(f"unknown service {name}")

    async def _check(self, service, subject):
        if not service.secure:
            return
        if subject is None:
            raise NotAuthenticated(f"call {service.name} requires a subject")
        authorizations = list(self._authorizations)
        if not authorizations:
            raise Forbidden(f"call {service.name} is not authorized")
        if not await authorizations[0].is_authorized(subject, CALL, service.name):
            raise Forbidden(f"call {service.name} is not authorized")


def _match(service, method, extra_path):
    request_path = "/" + "/".join(extra_path) if extra_path else ""
    for attribute_name, metadata in get_rpc_methods(type(service)).items():
        if metadata["method"] != method:
            continue
        path_params = _match_path(metadata["path"], request_path)
        if path_params is not None:
            return getattr(service, attribute_name), path_params
    raise NotFound(f"no route {method} {request_path!r} on service {service.name}")


def _match_path(template, path):
    names = _PATH_PARAM.findall(template)
    pattern = "^" + _PATH_PARAM.sub(r"(?P<\1>[^/]+)", template) + "$"
    match = re.match(pattern, path)
    if match is None:
        return None
    return {name: match.group(name) for name in names}
```

(`_logger`/`Forbidden` warning existant retiré pour rester concis — le réintroduire si le test actuel de
"no IAuthorization" en dépend ; vérifier `test_endpoint.py` existant avant de le perdre). Run : `OK`
attendu, y compris les tests existants qui utilisaient l'ancien `call()` générique **doivent être adaptés**
à ce stade (leurs fausses implémentations de service doivent migrer vers `@rpc_method`, sans quoi elles
échoueront — c'est le but de ce Step : plus aucun `IExposedService.call()` générique n'est routable après
Task 3). Commit `endpoints_service` ("endpoints_service: route to a matching @rpc_method instead of
service.call()").

- [ ] **Step 3 : vérification finale du lot**

Run, depuis `endpoints_service` : `uv run python -m unittest discover -s src/unittest/python`. Expected :
`OK`. **Attention** : à ce stade, `permissions_app`/`scripts`/`remote` ne compilent plus contre ce nouveau
`ServiceEndpoint` pour leurs services encore écrits en `call()` générique (`LoginService`,
`LoginCookieService`, `ChangePasswordService`, `ScriptService`) — c'est attendu, Task 4 les migre
immédiatement après. `RemoteCall`/`RemoteDispatch`/`RemoteCapabilities` (dans `remote`) restent, eux,
appelés via leur propre mécanisme (`RemoteCall`/`RemoteDispatch` ne passent jamais par `ServiceEndpoint`
pour leur propre logique interne de forward/dispatch générique — seul le point d'entrée HTTP public
`/api/services/<name>` en dépend, et `RemoteCapabilities` a une seule méthode déjà triviale à migrer,
voir Task 4).

---

### Task 4 : migration des services existants vers `@rpc_method`

**Files:**
- Modify (`permissions_app`): `services/login.py`, `services/change_password.py`
- Test (`permissions_app`): `src/unittest/python/test_login_service.py`,
  `test_change_password_service.py` (adapter les appels : ne plus passer par `.call(method, ...)`, appeler
  la méthode `@rpc_method` directement, ou passer par un vrai `ServiceEndpoint` — choisir la même approche
  que les tests existants utilisaient pour `.call()`, pour rester cohérent)
- Modify (`scripts`): `service.py`
- Test (`scripts`): `src/unittest/python/test_script_service.py`
- Modify (`remote`): `capabilities.py` (`RemoteCapabilities`, seule méthode `GET` sans extra_path)
- Test (`remote`): `test_capabilities.py`

**Interfaces:**
- Consumes (Task 2/3) : `@rpc_method`, dispatch de `ServiceEndpoint`.
- `RemoteCall` et `RemoteDispatch` restent **inchangés** (exclusion explicite, voir spec section 9 — ce
  sont des services structurellement génériques, leur `call()` générique n'est PAS routé par
  `ServiceEndpoint` de la même façon : ils gardent une unique méthode `@rpc_method(method=...,
  path="/{extra_path:path}")`-like... **non** : plus simple, ils gardent `call()` comme méthode
  `IExposedService` mais celle-ci n'est plus abstraite après Task 3 (voir note ci-dessous) — leur route
  `@rpc_method` est un unique wrapper `POST`/`GET` à chemin ouvert. Détail à l'étape 4 ci-dessous.

- [ ] **Step 1 (permissions_app) : migrer `LoginService`/`LoginCookieService`, tests d'abord**

Adapter `permissions_app/src/unittest/python/test_login_service.py` pour appeler la nouvelle méthode
`login`/`login_cookie` directement (plus de `service.call("POST", [], {}, body, None)`).

`permissions_app/src/main/python/ycappuccino/permissions/services/login.py` :

```python
from ycappuccino.api.decorators import rpc_method
from ycappuccino.api.endpoints_service import IExposedService, ServiceResult
from ycappuccino.api.storage import IManager
from ycappuccino.permissions import jwt_codec, passwords


class LoginService(IExposedService):
    name = "login"
    secure = False

    def __init__(
        self,
        manager: IManager,
        key: str = jwt_codec.DEFAULT_KEY,
        timeout: int = jwt_codec.DEFAULT_TIMEOUT,
    ):
        self._manager, self._key, self._timeout = manager, key, timeout

    async def start(self):
        pass

    async def stop(self):
        pass

    @rpc_method(method="POST", summary="exchange a login and a password for a token")
    async def login(self, login: str, password: str) -> dict:
        token = await _issue_token(self._manager, self._key, self._timeout, login, password)
        return {"token": token}


class LoginCookieService(IExposedService):
    name = "login_cookie"
    secure = False

    def __init__(
        self,
        manager: IManager,
        key: str = jwt_codec.DEFAULT_KEY,
        timeout: int = jwt_codec.DEFAULT_TIMEOUT,
    ):
        self._manager, self._key, self._timeout = manager, key, timeout

    async def start(self):
        pass

    async def stop(self):
        pass

    @rpc_method(method="POST", summary="log in and receive the token as a cookie")
    async def login_cookie(self, login: str, password: str) -> ServiceResult:
        token = await _issue_token(self._manager, self._key, self._timeout, login, password)
        return ServiceResult(
            body={"token": token},
            headers={"set-cookie": f"_ycappuccino={token};Path=/;HttpOnly"},
        )


async def _issue_token(manager, key, timeout, login, password):
    account_id = await passwords.check_login(manager, login, password)
    organization_id = await passwords.organization_of(manager, account_id)
    return jwt_codec.encode({"sub": account_id, "tid": organization_id}, key, timeout)
```

(le paramètre s'appelait `body["login"]`/`body["password"]` — devient deux paramètres typés `login: str`,
`password: str`, mappés automatiquement depuis le corps JSON par `ServiceEndpoint._match`/`call` — Task 3;
`NotFound` sur méthode invalide disparaît, remplacé par le `NotFound` générique déjà levé par
`ServiceEndpoint._match` si aucune route ne correspond). Run : `OK` attendu. Commit `permissions_app`
("permissions_app: migrate LoginService/LoginCookieService to @rpc_method").

- [ ] **Step 2 (permissions_app) : migrer `ChangePasswordService`, tests d'abord**

Même principe. `permissions_app/src/main/python/ycappuccino/permissions/services/change_password.py` :

```python
from ycappuccino.api.decorators import rpc_method
from ycappuccino.api.endpoints_service import IExposedService
from ycappuccino.api.storage import IManager
from ycappuccino.permissions import passwords


class ChangePasswordService(IExposedService):
    name = "change_password"
    secure = True

    def __init__(self, manager: IManager):
        self._manager = manager

    async def start(self):
        pass

    async def stop(self):
        pass

    @rpc_method(method="POST", summary="replace the password of a login")
    async def change_password(self, login: str, password: str, new_password: str) -> dict:
        await passwords.check_login(self._manager, login, password)
        account = await self._manager.get_one("login", login, subject=None)
        account.password(new_password)
        await self._manager.up_sert_model(account, subject=None)
        return {}
```

Run puis commit `permissions_app` ("permissions_app: migrate ChangePasswordService to @rpc_method").

- [ ] **Step 3 (scripts) : migrer `ScriptService`, tests d'abord**

`ScriptService` a un path param (`scriptId`) — cas qui exerce vraiment `_match_path` de Task 3.

`scripts/src/main/python/ycappuccino/scripts/service.py` :

```python
from ycappuccino.api.decorators import rpc_method
from ycappuccino.api.endpoints_service import IExposedService
from ycappuccino.api.endpoints_storage import NotFound
from ycappuccino.api.storage import IManager
from ycappuccino.scripts.execution import execute


class ScriptService(IExposedService):
    name = "scripts"
    secure = True

    def __init__(self, manager: IManager, resolve_service=None):
        self._manager = manager
        self._resolve_service = resolve_service or _resolve_from_framework

    async def start(self):
        pass

    async def stop(self):
        pass

    @rpc_method(method="POST", path="/{script_id}/execute", summary="execute a stored script")
    async def execute_script(self, script_id: str) -> dict:
        script = await self._manager.get_one("script", script_id, subject=None)
        if script is None:
            raise NotFound(f"unknown script {script_id}")
        result = execute(script.get_storage_model()["source"], self._resolve_service)
        return {"result": result}


def _resolve_from_framework(spec_name, ldap_filter):
    from ycappuccino.core.framework import Framework

    context = Framework.get_framework().context
    reference = context.get_service_reference(spec_name, ldap_filter)
    if reference is None:
        raise NotFound(f"no service available for {spec_name}")
    return context.get_service(reference)
```

Note : `subject` n'est plus transmis à `execute_script` (il ne l'était utilisé que pour
`get_one(..., subject=subject)` dans la version d'origine) — **écart assumé** : la nouvelle signature
`@rpc_method` ne reçoit que les paramètres typés dérivés du chemin/corps, jamais `subject` directement
(cohérent avec `LoginService`/`ChangePasswordService` ci-dessus, aucun des quatre services migrés n'avait
réellement besoin d'un accès différencié par sujet au-delà de l'authentification déjà gérée par
`ServiceEndpoint._check`). Si un futur service a besoin du sujet dans son corps de méthode, ajouter un
paramètre `subject` reconnu spécialement par `ServiceEndpoint.call` (non fait ici, aucun des quatre
services migrés n'en a besoin). Run puis commit `scripts` ("scripts: migrate ScriptService to
@rpc_method").

- [ ] **Step 4 (remote) : migrer `RemoteCapabilities`, garder `RemoteCall`/`RemoteDispatch` génériques**

`RemoteCapabilities` a une seule méthode `GET` sans paramètre : migration triviale.

```python
class RemoteCapabilities(IExposedService):
    name = CAPABILITIES_SERVICE_NAME
    secure = False

    def __init__(self, services: list[IExposedService]):
        self._services = services

    async def start(self):
        pass

    async def stop(self):
        pass

    @rpc_method(method="GET")
    async def capabilities(self) -> dict:
        result = {"services": [service.name for service in list(self._services) if service.name]}
        components = Framework.get_framework().list_components()
        if components:
            result["components"] = components
        return result
```

`RemoteCall` et `RemoteDispatch` ne peuvent PAS être exprimés comme un unique `@rpc_method` à chemin fixe
— leur raison d'être est de consommer un `extra_path` de longueur arbitraire (`/<peer>/<service>[/...]`
pour l'un, `/<qualified path>/<method>` pour l'autre). **Décision (déjà actée par la spec, précisée ici)** :
ils restent chacun leur propre implémentation de `IExposedService`, mais **`ServiceEndpoint.call` (Task 3)
tel qu'écrit ne sait router que vers des méthodes `@rpc_method` à chemin fixe/paramétré nommé** — un
chemin générique n'y a pas de place. Ajouter à `ServiceEndpoint._match` (Task 3, `endpoints_service`) un
filet de rattrapage : si aucun `@rpc_method` ne correspond, et que le service expose encore une méthode
`call(method, extra_path, params, body, subject)` (ancienne interface, détectée par
`hasattr(service, "call")`), l'appeler avec cette signature générique — **ceci doit être ajouté
rétroactivement à Task 3 Step 2** avant ce Step, comme dernier recours seulement pour ces deux services.
Réviser Task 3 Step 2 en conséquence (ajouter ce filet de rattrapage à `_match`/`call`) avant de committer
ce Step. `RemoteCall`/`RemoteDispatch` eux-mêmes ne changent pas.

Run (depuis `remote`) : `uv run python -m unittest discover -s src/unittest/python`. Expected : `OK`,
`test_capabilities.py` adapté à la nouvelle forme sans régression de `{"services": [...]}`/`"components"`.
Commit `remote` ("remote: migrate RemoteCapabilities to @rpc_method; keep RemoteCall/RemoteDispatch generic
via ServiceEndpoint's legacy-call fallback").

- [ ] **Step 5 : vérification finale du lot**

Run, dans `permissions_app`, `scripts`, `remote`, `endpoints_service`, `http_server` : `uv run python -m
unittest discover -s src/unittest/python`. Expected : `OK` partout — en particulier
`test_remote_framework.py`/`test_federated_framework.py`/`test_component_directory_framework.py`
(intégration réelle, appellent `greeting`/services applicatifs à travers `RemoteCall`/
`FederatedServiceEndpoint`, doivent rester verts sans modification).

---

### Task 5 : catalogue `ServiceDescriptor` persisté

**Files:**
- Create (`remote`): `src/main/python/ycappuccino/remote/models/service_descriptor.py`,
  `src/main/python/ycappuccino/remote/catalog.py`
- Test (`remote`): `src/unittest/python/test_service_descriptor.py`, `test_catalog.py`

**Interfaces:**
- Produces: `ServiceDescriptor` (`@Item`, collection `service_descriptors`, `secure_read=True,
  secure_write=True`) : `service_name`, `peer_id` (référence au `RemoteServer` gateway, `""` = local),
  `methods` (JSON : `[{"name", "http_method", "path", "params": {...}, "return_type": ...}]`, sérialisé en
  chaînes pour les types — voir Step 2).
- Produces: `ServiceCatalog` (`YCappuccinoComponent`, pas un `@Item`) : `publish_local(services:
  list[IExposedService])` (introspecte via `get_rpc_methods`, `up_sert_model` un `ServiceDescriptor` par
  service, `peer_id=""`), `refresh_peer(peer_id: str)` (interroge `__remote_capabilities__` du pair via
  `_http.call_peer`, mais celui-ci ne renvoie aujourd'hui que des noms, pas des signatures — **écart
  assumé** : cette tâche élargit `RemoteCapabilities.capabilities()` pour inclure, en plus de
  `"services"`/`"components"`, un `"service_descriptors"` optionnel dérivé du catalogue local du pair,
  omis si vide comme `"components"` l'est déjà — additif, mêmes garanties de rétrocompatibilité que
  l'addendum 2026-09-16 de `remote`).

- [ ] **Step 1 : écrire le test du modèle, puis l'implémenter**

Suivre exactement le patron de `test_remote_server.py`/`RemoteServer` (Task 1 Step 3) pour
`ServiceDescriptor` : round-trip `service_name`/`peer_id`/`methods` (stocké comme une liste de dicts JSON
via une `@Property(type="string")` sérialisée, ou une propriété dédiée si le framework de `@Item` supporte
un type `"array"`/`"object"` — vérifier `_property_schema` dans `api/decorators.py`, Task 2 Step 2
l'a déjà relu : `type` accepte toute chaîne JSON-Schema, `"array"` est valide, mais aucun `@Item` existant
dans les 14 dépôts n'a encore utilisé `type="array"` — **premier de ce genre** ; si `Manager`/`MemoryStorage`
ne sérialise pas nativement une liste de dicts imbriqués, stocker `methods` en JSON (`json.dumps`/
`json.loads` dans les setters/getters du modèle) plutôt que de modifier `storage`, pour rester dans le
périmètre de `remote` seul).

- [ ] **Step 2 : `ServiceCatalog.publish_local`, tests d'abord (fakes, pas de Framework réel)**

Vérifie qu'appeler `publish_local([un service avec deux méthodes @rpc_method])` fait un `up_sert_model`
par service dans un `IManager` fake, avec `methods` reconstituant fidèlement `get_rpc_methods` (les types
Python (`str`, `int`, ...) sérialisés en leur `__name__`, ex. `"str"`, pas l'objet type lui-même — un
`ServiceDescriptor` persisté doit rester JSON-serialisable).

- [ ] **Step 3 : élargir `RemoteCapabilities`, tests d'abord**

Étendre `test_capabilities.py` : quand un `ServiceCatalog` (optionnel, injecté) a des `ServiceDescriptor`
locaux, `capabilities()` (Task 4 Step 4) les inclut sous `"service_descriptors"`, omis si vide — même
garantie additive que `"components"`.

- [ ] **Step 4 : `ServiceCatalog.refresh_peer`, tests d'abord (fakes HTTP, pas de sous-processus)**

Vérifie qu'appeler `refresh_peer("peer-a")` interroge `__remote_capabilities__` du pair via
`_http.call_peer` (réutiliser le même mécanisme que `discovery.ServiceDirectory`/
`component_directory.ComponentDirectory`, s'en inspirer directement plutôt que dupliquer), et
`up_sert_model` un `ServiceDescriptor` par entrée de `"service_descriptors"` reçue, avec `peer_id="peer-a"`
cette fois — remplace toute entrée précédente pour ce pair (pas d'accumulation infinie).

- [ ] **Step 5 : test d'intégration réel (réutilise le port 18163 de Task 1, même paire de conteneurs, pas
  de nouveau port)**

Étendre `test_hmac_framework.py` (Task 1 Step 8) plutôt que créer un nouveau sous-processus : après
l'appel HMAC réussi, un `ServiceCatalog.refresh_peer("peer")` côté conteneur A doit produire un
`ServiceDescriptor` local dont `methods` correspond exactement à ce que le service réel exposé par le
conteneur B a publié via `publish_local` à son propre démarrage.

- [ ] **Step 6 : vérification finale**

Run (depuis `remote`) : `uv run python -m unittest discover -s src/unittest/python`. Expected : `OK`.
Commit `remote` (plusieurs commits, un par Step ci-dessus, messages courts : "remote: add
ServiceDescriptor", "remote: add ServiceCatalog.publish_local", "remote: widen RemoteCapabilities with
service_descriptors, additive", "remote: add ServiceCatalog.refresh_peer", "remote: prove the catalog
round-trips across two processes").

---

### Task 6 : `swagger` — schémas typés pour les routes de service

**Files:**
- Modify (`swagger`): `src/main/python/ycappuccino/swagger/generator.py`
- Test (`swagger`): `src/unittest/python/test_generator.py` (étendre)

**Interfaces:**
- Consumes (Task 2) : `ServiceRoute.params`/`return_type`.

- [ ] **Step 1 : écrire le test avant l'implémentation**

Étendre le test existant de `_service_paths`/`build_openapi` : un `IExposedService` factice avec
`routes = (ServiceRoute(method="POST", path="/{id}/execute", params={"id": str, "count": int},
return_type=dict),)` doit produire une opération OpenAPI avec `requestBody` (schéma JSON dérivé de
`params`, en excluant les noms déjà couverts par un `_path_param`) et une réponse `200` avec un schéma
dérivé de `return_type` (mappage simple : `str`→`"string"`, `int`→`"integer"`, `bool`→`"boolean"`,
`float`→`"number"`, `dict`→`"object"`, tout le reste → `"object"` sans le détailler davantage — cohérent
avec la profondeur déjà limitée des schémas CRUD). Une `ServiceRoute` sans `params`/`return_type` (celles
qu'il en reste, aucune après Task 4 en pratique, mais rétrocompatibilité du type) doit continuer à produire
exactement l'ancienne sortie `{"200": {"description": ...}}` sans `requestBody`.

Run : échec attendu (`_service_paths` ignore encore `params`/`return_type`).

- [ ] **Step 2 : implémenter**

`swagger/src/main/python/ycappuccino/swagger/generator.py`, `_service_paths` :

```python
def _service_paths(service: IExposedService) -> dict:
    paths: dict = {}
    for route in service.routes:
        path = f"/services/{service.name}{route.path}"
        path_param_names = set(_PATH_PARAM.findall(route.path))
        operation = {
            "tags": [service.name],
            "operationId": _operation_id(service.name, route),
            "parameters": [_path_param(name) for name in path_param_names],
            "responses": {"200": _response(
                route.summary or "successful operation", _type_schema(route.return_type)
            )},
        }
        if route.summary:
            operation["summary"] = route.summary
        body_params = {
            name: type_ for name, type_ in route.params.items() if name not in path_param_names
        }
        if body_params:
            operation["requestBody"] = _request_body(_params_schema(body_params))
        paths.setdefault(path, {})[route.method.lower()] = operation
    return paths


_JSON_TYPES = {str: "string", int: "integer", bool: "boolean", float: "number", dict: "object"}


def _type_schema(python_type) -> dict:
    return {"type": _JSON_TYPES.get(python_type, "object")}


def _params_schema(params: dict) -> dict:
    return {
        "type": "object",
        "properties": {name: _type_schema(type_) for name, type_ in params.items()},
        "required": list(params),
    }
```

(`_response`/`_request_body` déjà existants, réutilisés tels quels). Run : `OK` attendu, y compris pour les
`ServiceRoute` sans `params` (le cas `{}` produit `body_params == {}`, donc pas de `requestBody`, identique
à l'ancien comportement — `route.summary or "successful operation"` inchangé). Commit `swagger` ("swagger:
derive request/response schemas for service routes from ServiceRoute.params/return_type").

- [ ] **Step 3 : vérification de bout en bout, réutilise l'exemple existant**

Run (depuis `swagger`) : `uv run python -m unittest discover -s src/unittest/python`. Vérifier
manuellement `GET /api/swagger.json` sur un exemple chargeant `permissions_app` (services migrés Task 4) :
`/api/services/login` doit maintenant montrer un `requestBody` avec `login`/`password` typés, plus le
`{"200": {"description": ...}}` nu d'avant.

---

### Task 7 : nettoyage `YCappuccinoRemote` et code mort

**Files:**
- Delete (`api`): `IRemoteServer`/`IRemoteComponentProxy`/`IRemoteClient`/
  `IRemoteComponentProxyFactory`/`IRemoteClientFactory` (`api/remote.py`, fichier entier), `YCappuccinoRemote`
  (`api/proxy.py`), `ILoginService`/`ITenantTrigger` (`api/permissions.py`), `IService` (`api/core.py`),
  `IScheduler` (`api/scheduler.py`), `IRightManager`/`IEndpoint`/`IHandlerEndpoint` (`api/endpoints.py`),
  `IScriptInterpreter` (`api/scripts.py`), `IRightSubject`/`IBootStrap` (`api/storage.py`),
  `IClobReplaceService`/`IHost`/`IHostFactory` (`api/hosts.py`)
- Delete (`core`): `src/main/python/ycappuccino/core/bundles/list_components.py`
- Modify (`core`): `src/main/python/ycappuccino/core/component_factory.py` (`_INTERFACE_ROOTS`)
- Test (`api`): `src/unittest/python/test_interfaces.py` (retirer les cas couvrant les classes supprimées)
- Test (`core`): `src/unittest/python/test_component_factory.py` (vérifier qu'aucun cas ne dépendait de
  `YCappuccinoRemote`)

**Interfaces:** aucune nouvelle — suppression pure.

- [ ] **Step 1 : confirmer zéro implémenteur restant avant de supprimer quoi que ce soit**

Depuis la racine du workspace : `grep -rln "YCappuccinoRemote\|IRemoteServer\|IRemoteComponentProxy\b\|
IRemoteClient\b\|IRemoteComponentProxyFactory\|IRemoteClientFactory\|ILoginService\b\|ITenantTrigger\|
\bIService\b\|IScheduler\b\|IRightManager\|IHandlerEndpoint\|IScriptInterpreter\|IRightSubject\|
IBootStrap\b\|IClobReplaceService\|\bIHost\b\|IHostFactory" --include="*.py" .` (hors `api/` et
`core/bundles/list_components.py` eux-mêmes). Expected : aucune correspondance en dehors des fichiers de
définition et de `test_interfaces.py`/`test_component_factory.py` — confirmé une première fois par
l'investigation de préparation de ce plan, à revérifier ici car du code a pu changer entre-temps (Tasks
1-6).

- [ ] **Step 2 (api) : supprimer**

```bash
cd api
git rm -q src/main/python/ycappuccino/api/remote.py
```

Puis retirer, dans `proxy.py`/`permissions.py`/`core.py`/`scheduler.py`/`endpoints.py`/`scripts.py`/
`storage.py`/`hosts.py`, uniquement les classes listées ci-dessus (ne rien retirer d'autre dans ces
fichiers partagés — ils contiennent d'autres interfaces toujours vivantes). Adapter
`test_interfaces.py` : retirer les cas qui instanciaient/vérifiaient ces classes.

Run (depuis `api`) : `uv run python -m unittest discover -s src/unittest/python`. Expected : `OK`. Commit
`api` ("api: remove YCappuccinoRemote and its dead orphaned interfaces, zero implementors confirmed").

- [ ] **Step 3 (core) : supprimer `list_components.py`, réduire `_INTERFACE_ROOTS`**

```bash
cd core
git rm -q src/main/python/ycappuccino/core/bundles/list_components.py
```

`core/src/main/python/ycappuccino/core/component_factory.py` :

```python
_INTERFACE_ROOTS = (YCappuccinoComponent,)
```

(retirer l'import de `YCappuccinoRemote` en tête de fichier, devenu inutile). Adapter
`test_component_factory.py` si un cas dépendait explicitement de `YCappuccinoRemote` dans
`_INTERFACE_ROOTS` (peu probable d'après l'investigation, mais à vérifier). Run (depuis `core`) : `uv run
python -m unittest discover -s src/unittest/python`. Expected : `OK`. Commit `core` ("core: drop
YCappuccinoRemote from _INTERFACE_ROOTS and the orphaned ListComponent bundle").

- [ ] **Step 4 : vérification finale, tous dépôts**

Run, dans chacun des 14 dépôts (`api`, `core`, `storage`, `endpoints_storage`, `endpoints_service`,
`http_server`, `permissions_app`, `scripts`, `scheduler`, `swagger`, `hosts`, `remote`,
`component-creator`) : `uv run python -m unittest discover -s src/unittest/python`. Expected : `OK`
partout — cette étape est la seule de tout ce plan qui touche des dépôts sans lien fonctionnel avec le
reste (`scheduler`, `hosts`, `component-creator`, `storage`, `endpoints_storage`) ; elle ne fait que
vérifier qu'aucun ne dépendait, même indirectement, du code supprimé.
