"""
RemoteDispatch: the peer-side counterpart of remote_proxy.make_generic_proxy -- an IExposedService,
published under the reserved name "__remote_dispatch__", that receives a generic RPC call for ANY
locally-published specification and dispatches it to the REAL local instance providing it. This is
what makes the fully generic (zero hardcoded interface list) side of the 2026-09-16 dynamic-proxy
addendum possible: remote_call/FederatedServiceEndpoint only ever forward to a NAMED
IExposedService, which does not exist for an arbitrary app-defined interface -- there is no route to
reuse, so a new receiving mechanism is required (see design doc, addendum part B).

Wire shape:

    POST /api/services/__remote_dispatch__/<qualified path>/<method name>
    body: {"args": [...], "kwargs": {...}}         (either or both may be omitted, default [] / {})

    -> {"status": 200, "meta": {}, "data": {"result": <json value>}}

<qualified path> is the fully-qualified "module.ClassName" of the specification being called --
exactly what Framework.list_components() / RemoteCapabilities report, and what
ycappuccino.core.component_factory.resolve_class resolves back to the real interface class;
<method name> is the plain method name to call on it. The target is looked up as whatever local
Pelix service currently provides that specification's short (class) name -- the real, live
component instance load_bundles()/instantiate_component() already installed, found via the running
Framework's own BundleContext (context.get_service_reference/get_service), never a second registry
of remote's own. The method is invoked with the given args/kwargs and awaited if it returns a
coroutine, exactly like calling it locally would.

Supported argument/return shapes: JSON-serializable only (str/int/float/bool/None/list/dict), the
same constraint the rest of remote's envelope already has -- a non-JSON-serializable return value
fails at the usual http_server JSON-encoding step, not here; a method needing positional-only
parameters or *args/**kwargs works only through the "args" list, never through "kwargs". Async
generators, byte payloads, and any other non-JSON shape are an accepted, out-of-scope limitation,
not handled.

Security -- read before deploying, this is a MEANINGFUL widening of what becomes remotely
triggerable, not a cosmetic detail. Until this addendum, an unauthenticated network caller (remote
never forwards a subject to a peer, see spec section 3) could only ever reach an IExposedService its
own author had *deliberately* published under a chosen name -- and could still keep it unreachable
from a peer entirely by leaving it secure=True (a secure=True IExposedService called through
RemoteCall/FederatedServiceEndpoint just becomes NotAuthenticated/Forbidden, since no subject is
ever sent). __remote_dispatch__ has no such author-chosen boundary: ANY method of ANY specification
describe_component() ever saw locally -- IManager, ITrigger, IAuthorization, an application's own
internal interfaces never meant to be called over a network -- becomes callable this way, by
whoever can reach this instance's HTTP port, with no authorization check of its own beyond
excluding lifecycle (start/stop) and private (leading "_") method names. secure=False for the exact
same structural reason __remote_capabilities__ is (capabilities.py): a secure=True dispatcher could
never be reached by a peer that never authenticates either. This is the direct, accepted consequence
of the user's explicit choice of a FULLY GENERIC mechanism (any interface, zero hardcoded list) over
a smaller, curated one -- not an oversight, and not something this module tries to soften. Never
load ycappuccino.remote in an instance reachable outside a trusted internal network -- true before
this addendum, considerably more consequential after it.
"""

import inspect
import logging
from typing import Any, Callable

from ycappuccino.api.endpoints_service import IExposedService, ServiceResult
from ycappuccino.api.endpoints_storage import InvalidRequest, NotFound
from ycappuccino.core.component_factory import resolve_class
from ycappuccino.core.framework import Framework

_logger = logging.getLogger(__name__)

DISPATCH_SERVICE_NAME = "__remote_dispatch__"

# lifecycle / private methods are never dispatchable, whatever the interface -- a structural
# exclusion, not a per-interface allowlist (that would contradict "zero hardcoded interface list")
_NEVER_DISPATCHABLE = {"start", "stop"}


def _default_locate_service(specification_name: str) -> tuple:
    """(service, reference) for the local Pelix service currently providing this short
    specification name, or (None, None) if there is none -- the framework must be started."""
    context = Framework.get_framework().context
    if context is None:
        return None, None
    reference = context.get_service_reference(specification_name)
    if reference is None:
        return None, None
    return context.get_service(reference), reference


def _accepts_subject(target: Callable) -> bool:
    try:
        return "subject" in inspect.signature(target).parameters
    except (TypeError, ValueError):
        return False


def _default_release_service(reference: Any) -> None:
    if reference is None:
        return
    context = Framework.get_framework().context
    if context is not None:
        context.unget_service(reference)


class RemoteDispatch(IExposedService):
    name = DISPATCH_SERVICE_NAME
    secure = False

    def __init__(
        self,
        resolve: Callable | None = None,
        locate_service: Callable | None = None,
        release_service: Callable | None = None,
    ) -> None:
        # resolve/locate_service/release_service are injectable exactly like RemoteCall's/
        # ServiceDirectory's "opener": production defaults to the real resolve_class/Pelix
        # context, but a unit test can fake all three without a running Framework or any socket.
        self._resolve = resolve if resolve is not None else resolve_class
        self._locate_service = locate_service if locate_service is not None else _default_locate_service
        self._release_service = release_service if release_service is not None else _default_release_service

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def call(
        self, method: str, extra_path: list, params: dict, body: Any, subject: dict | None
    ) -> ServiceResult:
        if len(extra_path) != 2:
            raise InvalidRequest(f"{DISPATCH_SERVICE_NAME} expects /<qualified path>/<method name>")
        qualified_path, method_name = extra_path

        try:
            interface = self._resolve(qualified_path)
        except Exception:
            _logger.warning("%s: cannot resolve %r", DISPATCH_SERVICE_NAME, qualified_path, exc_info=True)
            raise NotFound(f"unknown specification {qualified_path!r}") from None

        service, reference = self._locate_service(interface.__name__)
        if service is None:
            raise NotFound(f"no local instance currently provides {qualified_path!r}")
        try:
            return await self._invoke(service, qualified_path, method_name, body, subject)
        finally:
            self._release_service(reference)

    async def _invoke(
        self, service: Any, qualified_path: str, method_name: str, body: Any, subject: dict | None
    ) -> ServiceResult:
        if method_name in _NEVER_DISPATCHABLE or method_name.startswith("_"):
            raise NotFound(f"{qualified_path!r} has no callable method {method_name!r}")
        target = getattr(service, method_name, None)
        if target is None or not callable(target):
            raise NotFound(f"{qualified_path!r} has no callable method {method_name!r}")

        payload = body or {}
        args = payload.get("args") or []
        kwargs = dict(payload.get("kwargs") or {})
        # the subject is the one http_server decoded from this request's own credentials, never one
        # the caller put in its payload
        kwargs.pop("subject", None)
        if _accepts_subject(target):
            kwargs["subject"] = subject
        result = target(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return ServiceResult(body={"result": result})
