"""
Shared HTTP-forwarding and error-translation helpers, used by RemoteCall (call.py), ServiceDirectory
(discovery.py, to query a peer's capabilities) and FederatedServiceEndpoint (federated_endpoint.py, to
forward a call to a peer's own /api/services/<name> route). Extracted so the urllib plumbing and the
peer-status-to-local-exception translation (see spec section 2) live in exactly one place.

REMOTE_SERVER_ITEM_ID is the @Item id of RemoteServer (models/remote_server.py), also shared to avoid
repeating the literal in every module that looks a peer up through IManager.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from ycappuccino.api.endpoints_service import ServiceResult
from ycappuccino.api.endpoints_storage import Forbidden, InvalidRequest, NotAuthenticated, NotFound
from ycappuccino.remote.peer_authentication import (
    PEER_HEADER,
    SIGNATURE_HEADER,
    SUBJECT_HEADER,
    TIMESTAMP_HEADER,
    sign,
)

REMOTE_SERVER_ITEM_ID = "remoteServer"
DEFAULT_TIMEOUT = 5.0
_HOP_BY_HOP_HEADERS = {"content-length", "content-type", "connection", "transfer-encoding", "date", "server"}


def build_url(document: dict, service: str, extra_path: tuple = (), params: dict | None = None) -> str:
    url = f"{document['scheme']}://{document['host']}:{document['port']}/api/services/{service}"
    if extra_path:
        url += "/" + "/".join(extra_path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def call_peer(
    document: dict,
    service: str,
    method: str,
    extra_path: tuple,
    params: dict | None,
    body: Any,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Callable | None = None,
    local_peer_id: str | None = None,
    subject: dict | None = None,
) -> ServiceResult:
    """
    Forward one call to `service` on the peer described by `document` (a RemoteServer storage model:
    host/port/scheme), over HTTP, translating its {"status","meta","data"} envelope into a
    ServiceResult, or into the matching local CrudError subclass for a 401/403/404/400/other >= 400
    status (see spec section 2). A network error (unreachable peer, timeout, DNS) is not caught here:
    it propagates unwrapped, exactly like today's RemoteCall.

    When the peer document carries a `secret`, the request is signed (peer_authentication.py) as
    `local_peer_id` (default: this Framework's application name), and `subject` -- the user this call
    is made on behalf of, if any -- travels signed in the X-YCappuccino-Subject header. Without a
    secret nothing is signed and no subject is sent: the peer sees an anonymous request.
    """
    opener = opener if opener is not None else urllib.request.urlopen
    url = build_url(document, service, extra_path, params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    secret = document.get("secret")
    if secret:
        timestamp = str(int(time.time()))
        subject_header = json.dumps(subject) if subject is not None else ""
        if subject_header:
            headers[SUBJECT_HEADER] = subject_header
        headers[PEER_HEADER] = local_peer_id or _local_peer_id()
        headers[TIMESTAMP_HEADER] = timestamp
        path = urllib.parse.urlsplit(url).path
        headers[SIGNATURE_HEADER] = sign(secret, method, path, timestamp, subject_header, data or b"")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)

    try:
        response = opener(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        with error:
            return _translate(error.code, _envelope(error.read()), error.headers)
    with response:
        return _translate(response.status, _envelope(response.read()), response.headers)


def _envelope(content: bytes) -> dict:
    """the peer's {"status","meta","data"} envelope; anything else (a peer whose API is not up yet answers
    with its HTTP server's own page) carries no data, its status alone decides"""
    try:
        payload = json.loads(content)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _local_peer_id() -> str:
    from ycappuccino.core.framework import Framework  # lazy: keeps unit tests framework-free

    return Framework.get_framework().get_app_name()


def _translate(status: int, payload: dict, headers: Any) -> ServiceResult:
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


def _forward_headers(headers: Any) -> dict:
    if not headers:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    return {key: value for key, value in items if key.lower() not in _HOP_BY_HOP_HEADERS}
