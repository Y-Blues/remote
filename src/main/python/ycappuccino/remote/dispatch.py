"""
RemoteDispatch: the receiving side of every generated proxy (remote_proxy.make_generic_proxy on a
backend, ycappuccino.client's proxies in a browser) -- an IExposedService, published under the
reserved name "__remote_dispatch__", that receives a JSON-RPC call for any locally-published
specification and runs it on the real local instance providing it.

Wire shape:

    POST /api/services/__remote_dispatch__/<qualified path>/<method name>
    body: {"args": [...], "kwargs": {...}}         (either or both may be omitted, default [] / {})

    -> {"status": 200, "meta": {}, "data": {"result": <json value>}}

<qualified path> is the "module.ClassName" of the specification, as Framework.list_components() /
RemoteCapabilities report it; the target is the local Pelix service providing that specification's
short name. Arguments and results are JSON only; a dataclass result travels as its JSON object (a
proxy rebuilds it from the method's return annotation). Lifecycle (start/stop), binding (bind/un_bind) and
private ("_") methods are never dispatchable.

Access (spec 2026-09-16-transparent-rpc-design.md, section 11.3) depends on the subject http_server
authenticated for the request:

- a peer (subject carries "peer", only PeerHmacAuthentication grants it): any dispatchable method;
- anyone else, a signed-in user or anonymous: only the methods marked @rpc_method on the resolved
  interface (not found otherwise). A secure one needs a subject authorized, by the first
  IAuthorization, to "call" "<qualified path>.<method name>"; an unsecured one checks its caller
  itself (Crud through Access, ServiceEndpoint through each service's own secure flag, login).

The target method receives that authenticated subject as its `subject` parameter when it declares
one; a subject put in the payload is ignored. The service itself stays secure=False: an anonymous
browser must be able to reach a public method such as login.
"""

import dataclasses
import inspect
import logging
from typing import Any, Callable

from ycappuccino.api.decorators import get_rpc_methods
from ycappuccino.api.endpoints_service import CALL, IExposedService, ServiceResult
from ycappuccino.api.endpoints_storage import Forbidden, IAuthorization, InvalidRequest, NotAuthenticated, NotFound
from ycappuccino.api.proxy import Proxy
from ycappuccino.core.component_factory import resolve_class
from ycappuccino.core.framework import Framework
from ycappuccino.remote.signatures import is_dispatchable

_logger = logging.getLogger(__name__)

DISPATCH_SERVICE_NAME = "__remote_dispatch__"


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


def _default_release_service(reference: Any) -> None:
    if reference is None:
        return
    context = Framework.get_framework().context
    if context is not None:
        context.unget_service(reference)


def _accepts_subject(target: Callable) -> bool:
    try:
        return "subject" in inspect.signature(target).parameters
    except (TypeError, ValueError):
        return False


def is_peer(subject: dict | None) -> bool:
    return subject is not None and "peer" in subject


class RemoteDispatch(IExposedService):
    name = DISPATCH_SERVICE_NAME
    secure = False

    def __init__(
        self,
        authorizations: list[IAuthorization] = (),
        resolve: Callable | None = None,
        locate_service: Callable | None = None,
        release_service: Callable | None = None,
    ) -> None:
        # resolve/locate_service/release_service default to the real resolve_class/Pelix context; a
        # unit test fakes them without a running Framework
        self._authorizations = authorizations
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
        if not is_dispatchable(method_name):
            raise NotFound(f"{qualified_path!r} has no callable method {method_name!r}")

        try:
            interface = self._resolve(qualified_path)
        except Exception:
            _logger.warning("%s: cannot resolve %r", DISPATCH_SERVICE_NAME, qualified_path, exc_info=True)
            raise NotFound(f"unknown specification {qualified_path!r}") from None

        if not is_peer(subject):
            await self._check_public(interface, qualified_path, method_name, subject)

        service, reference = self._locate_service(interface.__name__)
        if service is None:
            raise NotFound(f"no local instance currently provides {qualified_path!r}")
        try:
            return await self._invoke(service, qualified_path, method_name, body, subject)
        finally:
            self._release_service(reference)

    async def _check_public(
        self, interface: type, qualified_path: str, method_name: str, subject: dict | None
    ) -> None:
        metadata = get_rpc_methods(interface).get(method_name)
        if metadata is None:
            raise NotFound(f"{qualified_path!r} has no callable method {method_name!r}")
        if not metadata["secure"]:
            return
        resource = f"{qualified_path}.{method_name}"
        if subject is None:
            raise NotAuthenticated(f"call {resource} requires a subject")
        authorizations = list(self._authorizations)
        if not authorizations:
            _logger.warning("no IAuthorization service: call on %s is refused", resource)
            raise Forbidden(f"call {resource} is not authorized")
        if not await authorizations[0].is_authorized(subject, CALL, resource):
            raise Forbidden(f"call {resource} is not authorized")

    async def _invoke(
        self, service: Any, qualified_path: str, method_name: str, body: Any, subject: dict | None
    ) -> ServiceResult:
        if isinstance(service, Proxy):
            # the registered service is iPOPO's proxy: call the component itself, whose signature says
            # whether it takes a subject
            service = object.__getattribute__(service, "_obj")
        target = getattr(service, method_name, None)
        if target is None or not callable(target):
            raise NotFound(f"{qualified_path!r} has no callable method {method_name!r}")

        payload = body or {}
        args = payload.get("args") or []
        kwargs = dict(payload.get("kwargs") or {})
        kwargs.pop("subject", None)
        if _accepts_subject(target):
            kwargs["subject"] = subject
        result = target(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if dataclasses.is_dataclass(result) and not isinstance(result, type):
            result = dataclasses.asdict(result)
        return ServiceResult(body={"result": result})
