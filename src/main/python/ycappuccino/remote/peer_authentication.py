"""
PeerHmacAuthentication: recognizes a request signed by another YCappuccino instance with the secret
of its RemoteServer entry (spec 2026-09-16-transparent-rpc-design.md, sections 4 and 11.2).

The signed message is `method\npath\ntimestamp\nsubject\n` followed by the raw body, HMAC-SHA256 keyed
by the peer's secret; `path` excludes the query string, `subject` is the exact X-YCappuccino-Subject
header (empty when absent). A timestamp further than `tolerance` seconds from now is refused, bounding
replay. A recognized request's subject is {"peer": <peer id>}, plus the user subject the peer forwarded
when it called on someone's behalf; anything else returns None so the next IAuthentication can try.
"""

import hashlib
import hmac
import json
import time
from typing import Callable

from ycappuccino.api.http_server import IAuthentication
from ycappuccino.api.storage import IManager

PEER_HEADER = "X-YCappuccino-Peer"
TIMESTAMP_HEADER = "X-YCappuccino-Timestamp"
SIGNATURE_HEADER = "X-YCappuccino-Signature"
SUBJECT_HEADER = "X-YCappuccino-Subject"
_REMOTE_SERVER_ITEM_ID = "remoteServer"


def sign(secret: str, method: str, path: str, timestamp: str, subject_header: str, body: bytes) -> str:
    message = f"{method}\n{path}\n{timestamp}\n{subject_header}\n".encode() + (body or b"")
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class PeerHmacAuthentication(IAuthentication):

    def __init__(self, manager: IManager, now: Callable[[], float] | None = None, tolerance: float = 60.0) -> None:
        self._manager = manager
        self._now = now if now is not None else time.time
        self._tolerance = tolerance

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def authenticate(self, headers: dict, method: str, path: str, body: bytes) -> dict | None:
        peer_id = headers.get(PEER_HEADER.lower())
        timestamp = headers.get(TIMESTAMP_HEADER.lower())
        signature = headers.get(SIGNATURE_HEADER.lower())
        if not peer_id or not timestamp or not signature:
            return None
        try:
            if abs(self._now() - float(timestamp)) > self._tolerance:
                return None
        except ValueError:
            return None

        peer = await self._manager.get_one(_REMOTE_SERVER_ITEM_ID, peer_id, subject=None)
        secret = peer.get_storage_model().get("secret") if peer is not None else None
        if not secret:
            return None

        subject_header = headers.get(SUBJECT_HEADER.lower(), "")
        if not hmac.compare_digest(sign(secret, method, path, timestamp, subject_header, body), signature):
            return None

        subject = {}
        if subject_header:
            try:
                subject = json.loads(subject_header)
            except ValueError:
                return None
            if not isinstance(subject, dict):
                return None
        return {**subject, "peer": peer_id}
