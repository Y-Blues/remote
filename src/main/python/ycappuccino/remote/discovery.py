"""
ServiceDirectory: best-effort, cache-based discovery of which RemoteServer peer exposes which
IExposedService, via each peer's __remote_capabilities__ (capabilities.py). Used by
FederatedServiceEndpoint to resolve a service name it does not have locally, without any
peer/service knowledge written into the call site (see the 2026-09-16 addendum of the design doc).

Discovery mechanism: at start(), every currently registered RemoteServer is queried, best effort
(an unreachable peer is logged and skipped, never fails start()). Every "services" name reported
back is cached as service_name -> peer_id. A cache hit in locate() is free (no HTTP call). A cache
miss re-queries every currently registered peer live (not only the ones that failed earlier), since
any peer could have started exposing the target service since the last successful query, then
answers from whatever the cache now holds.

Staleness (accepted limitation, not a bug): the cache reflects only what discovery has seen so far.
Between two discovery events (start(), or the last locate() that triggered a re-query), a peer that
starts exposing a new service, stops exposing one, or changes host/port, is invisible to
already-cached lookups: a service already cached under peer A stays mapped to peer A even if A no
longer serves it -- discovered only when the actual forwarded call fails, not by ServiceDirectory
itself. There is no push invalidation, no heartbeat, no TTL. This is a deliberate simplicity
tradeoff, consistent with remote's original "no discovery/heartbeat/failover" scope for RemoteServer
itself (spec section "Hors périmètre") now extended, not contradicted, by this addendum: discovery
here is pull-based and best-effort, never authoritative.

If two peers both report the same service name, the first one discovered wins (dict insertion
order of get_many(), not remote-configurable) -- an edge case callers should avoid by not exposing
the same service name on two peers queried by the same instance.
"""

import logging
from typing import Callable, Optional

from ycappuccino.api.core_base import YCappuccinoComponent
from ycappuccino.remote._http import DEFAULT_TIMEOUT, call_peer
from ycappuccino.remote.capabilities import CAPABILITIES_SERVICE_NAME
from ycappuccino.remote.peers import IPeers, all_peers, has_address

_logger = logging.getLogger(__name__)


class ServiceDirectory(YCappuccinoComponent):

    def __init__(self, peers: list[IPeers], timeout: float = DEFAULT_TIMEOUT, opener: Callable | None = None) -> None:
        self._peers = peers
        self._timeout = timeout
        self._opener = opener
        self._cache: dict[str, str] = {}

    async def start(self) -> None:
        await self._discover_all()

    async def stop(self) -> None:
        pass

    async def locate(self, service_name: str) -> Optional[str]:
        if service_name in self._cache:
            return self._cache[service_name]
        await self._discover_all()
        return self._cache.get(service_name)

    async def _discover_all(self) -> None:
        for peer in await all_peers(self._peers):
            if has_address(peer):
                self._discover_peer(peer)

    def _discover_peer(self, document: dict) -> None:
        peer_id = document.get("_id")
        try:
            result = call_peer(
                document, CAPABILITIES_SERVICE_NAME, "GET", [], {}, None,
                timeout=self._timeout, opener=self._opener,
            )
        except Exception:
            _logger.warning("could not query capabilities of remote server %r", peer_id, exc_info=True)
            return
        services = result.body.get("services", []) if isinstance(result.body, dict) else []
        for service_name in services:
            self._cache.setdefault(service_name, peer_id)
