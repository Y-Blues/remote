"""
ServiceDirectory: best-effort discovery of which RemoteServer peer exposes which named service,
via each peer's __remote_capabilities__ (see capabilities.py). See discovery.py for the staleness
tradeoff this cache accepts (documented there, not repeated here).
"""

import unittest
import urllib.error

from discovery_fixtures import FakeCapabilitiesOpener, FakeManager

from ycappuccino.remote.configured_peers import ConfiguredPeers
from ycappuccino.remote.discovery import ServiceDirectory
from ycappuccino.remote.stored_peers import StoredPeers


class TestServiceDirectory(unittest.IsolatedAsyncioTestCase):

    async def test_start_eagerly_discovers_every_registered_peer(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeCapabilitiesOpener({"a.example:9000": ["service_a", "shared_service"]})
        directory = ServiceDirectory([StoredPeers(manager)], opener=opener)

        await directory.start()

        self.assertEqual(await directory.locate("service_a"), "a")
        self.assertEqual(await directory.locate("shared_service"), "a")
        # already cached by start(): locate() must not have re-queried the peer
        self.assertEqual(len(opener.requests), 1)

    async def test_unreachable_peer_at_startup_does_not_crash(self):
        manager = FakeManager({
            "down": {"host": "down.example", "port": 9000, "scheme": "http"},
            "up": {"host": "up.example", "port": 9000, "scheme": "http"},
        })
        opener = FakeCapabilitiesOpener({
            "down.example:9000": urllib.error.URLError("connection refused"),
            "up.example:9000": ["service_up"],
        })
        directory = ServiceDirectory([StoredPeers(manager)], opener=opener)

        with self.assertLogs("ycappuccino.remote.discovery", "WARNING"):
            await directory.start()  # must not raise

        self.assertEqual(await directory.locate("service_up"), "up")
        self.assertIsNone(await directory.locate("service_down"))

    async def test_cache_hit_does_not_requery_peers(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeCapabilitiesOpener({"a.example:9000": ["service_a"]})
        directory = ServiceDirectory([StoredPeers(manager)], opener=opener)
        await directory.start()
        opener.requests.clear()

        located = await directory.locate("service_a")

        self.assertEqual(located, "a")
        self.assertEqual(opener.requests, [])

    async def test_cache_miss_triggers_a_live_requery(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeCapabilitiesOpener({"a.example:9000": []})
        directory = ServiceDirectory([StoredPeers(manager)], opener=opener)
        await directory.start()

        # a peer registered after start() -- discovery has never run for it
        manager.add_peer("b", "b.example", 9001, "http")
        opener.outcomes["b.example:9001"] = ["service_b"]

        located = await directory.locate("service_b")

        self.assertEqual(located, "b")
        # start() queried "a" once; locate()'s miss re-queried both "a" and "b"
        self.assertEqual(len(opener.requests), 3)

    async def test_service_absent_everywhere_returns_none(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        opener = FakeCapabilitiesOpener({"a.example:9000": ["service_a"]})
        directory = ServiceDirectory([StoredPeers(manager)], opener=opener)
        await directory.start()

        located = await directory.locate("nonexistent")

        self.assertIsNone(located)


    async def test_configured_peers_are_asked_without_any_storage(self):
        opener = FakeCapabilitiesOpener({"b.example:9001": ["change_password"]})
        directory = ServiceDirectory([ConfiguredPeers(peers="usecases=http://b.example:9001, frontend")], opener=opener)

        self.assertEqual(await directory.locate("change_password"), "usecases")
        self.assertEqual(len(opener.requests), 1)


if __name__ == "__main__":
    unittest.main()
