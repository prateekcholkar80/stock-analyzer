from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from threading import RLock
from typing import Protocol, runtime_checkable

from app.exceptions import BrowserSessionNotFoundError


@runtime_checkable
class BrowserSessionAuthorizer(Protocol):
    def issue(self, session_id: str) -> str:
        ...

    def authorize(self, session_id: str, access_token: str) -> None:
        ...

    def revoke(self, session_id: str) -> None:
        ...


class InMemoryBrowserSessionAuthorizer:
    """Store only token digests and compare capabilities in constant time."""

    def __init__(self) -> None:
        self._token_digests: dict[str, bytes] = {}
        self._lock = RLock()

    def issue(self, session_id: str) -> str:
        normalized_id = _required("session ID", session_id)
        token = token_urlsafe(32)
        digest = _digest(token)
        with self._lock:
            if normalized_id in self._token_digests:
                raise ValueError("browser session token was already issued")
            self._token_digests[normalized_id] = digest
        return token

    def authorize(self, session_id: str, access_token: str) -> None:
        normalized_id = _required("session ID", session_id)
        normalized_token = _required("access token", access_token)
        with self._lock:
            expected = self._token_digests.get(normalized_id)
        if expected is None or not compare_digest(
            expected,
            _digest(normalized_token),
        ):
            raise BrowserSessionNotFoundError(
                "browser session authorization failed"
            )

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._token_digests.pop(_required("session ID", session_id), None)


def _digest(token: str) -> bytes:
    return sha256(token.encode("utf-8")).digest()


def _required(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"browser {label} must not be blank")
    return value.strip()
