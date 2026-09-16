"""
make_generic_proxy against a fake HTTP opener -- no real socket, no real peer. Error translation
(401/403/404/... -> NotAuthenticated/Forbidden/NotFound/...) is already exercised end to end for
call_peer by test_remote_call.py; not duplicated here.
"""

import abc
import json
import unittest

from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.remote.remote_proxy import make_generic_proxy


class IInventoryService(YCappuccinoComponent, abc.ABC):

    @abc.abstractmethod
    async def check_stock(self, sku: str, warehouse: str = "main") -> int:
        """units of `sku` available at `warehouse`"""


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
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(self.payload)


class TestMakeGenericProxy(unittest.IsolatedAsyncioTestCase):

    async def test_forwards_a_call_as_a_generic_kwargs_rpc_to_remote_dispatch(self):
        opener = FakeOpener({"status": 200, "meta": {}, "data": {"result": 42}})
        proxy_class = make_generic_proxy(IInventoryService, "somewhere.IInventoryService")
        proxy = proxy_class(peer_host="peer.example", peer_port=9000, peer_scheme="http", opener=opener)

        result = await proxy.check_stock("widget")

        self.assertEqual(result, 42)
        request = opener.requests[0]
        self.assertEqual(
            request.full_url,
            "http://peer.example:9000/api/services/__remote_dispatch__/somewhere.IInventoryService/check_stock",
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"kwargs": {"sku": "widget", "warehouse": "main"}})

    async def test_default_parameter_values_are_sent_when_omitted_by_the_caller(self):
        opener = FakeOpener({"status": 200, "meta": {}, "data": {"result": 0}})
        proxy_class = make_generic_proxy(IInventoryService, "somewhere.IInventoryService")
        proxy = proxy_class(opener=opener)

        await proxy.check_stock("sku-only")

        self.assertEqual(json.loads(opener.requests[0].data), {"kwargs": {"sku": "sku-only", "warehouse": "main"}})

    async def test_the_proxy_class_is_a_concrete_subclass_of_the_interface(self):
        proxy_class = make_generic_proxy(IInventoryService, "somewhere.IInventoryService")

        self.assertTrue(issubclass(proxy_class, IInventoryService))
        self.assertEqual(proxy_class.__name__, "RemoteInventoryService")
        proxy_class(opener=FakeOpener({}))  # concrete: does not raise TypeError (abstract methods missing)

    async def test_start_and_stop_are_no_ops(self):
        proxy_class = make_generic_proxy(IInventoryService, "somewhere.IInventoryService")
        proxy = proxy_class(opener=FakeOpener({}))

        await proxy.start()
        await proxy.stop()


if __name__ == "__main__":
    unittest.main()
