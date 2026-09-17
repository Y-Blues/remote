import io
import json
import unittest
import urllib.error

from ycappuccino.api.endpoints_storage import Forbidden, InvalidRequest, NotAuthenticated, NotFound
from ycappuccino.remote.call import RemoteCall
from ycappuccino.remote.models.remote_server import RemoteServer

PEERS = {"peer-a": {"host": "peer.example", "port": 9000, "scheme": "http"}}


class FakeManager:
    """fake IManager: only get_one("remoteServer", ...) is exercised by RemoteCall"""

    def __init__(self, peers=None):
        self._peers = peers or {}

    async def get_one(self, item_id, id, params=None, subject=None):
        assert item_id == "remoteServer"
        assert subject is None  # RemoteCall reads its registry as a system read
        document = self._peers.get(id)
        if document is None:
            return None
        server = RemoteServer()
        server.id(id)
        server.host(document["host"])
        server.port(document["port"])
        server.scheme(document["scheme"])
        if document.get("secret"):
            server.secret(document["secret"])
        return server

    async def start(self):
        pass

    async def stop(self):
        pass


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    """records the Request it receives; raises HTTPError like a real urlopen for status >= 400"""

    def __init__(self, status=200, payload=None, headers=None):
        self.status = status
        self.payload = payload if payload is not None else {"status": 200, "meta": {}, "data": {}}
        self.headers = headers or {}
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, self.status, "error", None,
                io.BytesIO(json.dumps(self.payload).encode()),
            )
        return FakeResponse(self.status, self.payload, self.headers)


class TestRemoteCall(unittest.IsolatedAsyncioTestCase):

    async def test_forwards_a_call_to_the_peer(self):
        opener = FakeOpener(payload={"status": 200, "meta": {}, "data": {"echo": "hi"}})
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        result = await remote_call.call("POST", ["peer-a", "echo"], {}, {"msg": "hi"}, None)

        self.assertEqual(result.body, {"echo": "hi"})
        request = opener.requests[0]
        self.assertEqual(request.full_url, "http://peer.example:9000/api/services/echo")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"msg": "hi"})

    async def test_forwards_extra_path_and_query_params(self):
        opener = FakeOpener()
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        await remote_call.call("GET", ["peer-a", "items", "sub"], {"limit": "5"}, None, None)

        request = opener.requests[0]
        self.assertEqual(request.full_url, "http://peer.example:9000/api/services/items/sub?limit=5")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)

    async def test_unknown_peer_is_not_found(self):
        remote_call = RemoteCall(FakeManager(PEERS), opener=FakeOpener())

        with self.assertRaises(NotFound):
            await remote_call.call("GET", ["unknown", "echo"], {}, None, None)

    async def test_missing_target_service_is_invalid(self):
        remote_call = RemoteCall(FakeManager(PEERS), opener=FakeOpener())

        with self.assertRaises(InvalidRequest):
            await remote_call.call("GET", ["peer-a"], {}, None, None)

    async def test_peer_401_becomes_not_authenticated(self):
        opener = FakeOpener(status=401, payload={"status": 401, "meta": {}, "data": {"error": "no auth"}})
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        with self.assertRaises(NotAuthenticated):
            await remote_call.call("GET", ["peer-a", "secret"], {}, None, None)

    async def test_peer_403_becomes_forbidden(self):
        opener = FakeOpener(status=403, payload={"status": 403, "meta": {}, "data": {"error": "no"}})
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        with self.assertRaises(Forbidden):
            await remote_call.call("GET", ["peer-a", "secret"], {}, None, None)

    async def test_peer_404_becomes_not_found(self):
        opener = FakeOpener(status=404, payload={"status": 404, "meta": {}, "data": {"error": "no"}})
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        with self.assertRaises(NotFound):
            await remote_call.call("GET", ["peer-a", "missing"], {}, None, None)

    async def test_peer_400_becomes_invalid_request(self):
        opener = FakeOpener(status=400, payload={"status": 400, "meta": {}, "data": {"error": "bad"}})
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        with self.assertRaises(InvalidRequest):
            await remote_call.call("POST", ["peer-a", "echo"], {}, {}, None)

    async def test_peer_500_becomes_a_generic_error(self):
        opener = FakeOpener(status=500, payload={"status": 500, "meta": {}, "data": {"error": "boom"}})
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        with self.assertRaises(Exception):
            await remote_call.call("GET", ["peer-a", "echo"], {}, None, None)

    async def test_response_headers_are_forwarded_except_transport_ones(self):
        opener = FakeOpener(headers={"Set-Cookie": "a=b", "Content-Type": "application/json"})
        remote_call = RemoteCall(FakeManager(PEERS), opener=opener)

        result = await remote_call.call("GET", ["peer-a", "echo"], {}, None, None)

        self.assertEqual(result.headers, {"Set-Cookie": "a=b"})

    async def test_the_caller_subject_is_forwarded_to_a_peer_with_a_secret(self):
        opener = FakeOpener()
        remote_call = RemoteCall(FakeManager({"peer-a": {**PEERS["peer-a"], "secret": "s3cr3t"}}), opener=opener)

        await remote_call.call("POST", ["peer-a", "echo"], {}, {}, {"sub": "alice", "tid": "acme"})

        headers = {key.lower(): value for key, value in opener.requests[0].header_items()}
        self.assertEqual(json.loads(headers["x-ycappuccino-subject"]), {"sub": "alice", "tid": "acme"})

    async def test_secure_flag_and_name(self):
        self.assertTrue(RemoteCall.secure)
        self.assertEqual(RemoteCall.name, "remote_call")


if __name__ == "__main__":
    unittest.main()
