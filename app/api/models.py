from datetime import date, datetime
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.fundamentals.session_provisioning import (
    ProviderSessionLifecycle,
    ProviderSessionRevocationReason,
    ProviderSessionStatus,
)
from app.models.browser_operations import (
    BrowserBenchmarkingFinancialsReference,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationSnapshot,
    BrowserSessionSnapshot,
    BrowserStructuredDocumentReference,
)
from app.models.conversation import InputChannel
from app.models.financial_documents import (
    BenchmarkingCompany,
    BenchmarkingRow,
    FinancialDocumentPeriod,
    FinancialDocumentRow,
    FinancialDocumentType,
    FinancialReportingBasis,
    StructuredFinancialDocument,
    StructuredBenchmarkingFinancialsDocument,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalValidationStatus,
)
from app.models.technical import TechnicalModel


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


class CreateBrowserSessionResponse(TechnicalModel):
    """Session capability returned once to the trusted browser client."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.http_session.v1"] = (
        "jarvis.http_session.v1"
    )
    session: BrowserSessionSnapshot
    access_token: str = Field(min_length=32, max_length=256)


class SubmitBrowserOperationRequest(TechnicalModel):
    """Client-owned idempotency and message; server owns operation identity."""

    # JSON transports send enum values as strings; the boundary converts them
    # into strict domain enums before constructing BrowserOperationRequest.
    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    kind: BrowserOperationKind
    input_channel: InputChannel
    message: str = Field(min_length=1, max_length=2_000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("browser operation message must not be blank")
        return normalized


class BrowserConversationInputRequest(TechnicalModel):
    """One typed message or already-transcribed voice utterance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    input_channel: InputChannel
    text: str = Field(min_length=1, max_length=2_000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("browser conversation input must not be blank")
        return normalized


class SpeechSynthesisRequest(TechnicalModel):
    """Stateless text-to-speech request -- not tied to any turn ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=2_000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("speech synthesis text must not be blank")
        return normalized


class SpeechTranscriptionResponse(TechnicalModel):
    """Transcription result -- transcript is None for no-speech-detected
    audio, which is valid data and returns 200, not an error.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.http_transcription.v1"] = (
        "jarvis.http_transcription.v1"
    )
    transcript: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class BrowserOperationResultResponse(TechnicalModel):
    """Polling response that never claims a result before completion."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.http_operation_result.v1"] = (
        "jarvis.http_operation_result.v1"
    )
    operation: BrowserOperationSnapshot
    output: BrowserOperationOutput | None = None


class BrowserStructuredFinancialDocument(TechnicalModel):
    """Browser-safe complete financial table without provider access scope."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.http_financial_document_payload.v1"] = (
        "jarvis.http_financial_document_payload.v1"
    )
    document_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    issuer: FundamentalIssuerIdentity
    document_type: FinancialDocumentType
    reporting_basis: FinancialReportingBasis
    currency: str | None = None
    source_unit: str = Field(min_length=1, max_length=80)
    skipped_period_labels: tuple[str, ...] = ()
    periods: tuple[FinancialDocumentPeriod, ...]
    rows: tuple[FinancialDocumentRow, ...]
    retrieved_at: datetime
    expires_at: datetime
    all_sections_expanded: Literal[True]
    validation_status: FundamentalValidationStatus
    limitations: tuple[str, ...] = ()
    document_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @classmethod
    def from_document(
        cls,
        document: StructuredFinancialDocument,
    ) -> "BrowserStructuredFinancialDocument":
        if not isinstance(document, StructuredFinancialDocument):
            raise TypeError("browser financial payload requires a document")
        return cls(
            document_id=document.document_id,
            issuer=document.issuer,
            document_type=document.document_type,
            reporting_basis=document.reporting_basis,
            currency=document.currency,
            source_unit=document.source_unit,
            skipped_period_labels=document.skipped_period_labels,
            periods=document.periods,
            rows=document.rows,
            retrieved_at=document.retrieved_at,
            expires_at=document.expires_at,
            all_sections_expanded=document.all_sections_expanded,
            validation_status=document.validation_status,
            limitations=document.limitations,
            document_fingerprint=document.document_fingerprint,
        )


class BrowserStructuredFinancialDocumentResponse(TechnicalModel):
    """Authenticated resolution of one released operation reference."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.http_financial_document.v1"] = (
        "jarvis.http_financial_document.v1"
    )
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    reference: BrowserStructuredDocumentReference
    document: BrowserStructuredFinancialDocument

    @model_validator(mode="after")
    def require_reference_document_match(self):
        document = self.document
        reference = self.reference
        if (
            document.document_id != reference.document_id
            or document.document_type is not reference.document_type
            or document.reporting_basis is not reference.reporting_basis
            or document.document_fingerprint
            != reference.document_fingerprint
            or document.issuer.exchange != reference.exchange
            or document.issuer.symbol != reference.symbol
        ):
            raise ValueError("financial document does not match its reference")
        return self


class BrowserBenchmarkingFinancialsDocument(TechnicalModel):
    """Browser-safe complete Financial benchmark matrix."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[
        "jarvis.http_benchmarking_financials_payload.v1"
    ] = "jarvis.http_benchmarking_financials_payload.v1"
    document_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    issuer: FundamentalIssuerIdentity
    observation_date: date
    reporting_basis: Literal["not_applicable"] = "not_applicable"
    companies: tuple[BenchmarkingCompany, ...] = Field(
        min_length=2,
        max_length=30,
    )
    rows: tuple[BenchmarkingRow, ...] = Field(
        min_length=1,
        max_length=300,
    )
    retrieved_at: datetime
    expires_at: datetime
    all_rows_captured: Literal[True]
    validation_status: FundamentalValidationStatus
    limitations: tuple[str, ...] = Field(default=(), max_length=100)
    document_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @classmethod
    def from_document(
        cls,
        document: StructuredBenchmarkingFinancialsDocument,
    ) -> "BrowserBenchmarkingFinancialsDocument":
        if not isinstance(document, StructuredBenchmarkingFinancialsDocument):
            raise TypeError("browser benchmarking payload requires a document")
        return cls(
            document_id=document.document_id,
            issuer=document.issuer,
            observation_date=document.observation_date,
            reporting_basis=document.reporting_basis,
            companies=document.companies,
            rows=document.rows,
            retrieved_at=document.retrieved_at,
            expires_at=document.expires_at,
            all_rows_captured=document.all_rows_captured,
            validation_status=document.validation_status,
            limitations=document.limitations,
            document_fingerprint=document.document_fingerprint,
        )


