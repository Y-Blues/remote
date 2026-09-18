"""
ServiceCatalog: stores what each peer lets this instance call, and answers where an interface can be
called, with which signatures, on which host:port. Real memory Manager, fake HTTP opener.
"""

import shutil
import unittest
import urllib.error

from discovery_fixtures import FakeResponse
from remote_fixtures import create_manager

from ycappuccino.api.endpoints_storage import NotFound
from ycappuccino.remote.catalog import ServiceCatalog
from ycappuccino.remote.stored_peers import StoredPeers
from ycappuccino.remote.models.remote_server import RemoteServer

LOGIN = "ycappuccino.api.permissions.ILoginService"
CRUD = "ycappuccino.api.endpoints_storage.ICrud"
LOGIN_METHODS = [{"name": "login", "params": {"login": "str", "password": "str"}, "return_type": "str", "rpc": None}]
CRUD_METHODS = [{"name": "get_one", "params": {"item_id": "str", "id": "str"}, "return_type": "dict", "rpc": None}]


class CapabilitiesOpener:
    """answers __remote_capabilities__ with the descriptors configured for the "host:port" of the URL"""

    def __init__(self, descriptors_by_address):
        self.descriptors_by_address = descriptors_by_address
        self.urls = []

    def __call__(self, request, timeout=None):
        self.urls.append(request.full_url)
        for address, descriptors in self.descriptors_by_address.items():
            if f"://{address}/" in request.full_url:
                if isinstance(descriptors, BaseException):
                    raise descriptors
                data = {"services": []}
                if descriptors:
                    data["descriptors"] = descriptors
                return FakeResponse({"status": 200, "meta": {}, "data": data})
        raise urllib.error.URLError("unreachable")


class TestServiceCatalog(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.manager, directory = create_manager()
        self.addCleanup(shutil.rmtree, directory, True)
        await self._add_peer("peer-a", "node-a", 9001)
        await self._add_peer("peer-b", "node-b", 9002)
        self.opener = CapabilitiesOpener({
            "node-a:9001": [{"specification": LOGIN, "methods": LOGIN_METHODS}],
            "node-b:9002": [
                {"specification": LOGIN, "methods": LOGIN_METHODS},
                {"specification": CRUD, "methods": CRUD_METHODS},
            ],
        })
        self.local = []
        self.catalog = ServiceCatalog(self.manager, [StoredPeers(self.manager)], opener=self.opener, local_descriptors=lambda: self.local)

    async def _add_peer(self, peer_id, host, port):
        server = RemoteServer()
        server.id(peer_id)
        server.host(host)
        server.port(port)
        server.scheme("http")
        await self.manager.up_sert_model(server, subject=None)

    async def test_refresh_peer_stores_what_the_peer_exposes(self):
        specifications = await self.catalog.refresh_peer("peer-b")

        self.assertEqual(specifications, [LOGIN, CRUD])
        self.assertEqual(
            await self.catalog.locate(CRUD),
            [{"peer_id": "peer-b", "host": "node-b", "port": 9002, "scheme": "http", "methods": CRUD_METHODS}],
        )

    async def test_refresh_peer_replaces_what_that_peer_exposed_before(self):
        await self.catalog.refresh_peer("peer-b")
        self.opener.descriptors_by_address["node-b:9002"] = [{"specification": LOGIN, "methods": LOGIN_METHODS}]

        await self.catalog.refresh_peer("peer-b")

        self.assertEqual(await self.catalog.locate(CRUD), [])
        self.assertEqual([entry["peer_id"] for entry in await self.catalog.locate(LOGIN)], ["peer-b"])

    async def test_an_unknown_peer_is_not_found(self):
        with self.assertRaises(NotFound):
            await self.catalog.refresh_peer("nobody")

    async def test_refresh_all_keeps_what_an_unreachable_peer_exposed_last(self):
        await self.catalog.refresh_all()
        self.opener.descriptors_by_address["node-a:9001"] = urllib.error.URLError("down")

        with self.assertLogs("ycappuccino.remote.catalog", "WARNING") as logs:
            await self.catalog.refresh_all()
        self.assertTrue(all(record.exc_info is None for record in logs.records))

        self.assertEqual(sorted(entry["peer_id"] for entry in await self.catalog.locate(LOGIN)), ["peer-a", "peer-b"])

    async def test_locate_lists_this_instance_first(self):
        self.local = [{"specification": LOGIN, "methods": LOGIN_METHODS}]
        await self.catalog.refresh_peer("peer-a")

        located = await self.catalog.locate(LOGIN)

        self.assertEqual(
            located,
            [
                {"peer_id": "", "methods": LOGIN_METHODS},
                {"peer_id": "peer-a", "host": "node-a", "port": 9001, "scheme": "http", "methods": LOGIN_METHODS},
            ],
        )

    async def test_locate_skips_a_peer_no_longer_registered(self):
        await self.catalog.refresh_peer("peer-a")
        await self.manager.delete("remoteServer", "peer-a", subject=None)

        self.assertEqual(await self.catalog.locate(LOGIN), [])


if __name__ == "__main__":
    unittest.main()
