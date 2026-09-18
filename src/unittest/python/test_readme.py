"""
Runs the "Tester avec remote" example from README.md, to keep it executable.
"""

import json
import unittest

from ycappuccino.remote.call import RemoteCall
from ycappuccino.remote.stored_peers import StoredPeers
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
        remote_call = RemoteCall([StoredPeers(manager)], opener=FakeOpener({"status": 200, "meta": {}, "data": {"ok": True}}))

        result = await remote_call.call("GET", ["peer-a", "echo"], {}, None, None)

        self.assertEqual(result.body, {"ok": True})


if __name__ == "__main__":
    unittest.main()
