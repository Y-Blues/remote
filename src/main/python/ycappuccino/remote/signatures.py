"""
describe_interface: the JSON description of the methods an interface lets a caller invoke through
__remote_dispatch__ (dispatch.py), published by __remote_capabilities__ and kept by ServiceCatalog.

    [{"name": "greet", "params": {"language": "str"}, "return_type": "dict",
      "rpc": {"method": "POST", "path": "/{language}", "summary": "...", "secure": False}}]

A peer may call any dispatchable method ("rpc" is None for one that is not @rpc_method); anyone else
only the @rpc_method ones. Types are names, never objects: "str", "dict | None", "module.QualName". The
`subject` parameter is left out: the caller never sends it, the receiving side injects it.
"""

import inspect
import typing
from typing import Any

from ycappuccino.api.decorators import get_rpc_methods

NEVER_DISPATCHABLE = frozenset({"start", "stop", "bind", "un_bind"})


def is_dispatchable(method_name: str) -> bool:
    return method_name not in NEVER_DISPATCHABLE and not method_name.startswith("_")


def type_name(annotation: Any) -> str:
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, type) and not typing.get_args(annotation):
        if annotation.__module__ == "builtins":
            return annotation.__name__
        return f"{annotation.__module__}.{annotation.__qualname__}"
    return str(annotation).replace("typing.", "")


def describe_interface(interface: type, public_only: bool) -> list[dict]:
    rpc_methods = get_rpc_methods(interface)
    methods = []
    for name, function in inspect.getmembers(interface, inspect.isfunction):
        if not is_dispatchable(name) or (public_only and name not in rpc_methods):
            continue
        methods.append(_describe_method(name, function, rpc_methods.get(name)))
    return methods


def _describe_method(name: str, function: Any, metadata: dict | None) -> dict:
    try:
        hints = typing.get_type_hints(function)
    except Exception:
        hints = dict(getattr(function, "__annotations__", {}))
    parameters = [
        parameter for parameter in inspect.signature(function).parameters if parameter not in ("self", "subject")
    ]
    return {
        "name": name,
        "params": {parameter: type_name(hints[parameter]) if parameter in hints else "Any" for parameter in parameters},
        "return_type": type_name(hints["return"]) if "return" in hints else "Any",
        "rpc": None if metadata is None else {key: metadata[key] for key in ("method", "path", "summary", "secure")},
    }
