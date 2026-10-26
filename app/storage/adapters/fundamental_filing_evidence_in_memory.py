"""Thread-safe in-memory filing-evidence persistence adapter."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from re import fullmatch
from threading import RLock

from app.exceptions import StorageConflictError, StorageError
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidencePolicyKey,
    StoredFundamentalFilingEvidence,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepositoryScope,
)


FundamentalFilingEvidenceClock = Callable[[], datetime]
_ARTIFACT_ID_PATTERN = r"^filing_artifact:[a-f0-9]{64}$"
_RECORD_ID_PATTERN = r"^filing_evidence_record:[a-f0-9]{64}$"


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _copy_record(
    record: StoredFundamentalFilingEvidence,
) -> StoredFundamentalFilingEvidence:
    return StoredFundamentalFilingEvidence.from_storage_json(
        record.to_storage_json()
    )


def _validated_record(
    record: StoredFundamentalFilingEvidence,
) -> StoredFundamentalFilingEvidence:
    value = StoredFundamentalFilingEvidence.model_validate(record)
    return _copy_record(value)


class InMemoryFundamentalFilingEvidenceRepository:
    """Reference adapter for immutable, point-in-time filing evidence."""

    adapter_name = "in_memory_fundamental_filing_evidence"

    def __init__(
        self,
        *,
        clock: FundamentalFilingEvidenceClock | None = None,
        initial_records: Iterable[StoredFundamentalFilingEvidence] = (),
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: dict[str, StoredFundamentalFilingEvidence] = {}
        self._lock = RLock()
        for record in initial_records:
            self.save_filing_evidence(record)

    def _now(self) -> datetime:
        return _require_aware(
            self._clock(),
            "fundamental filing evidence repository clock",
        )

    def save_filing_evidence(
        self,
        record: StoredFundamentalFilingEvidence,
    ) -> StoredFundamentalFilingEvidence:
        value = _validated_record(record)
        now = self._now()
        if value.stored_at > now:
            raise StorageError(
                "fundamental filing evidence storage time is in the future"
            )
        with self._lock:
            existing = self._records.get(value.record_id)
            if existing is not None:
                if existing != value:
                    raise StorageConflictError(
                        "filing evidence record ID contains different data"
                    )
                return _copy_record(existing)
            self._records[value.record_id] = _copy_record(value)
            return _copy_record(value)

    def get_filing_evidence(
        self,
        record_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> StoredFundamentalFilingEvidence | None:
        self._validate_identifier(
            record_id,
            pattern=_RECORD_ID_PATTERN,
            label="filing evidence record ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        read_at = _require_aware(as_of, "filing evidence read time")
        with self._lock:
            stored = self._records.get(record_id)
            if not self._is_visible(stored, scope=caller_scope, as_of=read_at):
                return None
            return _copy_record(stored)

    def list_filing_evidence_for_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        self._validate_identifier(
            artifact_id,
            pattern=_ARTIFACT_ID_PATTERN,
            label="filing artifact ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        read_at = _require_aware(as_of, "filing evidence read time")
        with self._lock:
            matches = (
                record
                for record in self._records.values()
                if record.artifact_id == artifact_id
                and self._is_visible(
                    record,
                    scope=caller_scope,
                    as_of=read_at,
                )
            )
            return self._ordered_copies(matches)

    def find_filing_evidence_by_policy(
        self,
        key: FundamentalFilingEvidencePolicyKey,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        policy_key = FundamentalFilingEvidencePolicyKey.model_validate(key)
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        read_at = _require_aware(as_of, "filing evidence read time")
        with self._lock:
            matches = (
                record
                for record in self._records.values()
                if FundamentalFilingEvidencePolicyKey.from_record(record)
                == policy_key
                and self._is_visible(
                    record,
                    scope=caller_scope,
                    as_of=read_at,
                )
            )
            return self._ordered_copies(matches)

    @staticmethod
    def _is_visible(
        record: StoredFundamentalFilingEvidence | None,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> bool:
        return (
            record is not None
            and record.stored_at <= as_of
            and record.scope == scope
        )

    @staticmethod
    def _ordered_copies(
        records: Iterable[StoredFundamentalFilingEvidence],
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        return tuple(
            _copy_record(record)
            for record in sorted(
                records,
                key=lambda item: (
                    item.evidence.extracted_at,
                    item.stored_at,
                    item.record_id,
                ),
            )
        )

    @staticmethod
    def _validate_identifier(
        value: str,
        *,
        pattern: str,
        label: str,
    ) -> None:
        if not isinstance(value, str) or fullmatch(pattern, value) is None:
            raise ValueError(f"{label} is invalid")
