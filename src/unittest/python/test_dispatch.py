"""
RemoteDispatch against fully faked resolve/locate_service/release_service -- no real Pelix context,
no real Framework, no socket. See dispatch.py's module docstring for the wire shape and the two access
levels (spec 2026-09-16-transparent-rpc-design.md, section 11.3).
"""

import dataclasses
import unittest

from remote_fixtures import FakeAuthorization

from ycappuccino.api.decorators import rpc_method
from ycappuccino.api.endpoints_storage import Forbidden, InvalidRequest, NotAuthenticated, NotFound
from ycappuccino.remote.dispatch import DISPATCH_SERVICE_NAME, RemoteDispatch

QUALIFIED_PATH = "somewhere.IInventoryService"
PEER = {"peer": "backend-1"}
ALICE = {"sub": "alice", "tid": "acme"}


@dataclasses.dataclass
class Stock:
    sku: str
    units: int


class IInventoryService:

    async def describe(self, sku) -> Stock:
        """internal only, returns a dataclass"""

    async def check_stock(self, sku, warehouse="main"):
        """internal only"""

    def not_a_coroutine(self, value):
        """internal only"""

    @rpc_method(secure=False)
    async def whoami(self, subject=None):
        """public, checks nothing"""

    @rpc_method(summary="restock an item")
    async def restock(self, sku, count=1):
        """public, needs an authorized caller"""


class FakeService(IInventoryService):
    def __init__(self):
        self.calls = []

    async def check_stock(self, sku, warehouse="main"):
        self.calls.append((sku, warehouse))
        return len(sku) * 2

    async def describe(self, sku) -> Stock:
        return Stock(sku, len(sku))

    async def whoami(self, subject=None):
        self.calls.append(subject)
        return subject

    async def restock(self, sku, count=1):
        self.calls.append(("restock", sku, count))
        return count

    def not_a_coroutine(self, value):
        return value * 3

    async def start(self):  # pragma: no cover - must never be dispatchable
        raise AssertionError("start() must never be remotely dispatchable")

    async def bind(self, service):  # pragma: no cover - must never be dispatchable
        raise AssertionError("bind() must never be remotely dispatchable")

    async def _private(self):  # pragma: no cover - must never be dispatchable
        raise AssertionError("a leading-underscore method must never be remotely dispatchable")


def _resolve(path):
    assert path == QUALIFIED_PATH
    return IInventoryService


