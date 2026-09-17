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

(2026-09-16, second addendum) Widened, additively, with core's Framework.list_components(): the
response now also carries "components" (every locally installed NATIVE component and the fully
qualified path of every specification it provides -- see core/README.md), used by
component_directory.ComponentDirectory to discover ANY qualified specification, not just
IExposedService names, and to dynamically synthesize a matching local proxy for one it does not
have locally (see component_directory.py, dispatch.py, remote_proxy.py). "components" is omitted
entirely when there is nothing to report (no Framework initialized, or zero native components
installed) so the response shape callers already depend on ({"services": [...]}, see
test_capabilities.py) is completely unchanged in that case -- a strictly additive key, never a
replacement of the existing "services" list. This is a further, larger widening of what a peer's
capabilities probe discloses than the "service names only" tradeoff above: see dispatch.py's own
module docstring for the full security discussion (arbitrary method invocation on ANY published
specification, not just deliberately exposed IExposedServices).

(2026-09-17, spec section 11.3) A peer (subject carrying "peer") still gets everything. Any other
caller -- a browser, signed in or not -- only gets, in "components", the specifications having at
least one @rpc_method: the public surface it can actually call through __remote_dispatch__, enough
for ycappuccino.client to create its proxies before anyone signs in.

"descriptors" (additive, omitted when empty) gives, for every interface listed in "components", the
signatures of the methods this caller may invoke (signatures.describe_interface): every dispatchable
method for a peer, the @rpc_method ones for anyone else. ServiceCatalog (catalog.py) stores them.
"""

import inspect

from ycappuccino.api.decorators import get_rpc_methods, rpc_method
from ycappuccino.api.endpoints_service import IExposedService
from ycappuccino.core.component_factory import resolve_class
from ycappuccino.core.framework import Framework
from ycappuccino.remote.signatures import describe_interface

CAPABILITIES_SERVICE_NAME = "__remote_capabilities__"


class RemoteCapabilities(IExposedService):
    name = CAPABILITIES_SERVICE_NAME
    secure = False

    def __init__(self, services: list[IExposedService]) -> None:
        self._services = services

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    @rpc_method(method="GET", summary="list the services and components this instance exposes", secure=False)
    async def capabilities(self, subject: dict | None) -> dict:
        result = {"services": [service.name for service in list(self._services) if service.name]}
        public_only = subject is None or "peer" not in subject
        components = Framework.get_framework().list_components()
        if public_only:
            components = _public_only(components)
        if components:
            result["components"] = components
        descriptors = describe_components(components, public_only)
        if descriptors:
            result["descriptors"] = descriptors
        return result


def _public_only(components: list) -> list:
    public = []
    for component in components:
        provides = [path for path in component["provides"] if _is_public(path)]
        if provides:
            public.append({**component, "provides": provides})
    return public


def describe_components(components: list, public_only: bool) -> list:
    """the descriptors of the interfaces the components provide, each described once"""
    descriptors = []
    specifications = dict.fromkeys(path for component in components for path in component["provides"])
    for qualified_path in specifications:
        try:
            interface = resolve_class(qualified_path)
        except Exception:
            continue  # a Pelix marker such as "pelix.http.servlet", never a class
        if not inspect.isabstract(interface):
            continue  # a component's own concrete class: only its interfaces are described
        methods = describe_interface(interface, public_only)
        if methods:
            descriptors.append({"specification": qualified_path, "methods": methods})
    return descriptors


def _is_public(qualified_path: str) -> bool:
    try:
        return bool(get_rpc_methods(resolve_class(qualified_path)))
    except Exception:
        return False
