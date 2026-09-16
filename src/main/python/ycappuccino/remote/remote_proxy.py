"""
make_generic_proxy: reflection-based synthesis of a concrete YCappuccinoComponent subclass
implementing an ARBITRARY interface, forwarding every one of its abstract (business) methods to
__remote_dispatch__ (dispatch.py) on a peer, as a generic (method name, kwargs) call. Used by
component_directory.ComponentDirectory to create, on the fly, a local stand-in for a specification
discovered on a peer but not available locally -- see the design doc, addendum part C.

Kin to (but independent of, and NOT importing) ycappuccino.client.remote_proxy.make_remote: both
forge a real, introspectable __init__ and business methods via exec() (spec §9.2's technique --
dataclasses/attrs/namedtuple do the same), so core's real DI (describe_component, which reads
inspect.signature by name and typing.get_type_hints by annotation) can wire them exactly like a
hand-written component. That is where the similarity ends: client's make_remote infers an HTTP
verb/path from each method's own NAME, because it targets a small, KNOWN set of interfaces
(ICrud/IDrafts/IItemCatalog/IServiceEndpoint) whose REST shape at /api/crud, /api/drafts, etc. is
fixed and already understood (see client's spec §9). remote targets interfaces it has NEVER seen
before (an application's own IInventoryService, say) with NO such wire convention to lean on -- so
there is nothing to infer a route from. Every call is therefore a raw, uninterpreted RPC: the
method's own parameter names/values become a JSON "kwargs" dict, POSTed whole to the single generic
__remote_dispatch__ endpoint (never a REST-shaped route), see dispatch.py for the receiving side.

Supported argument/return shapes: JSON-serializable only (str/int/float/bool/None/list/dict), same
constraint as dispatch.py's own docstring documents on the receiving side. A method whose signature
takes *args/**kwargs, or whose default values are not JSON-serializable, or whose return value isn't
either, is an accepted, out-of-scope limitation -- not handled here.
"""

import inspect

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
    for name in method_names:
        signature = inspect.signature(getattr(interface, name))
        namespace[name] = _build_method(name, signature)

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


def _build_init():
    """forges a REAL, introspectable __init__ (real parameter names/annotations, not a **kwargs
    sink) via exec, so describe_component's constructor introspection (inspect.signature by name)
    treats peer_host/peer_port/peer_scheme/timeout/opener as ordinary component properties --
    every parameter has a default, none is typed with a component interface, see
    core/component_factory.py's describe_component."""
    source = (
        "def __init__(self, peer_host: str = '', peer_port: int = 0, peer_scheme: str = 'http', "
        "timeout: float = _DEFAULT_TIMEOUT, opener=None):\n"
        "    self._peer_host = peer_host\n"
        "    self._peer_port = peer_port\n"
        "    self._peer_scheme = peer_scheme\n"
        "    self._timeout = timeout\n"
        "    self._opener = opener\n"
    )
    namespace = {"_DEFAULT_TIMEOUT": DEFAULT_TIMEOUT}
    exec(source, namespace)  # noqa: S102 - controlled source, built entirely from trusted inputs
    return namespace["__init__"]


def _build_method(name: str, signature: inspect.Signature):
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

    async def start(self):
        pass

    async def stop(self):
        pass

    async def _dispatch(self, method_name: str, kwargs: dict):
        kwargs = dict(kwargs)
        kwargs.pop("subject", None)  # never forwarded, see spec section 3 / dispatch.py

        document = {"host": self._peer_host, "port": self._peer_port, "scheme": self._peer_scheme}
        result = call_peer(
            document, DISPATCH_SERVICE_NAME, "POST",
            [self._ycappuccino_qualified_path, method_name],
            {}, {"kwargs": kwargs},
            timeout=self._timeout, opener=self._opener,
        )
        return result.body.get("result") if isinstance(result.body, dict) else result.body
