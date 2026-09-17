"""
The peers an instance knows: stored as RemoteServer items (StoredPeers), or declared in the application's
configuration (ConfiguredPeers) -- the only choice for an instance whose storage is itself a peer, or which
has no storage at all.
"""

import unittest

from discovery_fixtures import FakeManager

from ycappuccino.remote.configured_peers import ConfiguredPeers
from ycappuccino.remote.peers import IPeers
from ycappuccino.remote.stored_peers import StoredPeers


class TestConfiguredPeers(unittest.IsolatedAsyncioTestCase):

    async def test_peers_are_read_from_the_configuration_with_the_shared_secret(self):
        peers = ConfiguredPeers(peers="storage=http://localhost:8201, frontend", secret="s3cr3t")

        self.assertEqual(
            await peers.all(),
            [
                {"_id": "storage", "scheme": "http", "host": "localhost", "port": 8201, "secret": "s3cr3t"},
                {"_id": "frontend", "secret": "s3cr3t"},
            ],
        )
        self.assertEqual((await peers.get("frontend"))["secret"], "s3cr3t")
        self.assertIsNone(await peers.get("nobody"))

    async def test_no_configuration_means_no_peer(self):
        self.assertEqual(await ConfiguredPeers().all(), [])

    async def test_a_malformed_address_is_refused(self):
        with self.assertRaises(ValueError):
            await ConfiguredPeers(peers="storage=localhost:8201").all()

    def test_both_are_peer_sources(self):
        self.assertTrue(issubclass(ConfiguredPeers, IPeers))
        self.assertTrue(issubclass(StoredPeers, IPeers))


class TestStoredPeers(unittest.IsolatedAsyncioTestCase):

    async def test_peers_are_the_remote_server_items(self):
        manager = FakeManager({"a": {"host": "a.example", "port": 9000, "scheme": "http"}})
        peers = StoredPeers(manager)

        self.assertEqual(
            [(peer["_id"], peer["host"], peer["port"]) for peer in await peers.all()], [("a", "a.example", 9000)]
        )
        self.assertEqual((await peers.get("a"))["host"], "a.example")
        self.assertIsNone(await peers.get("b"))


if __name__ == "__main__":
    unittest.main()
