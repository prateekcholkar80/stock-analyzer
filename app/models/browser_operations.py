from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.conversation import InputChannel
from app.models.debate import JudgeFollowUpAnswer
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.presentation import (
    JarvisMultiTimeframeResearchExplanation,
    JarvisResearchExplanation,
)
from app.models.technical import TechnicalModel
from app.services.fundamental_evidence import (
    FundamentalEvidenceLoadResult,
    FundamentalEvidenceSource,
)
from app.models.workflow import JarvisWorkflowEvent


IST_OFFSET = timedelta(hours=5, minutes=30)
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"


class BrowserSessionState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class BrowserOperationKind(StrEnum):
    SWING_ANALYSIS = "swing_analysis"
    JUDGE_FOLLOW_UP = "judge_follow_up"


class BrowserOperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLATION_REQUESTED = "cancellation_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            BrowserOperationStatus.COMPLETED,
            BrowserOperationStatus.FAILED,
            BrowserOperationStatus.CANCELLED,
        }


class BrowserSessionSnapshot(TechnicalModel):
    """Immutable browser-session state independent of HTTP or WebSocket."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_session.v1"] = (
        "jarvis.browser_session.v1"
    )
    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    state: BrowserSessionState = BrowserSessionState.OPEN
    created_at: datetime
    updated_at: datetime
    active_operation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_ist(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != IST_OFFSET:
            raise ValueError("browser session timestamps must be in IST")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("browser session update cannot precede creation")
        if (
            self.state is BrowserSessionState.CLOSED
            and self.active_operation_id is not None
        ):
            raise ValueError("closed browser session cannot have active work")
        return self


class BrowserOperationRequest(TechnicalModel):
    """One idempotent unit of work accepted from a browser session."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_operation_request.v1"] = (
        "jarvis.browser_operation_request.v1"
    )
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    kind: BrowserOperationKind
    input_channel: InputChannel
    message: str = Field(min_length=1, max_length=2_000)
    fundamentals_requested: bool = False
    refresh_requested: bool = False
    requested_at: datetime

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("browser operation message must not be blank")
        return normalized

    @field_validator("requested_at")
    @classmethod
    def require_ist(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != IST_OFFSET:
            raise ValueError("browser operation request time must be in IST")
        return value

    @model_validator(mode="after")
    def require_fundamentals_for_refresh(self) -> Self:
        if self.refresh_requested and not self.fundamentals_requested:
            raise ValueError(
                "fundamental refresh requires fundamental research"
            )
        return self

    @property
    def idempotent_payload(self) -> tuple[object, ...]:
        return (
            self.session_id,
            self.idempotency_key,
            self.kind,
            self.input_channel,
            self.message,
            self.fundamentals_requested,
            self.refresh_requested,
        )


class BrowserOperationFailure(TechnicalModel):
    """Secret-safe terminal failure exposed to the browser."""

    model_config = ConfigDict(frozen=True, strict=True)

    code: str = Field(min_length=1, max_length=80, pattern=_ID_PATTERN)
    message: str = Field(min_length=1, max_length=300)
    retryable: bool = False

    @field_validator("code", "message")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("browser operation failure text must not be blank")
        return normalized


class BrowserOperationSnapshot(TechnicalModel):
    """Current operation state returned by polling and event transports."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_operation.v1"] = (
        "jarvis.browser_operation.v1"
    )
    request: BrowserOperationRequest
    status: BrowserOperationStatus
    updated_at: datetime
    last_event_sequence: int = Field(default=0, ge=0)
    result_available: bool = False
    cancellation_requested_at: datetime | None = None
    failure: BrowserOperationFailure | None = None

    @field_validator("updated_at", "cancellation_requested_at")
    @classmethod
    def require_optional_ist(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != IST_OFFSET
        ):
            raise ValueError("browser operation timestamps must be in IST")
        return value

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.updated_at < self.request.requested_at:
            raise ValueError("operation update cannot precede its request")
        required_cancellation_states = {
            BrowserOperationStatus.CANCELLATION_REQUESTED,
            BrowserOperationStatus.CANCELLED,
        }
        if (
            self.status in required_cancellation_states
            and self.cancellation_requested_at is None
        ):
            raise ValueError(
                "cancellation time must match a cancellation state"
            )
        if (
            self.cancellation_requested_at is not None
            and self.status
            in {BrowserOperationStatus.QUEUED, BrowserOperationStatus.RUNNING}
        ):
            raise ValueError(
                "pending operation cannot carry cancellation history"
            )
        if (
            self.cancellation_requested_at is not None
            and self.cancellation_requested_at < self.request.requested_at
        ):
            raise ValueError("cancellation cannot precede the request")
        if self.status is BrowserOperationStatus.COMPLETED:
            if not self.result_available or self.failure is not None:
                raise ValueError(
                    "completed operation requires only an available result"
                )
        elif self.result_available:
            raise ValueError("only completed operations may expose a result")
        if self.status is BrowserOperationStatus.FAILED:
            if self.failure is None:
                raise ValueError("failed operation requires a safe failure")
        elif self.failure is not None:
            raise ValueError("only failed operations may expose a failure")
        return self


class BrowserStructuredDocumentReference(TechnicalModel):
    """Safe browser reference to one validated cached financial document."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_structured_document_ref.v1"] = (
        "jarvis.browser_structured_document_ref.v1"
    )
    cache_entry_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_ID_PATTERN,
    )
    document_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    document_type: FinancialDocumentType
    reporting_basis: FinancialReportingBasis
    exchange: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    symbol: str = Field(min_length=1, max_length=100, pattern=_ID_PATTERN)
    source: FundamentalEvidenceSource
    document_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    retrieved_at: datetime
    stored_at: datetime
    expires_at: datetime
    all_sections_expanded: Literal[True]

    @field_validator("exchange", "symbol", mode="before")
    @classmethod
    def normalize_market_identity(cls, value: str) -> str:
        return value.upper()

    @field_validator("retrieved_at", "stored_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "structured document reference timestamps require timezone"
            )
        return value

    @model_validator(mode="after")
    def validate_freshness_window(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError(
                "structured document reference expiry must follow retrieval"
            )
        if self.stored_at >= self.expires_at:
            raise ValueError(
                "structured document reference must identify an active cache"
            )
        return self


class BrowserBenchmarkingFinancialsReference(TechnicalModel):
    """Safe browser reference to one cached Financial benchmark matrix."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal[
        "jarvis.browser_benchmarking_financials_ref.v1"
    ] = "jarvis.browser_benchmarking_financials_ref.v1"
    cache_entry_id: str = Field(
        pattern=r"^benchmarking_financials:[a-f0-9]{64}$"
    )
    document_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    document_type: Literal["benchmarking_financials"] = (
        "benchmarking_financials"
    )
    reporting_basis: Literal["not_applicable"] = "not_applicable"
    exchange: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    symbol: str = Field(min_length=1, max_length=100, pattern=_ID_PATTERN)
    source: FundamentalEvidenceSource
    document_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_date: date
    retrieved_at: datetime
    stored_at: datetime
    expires_at: datetime
    all_rows_captured: Literal[True]
    company_count: int = Field(ge=2, le=30)
    row_count: int = Field(ge=1, le=300)

    @field_validator("exchange", "symbol", mode="before")
    @classmethod
    def normalize_market_identity(cls, value: str) -> str:
        return value.upper()

    @field_validator("retrieved_at", "stored_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "Benchmarking Financials reference timestamps require timezone"
            )
        return value

    @model_validator(mode="after")
    def validate_freshness_window(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError(
                "Benchmarking Financials reference expiry must follow retrieval"
            )
        if self.stored_at < self.retrieved_at:
            raise ValueError(
                "Benchmarking Financials reference cannot predate retrieval"
            )
        if self.stored_at >= self.expires_at:
            raise ValueError(
                "Benchmarking Financials reference must identify active cache"
            )
        return self


class BrowserOperationOutput(TechnicalModel):
    """Immutable result fetched after one browser operation completes."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_operation_output.v1"] = (
        "jarvis.browser_operation_output.v1"
    )
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    kind: BrowserOperationKind
    completed_at: datetime
    research_response: JarvisSwingAnalysisResponse | None = None
    research_explanation: (
        JarvisResearchExplanation
        | JarvisMultiTimeframeResearchExplanation
        | None
    ) = None
    judge_follow_up: JudgeFollowUpAnswer | None = None
    presentation_failure: BrowserOperationFailure | None = None
    fundamental_evidence: tuple[FundamentalEvidenceLoadResult, ...] = Field(
        default=(),
        max_length=3,
    )
    structured_document_references: tuple[
        BrowserStructuredDocumentReference,
        ...,
    ] = Field(default=(), max_length=11)
    benchmarking_financials_reference: (
        BrowserBenchmarkingFinancialsReference | None
    ) = None

    @field_validator("completed_at")
    @classmethod
    def require_completed_at_ist(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != IST_OFFSET:
            raise ValueError("browser operation completion time must be in IST")
        return value

    @model_validator(mode="after")
    def validate_kind_payload(self) -> Self:
        if self.kind is BrowserOperationKind.SWING_ANALYSIS:
            if self.research_response is None:
                raise ValueError("swing analysis output requires its response")
            if self.research_response.operation_id != self.operation_id:
                raise ValueError("research response operation ID must match")
            if self.research_response.result is None:
                raise ValueError("completed swing output requires analysis")
            has_explanation = self.research_explanation is not None
            has_presentation_failure = self.presentation_failure is not None
            if has_explanation == has_presentation_failure:
                raise ValueError(
                    "swing output requires either an explanation or a "
                    "presentation failure"
                )
            if self.judge_follow_up is not None:
                raise ValueError("swing output cannot contain a follow-up")
            capabilities = tuple(
                item.retrieval.capability for item in self.fundamental_evidence
            )
            if len(capabilities) != len(set(capabilities)):
                raise ValueError(
                    "swing output fundamental capabilities must be unique"
                )
            scenarios = tuple(
                (item.document_type, item.reporting_basis)
                for item in self.structured_document_references
            )
            if len(scenarios) != len(set(scenarios)):
                raise ValueError(
                    "swing output structured document scenarios must be unique"
                )
            cache_entries = tuple(
                item.cache_entry_id
                for item in self.structured_document_references
            )
            if len(cache_entries) != len(set(cache_entries)):
                raise ValueError(
                    "swing output structured cache references must be unique"
                )
            if (
                self.benchmarking_financials_reference is not None
                and self.benchmarking_financials_reference.cache_entry_id
                in cache_entries
            ):
                raise ValueError(
                    "Benchmarking and financial document cache references "
                    "must be distinct"
                )
        else:
            if self.judge_follow_up is None:
                raise ValueError("follow-up output requires a Judge answer")
            if any(
                value is not None
                for value in (
                    self.research_response,
                    self.research_explanation,
                    self.presentation_failure,
                )
            ):
                raise ValueError(
                    "follow-up output cannot contain swing-analysis fields"
                )
            if self.fundamental_evidence:
                raise ValueError(
                    "follow-up output cannot contain fundamental evidence"
                )
            if self.structured_document_references:
                raise ValueError(
                    "follow-up output cannot contain structured documents"
                )
            if self.benchmarking_financials_reference is not None:
                raise ValueError(
                    "follow-up output cannot contain Benchmarking Financials"
                )
        return self


class WorkflowEventReplayCursor(TechnicalModel):
    """Request events strictly after one acknowledged sequence."""

    model_config = ConfigDict(frozen=True, strict=True)

    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class WorkflowEventBatch(TechnicalModel):
    """Ordered reconnect/replay page for one operation."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.workflow_event_batch.v1"] = (
        "jarvis.workflow_event_batch.v1"
    )
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    after_sequence: int = Field(ge=0)
    events: tuple[JarvisWorkflowEvent, ...] = ()
    next_sequence: int = Field(ge=0)
    has_more: bool

    @model_validator(mode="after")
    def validate_event_page(self) -> Self:
        expected = self.after_sequence
        for event in self.events:
            if event.operation_id != self.operation_id:
                raise ValueError("event batch cannot mix operations")
            if event.sequence <= expected:
                raise ValueError("event batch must be strictly ordered")
            expected = event.sequence
        if self.next_sequence != expected:
            raise ValueError("event batch cursor must match its last event")
        return self
