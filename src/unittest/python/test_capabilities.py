"""
RemoteCapabilities: an IExposedService listing the names of the IExposedService instances
published locally. See discovery.py / capabilities.py for how ServiceDirectory relies on this,
and the design doc addendum for the accepted information-disclosure tradeoff (secure=False).
"""

import unittest

from remote_fixtures import FakeExposedService

from ycappuccino.api.decorators import get_rpc_methods
from ycappuccino.api.endpoints_storage import NotFound
from ycappuccino.core.framework import ComponentDescription, Framework
from ycappuccino.endpoints_service.endpoint import ServiceEndpoint
from ycappuccino.remote.capabilities import CAPABILITIES_SERVICE_NAME, RemoteCapabilities


class _FakeComponent:
    __module__ = "somewhere"
    __qualname__ = "FakeComponent"


class TestRemoteCapabilities(unittest.IsolatedAsyncioTestCase):

    async def test_lists_the_names_of_the_local_services(self):
        services = [FakeExposedService("echo"), FakeExposedService("secret", secure=True)]
        capabilities = RemoteCapabilities(services)

        result = await capabilities.capabilities(None)

        self.assertEqual(result, {"services": ["echo", "secret"]})

    async def test_ignores_services_without_a_name(self):
        unnamed = FakeExposedService("")
        capabilities = RemoteCapabilities([unnamed])

        result = await capabilities.capabilities(None)

        self.assertEqual(result, {"services": []})

    async def test_reflects_the_live_list(self):
        services = []
        capabilities = RemoteCapabilities(services)
        services.append(FakeExposedService("added_later"))

        result = await capabilities.capabilities(None)

        self.assertEqual(result, {"services": ["added_later"]})

    async def test_is_named_and_unsecured(self):
        self.assertEqual(RemoteCapabilities.name, CAPABILITIES_SERVICE_NAME)
        self.assertFalse(RemoteCapabilities.secure)
        self.assertEqual(CAPABILITIES_SERVICE_NAME, "__remote_capabilities__")

    async def test_it_answers_a_public_get(self):
        metadata = get_rpc_methods(RemoteCapabilities)["capabilities"]

        self.assertEqual((metadata["method"], metadata["path"], metadata["secure"]), ("GET", "", False))

    async def test_get_is_routed_by_the_service_endpoint_and_anything_else_is_not_found(self):
        previous = Framework._singleton
        Framework._singleton = Framework()
        self.addCleanup(setattr, Framework, "_singleton", previous)
        endpoint = ServiceEndpoint([RemoteCapabilities([FakeExposedService("echo")])], [])

        result = await endpoint.call(CAPABILITIES_SERVICE_NAME, "GET", [], {}, None, None)

        self.assertEqual(result.body, {"services": ["echo"]})
        with self.assertRaises(NotFound):
            await endpoint.call(CAPABILITIES_SERVICE_NAME, "POST", [], {}, None, None)

    async def test_omits_components_when_the_framework_reports_none(self):
        # No Framework().init() ever ran in this process (or the one that did already reset the
        # singleton on stop(), see Framework.stop()): list_components() is empty, so "components"
        # must not appear at all -- the pre-existing {"services": [...]} shape stays byte-for-byte
        # identical, see the module docstring's "strictly additive key" note.
        previous = Framework._singleton
        Framework._singleton = Framework()
        self.addCleanup(setattr, Framework, "_singleton", previous)

        capabilities = RemoteCapabilities([FakeExposedService("echo")])
        result = await capabilities.capabilities(None)

        self.assertEqual(result, {"services": ["echo"]})

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
        result = await capabilities.capabilities({"peer": "backend-1"})

        self.assertEqual(
            result,
            {
                "services": ["echo"],
                "components": [
                    {"module": "somewhere", "class": "FakeComponent", "provides": ["somewhere.FakeComponent"]}
                ],
            },
        )


    async def test_a_caller_other_than_a_peer_only_sees_the_public_interfaces(self):
        description = ComponentDescription(
            _FakeComponent,
            provides=["FakeComponent", "ILoginService", "IManager"],
            provides_qualified=[
                "somewhere.FakeComponent",
                "ycappuccino.api.permissions.ILoginService",
                "ycappuccino.api.storage.IManager",
            ],
        )
        framework = Framework()
        framework._components = {"somewhere.FakeComponent": description}
        previous = Framework._singleton
        Framework._singleton = framework
        self.addCleanup(setattr, Framework, "_singleton", previous)
        capabilities = RemoteCapabilities([])

        for subject in (None, {"sub": "alice", "tid": "acme"}):
            with self.subTest(subject=subject):
                result = await capabilities.capabilities(subject)

                self.assertEqual(
                    result["components"],
                    [{"module": "somewhere", "class": "FakeComponent",
                      "provides": ["ycappuccino.api.permissions.ILoginService"]}],
                )

    async def test_a_caller_other_than_a_peer_sees_no_component_without_public_interface(self):
        description = ComponentDescription(
            _FakeComponent, provides=["IManager"], provides_qualified=["ycappuccino.api.storage.IManager"]
        )
        framework = Framework()
        framework._components = {"somewhere.FakeComponent": description}
        previous = Framework._singleton
        Framework._singleton = framework
        self.addCleanup(setattr, Framework, "_singleton", previous)

        result = await RemoteCapabilities([]).capabilities(None)

        self.assertEqual(result, {"services": []})


if __name__ == "__main__":
    unittest.main()