class TestRemoteDispatch(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.service = FakeService()
        self.released = []
        self.authorization = FakeAuthorization(allowed={("call", f"{QUALIFIED_PATH}.restock")})
        self.dispatch = RemoteDispatch(
            authorizations=[self.authorization],
            resolve=_resolve,
            locate_service=lambda spec: (self.service, spec) if spec == "IInventoryService" else (None, None),
            release_service=self.released.append,
        )

    async def call(self, method_name, body=None, subject=None):
        return await self.dispatch.call("POST", [QUALIFIED_PATH, method_name], {}, body or {}, subject)

    # --- peer: internal level, any dispatchable method ---

    async def test_a_peer_dispatches_kwargs_to_the_real_local_method_and_awaits_it(self):
        result = await self.call("check_stock", {"kwargs": {"sku": "widget"}}, PEER)

        self.assertEqual(result.body, {"result": 12})
        self.assertEqual(self.service.calls, [("widget", "main")])

    async def test_a_peer_dispatches_positional_args_too(self):
        result = await self.call("check_stock", {"args": ["ab", "east"]}, PEER)

        self.assertEqual(result.body, {"result": 4})

    async def test_a_peer_calls_a_plain_non_coroutine_method_too(self):
        result = await self.call("not_a_coroutine", {"kwargs": {"value": 7}}, PEER)

        self.assertEqual(result.body, {"result": 21})

    async def test_releases_the_service_reference(self):
        await self.call("check_stock", {"kwargs": {"sku": "x"}}, PEER)

        self.assertEqual(self.released, ["IInventoryService"])

    # --- anyone else: public level, @rpc_method only ---

    async def test_a_user_cannot_reach_a_method_that_is_not_public(self):
        with self.assertRaises(NotFound):
            await self.call("check_stock", {"kwargs": {"sku": "x"}}, ALICE)
        self.assertEqual(self.service.calls, [])

    async def test_anonymous_cannot_reach_a_method_that_is_not_public(self):
        with self.assertRaises(NotFound):
            await self.call("check_stock", {"kwargs": {"sku": "x"}}, None)

    async def test_anonymous_calls_a_public_unsecured_method(self):
        result = await self.call("whoami", {}, None)

        self.assertEqual(result.body, {"result": None})

    async def test_anonymous_cannot_call_a_public_secured_method(self):
        with self.assertRaises(NotAuthenticated):
            await self.call("restock", {"kwargs": {"sku": "x"}}, None)

    async def test_an_authorized_user_calls_a_public_secured_method(self):
        result = await self.call("restock", {"kwargs": {"sku": "x", "count": 3}}, ALICE)

        self.assertEqual(result.body, {"result": 3})
        self.assertEqual(self.authorization.calls, [("alice", "call", f"{QUALIFIED_PATH}.restock")])

    async def test_an_unauthorized_user_is_forbidden(self):
        self.authorization.allowed = set()

        with self.assertRaises(Forbidden):
            await self.call("restock", {"kwargs": {"sku": "x"}}, ALICE)
        self.assertEqual(self.service.calls, [])

    async def test_without_any_authorization_a_secured_method_is_forbidden(self):
        dispatch = RemoteDispatch(resolve=_resolve, locate_service=lambda spec: (self.service, spec))

        with self.assertRaises(Forbidden):
            await dispatch.call("POST", [QUALIFIED_PATH, "restock"], {}, {"kwargs": {"sku": "x"}}, ALICE)

    # --- subject ---

    async def test_forwards_the_authenticated_subject_to_a_method_that_accepts_one(self):
        result = await self.call("whoami", {}, ALICE)

        self.assertEqual(result.body, {"result": ALICE})

    async def test_does_not_forward_subject_to_a_method_without_that_parameter(self):
        result = await self.call("check_stock", {"kwargs": {"sku": "widget"}}, PEER)

        self.assertEqual(result.body, {"result": 12})

    async def test_a_subject_in_the_payload_is_replaced_by_the_authenticated_one(self):
        result = await self.call("whoami", {"kwargs": {"subject": {"sub": "spoofed"}}}, ALICE)

        self.assertEqual(result.body, {"result": ALICE})

    async def test_a_dataclass_result_travels_as_a_json_object(self):
        result = await self.call("describe", {"kwargs": {"sku": "abc"}}, PEER)

        self.assertEqual(result.body, {"result": {"sku": "abc", "units": 3}})

    async def test_the_real_component_behind_an_ipopo_proxy_is_called(self):
        # the framework registers a Proxy wrapping the component: dispatch must see the real signature
        from ycappuccino.api.proxy import Proxy

        proxy = Proxy()
        proxy._obj = self.service
        dispatch = RemoteDispatch(resolve=_resolve, locate_service=lambda spec: (proxy, spec))

        result = await dispatch.call("POST", [QUALIFIED_PATH, "whoami"], {}, {}, ALICE)

        self.assertEqual(result.body, {"result": ALICE})

    # --- structural refusals ---

    async def test_missing_extra_path_is_invalid(self):
        with self.assertRaises(InvalidRequest):
            await self.dispatch.call("POST", [QUALIFIED_PATH], {}, {}, PEER)

    async def test_unresolvable_qualified_path_is_not_found(self):
        dispatch = RemoteDispatch(
            resolve=lambda path: (_ for _ in ()).throw(ImportError("nope")),
            locate_service=lambda spec: (None, None),
        )

        with self.assertRaises(NotFound):
            await dispatch.call("POST", ["nowhere.Nothing", "call"], {}, {}, PEER)

    async def test_no_local_instance_is_not_found(self):
        dispatch = RemoteDispatch(resolve=_resolve, locate_service=lambda spec: (None, None))

        with self.assertRaises(NotFound):
            await dispatch.call("POST", [QUALIFIED_PATH, "check_stock"], {}, {}, PEER)

    async def test_unknown_method_is_not_found(self):
        with self.assertRaises(NotFound):
            await self.call("does_not_exist", {}, PEER)

    async def test_lifecycle_bind_and_private_methods_are_never_dispatchable(self):
        for name in ("start", "stop", "bind", "un_bind", "_private"):
            with self.subTest(name=name), self.assertRaises(NotFound):
                await self.call(name, {}, PEER)

    async def test_is_named_and_left_to_its_own_checks(self):
        self.assertEqual(RemoteDispatch.name, DISPATCH_SERVICE_NAME)
        self.assertFalse(RemoteDispatch.secure)
        self.assertEqual(DISPATCH_SERVICE_NAME, "__remote_dispatch__")


if __name__ == "__main__":
    unittest.main()
