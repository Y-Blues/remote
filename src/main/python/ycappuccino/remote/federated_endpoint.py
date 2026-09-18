"""
FederatedServiceEndpoint: an IServiceEndpoint that tries local services first -- identical
behavior to endpoints_service.ServiceEndpoint, including its authorization semantics -- then, on a
local NotFound, asks ServiceDirectory (discovery.py) to locate a peer that exposes the named
service and forwards the call directly to that peer's own /api/services/<name>[...] route (NOT
through remote_call's <peer_id>/<target> addressing: the peer is already known, so there is no
reason to go through remote_call's own extra_path convention). If no peer has it either, it raises
NotFound exactly as a local-only ServiceEndpoint would for an unknown name.

The call site of a caller using FederatedServiceEndpoint (`endpoint.call(name, method, extra_path,
params, body, subject)`) is therefore indistinguishable from a purely local call: no peer id, no
"this is a remote call" marker, anywhere. See the 2026-09-16 addendum of the design doc and the
README section "Appel transparent".

A local service runs through endpoints_service's call_service (its call() override or its matching
@rpc_method), exactly as under ServiceEndpoint. Why this still duplicates ServiceEndpoint's
local-lookup/authorization logic instead of delegating to a ServiceEndpoint instance: iPOPO/Pelix constructor injection has a deterministic tie-break rule for multiple
providers of the same specification (highest service.ranking, then lowest service id / first
registered -- see pelix.internals.registry), but neither ServiceEndpoint nor
FederatedServiceEndpoint sets an explicit ranking, and more importantly http_server's ApiServlet
only ever calls services[0] of its live list[IServiceEndpoint] for EVERY /api/services/* request
(see http_server's servlet.py, _route_services). Loading both ycappuccino.endpoints_service and
ycappuccino.remote in the same instance therefore makes ONE of the two IServiceEndpoint providers
entirely dead for HTTP-routed calls, silently, based on incidental bundle_prefix scan order -- not
a documented, stable priority a design should rely on. FederatedServiceEndpoint must therefore be a
complete, independent implementation that an app chooses INSTEAD OF endpoints_service.ServiceEndpoint,
never alongside it. See README.md.
"""

import logging
from typing import Any, Callable

from ycappuccino.api.endpoints_service import CALL, IExposedService, IServiceEndpoint, ServiceResult
from ycappuccino.api.endpoints_storage import Forbidden, IAuthorization, NotAuthenticated, NotFound
from ycappuccino.endpoints_service.endpoint import call_service
from ycappuccino.remote._http import DEFAULT_TIMEOUT, call_peer
from ycappuccino.remote.discovery import ServiceDirectory
from ycappuccino.remote.peers import IPeers, find_peer

_logger = logging.getLogger(__name__)


class FederatedServiceEndpoint(IServiceEndpoint):

    def __init__(
        self,
        services: list[IExposedService],
        authorizations: list[IAuthorization],
        directory: ServiceDirectory,
        peers: list[IPeers],
        timeout: float = DEFAULT_TIMEOUT,
        opener: Callable | None = None,
    ) -> None:
        self._services = services
        self._authorizations = authorizations
        self._directory = directory
        self._peers = peers
        self._timeout = timeout
        self._opener = opener

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def call(
        self, name: str, method: str, extra_path: list, params: dict, body: Any, subject: dict | None
    ) -> ServiceResult:
        service = self._find_local(name)
        if service is not None:
            await self._check(service, subject)
            return await call_service(service, method, extra_path, params, body, subject)

        return await self._call_remote(name, method, extra_path, params, body, subject)

    def _find_local(self, name: str) -> IExposedService | None:
        for service in list(self._services):
            if service.name == name:
                return service
        return None

    async def _check(self, service: IExposedService, subject: dict | None) -> None:
        if not service.secure:
            return
        if subject is None:
            raise NotAuthenticated(f"call {service.name} requires a subject")
        authorizations = list(self._authorizations)
        if not authorizations:
            _logger.warning("no IAuthorization service: call on %s is refused", service.name)
            raise Forbidden(f"call {service.name} is not authorized")
        if not await authorizations[0].is_authorized(subject, CALL, service.name):
            raise Forbidden(f"call {service.name} is not authorized")

    async def _call_remote(
        self, name: str, method: str, extra_path: list, params: dict, body: Any, subject: dict | None
    ) -> ServiceResult:
        peer_id = await self._directory.locate(name)
        if peer_id is None:
            raise NotFound(f"unknown service {name}")

        document = await find_peer(self._peers, peer_id)
        if document is None:
            raise NotFound(f"unknown service {name}")

        return call_peer(
            document, name, method, extra_path, params, body,
            timeout=self._timeout, opener=self._opener, subject=subject,
        )
