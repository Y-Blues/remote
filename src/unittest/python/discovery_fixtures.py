"""
Fakes shared by the discovery/federated_endpoint tests: a fake IManager over RemoteServer models
(get_one and get_many, same shape as a real Manager would give ServiceDirectory/RemoteCall), and a
fake HTTP opener that answers __remote_capabilities__ (or any service) queries per peer, without a
real socket -- same style as test_remote_call.py's FakeOpener/FakeResponse.
"""

import json
import urllib.error

from ycappuccino.remote.models.remote_server import RemoteServer


class FakeManager:
    """fake IManager: only get_one/get_many("remoteServer", ...) are exercised"""

    def __init__(self, peers=None):
        self._peers = dict(peers or {})

    def add_peer(self, id, host, port, scheme="http"):
        self._peers[id] = {"host": host, "port": port, "scheme": scheme}

    def remove_peer(self, id):
        del self._peers[id]

    async def get_one(self, item_id, id, params=None, subject=None):
        assert item_id == "remoteServer"
        assert subject is None
        return self._to_model(id, self._peers.get(id))

    async def get_many(self, item_id, params=None, subject=None):
        assert item_id == "remoteServer"
        assert subject is None
        return [self._to_model(id, document) for id, document in self._peers.items()]

    @staticmethod
    def _to_model(id, document):
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


class FakeCapabilitiesOpener:
    """
    Routes a request to a canned outcome keyed by "host:port" found in its URL: either a list of
    service names (successful __remote_capabilities__ response) or an Exception instance to raise
    (simulating an unreachable peer -- URLError, exactly what a real urlopen would raise, not
    caught/translated by _http.call_peer, so the caller must handle it itself).
    """

    def __init__(self, outcomes=None):
        self.outcomes = dict(outcomes or {})
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request.full_url)
        for key, outcome in self.outcomes.items():
            if key in request.full_url:
                if isinstance(outcome, BaseException):
                    raise outcome
                return FakeResponse({"status": 200, "meta": {}, "data": {"services": outcome}})
        raise urllib.error.URLError("no route configured for this URL in the test fake")
