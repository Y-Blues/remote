"""
ComponentDirectory against fakes: no real Framework, no real Pelix, no socket. `_discover_all()` is
called directly in most tests (the synchronous discovery+spawn step both locate() and execute()
build on) rather than through start(), since start() deliberately hands its own discovery+spawn
work to a detached background thread (see component_directory.py's module docstring, "Piège de
timing") -- a dedicated test at the bottom of this file proves that threaded contract instead.
"""

import abc
import time
import unittest
import urllib.error

from discovery_fixtures import FakeManager, FakeResponse

from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.remote.component_directory import ComponentDirectory


class IInventoryService(YCappuccinoComponent, abc.ABC):
    """a real, resolvable interface -- QUALIFIED_A below points at it, so make_generic_proxy has
    something genuine to synthesize a proxy for (resolve_class must be able to import it)."""

    @abc.abstractmethod
    async def check_stock(self, sku: str) -> int:
        """units available"""


class ConcreteThing:
    """a plain, non-abstract class -- stands in for a peer's own concrete component class name,
    always present alongside its interfaces in a "provides" list, see
    component_factory._provided_specifications."""


QUALIFIED_A = f"{__name__}.IInventoryService"
# QUALIFIED_B is never resolved in any test that asserts on proxy creation -- only used to prove
# locate()'s cache-miss requery finds a *newly* registered peer, resolve_class is irrelevant there
QUALIFIED_B = "somewhere.IBillingService"


class FakeComponentsOpener:
    """routes a __remote_capabilities__ request to a canned "components" list, keyed by
    "host:port" found in its URL -- same style as discovery_fixtures.FakeCapabilitiesOpener, but
    for the widened {"components": [...]} shape (2026-09-16 second addendum)."""

    def __init__(self, outcomes=None):
        self.outcomes = dict(outcomes or {})
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request.full_url)
        for key, outcome in self.outcomes.items():
            if key in request.full_url:
                if isinstance(outcome, BaseException):
                    raise outcome
                return FakeResponse({"status": 200, "meta": {}, "data": {"services": [], "components": outcome}})
        raise urllib.error.URLError("no route configured for this URL in the test fake")


def _component(module, klass, provides):
    return {"module": module, "class": klass, "provides": provides}


class RecordingInstantiate:
    def __init__(self):
        self.calls = []

    def __call__(self, component, properties):
        self.calls.append((component, properties))
        return "handle"


