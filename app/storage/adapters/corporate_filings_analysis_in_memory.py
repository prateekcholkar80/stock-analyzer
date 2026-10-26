"""Thread-safe in-memory cache for grounded corporate-filings analysis."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from re import fullmatch
from threading import RLock

from app.exceptions import StorageConflictError, StorageError
from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisReuseKey,
)
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisDeleteAuthorization,
    CorporateFilingsAnalysisDeleteReceipt,
    CorporateFilingsAnalysisDeleteStatus,
    CorporateFilingsAnalysisPeriodKey,
    CorporateFilingsAnalysisRepositoryScope,
    StoredCorporateFilingsAnalysis,
)


CorporateFilingsAnalysisClock = Callable[[], datetime]
_RECORD_ID_PATTERN = r"^corporate_filings_analysis:[a-f0-9]{64}$"
_ACTIVE_DELETE_DETAIL = (
    "The corporate-filings analysis is the active successful result."
)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _copy_record(
    record: StoredCorporateFilingsAnalysis,
) -> StoredCorporateFilingsAnalysis:
    return StoredCorporateFilingsAnalysis.from_storage_json(
        record.to_storage_json()
    )


def _validated_record(
    record: StoredCorporateFilingsAnalysis,
) -> StoredCorporateFilingsAnalysis:
    return _copy_record(StoredCorporateFilingsAnalysis.model_validate(record))


def _scope_key(scope: CorporateFilingsAnalysisRepositoryScope) -> str:
    return scope.scope_fingerprint


def _reuse_index_key(
    *,
    scope: CorporateFilingsAnalysisRepositoryScope,
    reuse_key: CorporateFilingsAnalysisReuseKey,
) -> tuple[str, str]:
    return (_scope_key(scope), reuse_key.reuse_key_fingerprint)


def _active_index_key(
    *,
    scope: CorporateFilingsAnalysisRepositoryScope,
    period_key: CorporateFilingsAnalysisPeriodKey,
) -> tuple[str, str]:
    return (_scope_key(scope), period_key.period_key_fingerprint)


class InMemoryCorporateFilingsAnalysisRepository:
    """Reference adapter with exact reuse and atomic active-result CAS."""

    adapter_name = "in_memory_corporate_filings_analysis"

    def __init__(
        self,
        *,
        clock: CorporateFilingsAnalysisClock | None = None,
        initial_records: Iterable[StoredCorporateFilingsAnalysis] = (),
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: dict[str, StoredCorporateFilingsAnalysis] = {}
        self._reuse_record_ids: dict[tuple[str, str], str] = {}
        self._active_record_ids: dict[tuple[str, str], str] = {}
        self._active_history: dict[
            tuple[str, str],
            list[tuple[datetime, str]],
        ] = {}
        self._delete_receipts: dict[
            str,
            CorporateFilingsAnalysisDeleteReceipt,
        ] = {}
        self._lock = RLock()
        for record in initial_records:
            self.save_corporate_filings_analysis(record)

    def _now(self) -> datetime:
        return _require_aware(
            self._clock(),
            "corporate filings analysis repository clock",
        )

    def save_corporate_filings_analysis(
        self,
        record: StoredCorporateFilingsAnalysis,
    ) -> StoredCorporateFilingsAnalysis:
        value = _validated_record(record)
        now = self._now()
        self._validate_writable(value, as_of=now)
        with self._lock:
            return self._save_locked(value)

    def find_corporate_filings_analysis_by_reuse_key(
        self,
        reuse_key: CorporateFilingsAnalysisReuseKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> StoredCorporateFilingsAnalysis | None:
        key = CorporateFilingsAnalysisReuseKey.model_validate(reuse_key)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        read_at = _require_aware(as_of, "corporate analysis reuse read time")
        index_key = _reuse_index_key(scope=caller_scope, reuse_key=key)
        with self._lock:
            record_id = self._reuse_record_ids.get(index_key)
            if record_id is None:
                return None
            return self._read_locked(
                record_id,
                scope=caller_scope,
                as_of=read_at,
            )

    def list_corporate_filings_analysis_history(
        self,
        period_key: CorporateFilingsAnalysisPeriodKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredCorporateFilingsAnalysis, ...]:
        key = CorporateFilingsAnalysisPeriodKey.model_validate(period_key)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        read_at = _require_aware(as_of, "corporate analysis history read time")
        with self._lock:
            records = (
                record
                for record in self._records.values()
                if record.scope == caller_scope
                and record.period_key == key
                and record.stored_at <= read_at
            )
            return tuple(
                _copy_record(record)
                for record in sorted(
                    records,
                    key=lambda item: (
                        item.stored_at,
                        item.result.execution.completed_at,
                        item.record_id,
                    ),
                )
            )

    def get_active_corporate_filings_analysis(
        self,
        period_key: CorporateFilingsAnalysisPeriodKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> StoredCorporateFilingsAnalysis | None:
        key = CorporateFilingsAnalysisPeriodKey.model_validate(period_key)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        read_at = _require_aware(as_of, "active corporate analysis read time")
        index_key = _active_index_key(scope=caller_scope, period_key=key)
        with self._lock:
            history = self._active_history.get(index_key, ())
            visible_ids = tuple(
                record_id
                for activated_at, record_id in history
                if activated_at <= read_at
            )
            if not visible_ids:
                return None
            return self._read_locked(
                visible_ids[-1],
                scope=caller_scope,
                as_of=read_at,
            )

    def commit_corporate_filings_analysis(
        self,
        record: StoredCorporateFilingsAnalysis,
        *,
        expected_active_record_id: str | None,
    ) -> StoredCorporateFilingsAnalysis:
        value = _validated_record(record)
        self._validate_optional_record_id(expected_active_record_id)
        now = self._now()
        self._validate_writable(value, as_of=now)
        scope = value.scope
        active_key = _active_index_key(
            scope=scope,
            period_key=value.period_key,
        )
        reuse_key = _reuse_index_key(
            scope=scope,
            reuse_key=value.reuse_key,
        )

        with self._lock:
            current_active = self._active_record_ids.get(active_key)
            if current_active != expected_active_record_id:
                raise StorageConflictError(
                    "active corporate filings analysis changed before commit"
                )
            self._validate_save_conflicts_locked(
                value,
                reuse_index_key=reuse_key,
            )

            if current_active == value.record_id:
                return _copy_record(self._records[value.record_id])

            # Every validation and CAS precondition succeeds before mutation.
            # Record, reuse index and pointer become visible under one lock.
            if value.record_id not in self._records:
                self._records[value.record_id] = _copy_record(value)
            self._reuse_record_ids[reuse_key] = value.record_id
            self._active_record_ids[active_key] = value.record_id
            self._active_history.setdefault(active_key, []).append(
                (now, value.record_id)
            )
            return _copy_record(self._records[value.record_id])

    def delete_corporate_filings_analysis(
        self,
        record_id: str,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        authorization: CorporateFilingsAnalysisDeleteAuthorization,
    ) -> CorporateFilingsAnalysisDeleteReceipt:
        self._validate_identifier(record_id)
        caller_scope = CorporateFilingsAnalysisRepositoryScope.model_validate(
            scope
        )
        approved = CorporateFilingsAnalysisDeleteAuthorization.model_validate(
            authorization
        )
        if approved.record_id != record_id:
            raise StorageError(
                "corporate analysis deletion authorization mismatches target"
            )
        completed_at = self._now()
        if approved.authorized_at > completed_at:
            raise StorageError(
                "corporate analysis deletion authorization is in the future"
            )

        with self._lock:
            previous_receipt = self._delete_receipts.get(approved.audit_id)
            if previous_receipt is not None:
                if previous_receipt.authorization != approved:
                    raise StorageConflictError(
                        "corporate analysis deletion audit ID was reused"
                    )
                return previous_receipt.model_copy(deep=True)

            stored = self._records.get(record_id)
            if stored is None or stored.scope != caller_scope:
                return self._retain_receipt_locked(
                    authorization=approved,
                    status=CorporateFilingsAnalysisDeleteStatus.NOT_FOUND,
                    completed_at=completed_at,
                )
            if approved.record_fingerprint != stored.record_fingerprint:
                raise StorageError(
                    "corporate analysis deletion fingerprint mismatches target"
                )
            if record_id in self._active_record_ids.values():
                return self._retain_receipt_locked(
                    authorization=approved,
                    status=(
                        CorporateFilingsAnalysisDeleteStatus.
                        REJECTED_ACTIVE_RESULT
                    ),
                    completed_at=completed_at,
                    sanitized_detail=_ACTIVE_DELETE_DETAIL,
                )

            receipt = self._retain_receipt_locked(
                authorization=approved,
                status=CorporateFilingsAnalysisDeleteStatus.DELETED,
                completed_at=completed_at,
            )
            reuse_key = _reuse_index_key(
                scope=stored.scope,
                reuse_key=stored.reuse_key,
            )
            if self._reuse_record_ids.get(reuse_key) == record_id:
                del self._reuse_record_ids[reuse_key]
            del self._records[record_id]
            return receipt

    def _save_locked(
        self,
        value: StoredCorporateFilingsAnalysis,
    ) -> StoredCorporateFilingsAnalysis:
        reuse_key = _reuse_index_key(
            scope=value.scope,
            reuse_key=value.reuse_key,
        )
        self._validate_save_conflicts_locked(
            value,
            reuse_index_key=reuse_key,
        )
        existing = self._records.get(value.record_id)
        if existing is None:
            self._records[value.record_id] = _copy_record(value)
            self._reuse_record_ids[reuse_key] = value.record_id
            existing = self._records[value.record_id]
        return _copy_record(existing)

    def _validate_save_conflicts_locked(
        self,
        value: StoredCorporateFilingsAnalysis,
        *,
        reuse_index_key: tuple[str, str],
    ) -> None:
        existing = self._records.get(value.record_id)
        if existing is not None and existing != value:
            raise StorageConflictError(
                "corporate analysis record ID contains different data"
            )
        reused_record_id = self._reuse_record_ids.get(reuse_index_key)
        if reused_record_id is not None and reused_record_id != value.record_id:
            raise StorageConflictError(
                "corporate analysis reuse key contains different data"
            )

    def _read_locked(
        self,
        record_id: str,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> StoredCorporateFilingsAnalysis | None:
        stored = self._records.get(record_id)
        if (
            stored is None
            or stored.scope != scope
            or stored.stored_at > as_of
        ):
            return None
        return _copy_record(stored)

    def _retain_receipt_locked(
        self,
        *,
        authorization: CorporateFilingsAnalysisDeleteAuthorization,
        status: CorporateFilingsAnalysisDeleteStatus,
        completed_at: datetime,
        sanitized_detail: str | None = None,
    ) -> CorporateFilingsAnalysisDeleteReceipt:
        receipt = CorporateFilingsAnalysisDeleteReceipt(
            authorization=authorization,
            status=status,
            completed_at=completed_at,
            sanitized_detail=sanitized_detail,
        )
        self._delete_receipts[authorization.audit_id] = receipt
        return receipt.model_copy(deep=True)

    @staticmethod
    def _validate_writable(
        record: StoredCorporateFilingsAnalysis,
        *,
        as_of: datetime,
    ) -> None:
        if record.stored_at > as_of:
            raise StorageError(
                "corporate filings analysis storage time is in the future"
            )

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if not isinstance(value, str) or fullmatch(_RECORD_ID_PATTERN, value) is None:
            raise ValueError("corporate filings analysis record ID is invalid")

    @classmethod
    def _validate_optional_record_id(cls, value: str | None) -> None:
        if value is not None:
            cls._validate_identifier(value)
