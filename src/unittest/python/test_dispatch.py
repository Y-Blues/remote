"""
RemoteDispatch against fully faked resolve/locate_service/release_service -- no real Pelix context,
no real Framework, no socket. See dispatch.py's module docstring for the wire shape and the security
discussion.
"""

import unittest

from ycappuccino.api.endpoints_storage import InvalidRequest, NotFound
from ycappuccino.remote.dispatch import DISPATCH_SERVICE_NAME, RemoteDispatch

QUALIFIED_PATH = "somewhere.IInventoryService"


class FakeInterface:
    pass


FakeInterface.__name__ = "IInventoryService"  # class-body "__name__ = ..." would NOT rename it


class FakeService:
    def __init__(self):
        self.calls = []

    async def check_stock(self, sku, warehouse="main"):
        self.calls.append((sku, warehouse))
        return len(sku) * 2

    async def whoami(self, subject=None):
        self.calls.append(subject)
        return subject

    def not_a_coroutine(self, value):
        return value * 3

    async def start(self):  # pragma: no cover - must never be dispatchable
        raise AssertionError("start() must never be remotely dispatchable")

    async def _private(self):  # pragma: no cover - must never be dispatchable
        raise AssertionError("a leading-underscore method must never be remotely dispatchable")


def _resolve(path):
    assert path == QUALIFIED_PATH
    return FakeInterface


class TestRemoteDispatch(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.service = FakeService()
        self.released = []
        self.dispatch = RemoteDispatch(
            resolve=_resolve,
            locate_service=lambda spec: (self.service, spec) if spec == "IInventoryService" else (None, None),
            release_service=self.released.append,
        )

    async def test_dispatches_kwargs_to_the_real_local_method_and_awaits_it(self):
        result = await self.dispatch.call(
            "POST", [QUALIFIED_PATH, "check_stock"], {}, {"kwargs": {"sku": "widget"}}, None
        )

        self.assertEqual(result.body, {"result": 12})
        self.assertEqual(self.service.calls, [("widget", "main")])

    async def test_dispatches_positional_args_too(self):
        result = await self.dispatch.call(
            "POST", [QUALIFIED_PATH, "check_stock"], {}, {"args": ["ab", "east"]}, None
        )

        self.assertEqual(result.body, {"result": 4})
        self.assertEqual(self.service.calls, [("ab", "east")])

    async def test_releases_the_service_reference_even_on_success(self):
        await self.dispatch.call("POST", [QUALIFIED_PATH, "check_stock"], {}, {"kwargs": {"sku": "x"}}, None)

        self.assertEqual(self.released, ["IInventoryService"])

    async def test_works_for_a_plain_non_coroutine_method_too(self):
        result = await self.dispatch.call(
            "POST", [QUALIFIED_PATH, "not_a_coroutine"], {}, {"kwargs": {"value": 7}}, None
        )

        self.assertEqual(result.body, {"result": 21})

    async def test_forwards_the_already_decoded_subject_to_a_method_that_accepts_one(self):
        result = await self.dispatch.call(
            "POST", [QUALIFIED_PATH, "whoami"], {}, {}, {"sub": "alice", "tid": "acme"}
        )

        self.assertEqual(result.body, {"result": {"sub": "alice", "tid": "acme"}})

    async def test_does_not_forward_subject_to_a_method_without_that_parameter(self):
        result = await self.dispatch.call(
            "POST", [QUALIFIED_PATH, "check_stock"], {}, {"kwargs": {"sku": "widget"}}, {"sub": "alice"}
        )

        self.assertEqual(result.body, {"result": 12})
        self.assertEqual(self.service.calls, [("widget", "main")])

    async def test_client_supplied_kwargs_subject_is_overridden_by_the_decoded_one(self):
        result = await self.dispatch.call(
            "POST", [QUALIFIED_PATH, "whoami"], {}, {"kwargs": {"subject": {"sub": "spoofed"}}}, {"sub": "real"}
        )

        self.assertEqual(result.body, {"result": {"sub": "real"}})

    async def test_missing_extra_path_is_invalid(self):
        with self.assertRaises(InvalidRequest):
            await self.dispatch.call("POST", [QUALIFIED_PATH], {}, {}, None)

    async def test_unresolvable_qualified_path_is_not_found(self):
        dispatch = RemoteDispatch(
            resolve=lambda path: (_ for _ in ()).throw(ImportError("nope")),
            locate_service=lambda spec: (None, None),
        )

        with self.assertRaises(NotFound):
            await dispatch.call("POST", ["nowhere.Nothing", "call"], {}, {}, None)

    async def test_no_local_instance_is_not_found(self):
        dispatch = RemoteDispatch(resolve=_resolve, locate_service=lambda spec: (None, None))

        with self.assertRaises(NotFound):
            await dispatch.call("POST", [QUALIFIED_PATH, "check_stock"], {}, {}, None)

    async def test_unknown_method_is_not_found(self):
        with self.assertRaises(NotFound):
            await self.dispatch.call("POST", [QUALIFIED_PATH, "does_not_exist"], {}, {}, None)

    async def test_lifecycle_methods_are_never_dispatchable(self):
        with self.assertRaises(NotFound):
            await self.dispatch.call("POST", [QUALIFIED_PATH, "start"], {}, {}, None)

    async def test_private_methods_are_never_dispatchable(self):
        with self.assertRaises(NotFound):
            await self.dispatch.call("POST", [QUALIFIED_PATH, "_private"], {}, {}, None)

    async def test_is_named_and_unsecured(self):
        self.assertEqual(RemoteDispatch.name, DISPATCH_SERVICE_NAME)
        self.assertFalse(RemoteDispatch.secure)
        self.assertEqual(DISPATCH_SERVICE_NAME, "__remote_dispatch__")


if __name__ == "__main__":
    unittest.main()
