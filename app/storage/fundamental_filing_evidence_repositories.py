"""Database-neutral persistence for extracted company-filing evidence."""

from datetime import datetime
from hashlib import sha256
import json
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamental_filing_evidence import (
    FundamentalFilingEvidencePageSpan,
    FundamentalFilingEvidenceValidationResult,
    FundamentalFilingExtractedEvidence,
    FundamentalFilingValidationCode,
    FundamentalFilingValidationFinding,
    FundamentalFilingValidationStatus,
)
from app.models.fundamentals import FundamentalModel
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepositoryScope,
)


_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_ARTIFACT_ID_PATTERN = r"^filing_artifact:[a-f0-9]{64}$"


def _fingerprint(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validation_policy_fingerprint(
    *,
    policy_id: str,
    policy_version: str,
) -> str:
    """Return the stable identity of a deterministic validation policy."""

    return _fingerprint(
        {
            "policy_id": policy_id,
            "policy_version": policy_version,
        }
    )


def _decode_validation_result(
    value: object,
) -> FundamentalFilingEvidenceValidationResult:
    if isinstance(value, FundamentalFilingEvidenceValidationResult):
        return value
    if not isinstance(value, dict):
        raise ValueError("filing validation result has an invalid shape")
    expected_fields = {
        "policy_id",
        "policy_version",
        "extraction_fingerprint",
        "releaseable",
        "findings",
        "evidence_page_spans",
    }
    if set(value) != expected_fields:
        raise ValueError("filing validation result fields are invalid")
    findings_payload = value["findings"]
    spans_payload = value["evidence_page_spans"]
    if not isinstance(findings_payload, (list, tuple)) or not isinstance(
        spans_payload,
        (list, tuple),
    ):
        raise ValueError("filing validation collections are invalid")
    findings = tuple(_decode_validation_finding(item) for item in findings_payload)
    spans = tuple(_decode_page_span(item) for item in spans_payload)
    return FundamentalFilingEvidenceValidationResult(
        policy_id=value["policy_id"],
        policy_version=value["policy_version"],
        extraction_fingerprint=value["extraction_fingerprint"],
        releaseable=value["releaseable"],
        findings=findings,
        evidence_page_spans=spans,
    )


def _decode_validation_finding(
    value: object,
) -> FundamentalFilingValidationFinding:
    if isinstance(value, FundamentalFilingValidationFinding):
        return value
    if not isinstance(value, dict) or set(value) != {
        "code",
        "status",
        "message",
        "affected_ids",
    }:
        raise ValueError("filing validation finding fields are invalid")
    affected_ids = value["affected_ids"]
    if not isinstance(affected_ids, (list, tuple)):
        raise ValueError("filing validation affected IDs are invalid")
    return FundamentalFilingValidationFinding(
        code=FundamentalFilingValidationCode(value["code"]),
        status=FundamentalFilingValidationStatus(value["status"]),
        message=value["message"],
        affected_ids=tuple(affected_ids),
    )


def _decode_page_span(value: object) -> FundamentalFilingEvidencePageSpan:
    if isinstance(value, FundamentalFilingEvidencePageSpan):
        return value
    if not isinstance(value, dict) or set(value) != {
        "evidence_id",
        "first_page",
        "last_page",
        "cited_pages",
    }:
        raise ValueError("filing evidence page-span fields are invalid")
    cited_pages = value["cited_pages"]
    if not isinstance(cited_pages, (list, tuple)):
        raise ValueError("filing evidence cited pages are invalid")
    return FundamentalFilingEvidencePageSpan(
        evidence_id=value["evidence_id"],
        first_page=value["first_page"],
        last_page=value["last_page"],
        cited_pages=tuple(cited_pages),
    )


class FundamentalFilingEvidencePolicyKey(FundamentalModel):
    """Exact artifact and policy identity used for scoped version reads."""

    artifact_id: str = Field(pattern=_ARTIFACT_ID_PATTERN)
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    extraction_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    validation_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @classmethod
    def from_record(
        cls,
        record: "StoredFundamentalFilingEvidence",
    ) -> "FundamentalFilingEvidencePolicyKey":
        return cls(
            artifact_id=record.artifact_id,
            artifact_fingerprint=record.artifact_fingerprint,
            extraction_policy_fingerprint=(
                record.extraction_policy_fingerprint
            ),
            validation_policy_fingerprint=(
                record.validation_policy_fingerprint
            ),
        )


class StoredFundamentalFilingEvidence(FundamentalModel):
    """One immutable extraction and its deterministic release validation."""

    schema_version: Literal[
        "jarvis.stored_fundamental_filing_evidence.v1"
    ] = "jarvis.stored_fundamental_filing_evidence.v1"
    artifact_id: str = Field(pattern=_ARTIFACT_ID_PATTERN)
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    extraction_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    validation_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    extraction_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    validation_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    stored_at: datetime
    evidence: FundamentalFilingExtractedEvidence
    validation: FundamentalFilingEvidenceValidationResult

    @classmethod
    def from_validated(
        cls,
        *,
        evidence: FundamentalFilingExtractedEvidence,
        validation: FundamentalFilingEvidenceValidationResult,
        stored_at: datetime,
    ) -> "StoredFundamentalFilingEvidence":
        return cls(
            artifact_id=evidence.artifact.artifact_id,
            artifact_fingerprint=evidence.artifact.artifact_fingerprint,
            extraction_policy_fingerprint=evidence.policy.policy_fingerprint,
            validation_policy_fingerprint=validation_policy_fingerprint(
                policy_id=validation.policy_id,
                policy_version=validation.policy_version,
            ),
            extraction_fingerprint=evidence.extraction_fingerprint,
            validation_fingerprint=validation.validation_fingerprint,
            stored_at=stored_at,
            evidence=evidence,
            validation=validation,
        )

    @field_validator("stored_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("filing evidence storage time requires timezone")
        return value

    @field_validator("validation", mode="before")
    @classmethod
    def decode_validation_result(
        cls,
        value: object,
    ) -> FundamentalFilingEvidenceValidationResult:
        return _decode_validation_result(value)

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        expected = {
            "artifact_id": self.evidence.artifact.artifact_id,
            "artifact_fingerprint": (
                self.evidence.artifact.artifact_fingerprint
            ),
            "extraction_policy_fingerprint": (
                self.evidence.policy.policy_fingerprint
            ),
            "validation_policy_fingerprint": validation_policy_fingerprint(
                policy_id=self.validation.policy_id,
                policy_version=self.validation.policy_version,
            ),
            "extraction_fingerprint": self.evidence.extraction_fingerprint,
            "validation_fingerprint": (
                self.validation.validation_fingerprint
            ),
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(
                    f"stored filing evidence {field_name} is inconsistent"
                )
        if (
            self.validation.extraction_fingerprint
            != self.evidence.extraction_fingerprint
        ):
            raise ValueError(
                "filing validation does not bind the stored extraction"
            )
        if self.stored_at < self.evidence.extracted_at:
            raise ValueError(
                "filing evidence storage cannot predate extraction"
            )
        return self

    @computed_field
    @property
    def record_fingerprint(self) -> str:
        return _fingerprint(
            {
                "schema_version": self.schema_version,
                "artifact_id": self.artifact_id,
                "artifact_fingerprint": self.artifact_fingerprint,
                "extraction_policy_fingerprint": (
                    self.extraction_policy_fingerprint
                ),
                "validation_policy_fingerprint": (
                    self.validation_policy_fingerprint
                ),
                "extraction_fingerprint": self.extraction_fingerprint,
                "validation_fingerprint": self.validation_fingerprint,
            }
        )

    @computed_field
    @property
    def record_id(self) -> str:
        return f"filing_evidence_record:{self.record_fingerprint}"

    @computed_field
    @property
    def releaseable(self) -> bool:
        return self.validation.releaseable

    @computed_field
    @property
    def scope(self) -> FundamentalFilingRepositoryScope:
        return FundamentalFilingRepositoryScope.from_artifact(
            self.evidence.artifact
        )

    def to_storage_json(self) -> str:
        """Serialize only declared fields for strict durable reconstruction."""

        return self.model_dump_json(exclude_computed_fields=True)

    @classmethod
    def from_storage_json(cls, value: str) -> "StoredFundamentalFilingEvidence":
        """Reconstruct one strict record from its adapter-owned JSON."""

        return cls.model_validate_json(value)


@runtime_checkable
class FundamentalFilingEvidenceRepository(Protocol):
    """Tenant-isolated immutable extraction and validation persistence.

    Records are durable research artifacts and do not expire with provider
    recheck dates. Implementations must provide idempotent immutable saves,
    exact scoped reads, deterministic version ordering, and no broad tenant or
    deletion escape hatch. Failed validation results remain auditable but must
    never be represented as releaseable.
    """

    @property
    def adapter_name(self) -> str:
        """Return a non-secret persistence adapter name for diagnostics."""
        ...

    def save_filing_evidence(
        self,
        record: StoredFundamentalFilingEvidence,
    ) -> StoredFundamentalFilingEvidence:
        """Persist an immutable record or return its identical stored value."""
        ...

    def get_filing_evidence(
        self,
        record_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> StoredFundamentalFilingEvidence | None:
        """Resolve one scoped record only after its persistence timestamp."""
        ...

    def list_filing_evidence_for_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        """Return scoped extraction versions visible at the read timestamp."""
        ...

    def find_filing_evidence_by_policy(
        self,
        key: FundamentalFilingEvidencePolicyKey,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        """Return visible exact artifact-policy versions in stable order."""
        ...