class BrowserBenchmarkingFinancialsDocumentResponse(TechnicalModel):
    """Authenticated resolution of one released benchmarking reference."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[
        "jarvis.http_benchmarking_financials.v1"
    ] = "jarvis.http_benchmarking_financials.v1"
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    reference: BrowserBenchmarkingFinancialsReference
    document: BrowserBenchmarkingFinancialsDocument

    @model_validator(mode="after")
    def require_reference_document_match(self):
        document = self.document
        reference = self.reference
        if (
            document.document_id != reference.document_id
            or document.reporting_basis != reference.reporting_basis
            or document.document_fingerprint
            != reference.document_fingerprint
            or document.observation_date != reference.observation_date
            or document.issuer.exchange != reference.exchange
            or document.issuer.symbol != reference.symbol
            or len(document.companies) != reference.company_count
            or len(document.rows) != reference.row_count
        ):
            raise ValueError(
                "benchmarking document does not match its reference"
            )
        return self


class ProviderSessionTargetRequest(TechnicalModel):
    """Non-secret provider connection identity supplied by the browser."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    account_reference_hash: str | None = Field(
        default=None,
        pattern=_FINGERPRINT_PATTERN,
    )


class ProviderSessionStatusRequest(TechnicalModel):
    """Secret-free target for an authenticated status request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: ProviderSessionTargetRequest


class ProvisionProviderSessionRequest(TechnicalModel):
    """Explicit browser authorization for an interactive login window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    target: ProviderSessionTargetRequest
    user_interaction_authorized: bool
    replace_existing: bool = False

    @field_validator("user_interaction_authorized", mode="before")
    @classmethod
    def require_explicit_authorization(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("interactive session requires explicit authorization")
        return True

    @field_validator("replace_existing", mode="before")
    @classmethod
    def require_boolean_replacement_flag(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise ValueError("session replacement flag must be boolean")
        return value


class RevokeProviderSessionRequest(TechnicalModel):
    """Explicit secure-revocation command; insecure deletion is unsupported."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    target: ProviderSessionTargetRequest
    reason: ProviderSessionRevocationReason
    secure_delete_required: bool = True

    @field_validator("secure_delete_required", mode="before")
    @classmethod
    def require_secure_deletion(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("provider session revocation must be secure")
        return True


class ProviderSessionLifecycleResponse(TechnicalModel):
    """Browser-safe lifecycle projection with no tenant or session reference."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.http_provider_session.v1"] = (
        "jarvis.http_provider_session.v1"
    )
    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    status: ProviderSessionStatus
    checked_at: datetime
    provisioned_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revocation_reason: ProviderSessionRevocationReason | None = None
    lifecycle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @field_validator(
        "checked_at",
        "provisioned_at",
        "expires_at",
        "revoked_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("provider session timestamps require timezone")
        return value

    @model_validator(mode="after")
    def require_status_consistency(self) -> Self:
        if self.status is ProviderSessionStatus.UNCONFIGURED:
            if any(
                value is not None
                for value in (
                    self.provisioned_at,
                    self.expires_at,
                    self.revoked_at,
                    self.revocation_reason,
                )
            ):
                raise ValueError("unconfigured session has lifecycle state")
            return self
        if self.provisioned_at is None or self.checked_at < self.provisioned_at:
            raise ValueError("configured session requires a valid provision time")
        if self.status is ProviderSessionStatus.READY:
            if self.revoked_at is not None or self.revocation_reason is not None:
                raise ValueError("ready session cannot be revoked")
            if self.expires_at is not None and self.checked_at >= self.expires_at:
                raise ValueError("ready session cannot be expired")
        elif self.status is ProviderSessionStatus.EXPIRED:
            if self.expires_at is None or self.checked_at < self.expires_at:
                raise ValueError("expired session requires reached expiry")
            if self.revoked_at is not None or self.revocation_reason is not None:
                raise ValueError("expired session cannot be revoked")
        elif self.status is ProviderSessionStatus.REVOKED:
            if self.revoked_at is None or self.revocation_reason is None:
                raise ValueError("revoked session requires time and reason")
            if self.revoked_at < self.provisioned_at:
                raise ValueError("revocation cannot predate provisioning")
        return self

    @classmethod
    def from_lifecycle(
        cls,
        lifecycle: ProviderSessionLifecycle,
    ) -> "ProviderSessionLifecycleResponse":
        if not isinstance(lifecycle, ProviderSessionLifecycle):
            raise TypeError("provider session response requires lifecycle data")
        return cls(
            provider_connection_id=lifecycle.connection.provider_connection_id,
            provider=lifecycle.connection.provider,
            status=lifecycle.status,
            checked_at=lifecycle.checked_at,
            provisioned_at=lifecycle.provisioned_at,
            expires_at=lifecycle.expires_at,
            revoked_at=lifecycle.revoked_at,
            revocation_reason=lifecycle.revocation_reason,
            lifecycle_fingerprint=lifecycle.lifecycle_fingerprint,
        )
