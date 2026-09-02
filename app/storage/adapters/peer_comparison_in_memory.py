"""Thread-safe in-memory cache for Peer Comparison JSON documents."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from re import fullmatch
from threading import RLock

from app.exceptions import StorageConflictError, StorageError
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.models.peer_comparison_storage import (
    PeerComparisonCacheKey,
    StoredPeerComparisonDocument,
)


PeerComparisonClock = Callable[[], datetime]
_CACHE_ENTRY_ID_PATTERN = r"^peer_comparison:[a-f0-9]{64}$"


def _copy(stored: StoredPeerComparisonDocument) -> StoredPeerComparisonDocument:
    return stored.model_copy(deep=True)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


class InMemoryPeerComparisonRepository:
    """Reference adapter with strict scope, freshness and expiry rules."""

    adapter_name = "in_memory_peer_comparisons"

    def __init__(
        self,
        *,
        clock: PeerComparisonClock | None = None,
        initial_entries: Iterable[StoredPeerComparisonDocument] = (),
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entries: dict[str, StoredPeerComparisonDocument] = {}
        self._lock = RLock()

        now = self._now()
        with self._lock:
            for entry in initial_entries:
                value = StoredPeerComparisonDocument.model_validate(entry)
                if value.stored_at > now:
                    raise StorageError(
                        "Peer Comparison storage time is in the future"
                    )
                if value.is_expired(as_of=now):
                    continue
                self._save_locked(value)

    def _now(self) -> datetime:
        return _require_aware(
            self._clock(),
            "Peer Comparison repository clock",
        )

    def _purge_expired_locked(self, *, as_of: datetime) -> int:
        expired_ids = [
            cache_entry_id
            for cache_entry_id, stored in self._entries.items()
            if stored.is_expired(as_of=as_of)
        ]
        for cache_entry_id in expired_ids:
            del self._entries[cache_entry_id]
        return len(expired_ids)

    def _save_locked(
        self,
        value: StoredPeerComparisonDocument,
    ) -> StoredPeerComparisonDocument:
        cache_entry_id = value.cache_key.cache_entry_id
        existing = self._entries.get(cache_entry_id)
        if existing is not None:
            if existing.storage_fingerprint != value.storage_fingerprint:
                raise StorageConflictError(
                    "Peer Comparison cache key contains different data"
                )
            return _copy(existing)
        self._entries[cache_entry_id] = _copy(value)
        return _copy(value)

    def save_peer_comparison(
        self,
        stored: StoredPeerComparisonDocument,
    ) -> StoredPeerComparisonDocument:
        value = StoredPeerComparisonDocument.model_validate(stored)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            self._validate_writable(value, as_of=now)
            return self._save_locked(value)

    def replace_peer_comparison(
        self,
        stored: StoredPeerComparisonDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredPeerComparisonDocument:
        value = StoredPeerComparisonDocument.model_validate(stored)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            if value.cache_key.repository_scope != caller_scope:
                raise StorageError(
                    "Peer Comparison replacement scope does not match key"
                )
            self._validate_writable(value, as_of=now)

            cache_entry_id = value.cache_key.cache_entry_id
            existing = self._entries.get(cache_entry_id)
            if existing is None:
                self._entries[cache_entry_id] = _copy(value)
                return _copy(value)
            if existing.storage_fingerprint == value.storage_fingerprint:
                return _copy(existing)
            if value.retrieved_at <= existing.retrieved_at:
                raise StorageConflictError(
                    "Peer Comparison replacement is not newer than cache"
                )
            self._entries[cache_entry_id] = _copy(value)
            return _copy(value)

    def get_peer_comparison(
        self,
        key: PeerComparisonCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredPeerComparisonDocument | None:
        read_at = _require_aware(as_of, "Peer Comparison cache read time")
        requested_key = PeerComparisonCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        with self._lock:
            self._purge_expired_locked(as_of=read_at)
            if requested_key.repository_scope != caller_scope:
                return None
            stored = self._entries.get(requested_key.cache_entry_id)
            if stored is None or stored.stored_at > read_at:
                return None
            return _copy(stored)

    def get_peer_comparison_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredPeerComparisonDocument | None:
        if (
            not isinstance(cache_entry_id, str)
            or fullmatch(_CACHE_ENTRY_ID_PATTERN, cache_entry_id) is None
        ):
            raise ValueError("Peer Comparison cache entry ID is invalid")
        read_at = _require_aware(as_of, "Peer Comparison cache read time")
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        with self._lock:
            self._purge_expired_locked(as_of=read_at)
            stored = self._entries.get(cache_entry_id)
            if (
                stored is None
                or stored.stored_at > read_at
                or stored.cache_key.repository_scope != caller_scope
            ):
                return None
            return _copy(stored)

    def delete_peer_comparison(
        self,
        key: PeerComparisonCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        requested_key = PeerComparisonCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            if requested_key.repository_scope != caller_scope:
                return False
            return self._entries.pop(requested_key.cache_entry_id, None) is not None

    def purge_expired_peer_comparisons(self, *, as_of: datetime) -> int:
        purge_at = _require_aware(as_of, "Peer Comparison purge time")
        with self._lock:
            return self._purge_expired_locked(as_of=purge_at)

    @staticmethod
    def _validate_writable(
        value: StoredPeerComparisonDocument,
        *,
        as_of: datetime,
    ) -> None:
        if value.stored_at > as_of:
            raise StorageError("Peer Comparison storage time is in the future")
        if value.is_expired(as_of=as_of):
            raise StorageError("expired Peer Comparison cannot be saved")
