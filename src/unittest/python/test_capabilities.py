"""
RemoteCapabilities: an IExposedService listing the names of the IExposedService instances
published locally. See discovery.py / capabilities.py for how ServiceDirectory relies on this,
and the design doc addendum for the accepted information-disclosure tradeoff (secure=False).
"""

import unittest

from remote_fixtures import FakeExposedService

from ycappuccino.core.framework import ComponentDescription, Framework
from ycappuccino.remote.capabilities import CAPABILITIES_SERVICE_NAME, RemoteCapabilities


class _FakeComponent:
    __module__ = "somewhere"
    __qualname__ = "FakeComponent"


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

    async def test_omits_components_when_the_framework_reports_none(self):
        # No Framework().init() ever ran in this process (or the one that did already reset the
        # singleton on stop(), see Framework.stop()): list_components() is empty, so "components"
        # must not appear at all -- the pre-existing {"services": [...]} shape stays byte-for-byte
        # identical, see the module docstring's "strictly additive key" note.
        previous = Framework._singleton
        Framework._singleton = Framework()
        self.addCleanup(setattr, Framework, "_singleton", previous)

        capabilities = RemoteCapabilities([FakeExposedService("echo")])
        result = await capabilities.call("GET", [], {}, None, None)

        self.assertEqual(result.body, {"services": ["echo"]})

    async def test_adds_components_when_the_framework_has_native_components_installed(self):
        description = ComponentDescription(
            _FakeComponent, provides=["FakeComponent"], provides_qualified=["somewhere.FakeComponent"]
        )
        framework = Framework()
        framework._components = {"somewhere.FakeComponent": description}
        previous = Framework._singleton
        Framework._singleton = framework
        self.addCleanup(setattr, Framework, "_singleton", previous)

        capabilities = RemoteCapabilities([FakeExposedService("echo")])
        result = await capabilities.call("GET", [], {}, None, None)

        self.assertEqual(
            result.body,
            {
                "services": ["echo"],
                "components": [
                    {"module": "somewhere", "class": "FakeComponent", "provides": ["somewhere.FakeComponent"]}
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
