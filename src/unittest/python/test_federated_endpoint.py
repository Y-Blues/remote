"""
FederatedServiceEndpoint: an IServiceEndpoint that tries local services first, exactly like
endpoints_service.ServiceEndpoint (same authorization semantics, duplicated on purpose -- see
federated_endpoint.py's module docstring for why it cannot depend on ServiceEndpoint instead), then
falls back to ServiceDirectory + a direct HTTP call to the resolved peer's own /api/services/<name>.
"""

import json
import unittest

from discovery_fixtures import FakeManager, FakeResponse
from remote_fixtures import ALICE, FakeAuthorization, FakeExposedService

from ycappuccino.api.endpoints_service import CALL
from ycappuccino.api.endpoints_storage import Forbidden, NotAuthenticated, NotFound
from ycappuccino.remote.federated_endpoint import FederatedServiceEndpoint


class FakeDirectory:
    def __init__(self, mapping=None):
        self.mapping = dict(mapping or {})
        self.calls = []

    async def locate(self, service_name):
        self.calls.append(service_name)
        return self.mapping.get(service_name)


class FakeOpener:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {"status": 200, "meta": {}, "data": {}}
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(self.payload)


class TestFederatedServiceEndpoint(unittest.IsolatedAsyncioTestCase):

    # --- local-first behavior, mirroring endpoints_service.ServiceEndpoint exactly ---

    async def test_calls_a_local_unsecured_service_without_a_subject(self):
        echo = FakeExposedService("echo", secure=False)
        endpoint = FederatedServiceEndpoint([echo], [], FakeDirectory(), FakeManager())

        result = await endpoint.call("echo", "POST", ["extra"], {"q": "1"}, {"msg": "hi"}, None)

        self.assertEqual(result.body, {"ok": True})
        self.assertEqual(echo.calls, [("POST", ["extra"], {"q": "1"}, {"msg": "hi"}, None)])

    async def test_local_secured_service_requires_a_subject(self):
        secret = FakeExposedService("secret")
        endpoint = FederatedServiceEndpoint([secret], [FakeAuthorization()], FakeDirectory(), FakeManager())

        with self.assertRaises(NotAuthenticated):
            await endpoint.call("secret", "POST", [], {}, {}, None)
        self.assertEqual(secret.calls, [])

    async def test_local_secured_service_without_authorization_is_forbidden(self):
        secret = FakeExposedService("secret")
        endpoint = FederatedServiceEndpoint([secret], [], FakeDirectory(), FakeManager())

        with self.assertLogs("ycappuccino.remote.federated_endpoint", "WARNING"):
            with self.assertRaises(Forbidden):
                await endpoint.call("secret", "POST", [], {}, {}, ALICE)

    async def test_local_secured_service_asks_the_authorization(self):
        secret = FakeExposedService("secret")
        authorization = FakeAuthorization(allowed=())
        endpoint = FederatedServiceEndpoint([secret], [authorization], FakeDirectory(), FakeManager())

        with self.assertRaises(Forbidden):
            await endpoint.call("secret", "POST", [], {}, {}, ALICE)

        authorization.allowed = {(CALL, "secret")}
        await endpoint.call("secret", "POST", [], {}, {}, ALICE)

        self.assertEqual(len(secret.calls), 1)

    async def test_local_service_takes_priority_over_a_directory_entry(self):
        echo = FakeExposedService("echo", secure=False)
        directory = FakeDirectory({"echo": "peer-a"})
        endpoint = FederatedServiceEndpoint([echo], [], directory, FakeManager())

        await endpoint.call("echo", "POST", [], {}, {}, None)

        self.assertEqual(directory.calls, [])  # directory never consulted: local wins

    # --- remote fallback, the new behavior ---

    async def test_local_miss_asks_the_directory_and_forwards_to_the_peer(self):
        manager = FakeManager({"peer-a": {"host": "peer.example", "port": 9000, "scheme": "http"}})
        directory = FakeDirectory({"remote_echo": "peer-a"})
        opener = FakeOpener({"status": 200, "meta": {}, "data": {"echo": "hi"}})
        endpoint = FederatedServiceEndpoint([], [], directory, manager, opener=opener)

        result = await endpoint.call("remote_echo", "POST", [], {}, {"msg": "hi"}, None)

        self.assertEqual(result.body, {"echo": "hi"})
        self.assertEqual(directory.calls, ["remote_echo"])
        request = opener.requests[0]
        self.assertEqual(request.full_url, "http://peer.example:9000/api/services/remote_echo")
        self.assertEqual(request.get_method(), "POST")

    async def test_forwarding_to_a_peer_carries_the_caller_subject(self):
        manager = FakeManager({"peer-a": {"host": "peer.example", "port": 9000, "scheme": "http", "secret": "s3cr3t"}})
        opener = FakeOpener({"status": 200, "meta": {}, "data": {}})
        endpoint = FederatedServiceEndpoint([], [], FakeDirectory({"remote_echo": "peer-a"}), manager, opener=opener)

        await endpoint.call("remote_echo", "POST", [], {}, {}, {"sub": "alice", "tid": "acme"})

        headers = {key.lower(): value for key, value in opener.requests[0].header_items()}
        self.assertEqual(json.loads(headers["x-ycappuccino-subject"]), {"sub": "alice", "tid": "acme"})

    async def test_missing_everywhere_is_not_found(self):
        endpoint = FederatedServiceEndpoint([], [], FakeDirectory(), FakeManager())

        with self.assertRaises(NotFound):
            await endpoint.call("nowhere", "GET", [], {}, None, None)

    async def test_directory_points_to_a_peer_no_longer_registered_is_not_found(self):
        directory = FakeDirectory({"ghost": "gone"})
        endpoint = FederatedServiceEndpoint([], [], directory, FakeManager())

        with self.assertRaises(NotFound):
            await endpoint.call("ghost", "GET", [], {}, None, None)


if __name__ == "__main__":
    unittest.main()
