"""
make_generic_proxy: reflection-based synthesis of a concrete YCappuccinoComponent subclass
implementing an ARBITRARY interface, forwarding every one of its abstract (business) methods to
__remote_dispatch__ (dispatch.py) on a peer, as a generic (method name, kwargs) call. Used by
component_directory.ComponentDirectory to create, on the fly, a local stand-in for a specification
discovered on a peer but not available locally -- see the design doc, addendum part C.

Same technique as ycappuccino.client.rpc_proxy.make_rpc_proxy (the browser's proxies), duplicated on
purpose since neither repo depends on the other: a real __init__ and real method signatures are forged
with exec, so core's describe_component introspects them like hand-written code. Every call is a
JSON-RPC call -- the method's own parameters become a "kwargs" object POSTed to __remote_dispatch__
(dispatch.py, the receiving side). Here the proxy targets a peer (host/port/secret), signs its calls,
and forwards the `subject` it is given; the browser's proxy targets its own backend with the user's
token and never sends a subject.

Supported argument/return shapes: JSON-serializable only (str/int/float/bool/None/list/dict), same
constraint as dispatch.py's own docstring documents on the receiving side. A method whose signature
takes *args/**kwargs, or whose default values are not JSON-serializable, or whose return value isn't
either, is an accepted, out-of-scope limitation -- not handled here.
"""

import dataclasses
import inspect
import typing
from typing import Any, Callable

from ycappuccino.remote._http import DEFAULT_TIMEOUT, call_peer
from ycappuccino.remote.dispatch import DISPATCH_SERVICE_NAME


def make_generic_proxy(interface: type, qualified_path: str) -> type:
    """
    Synthesize a concrete implementation of `interface` (an abstract YCappuccino interface --
    a YCappuccinoComponent subclass, api.endpoints_service.IExposedService or any future
    application-defined one), forwarding every abstract business method (everything but
    start/stop) to __remote_dispatch__ on a peer identified by `qualified_path` (the interface's
    own fully-qualified "module.ClassName" path, as reported by Framework.list_components() /
    RemoteCapabilities). The synthesized class's constructor takes the peer's address
    (peer_host/peer_port/peer_scheme) plus timeout/opener as ordinary component properties (the
    same "unannotated parameter with a default" convention RemoteCall/ServiceDirectory already
    use for `opener`) -- meant to be created with
    Framework.instantiate_component(proxy_class, properties={...}), see component_directory.py.
    """
    method_names = _abstract_business_methods(interface)

    namespace: dict = {
        "__module__": interface.__module__,
        "_ycappuccino_qualified_path": qualified_path,
        "__init__": _build_init(),
    }
    return_types = {}
    for name in method_names:
        method = getattr(interface, name)
        namespace[name] = _build_method(name, inspect.signature(method))
        return_types[name] = _dataclass_return_type(method)
    namespace["_ycappuccino_return_types"] = return_types

    class_name = "Remote" + (interface.__name__[1:] if interface.__name__.startswith("I") else interface.__name__)
    klass = type(class_name, (_GenericRemoteProxyBase, interface), namespace)
    klass.__qualname__ = class_name
    return klass


def _abstract_business_methods(interface: type) -> list:
    """the interface's own abstract methods, alphabetically (deterministic), excluding the
    YCappuccinoComponent lifecycle methods (start/stop), which _GenericRemoteProxyBase implements"""
    return sorted(
        name
        for name, member in inspect.getmembers(interface)
        if getattr(member, "__isabstractmethod__", False) and name not in ("start", "stop")
    )


def _dataclass_return_type(method: Callable) -> type | None:
    try:
        return_type = typing.get_type_hints(method).get("return")
    except Exception:
        return None
    return return_type if isinstance(return_type, type) and dataclasses.is_dataclass(return_type) else None


def _build_init() -> Callable:
    """forges a REAL, introspectable __init__ (real parameter names/annotations, not a **kwargs
    sink) via exec, so describe_component's constructor introspection (inspect.signature by name)
    treats peer_host/peer_port/peer_scheme/timeout/opener as ordinary component properties --
    every parameter has a default, none is typed with a component interface, see
    core/component_factory.py's describe_component."""
    source = (
        "def __init__(self, peer_host: str = '', peer_port: int = 0, peer_scheme: str = 'http', "
        "peer_secret: str = None, timeout: float = _DEFAULT_TIMEOUT, opener=None):\n"
        "    self._peer_host = peer_host\n"
        "    self._peer_port = peer_port\n"
        "    self._peer_scheme = peer_scheme\n"
        "    self._peer_secret = peer_secret\n"
        "    self._timeout = timeout\n"
        "    self._opener = opener\n"
    )
    namespace = {"_DEFAULT_TIMEOUT": DEFAULT_TIMEOUT}
    exec(source, namespace)  # noqa: S102 - controlled source, built entirely from trusted inputs
    return namespace["__init__"]


def _build_method(name: str, signature: inspect.Signature) -> Callable:
    """forges a method with the SAME calling convention as the interface's own abstract method
    (same parameter names/defaults, so callers positionally/by-keyword exactly as they would a
    hand-written component), whose body only ever calls self._dispatch(name, {param: value})"""
    parameters = list(signature.parameters.values())[1:]  # drop self
    arg_srcs = []
    dict_items = []
    namespace: dict = {}
    for parameter in parameters:
        if parameter.default is inspect.Parameter.empty:
            arg_srcs.append(parameter.name)
        else:
            default_name = f"_default_{parameter.name}"
            namespace[default_name] = parameter.default
            arg_srcs.append(f"{parameter.name}={default_name}")
        dict_items.append(f"{parameter.name!r}: {parameter.name}")
    source = (
        f"async def {name}(self, {', '.join(arg_srcs)}):\n"
        f"    return await self._dispatch({name!r}, {{{', '.join(dict_items)}}})\n"
    )
    exec(source, namespace)  # noqa: S102 - controlled source, built entirely from trusted inputs
    return namespace[name]


class _GenericRemoteProxyBase:
    """mixed into every class make_generic_proxy() builds; provides start()/stop() (no-ops -- no
    bulk caching or plural resolution to do here, unlike client's proxies, see module docstring)
    and _dispatch() (the actual RPC call). Never used on its own."""

    _ycappuccino_qualified_path = ""
    _ycappuccino_return_types: dict = {}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def _dispatch(self, method_name: str, kwargs: dict) -> Any:
        kwargs = dict(kwargs)
        # the subject travels signed in a header (spec section 11.2), never in the payload, where the
        # peer's dispatcher would ignore it anyway
        subject = kwargs.pop("subject", None)

        document = {
            "host": self._peer_host, "port": self._peer_port, "scheme": self._peer_scheme,
            "secret": self._peer_secret,
        }
        result = call_peer(
            document, DISPATCH_SERVICE_NAME, "POST",
            [self._ycappuccino_qualified_path, method_name],
            {}, {"kwargs": kwargs},
            timeout=self._timeout, opener=self._opener, subject=subject,
        )
        value = result.body.get("result") if isinstance(result.body, dict) else result.body
        return_type = self._ycappuccino_return_types.get(method_name)
        if return_type is not None and isinstance(value, dict):
            return return_type(**value)
        return value
