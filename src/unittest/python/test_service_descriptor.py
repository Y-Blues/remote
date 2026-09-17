import shutil
import unittest

from remote_fixtures import create_manager

from ycappuccino.remote.models.service_descriptor import ServiceDescriptor, descriptor_id

METHODS = [
    {"name": "login", "params": {"login": "str", "password": "str"}, "return_type": "str",
     "rpc": {"method": "POST", "path": "", "summary": "log in", "secure": False}},
]


class TestServiceDescriptor(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.manager, directory = create_manager()
        self.addCleanup(shutil.rmtree, directory, True)

    async def test_round_trip_through_the_manager(self):
        descriptor = ServiceDescriptor()
        descriptor.id(descriptor_id("eu-node-2", "ycappuccino.api.permissions.ILoginService"))
        descriptor.peer_id("eu-node-2")
        descriptor.specification("ycappuccino.api.permissions.ILoginService")
        descriptor.methods(METHODS)

        await self.manager.up_sert_model(descriptor)
        stored = (await self.manager.get_one(
            "serviceDescriptor", "eu-node-2:ycappuccino.api.permissions.ILoginService"
        )).get_storage_model()

        self.assertEqual(
            (stored["peer_id"], stored["specification"], stored["methods"]),
            ("eu-node-2", "ycappuccino.api.permissions.ILoginService", METHODS),
        )

    async def test_it_is_secured_in_its_own_collection(self):
        from ycappuccino.api.decorators import get_item

        item = get_item("serviceDescriptor")

        self.assertEqual((item["collection"], item["plural"]), ("service_descriptors", "service-descriptors"))
        self.assertTrue(item["secureRead"] and item["secureWrite"])


if __name__ == "__main__":
    unittest.main()
