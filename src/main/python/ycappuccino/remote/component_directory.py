"""
ComponentDirectory: best-effort discovery of which RemoteServer peer provides which qualified
specification (any "module.ClassName" path, not just IExposedService names -- see
discovery.ServiceDirectory for that narrower, older mechanism, kept separate on purpose, see
"Why a separate component" below), via each peer's widened __remote_capabilities__ ("components",
capabilities.py). For every discovered qualified path NOT already available locally, it
synthesizes a dynamic proxy (remote_proxy.make_generic_proxy) and installs it as a real, running
native component (Framework.instantiate_component) -- "creating a remote on the fly", the
2026-09-16 addendum's core ask.

Why a separate component from ServiceDirectory, not a merge: ServiceDirectory answers a narrower
question ("which peer serves IExposedService X") for FederatedServiceEndpoint's own call-time
fallback -- it never creates anything, just returns a peer id to forward a call to directly.
ComponentDirectory answers a different one ("what specifications exist anywhere, and do I need a
local stand-in for one I don't have") and has a real side effect (installing a component). Folding
proxy-spawning into ServiceDirectory would make a single class respond to two independent
"customers" (FederatedServiceEndpoint's synchronous per-call lookup vs. reactive proxy
installation) for two different discovery mechanisms (service names only vs. every qualified
specification) -- kept apart, like RemoteCall and RemoteDispatch, or like ServiceDirectory and
FederatedServiceEndpoint already are.

Discovery mechanism, mirroring ServiceDirectory's own discipline: every currently registered
RemoteServer is queried, best effort (an unreachable peer is logged and skipped, never raises). A
qualified path already seen is never overwritten by a later discovery (first peer discovered
wins, same "avoid duplicate specifications across peers" caveat as ServiceDirectory's own).
`locate(qualified_path)` reads the cache first, and re-queries every peer live on a miss --
byte-for-byte the same contract as ServiceDirectory.locate(), for the same staleness tradeoff
reasons (see discovery.py's own docstring).

WHEN discovery (and therefore proxy creation) actually runs -- this is the part ServiceDirectory
does not need to solve, since its own consumer (FederatedServiceEndpoint) re-triggers it lazily on
every call: ComponentDirectory is also an ITrigger on "remoteServer" upserts (item_id=
REMOTE_SERVER_ITEM_ID). Registering a new peer (`manager.up_sert_model(RemoteServer(...))`) is
therefore what proactively drives (re)discovery and proxy creation -- there is no other event that
would, since nothing here is called on every business call the way ServiceDirectory.locate() is.
core's own README states plainly that reacting to an event (an ITrigger, a service bound later) is
NOT the dangerous case its "Piège de timing" warns about -- only a SYNCHRONOUS
instantiate_component() call from a component's OWN start()/stop() can deadlock (see core/README.md,
"Installer un composant à l'exécution") -- so execute() calls instantiate_component() directly,
synchronously, no thread needed. start() itself, however, IS that dangerous case for whatever
RemoteServer rows are ALREADY registered when this component boots (mirroring
ycappuccino-component-creator's ComponentActivator.start() exactly, see its module docstring): it
hands the whole "discover what's already there, then spawn any missing proxies" job to a detached
background thread and returns immediately, never blocking on the result.

THE TIMING HAZARD, STATED HONESTLY (see core/README.md's "Piège de timing" and the design doc
addendum for the full discussion): because start()'s own proxy creation is fire-and-forget on a
background thread, load_bundles()'s single bundle_prefix scan pass provides NO guarantee that a
dynamically-created proxy exists by the time another native component scanned in that SAME pass
gets constructed. A consumer that wants one of these interfaces as a plain REQUIRED (non-optional,
non-list) constructor dependency will NOT reliably get it. The operational contract this addendum
lands on (deliberately option (b) of the two named in the design doc, not (a)): a consumer of a
dynamically-federated interface MUST declare it as an OPTIONAL or AGGREGATE (list[...]) dependency
and react to it arriving via bind() -- exactly the pattern core's own dependency injection already
supports for any service appearing after validation, proven for real in
test_component_directory_framework.py (a genuine two-process, two-timing-window test: the consumer
is validated with an EMPTY list, the proxy is created afterwards on a peer registered afterwards
too, and the consumer's list is only non-empty once iPOPO's own bind() fires). A component that
truly needs synchronous, guaranteed-present access to a dynamically-discovered interface must
instead be created dynamically ITSELF, sequenced after `locate()`/discovery has resolved that
specification -- option (a) -- which this addendum does not build a generic orchestration for
(out of scope, see design doc addendum part D).
"""

import logging
import threading
from typing import Optional

