import shutil
import unittest

from remote_fixtures import create_manager

from ycappuccino.remote.models.remote_server import RemoteServer


class TestRemoteServer(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.manager, directory = create_manager()
        self.addCleanup(shutil.rmtree, directory, True)

    async def test_round_trip_through_the_manager(self):
        server = RemoteServer()
        server.id("eu-node-2")
        server.host("eu-node-2.internal")
        server.port(9000)
        server.scheme("http")

        await self.manager.up_sert_model(server)
        stored = (await self.manager.get_one("remoteServer", "eu-node-2")).get_storage_model()

        self.assertEqual(stored["host"], "eu-node-2.internal")
        self.assertEqual(stored["port"], 9000)
        self.assertEqual(stored["scheme"], "http")


if __name__ == "__main__":
    unittest.main()
