"""
RemoteCall: an IExposedService that forwards a call to a named IExposedService on another
YCappuccino instance (a RemoteServer registered peer), over plain HTTP.

See spec (2026-09-15-remote-design.md) for the design decisions: addressing via extra_path
(peer id, then target service, then the target's own extra_path), no forwarded authentication
in this first version (target services on the peer must be secure=False), network errors
propagate unwrapped (no retry).
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from ycappuccino.api.endpoints_service import IExposedService, ServiceResult
from ycappuccino.api.endpoints_storage import Forbidden, InvalidRequest, NotAuthenticated, NotFound
from ycappuccino.api.storage import IManager

_ITEM_ID = "remoteServer"
_HOP_BY_HOP_HEADERS = {"content-length", "content-type", "connection", "transfer-encoding", "date", "server"}


class RemoteCall(IExposedService):
    name = "remote_call"
    secure = True

    def __init__(self, manager: IManager, timeout: float = 5.0, opener=None):
        self._manager = manager
        self._timeout = timeout
        self._opener = opener if opener is not None else urllib.request.urlopen

    async def start(self):
        pass

    async def stop(self):
        pass

    async def call(self, method, extra_path, params, body, subject):
        if len(extra_path) < 2:
            raise InvalidRequest("remote_call expects /<peer id>/<service name>[/<extra path>...]")
        peer_id, target_service, *target_extra = extra_path

        peer = await self._manager.get_one(_ITEM_ID, peer_id, subject=None)
        if peer is None:
            raise NotFound(f"unknown remote server {peer_id!r}")

        document = peer.get_storage_model()
        url = _build_url(document, target_service, target_extra, params)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = urllib.request.Request(url, data=data, method=method, headers=headers)

        try:
            response = self._opener(request, timeout=self._timeout)
        except urllib.error.HTTPError as error:
            with error:
                return _translate(error.code, json.loads(error.read()), error.headers)
        with response:
            return _translate(response.status, json.loads(response.read()), response.headers)


def _build_url(document, service, extra_path, params):
    url = f"{document['scheme']}://{document['host']}:{document['port']}/api/services/{service}"
    if extra_path:
        url += "/" + "/".join(extra_path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def _translate(status, payload, headers):
    data = payload.get("data")
    message = data.get("error", "remote call failed") if isinstance(data, dict) else "remote call failed"
    if status == 401:
        raise NotAuthenticated(message)
    if status == 403:
        raise Forbidden(message)
    if status == 404:
        raise NotFound(message)
    if status == 400:
        raise InvalidRequest(message)
    if status >= 400:
        raise RuntimeError(message)
    return ServiceResult(body=data, headers=_forward_headers(headers))


def _forward_headers(headers):
    if not headers:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    return {key: value for key, value in items if key.lower() not in _HOP_BY_HOP_HEADERS}