class TestComponentDirectoryDiscovery(unittest.IsolatedAsyncioTestCase):

    async def test_locate_returns_the_peer_that_provides_a_qualified_path(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [QUALIFIED_A])]}
        )
        directory = ComponentDirectory(manager, opener=opener, local_specifications=lambda: set())

        located = await directory.locate(QUALIFIED_A)

        self.assertEqual(located, "a")

    async def test_unreachable_peer_does_not_crash_discovery(self):
        manager = FakeManager({"down": {"host": "down.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener({"down.example:9000": urllib.error.URLError("connection refused")})
        directory = ComponentDirectory(manager, opener=opener, local_specifications=lambda: set())

        with self.assertLogs("ycappuccino.remote.component_directory", "WARNING"):
            located = await directory.locate(QUALIFIED_A)  # must not raise

        self.assertIsNone(located)

    async def test_locate_cache_hit_does_not_requery(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [QUALIFIED_A])]}
        )
        directory = ComponentDirectory(manager, opener=opener, local_specifications=lambda: set())
        await directory._discover_all()
        opener.requests.clear()

        located = await directory.locate(QUALIFIED_A)

        self.assertEqual(located, "a")
        self.assertEqual(opener.requests, [])

    async def test_locate_cache_miss_triggers_a_live_requery(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener({"a.example:9000": []})
        directory = ComponentDirectory(manager, opener=opener, local_specifications=lambda: set())
        await directory._discover_all()

        manager.add_peer("b", "b.example", 9001, "http")
        opener.outcomes["b.example:9001"] = [_component("somewhere", "BillingService", [QUALIFIED_B])]

        located = await directory.locate(QUALIFIED_B)

        self.assertEqual(located, "b")

    async def test_absent_everywhere_returns_none(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener({"a.example:9000": []})
        directory = ComponentDirectory(manager, opener=opener, local_specifications=lambda: set())

        self.assertIsNone(await directory.locate("nowhere.Nothing"))


class TestComponentDirectoryProxySpawning(unittest.IsolatedAsyncioTestCase):

    async def test_spawns_a_proxy_for_a_qualified_path_not_available_locally(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [QUALIFIED_A])]}
        )
        instantiate = RecordingInstantiate()
        directory = ComponentDirectory(
            manager, opener=opener, instantiate=instantiate, local_specifications=lambda: set()
        )

        await directory._discover_all()

        self.assertEqual(len(instantiate.calls), 1)
        component, properties = instantiate.calls[0]
        self.assertTrue(issubclass(component, IInventoryService))
        self.assertEqual(
            properties,
            {"peer_host": "a.example", "peer_port": 9000, "peer_scheme": "http", "timeout": 5.0, "opener": opener},
        )

    async def test_does_not_spawn_a_proxy_already_available_locally(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [QUALIFIED_A])]}
        )
        instantiate = RecordingInstantiate()
        directory = ComponentDirectory(
            manager, opener=opener, instantiate=instantiate, local_specifications=lambda: {QUALIFIED_A}
        )

        await directory._discover_all()

        self.assertEqual(instantiate.calls, [])

    async def test_does_not_spawn_the_same_proxy_twice(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [QUALIFIED_A])]}
        )
        instantiate = RecordingInstantiate()
        directory = ComponentDirectory(
            manager, opener=opener, instantiate=instantiate, local_specifications=lambda: set()
        )

        await directory._discover_all()
        await directory._discover_all()

        self.assertEqual(len(instantiate.calls), 1)

    async def test_does_not_spawn_a_proxy_for_a_peers_own_concrete_class_name(self):
        # a peer's "provides" always lists BOTH a component's own concrete class name and every
        # interface it implements (see component_factory._provided_specifications) -- only the
        # latter (an abstract class) is worth proxying, see component_directory.py's comment.
        concrete_path = f"{__name__}.ConcreteThing"
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [concrete_path, QUALIFIED_A])]}
        )
        instantiate = RecordingInstantiate()
        directory = ComponentDirectory(
            manager, opener=opener, instantiate=instantiate, local_specifications=lambda: set()
        )

        await directory._discover_all()

        self.assertEqual(len(instantiate.calls), 1)
        component, _ = instantiate.calls[0]
        self.assertTrue(issubclass(component, IInventoryService))

    async def test_unresolvable_qualified_path_is_skipped_without_crashing(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("nowhere", "Nothing", ["nowhere.Nothing"])]}
        )
        instantiate = RecordingInstantiate()
        directory = ComponentDirectory(
            manager, opener=opener, instantiate=instantiate, local_specifications=lambda: set()
        )

        with self.assertLogs("ycappuccino.remote.component_directory", "WARNING"):
            await directory._discover_all()  # must not raise: "nowhere.Nothing" is not importable

        self.assertEqual(instantiate.calls, [])

    async def test_execute_on_a_remote_server_upsert_triggers_discovery_and_spawning(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [QUALIFIED_A])]}
        )
        instantiate = RecordingInstantiate()
        directory = ComponentDirectory(
            manager, opener=opener, instantiate=instantiate, local_specifications=lambda: set()
        )

        await directory.execute("upsert", "remoteServer", None)

        self.assertEqual(len(instantiate.calls), 1)

    async def test_is_an_itrigger_on_remote_server_upserts(self):
        self.assertEqual(ComponentDirectory.item_id, "remoteServer")
        self.assertEqual(ComponentDirectory.actions, ("upsert",))
        self.assertTrue(ComponentDirectory.post)


class TestComponentDirectoryStart(unittest.IsolatedAsyncioTestCase):

    async def test_start_spawns_proxies_off_a_background_thread_without_blocking(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeComponentsOpener(
            {"a.example:9000": [_component("somewhere", "InventoryService", [QUALIFIED_A])]}
        )
        instantiate = RecordingInstantiate()
        directory = ComponentDirectory(
            manager, opener=opener, instantiate=instantiate, local_specifications=lambda: set()
        )

        await directory.start()  # must return immediately, before discovery necessarily ran

        deadline = time.monotonic() + 2.0
        while not instantiate.calls and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(len(instantiate.calls), 1)


if __name__ == "__main__":
    unittest.main()
