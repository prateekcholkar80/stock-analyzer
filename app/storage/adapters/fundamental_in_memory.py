"""Thread-safe in-memory cache for validated fundamental evidence."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import RLock

from app.exceptions import StorageConflictError, StorageError
from app.models.fundamental_storage import (
    FundamentalRepositoryScope,
    FundamentalSnapshotCacheKey,
    FundamentalSnapshotQuery,
    FundamentalSnapshotSummary,
    StoredFundamentalSnapshot,
    fundamental_snapshot_summary,
)


FundamentalCacheClock = Callable[[], datetime]


def _copy(
    stored: StoredFundamentalSnapshot,
) -> StoredFundamentalSnapshot:
    return stored.model_copy(deep=True)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


class InMemoryFundamentalSnapshotRepository:
    """Reference adapter with bounded expiry and mandatory tenant scope."""

    adapter_name = "in_memory_fundamentals"

    def __init__(
        self,
        *,
        clock: FundamentalCacheClock | None = None,
        initial_entries: Iterable[StoredFundamentalSnapshot] = (),
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entries: dict[str, StoredFundamentalSnapshot] = {}
        self._lock = RLock()

        now = self._now()
        with self._lock:
            for entry in initial_entries:
                value = StoredFundamentalSnapshot.model_validate(entry)
                if value.stored_at > now:
                    raise StorageError(
                        "fundamental snapshot storage time is in the future"
                    )
                if value.is_expired(as_of=now):
                    continue
                self._save_locked(value)

    def _now(self) -> datetime:
        return _require_aware(
            self._clock(),
            "fundamental repository clock",
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
        value: StoredFundamentalSnapshot,
    ) -> StoredFundamentalSnapshot:
        cache_entry_id = value.cache_key.cache_entry_id
        existing = self._entries.get(cache_entry_id)
        if existing is not None:
            if existing.storage_fingerprint != value.storage_fingerprint:
                raise StorageConflictError(
                    "fundamental cache key already contains different data"
                )
            return _copy(existing)
        self._entries[cache_entry_id] = _copy(value)
        return _copy(value)

    def save_fundamental_snapshot(
        self,
        stored: StoredFundamentalSnapshot,
    ) -> StoredFundamentalSnapshot:
        value = StoredFundamentalSnapshot.model_validate(stored)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            if value.stored_at > now:
                raise StorageError(
                    "fundamental snapshot storage time is in the future"
                )
            if value.is_expired(as_of=now):
                raise StorageError(
                    "expired fundamental snapshot cannot be saved"
                )
            return self._save_locked(value)

    def get_fundamental_snapshot(
        self,
        key: FundamentalSnapshotCacheKey,
        *,
        scope: FundamentalRepositoryScope,
        as_of: datetime,
    ) -> StoredFundamentalSnapshot | None:
        read_at = _require_aware(as_of, "fundamental cache read time")
        requested_key = FundamentalSnapshotCacheKey.model_validate(key)
        caller_scope = FundamentalRepositoryScope.model_validate(scope)
        with self._lock:
            self._purge_expired_locked(as_of=read_at)
            if requested_key.repository_scope != caller_scope:
                return None
            stored = self._entries.get(requested_key.cache_entry_id)
            if stored is None or stored.stored_at > read_at:
                return None
            return _copy(stored)

    def list_fundamental_snapshots(
        self,
        query: FundamentalSnapshotQuery,
        *,
        as_of: datetime,
    ) -> tuple[FundamentalSnapshotSummary, ...]:
        read_at = _require_aware(as_of, "fundamental cache list time")
        filters = FundamentalSnapshotQuery.model_validate(query)
        with self._lock:
            self._purge_expired_locked(as_of=read_at)
            values = [
                stored
                for stored in self._entries.values()
                if stored.stored_at <= read_at
                and stored.cache_key.repository_scope
                == filters.repository_scope
                and (
                    filters.symbol is None
                    or stored.cache_key.issuer.symbol == filters.symbol
                )
                and (
                    filters.exchange is None
                    or stored.cache_key.issuer.exchange == filters.exchange
                )
                and (
                    not filters.capabilities
                    or stored.cache_key.capability in filters.capabilities
                )
                and (
                    filters.retrieved_from is None
                    or stored.retrieved_at >= filters.retrieved_from
                )
                and (
                    filters.retrieved_to is None
                    or stored.retrieved_at <= filters.retrieved_to
                )
            ]

        values.sort(
            key=lambda stored: (
                stored.retrieved_at,
                stored.stored_at,
                stored.cache_key.cache_entry_id,
            ),
            reverse=True,
        )
        selected = values[
            filters.offset:filters.offset + filters.limit
        ]
        return tuple(
            fundamental_snapshot_summary(stored)
            for stored in selected
        )

    def delete_fundamental_snapshot(
        self,
        key: FundamentalSnapshotCacheKey,
        *,
        scope: FundamentalRepositoryScope,
    ) -> bool:
        requested_key = FundamentalSnapshotCacheKey.model_validate(key)
        caller_scope = FundamentalRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            if requested_key.repository_scope != caller_scope:
                return False
            return (
                self._entries.pop(requested_key.cache_entry_id, None)
                is not None
            )

    def purge_expired_fundamental_snapshots(
        self,
        *,
        as_of: datetime,
    ) -> int:
        purge_at = _require_aware(as_of, "fundamental cache purge time")
        with self._lock:
            return self._purge_expired_locked(as_of=purge_at)
