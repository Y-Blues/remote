"""
RemoteCall: an IExposedService that forwards a call to a named IExposedService on another
YCappuccino instance (a RemoteServer registered peer), over plain HTTP.

See spec (2026-09-15-remote-design.md) for the design decisions: addressing via extra_path
(peer id, then target service, then the target's own extra_path), no forwarded authentication
in this first version (target services on the peer must be secure=False), network errors
propagate unwrapped (no retry). The actual HTTP call and status translation are shared with
ServiceDirectory and FederatedServiceEndpoint, see _http.py (2026-09-16 addendum).
"""

from ycappuccino.api.endpoints_service import IExposedService
from ycappuccino.api.endpoints_storage import InvalidRequest, NotFound
from ycappuccino.api.storage import IManager
from ycappuccino.remote._http import DEFAULT_TIMEOUT, REMOTE_SERVER_ITEM_ID, call_peer


class RemoteCall(IExposedService):
    name = "remote_call"
    secure = True

    def __init__(self, manager: IManager, timeout: float = DEFAULT_TIMEOUT, opener=None):
        self._manager = manager
        self._timeout = timeout
        self._opener = opener

    async def start(self):
        pass

    async def stop(self):
        pass

    async def call(self, method, extra_path, params, body, subject):
        if len(extra_path) < 2:
            raise InvalidRequest("remote_call expects /<peer id>/<service name>[/<extra path>...]")
        peer_id, target_service, *target_extra = extra_path

        peer = await self._manager.get_one(REMOTE_SERVER_ITEM_ID, peer_id, subject=None)
        if peer is None:
            raise NotFound(f"unknown remote server {peer_id!r}")

        document = peer.get_storage_model()
        return call_peer(
            document, target_service, method, target_extra, params, body,
            timeout=self._timeout, opener=self._opener,
        )
