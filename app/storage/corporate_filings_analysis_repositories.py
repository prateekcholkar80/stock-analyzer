"""Database-neutral persistence for grounded corporate-filings analysis."""

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisKind,
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisResult,
    CorporateFilingsAnalysisReuseKey,
    validate_corporate_filings_analysis_response,
)
from app.models.fundamental_filings import (
    FundamentalFilingPeriod,
    FundamentalFilingPeriodState,
    FundamentalFilingPeriodType,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
    ProviderConnectionScope,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_RECORD_ID_PATTERN = r"^corporate_filings_analysis:[a-f0-9]{64}$"


def _fingerprint(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


class CorporateFilingsAnalysisRepositoryScope(FundamentalModel):
    """Tenant, provider connection and verified issuer persistence boundary."""

    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity

    @classmethod
    def from_request(
        cls,
        request: CorporateFilingsAnalysisRequest,
    ) -> "CorporateFilingsAnalysisRepositoryScope":
        return cls(
            connection=request.bundle.connection,
            issuer=request.bundle.issuer,
        )

    @computed_field
    @property
    def scope_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class CorporateFilingsAnalysisPeriodKey(FundamentalModel):
    """One independently active document-analysis view for one quarter."""

    fiscal_year: int = Field(ge=1900, le=2200)
    fiscal_quarter: int = Field(ge=1, le=4)
    analysis_kind: CorporateFilingsAnalysisKind

    @classmethod
    def from_request(
        cls,
        request: CorporateFilingsAnalysisRequest,
    ) -> "CorporateFilingsAnalysisPeriodKey":
        period = request.bundle.period
        if period.fiscal_year is None or period.fiscal_quarter is None:
            raise ValueError("corporate filing analysis period is unresolved")
        return cls(
            fiscal_year=period.fiscal_year,
            fiscal_quarter=period.fiscal_quarter,
            analysis_kind=request.bundle.analysis_kind,
        )

    @classmethod
    def from_record(
        cls,
        record: "StoredCorporateFilingsAnalysis",
    ) -> "CorporateFilingsAnalysisPeriodKey":
        period = record.period
        if period.fiscal_year is None or period.fiscal_quarter is None:
            raise ValueError("stored corporate filing period is unresolved")
        return cls(
            fiscal_year=period.fiscal_year,
            fiscal_quarter=period.fiscal_quarter,
            analysis_kind=record.analysis_kind,
        )

    @computed_field
    @property
    def period_key_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class StoredCorporateFilingsAnalysis(FundamentalModel):
    """One immutable, releaseable LLM result without its raw input chunks."""

    schema_version: Literal["jarvis.stored_corporate_filings_analysis.v1"] = (
        "jarvis.stored_corporate_filings_analysis.v1"
    )
    scope: CorporateFilingsAnalysisRepositoryScope
    period: FundamentalFilingPeriod
    analysis_kind: CorporateFilingsAnalysisKind
    reuse_key: CorporateFilingsAnalysisReuseKey
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    filing_package_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=r"^filing_package:[a-f0-9]{64}$",
    )
    filing_package_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    bundle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    skill_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    skill_version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+){2}$")
    skill_sha256: str = Field(pattern=_FINGERPRINT_PATTERN)
    model_configuration_fingerprint: str = Field(
        pattern=_FINGERPRINT_PATTERN
    )
    stored_at: datetime
    result: CorporateFilingsAnalysisResult

    @classmethod
    def from_validated(
        cls,
        *,
        request: CorporateFilingsAnalysisRequest,
        result: CorporateFilingsAnalysisResult,
        stored_at: datetime,
    ) -> "StoredCorporateFilingsAnalysis":
        validate_corporate_filings_analysis_response(request, result)
        if not result.releaseable:
            raise ValueError(
                "only releaseable corporate filing analysis may be stored"
            )
        return cls(
            scope=CorporateFilingsAnalysisRepositoryScope.from_request(request),
            period=request.bundle.period,
            analysis_kind=request.bundle.analysis_kind,
            reuse_key=request.reuse_key,
            request_fingerprint=request.request_fingerprint,
            filing_package_id=request.bundle.filing_package_id,
            filing_package_fingerprint=(
                request.bundle.filing_package_fingerprint
            ),
            bundle_fingerprint=request.bundle.bundle_fingerprint,
            policy_fingerprint=request.policy.policy_fingerprint,
            skill_id=request.policy.skill_id,
            skill_version=request.policy.skill_version,
            skill_sha256=request.policy.skill_sha256,
            model_configuration_fingerprint=(
                request.policy.model_configuration_fingerprint
            ),
            stored_at=stored_at,
            result=result,
        )

    @field_validator("stored_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "corporate filing analysis storage time")

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if (
            self.period.state is not FundamentalFilingPeriodState.RESOLVED
            or self.period.period_type is not FundamentalFilingPeriodType.QUARTERLY
            or self.period.fiscal_year is None
            or self.period.fiscal_quarter is None
        ):
            raise ValueError(
                "stored corporate filing analysis requires a resolved quarter"
            )
        if self.filing_package_id != (
            f"filing_package:{self.filing_package_fingerprint}"
        ):
            raise ValueError("stored filing package identity is inconsistent")
        expected_issuer_fingerprint = _fingerprint(
            self.scope.issuer.model_dump(mode="json")
        )
        expected_reuse_bindings = {
            "tenant_id": self.scope.connection.tenant_id,
            "provider_connection_id": (
                self.scope.connection.provider_connection_id
            ),
            "issuer_fingerprint": expected_issuer_fingerprint,
            "bundle_fingerprint": self.bundle_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "model_configuration_fingerprint": (
                self.model_configuration_fingerprint
            ),
        }
        for field_name, expected in expected_reuse_bindings.items():
            if getattr(self.reuse_key, field_name) != expected:
                raise ValueError(
                    f"stored analysis reuse-key {field_name} is inconsistent"
                )
        expected_result_bindings = {
            "request_fingerprint": self.request_fingerprint,
            "reuse_key_fingerprint": self.reuse_key.reuse_key_fingerprint,
            "bundle_fingerprint": self.bundle_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
        }
        for field_name, expected in expected_result_bindings.items():
            if getattr(self.result, field_name) != expected:
                raise ValueError(
                    f"stored analysis result {field_name} is inconsistent"
                )
        if not self.result.releaseable:
            raise ValueError("stored corporate filing analysis is not releaseable")
        execution = self.result.execution
        if execution is None:
            raise ValueError("stored analysis requires execution metadata")
        if execution.model_configuration_fingerprint != (
            self.model_configuration_fingerprint
        ):
            raise ValueError("stored analysis model identity is inconsistent")
        if self.stored_at < execution.completed_at:
            raise ValueError("analysis cannot be stored before completion")
        return self

    @computed_field
    @property
    def period_key(self) -> CorporateFilingsAnalysisPeriodKey:
        return CorporateFilingsAnalysisPeriodKey.from_record(self)

    @computed_field
    @property
    def record_fingerprint(self) -> str:
        return _fingerprint(
            {
                "schema_version": self.schema_version,
                "scope_fingerprint": self.scope.scope_fingerprint,
                "period_key_fingerprint": (
                    self.period_key.period_key_fingerprint
                ),
                "reuse_key_fingerprint": (
                    self.reuse_key.reuse_key_fingerprint
                ),
                "request_fingerprint": self.request_fingerprint,
                "filing_package_id": self.filing_package_id,
                "bundle_fingerprint": self.bundle_fingerprint,
                "policy_fingerprint": self.policy_fingerprint,
                "skill_id": self.skill_id,
                "skill_version": self.skill_version,
                "skill_sha256": self.skill_sha256,
                "model_configuration_fingerprint": (
                    self.model_configuration_fingerprint
                ),
                "result_fingerprint": self.result.result_fingerprint,
            }
        )

    @computed_field
    @property
    def record_id(self) -> str:
        return f"corporate_filings_analysis:{self.record_fingerprint}"

    def to_storage_json(self) -> str:
        """Serialize only bounded analysis output, never raw source chunks."""

        return self.model_dump_json(exclude_computed_fields=True)

    @classmethod
    def from_storage_json(cls, value: str) -> "StoredCorporateFilingsAnalysis":
        return cls.model_validate_json(value)


class CorporateFilingsAnalysisDeleteStatus(StrEnum):
    DELETED = "deleted"
    NOT_FOUND = "not_found"
    REJECTED_ACTIVE_RESULT = "rejected_active_result"


class CorporateFilingsAnalysisDeleteAuthorization(FundamentalModel):
    """Exact, human-attributed authorization for one cached JSON deletion."""

    schema_version: Literal[
        "jarvis.corporate_filings_analysis_delete_authorization.v1"
    ] = "jarvis.corporate_filings_analysis_delete_authorization.v1"
    audit_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    operation_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    authorized_by: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    record_id: str = Field(pattern=_RECORD_ID_PATTERN)
    record_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    reason: str = Field(min_length=10, max_length=500)
    authorized_at: datetime

    @field_validator("authorized_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "analysis deletion authorization time")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("analysis deletion reason must be a single line")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.record_id != (
            f"corporate_filings_analysis:{self.record_fingerprint}"
        ):
            raise ValueError("analysis deletion must bind the exact record")
        return self


class CorporateFilingsAnalysisDeleteReceipt(FundamentalModel):
    schema_version: Literal[
        "jarvis.corporate_filings_analysis_delete_receipt.v1"
    ] = "jarvis.corporate_filings_analysis_delete_receipt.v1"
    authorization: CorporateFilingsAnalysisDeleteAuthorization
    status: CorporateFilingsAnalysisDeleteStatus
    completed_at: datetime
    sanitized_detail: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    @field_validator("completed_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "analysis deletion completion time")

    @field_validator("sanitized_detail")
    @classmethod
    def validate_detail(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("analysis deletion detail must be a single line")
        return value

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.completed_at < self.authorization.authorized_at:
            raise ValueError("analysis deletion cannot predate authorization")
        if (
            self.status
            is CorporateFilingsAnalysisDeleteStatus.REJECTED_ACTIVE_RESULT
            and self.sanitized_detail is None
        ):
            raise ValueError(
                "rejected active-result deletion requires a safe explanation"
            )
        return self

    @computed_field
    @property
    def deleted(self) -> bool:
        return self.status is CorporateFilingsAnalysisDeleteStatus.DELETED


@runtime_checkable
class CorporateFilingsAnalysisRepository(Protocol):
    """Tenant-isolated immutable cache with CAS active-result publication.

    Implementations must persist the complete validated result before moving
    an active pointer. ``expected_active_record_id=None`` requires no current
    active result; otherwise the exact supplied ID must be active. A conflict,
    failed write or incomplete result leaves the previous active result
    untouched. Raw document chunks and prompts must never be persisted here.
    """

    @property
    def adapter_name(self) -> str:
        ...

    def save_corporate_filings_analysis(
        self,
        record: StoredCorporateFilingsAnalysis,
    ) -> StoredCorporateFilingsAnalysis:
        """Idempotently persist one immutable, releaseable analysis JSON."""
        ...

    def find_corporate_filings_analysis_by_reuse_key(
        self,
        reuse_key: CorporateFilingsAnalysisReuseKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> StoredCorporateFilingsAnalysis | None:
        """Return an exact reusable result visible at the requested time."""
        ...

    def list_corporate_filings_analysis_history(
        self,
        period_key: CorporateFilingsAnalysisPeriodKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> tuple[StoredCorporateFilingsAnalysis, ...]:
        """Return immutable scoped versions for one fiscal-quarter view."""
        ...

    def get_active_corporate_filings_analysis(
        self,
        period_key: CorporateFilingsAnalysisPeriodKey,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        as_of: datetime,
    ) -> StoredCorporateFilingsAnalysis | None:
        """Return the currently active successful result inside its scope."""
        ...

    def commit_corporate_filings_analysis(
        self,
        record: StoredCorporateFilingsAnalysis,
        *,
        expected_active_record_id: str | None,
    ) -> StoredCorporateFilingsAnalysis:
        """Persist and atomically activate one successful result using CAS."""
        ...

    def delete_corporate_filings_analysis(
        self,
        record_id: str,
        *,
        scope: CorporateFilingsAnalysisRepositoryScope,
        authorization: CorporateFilingsAnalysisDeleteAuthorization,
    ) -> CorporateFilingsAnalysisDeleteReceipt:
        """Delete one inactive result after exact audited authorization."""
        ...