from ycappuccino.api.storage import IManager, ITrigger
from ycappuccino.core.component_factory import resolve_class
from ycappuccino.core.framework import Framework
from ycappuccino.remote._http import DEFAULT_TIMEOUT, REMOTE_SERVER_ITEM_ID, call_peer
from ycappuccino.remote.capabilities import CAPABILITIES_SERVICE_NAME
from ycappuccino.remote.remote_proxy import make_generic_proxy

_logger = logging.getLogger(__name__)


def _default_instantiate(component, properties):
    return Framework.get_framework().instantiate_component(component, properties)


def _default_local_specifications() -> set:
    provided: set = set()
    for description in Framework.get_framework().list_components():
        provided.update(description.get("provides", []))
    return provided


class ComponentDirectory(ITrigger):

    item_id = REMOTE_SERVER_ITEM_ID
    actions = ("upsert",)
    post = True

    def __init__(
        self,
        manager: IManager,
        timeout: float = DEFAULT_TIMEOUT,
        opener=None,
        instantiate=None,
        local_specifications=None,
    ):
        # instantiate/local_specifications are injectable exactly like opener: production
        # defaults to the real Framework, a unit test fakes both without any real Pelix instance.
        self._manager = manager
        self._timeout = timeout
        self._opener = opener
        self._instantiate = instantiate if instantiate is not None else _default_instantiate
        self._local_specifications = (
            local_specifications if local_specifications is not None else _default_local_specifications
        )
        self._cache: dict = {}  # qualified_path -> (peer_id, document)
        self._created: set = set()  # qualified_path already given a local proxy
        self._lock = threading.Lock()

    async def start(self):
        # see module docstring: this must not block on instantiate_component() calls made while
        # this very component is still being validated -- background thread, fire-and-forget,
        # exactly like ComponentActivator.start().
        threading.Thread(target=self._bootstrap, name="ComponentDirectory-bootstrap", daemon=True).start()

    def _bootstrap(self):
        import asyncio

        try:
            asyncio.run(self._discover_all())
        except Exception:
            _logger.exception("ComponentDirectory: initial discovery failed")

    async def stop(self):
        pass

    async def execute(self, action: str, item_id: str, model) -> None:
        # reacting to a RemoteServer upsert is NOT a nested call from this component's own
        # start()/stop() -- core's README says plainly this case is safe, see module docstring.
        await self._discover_all()

    async def locate(self, qualified_path: str) -> Optional[str]:
        if qualified_path in self._cache:
            return self._cache[qualified_path][0]
        await self._discover_all()
        entry = self._cache.get(qualified_path)
        return entry[0] if entry else None

    async def _discover_all(self):
        peers = await self._manager.get_many(REMOTE_SERVER_ITEM_ID, subject=None)
        for peer in peers:
            self._discover_peer(peer.get_storage_model())
        self._spawn_missing_proxies()

    def _discover_peer(self, document):
        peer_id = document.get("_id")
        try:
            result = call_peer(
                document, CAPABILITIES_SERVICE_NAME, "GET", [], {}, None,
                timeout=self._timeout, opener=self._opener,
            )
        except Exception:
            _logger.warning("could not query capabilities of remote server %r", peer_id, exc_info=True)
            return
        components = result.body.get("components", []) if isinstance(result.body, dict) else []
        for component in components:
            for qualified_path in component.get("provides", []):
                self._cache.setdefault(qualified_path, (peer_id, document))

    def _spawn_missing_proxies(self):
        local = self._local_specifications()
        with self._lock:
            pending = [
                (qualified_path, peer_id, document)
                for qualified_path, (peer_id, document) in self._cache.items()
                if qualified_path not in self._created and qualified_path not in local
            ]
            self._created.update(qualified_path for qualified_path, _, _ in pending)
        for qualified_path, peer_id, document in pending:
            self._spawn_proxy(qualified_path, peer_id, document)

    def _spawn_proxy(self, qualified_path, peer_id, document):
        try:
            interface = resolve_class(qualified_path)
        except Exception:
            _logger.warning(
                "ComponentDirectory: cannot resolve %r, no proxy created", qualified_path, exc_info=True
            )
            return
        try:
            proxy_class = make_generic_proxy(interface, qualified_path)
            self._instantiate(
                proxy_class,
                {
                    "peer_host": document["host"],
                    "peer_port": document["port"],
                    "peer_scheme": document["scheme"],
                    "timeout": self._timeout,
                    "opener": self._opener,
                },
            )
        except Exception:
            _logger.warning(
                "ComponentDirectory: failed to create a dynamic proxy for %r (peer %r)",
                qualified_path, peer_id, exc_info=True,
            )
