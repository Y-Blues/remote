"""
RemoteCapabilities: an IExposedService, published under the reserved name "__remote_capabilities__",
that lists the names of the IExposedService instances published locally by this instance. Any
instance that loads ycappuccino.remote in its bundle_prefix therefore automatically answers "what do
you expose?" to any peer that asks -- this is what lets ServiceDirectory (discovery.py) build its
service_name -> peer_id cache without a manually maintained service list on RemoteServer.

Security tradeoff (deliberate, not an oversight -- see the 2026-09-16 addendum of the design doc):
secure=False. Dynamic discovery is impossible otherwise: RemoteCall and FederatedServiceEndpoint
never forward a subject to a peer (see spec section 3, "trusted internal cluster, no auth
forwarding"), so a secure=True capabilities service could never be queried by another instance.
This means ANY caller able to reach this instance's HTTP port can list the NAMES of every
IExposedService it publishes locally (not call them -- the peer still applies its own
authorization/secure flag to each named service when the name is actually called -- and not see
any data). This is a real, minor information disclosure, accepted as part of remote's existing
"trusted internal cluster" scope. Do not put anything sensitive in a service's *name*.
"""

from ycappuccino.api.endpoints_service import IExposedService, ServiceResult

CAPABILITIES_SERVICE_NAME = "__remote_capabilities__"


class RemoteCapabilities(IExposedService):
    name = CAPABILITIES_SERVICE_NAME
    secure = False

    def __init__(self, services: list[IExposedService]):
        self._services = services

    async def start(self):
        pass

    async def stop(self):
        pass

    async def call(self, method, extra_path, params, body, subject):
        return ServiceResult(body={"services": [service.name for service in list(self._services) if service.name]})
