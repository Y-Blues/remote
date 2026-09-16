"""
RemoteCapabilities: an IExposedService listing the names of the IExposedService instances
published locally. See discovery.py / capabilities.py for how ServiceDirectory relies on this,
and the design doc addendum for the accepted information-disclosure tradeoff (secure=False).
"""

import unittest

from remote_fixtures import FakeExposedService

from ycappuccino.remote.capabilities import CAPABILITIES_SERVICE_NAME, RemoteCapabilities


class TestRemoteCapabilities(unittest.IsolatedAsyncioTestCase):

    async def test_lists_the_names_of_the_local_services(self):
        services = [FakeExposedService("echo"), FakeExposedService("secret", secure=True)]
        capabilities = RemoteCapabilities(services)

        result = await capabilities.call("GET", [], {}, None, None)

        self.assertEqual(result.body, {"services": ["echo", "secret"]})

    async def test_ignores_services_without_a_name(self):
        unnamed = FakeExposedService("")
        capabilities = RemoteCapabilities([unnamed])

        result = await capabilities.call("GET", [], {}, None, None)

        self.assertEqual(result.body, {"services": []})

    async def test_reflects_the_live_list(self):
        services = []
        capabilities = RemoteCapabilities(services)
        services.append(FakeExposedService("added_later"))

        result = await capabilities.call("GET", [], {}, None, None)

        self.assertEqual(result.body, {"services": ["added_later"]})

    async def test_is_named_and_unsecured(self):
        self.assertEqual(RemoteCapabilities.name, CAPABILITIES_SERVICE_NAME)
        self.assertFalse(RemoteCapabilities.secure)
        self.assertEqual(CAPABILITIES_SERVICE_NAME, "__remote_capabilities__")


if __name__ == "__main__":
    unittest.main()
