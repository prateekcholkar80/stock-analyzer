"""Thread-safe in-memory cache for Benchmarking Financials JSON documents."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from re import fullmatch
from threading import RLock

from app.exceptions import StorageConflictError, StorageError
from app.models.benchmarking_financials_storage import (
    BenchmarkingFinancialsCacheKey,
    StoredBenchmarkingFinancialsDocument,
)
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)


BenchmarkingFinancialsClock = Callable[[], datetime]
_CACHE_ENTRY_ID_PATTERN = r"^benchmarking_financials:[a-f0-9]{64}$"


def _copy(
    stored: StoredBenchmarkingFinancialsDocument,
) -> StoredBenchmarkingFinancialsDocument:
    return stored.model_copy(deep=True)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


class InMemoryBenchmarkingFinancialsRepository:
    """Reference adapter with strict scope, freshness and expiry rules."""

    adapter_name = "in_memory_benchmarking_financials"

    def __init__(
        self,
        *,
        clock: BenchmarkingFinancialsClock | None = None,
        initial_entries: Iterable[StoredBenchmarkingFinancialsDocument] = (),
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entries: dict[str, StoredBenchmarkingFinancialsDocument] = {}
        self._lock = RLock()

        now = self._now()
        with self._lock:
            for entry in initial_entries:
                value = StoredBenchmarkingFinancialsDocument.model_validate(
                    entry
                )
                if value.stored_at > now:
                    raise StorageError(
                        "Benchmarking Financials storage time is in the future"
                    )
                if value.is_expired(as_of=now):
                    continue
                self._save_locked(value)

    def _now(self) -> datetime:
        return _require_aware(
            self._clock(),
            "Benchmarking Financials repository clock",
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
        value: StoredBenchmarkingFinancialsDocument,
    ) -> StoredBenchmarkingFinancialsDocument:
        cache_entry_id = value.cache_key.cache_entry_id
        existing = self._entries.get(cache_entry_id)
        if existing is not None:
            if existing.storage_fingerprint != value.storage_fingerprint:
                raise StorageConflictError(
                    "Benchmarking Financials cache key contains different data"
                )
            return _copy(existing)
        self._entries[cache_entry_id] = _copy(value)
        return _copy(value)

    def save_benchmarking_financials(
        self,
        stored: StoredBenchmarkingFinancialsDocument,
    ) -> StoredBenchmarkingFinancialsDocument:
        value = StoredBenchmarkingFinancialsDocument.model_validate(stored)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            self._validate_writable(value, as_of=now)
            return self._save_locked(value)

    def replace_benchmarking_financials(
        self,
        stored: StoredBenchmarkingFinancialsDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredBenchmarkingFinancialsDocument:
        value = StoredBenchmarkingFinancialsDocument.model_validate(stored)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            if value.cache_key.repository_scope != caller_scope:
                raise StorageError(
                    "Benchmarking Financials replacement scope does not match key"
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
                    "Benchmarking Financials replacement is not newer than cache"
                )
            self._entries[cache_entry_id] = _copy(value)
            return _copy(value)

    def get_benchmarking_financials(
        self,
        key: BenchmarkingFinancialsCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredBenchmarkingFinancialsDocument | None:
        read_at = _require_aware(
            as_of,
            "Benchmarking Financials cache read time",
        )
        requested_key = BenchmarkingFinancialsCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        with self._lock:
            self._purge_expired_locked(as_of=read_at)
            if requested_key.repository_scope != caller_scope:
                return None
            stored = self._entries.get(requested_key.cache_entry_id)
            if stored is None or stored.stored_at > read_at:
                return None
            return _copy(stored)

    def get_benchmarking_financials_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredBenchmarkingFinancialsDocument | None:
        if (
            not isinstance(cache_entry_id, str)
            or fullmatch(_CACHE_ENTRY_ID_PATTERN, cache_entry_id) is None
        ):
            raise ValueError(
                "Benchmarking Financials cache entry ID is invalid"
            )
        read_at = _require_aware(
            as_of,
            "Benchmarking Financials cache read time",
        )
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

    def delete_benchmarking_financials(
        self,
        key: BenchmarkingFinancialsCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        requested_key = BenchmarkingFinancialsCacheKey.model_validate(key)
        caller_scope = StructuredDocumentRepositoryScope.model_validate(scope)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(as_of=now)
            if requested_key.repository_scope != caller_scope:
                return False
            return (
                self._entries.pop(requested_key.cache_entry_id, None)
                is not None
            )

    def purge_expired_benchmarking_financials(
        self,
        *,
        as_of: datetime,
    ) -> int:
        purge_at = _require_aware(
            as_of,
            "Benchmarking Financials purge time",
        )
        with self._lock:
            return self._purge_expired_locked(as_of=purge_at)

    @staticmethod
    def _validate_writable(
        value: StoredBenchmarkingFinancialsDocument,
        *,
        as_of: datetime,
    ) -> None:
        if value.stored_at > as_of:
            raise StorageError(
                "Benchmarking Financials storage time is in the future"
            )
        if value.is_expired(as_of=as_of):
            raise StorageError(
                "expired Benchmarking Financials cannot be saved"
            )
