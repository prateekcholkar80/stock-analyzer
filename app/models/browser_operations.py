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
from app.models.fundamental_analysis import FundamentalAnalysis
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
from app.models.workflow import (
    JarvisWorkflowEvent,
    WorkflowEventState,
    WorkflowParticipantKind,
    WorkflowStage,
)


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


class BrowserAgentExecutionStatus(StrEnum):
    """Terminal disposition of one independently auditable agent invocation."""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED_DEPENDENCY = "skipped_dependency"
    NOT_STARTED = "not_started"


class BrowserAgentExecutionOutcome(TechnicalModel):
    """Immutable, secret-safe outcome for one agent invocation."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.agent_execution_outcome.v1"] = (
        "jarvis.agent_execution_outcome.v1"
    )
    invocation_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_ID_PATTERN,
    )
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    activity_id: str = Field(min_length=1, max_length=160)
    participant_id: str = Field(min_length=1, max_length=160)
    participant_kind: WorkflowParticipantKind
    participant_label: str = Field(min_length=1, max_length=80)
    stage: WorkflowStage
    status: BrowserAgentExecutionStatus
    recorded_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    round_number: int | None = Field(default=None, ge=1)
    depends_on: tuple[str, ...] = Field(default=(), max_length=16)
    failure: BrowserOperationFailure | None = None
    result_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=_ID_PATTERN,
    )

    @field_validator("recorded_at", "started_at", "completed_at")
    @classmethod
    def require_optional_ist(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != IST_OFFSET
        ):
            raise ValueError("agent outcome timestamps must be in IST")
        return value

    @field_validator(
        "activity_id",
        "participant_id",
        "participant_label",
        "depends_on",
    )
    @classmethod
    def normalize_agent_text(cls, value):
        if isinstance(value, tuple):
            normalized = tuple(item.strip() for item in value)
            if any(not item for item in normalized):
                raise ValueError("agent outcome dependencies must not be blank")
            return normalized
        normalized = value.strip()
        if not normalized:
            raise ValueError("agent outcome text must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_terminal_outcome(self) -> Self:
        supported_kinds = {
            WorkflowParticipantKind.ANALYST,
            WorkflowParticipantKind.ADVOCATE,
            WorkflowParticipantKind.JUDGE,
            WorkflowParticipantKind.PRESENTER,
        }
        if self.participant_kind not in supported_kinds:
            raise ValueError("agent outcome requires an agent participant")
        expected_invocation_id = self.participant_id
        debate_stages = {
            WorkflowStage.BULL_DEBATING,
            WorkflowStage.BEAR_DEBATING,
        }
        if self.stage in debate_stages:
            if self.round_number is None:
                raise ValueError("Bull/Bear agent outcome requires its round")
            expected_invocation_id = (
                f"{expected_invocation_id}:round:{self.round_number}"
            )
        elif self.round_number is not None:
            raise ValueError("only Bull/Bear agent outcomes have a round")
        if self.invocation_id != expected_invocation_id:
            raise ValueError(
                "agent invocation ID must match participant identity and round"
            )
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("agent outcome dependencies must be unique")
        if self.invocation_id in self.depends_on:
            raise ValueError("agent outcome cannot depend on itself")

        terminal = self.status in {
            BrowserAgentExecutionStatus.COMPLETED,
            BrowserAgentExecutionStatus.FAILED,
        }
        if terminal:
            if self.started_at is None or self.completed_at is None:
                raise ValueError(
                    "completed or failed agent outcome requires timestamps"
                )
            if self.completed_at < self.started_at:
                raise ValueError("agent outcome cannot complete before start")
        elif self.started_at is not None:
            raise ValueError("unstarted agent outcome cannot have a start time")

        if self.completed_at is not None and self.recorded_at < self.completed_at:
            raise ValueError("agent outcome cannot be recorded before completion")
        if self.status is BrowserAgentExecutionStatus.FAILED:
            if self.failure is None:
                raise ValueError("failed agent outcome requires a safe failure")
            if self.result_reference is not None:
                raise ValueError("failed agent outcome cannot expose a result")
        elif self.failure is not None:
            raise ValueError("only failed agent outcome may contain a failure")
        if self.status is BrowserAgentExecutionStatus.SKIPPED_DEPENDENCY:
            if self.completed_at is None or not self.depends_on:
                raise ValueError(
                    "dependency-skipped agent outcome requires time and dependency"
                )
            if self.result_reference is not None:
                raise ValueError("skipped agent outcome cannot expose a result")
        if self.status is BrowserAgentExecutionStatus.NOT_STARTED:
            if self.completed_at is not None or self.result_reference is not None:
                raise ValueError("not-started agent outcome cannot be completed")
        return self


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


class BrowserFundamentalPackageReference(TechnicalModel):
    """Browser-safe identity for one atomically committed evidence package."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal[
        "jarvis.browser_fundamental_package_ref.v1"
    ] = "jarvis.browser_fundamental_package_ref.v1"
    package_id: str = Field(
        pattern=r"^fundamental_package:[a-f0-9]{64}$"
    )
    manifest_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "fundamental package reference expiry requires timezone"
            )
        return value

    @model_validator(mode="after")
    def validate_identity_binding(self) -> Self:
        if self.package_id != (
            f"fundamental_package:{self.manifest_fingerprint}"
        ):
            raise ValueError(
                "fundamental package ID must match its manifest fingerprint"
            )
        return self


class BrowserFundamentalPackagePublicationOutcome(TechnicalModel):
    """Audit evidence that a complete refresh became the active package."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal[
        "jarvis.browser_fundamental_package_publication.v1"
    ] = "jarvis.browser_fundamental_package_publication.v1"
    status: Literal["committed"] = "committed"
    reference: BrowserFundamentalPackageReference


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
    decision_failure: BrowserOperationFailure | None = None
    fundamental_failure: BrowserOperationFailure | None = None
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
    fundamental_package_reference: (
        BrowserFundamentalPackageReference | None
    ) = None
    fundamental_analysis: FundamentalAnalysis | None = None

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
            has_analysis = self.research_response.result is not None
            has_explanation = self.research_explanation is not None
            has_presentation_failure = self.presentation_failure is not None
            if has_analysis:
                if self.decision_failure is not None:
                    raise ValueError(
                        "successful swing output cannot contain a decision "
                        "failure"
                    )
                if has_explanation == has_presentation_failure:
                    raise ValueError(
                        "swing output requires either an explanation or a "
                        "presentation failure"
                    )
            else:
                llm_failure = self.research_response.failure
                if llm_failure is None or self.decision_failure is None:
                    raise ValueError(
                        "swing output without a conclusion requires its "
                        "decision failure"
                    )
                if has_explanation or has_presentation_failure:
                    raise ValueError(
                        "swing output without a conclusion cannot contain "
                        "presentation fields"
                    )
                expected_code = f"llm.{llm_failure.code.value}"
                if (
                    self.decision_failure.code != expected_code
                    or self.decision_failure.message
                    != llm_failure.display_message
                    or self.decision_failure.retryable
                    is not llm_failure.retryable
                ):
                    raise ValueError(
                        "decision failure must match the Jarvis LLM failure"
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
            if self.fundamental_analysis is not None:
                available_references = set(cache_entries)
                if self.benchmarking_financials_reference is not None:
                    available_references.add(
                        self.benchmarking_financials_reference.cache_entry_id
                    )
                cited_references = set(
                    self.fundamental_analysis.document_citations
                )
                if not cited_references.issubset(available_references):
                    raise ValueError(
                        "fundamental analysis may cite only exposed cached "
                        "document references"
                    )

                referenced_subjects = {
                    (item.exchange, item.symbol)
                    for item in self.structured_document_references
                    if item.cache_entry_id in cited_references
                }
                if (
                    self.benchmarking_financials_reference is not None
                    and self.benchmarking_financials_reference.cache_entry_id
                    in cited_references
                ):
                    referenced_subjects.add(
                        (
                            self.benchmarking_financials_reference.exchange,
                            self.benchmarking_financials_reference.symbol,
                        )
                    )
                if len(referenced_subjects) != 1:
                    raise ValueError(
                        "fundamental analysis citations must identify one "
                        "issuer"
                    )
            if (
                self.fundamental_package_reference is not None
                and self.fundamental_package_reference.expires_at
                <= self.completed_at
            ):
                raise ValueError(
                    "swing output cannot expose an expired fundamental package"
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
                    self.decision_failure,
                    self.fundamental_failure,
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
            if self.fundamental_package_reference is not None:
                raise ValueError(
                    "follow-up output cannot contain a fundamental package"
                )
            if self.fundamental_analysis is not None:
                raise ValueError(
                    "follow-up output cannot contain fundamental analysis"
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


class BrowserOperationAuditLog(TechnicalModel):
    """Complete immutable event and result record for one operation ID."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_operation_audit.v1"] = (
        "jarvis.browser_operation_audit.v1"
    )
    operation: BrowserOperationSnapshot
    events: tuple[JarvisWorkflowEvent, ...] = ()
    agent_outcomes: tuple[BrowserAgentExecutionOutcome, ...] = Field(
        default=(),
        max_length=64,
    )
    fundamental_package_publication: (
        BrowserFundamentalPackagePublicationOutcome | None
    ) = None
    result: BrowserOperationOutput | None = None

    @model_validator(mode="after")
    def validate_complete_operation_history(self) -> Self:
        operation_id = self.operation.request.operation_id
        if len(self.events) != self.operation.last_event_sequence:
            raise ValueError(
                "operation audit must contain its complete event history"
            )
        for expected_sequence, event in enumerate(self.events, start=1):
            if event.operation_id != operation_id:
                raise ValueError("operation audit cannot mix operation IDs")
            if event.sequence != expected_sequence:
                raise ValueError(
                    "operation audit event sequence must be contiguous"
                )
            if event.occurred_at > self.operation.updated_at:
                raise ValueError(
                    "operation audit event cannot follow operation state"
                )
        outcome_by_id = {
            outcome.invocation_id: outcome for outcome in self.agent_outcomes
        }
        if len(outcome_by_id) != len(self.agent_outcomes):
            raise ValueError("operation audit agent invocations must be unique")
        for outcome in self.agent_outcomes:
            if outcome.operation_id != operation_id:
                raise ValueError("operation audit cannot mix agent outcomes")
            if outcome.recorded_at > self.operation.updated_at:
                raise ValueError("agent outcome cannot follow operation state")
            missing_dependencies = set(outcome.depends_on) - outcome_by_id.keys()
            if missing_dependencies:
                raise ValueError(
                    "agent outcome dependencies must exist in the same ledger"
                )
            matching_events = tuple(
                event
                for event in self.events
                if event.activity.participant_id == outcome.participant_id
                and event.stage is outcome.stage
                and event.round_number == outcome.round_number
            )
            if outcome.status in {
                BrowserAgentExecutionStatus.COMPLETED,
                BrowserAgentExecutionStatus.FAILED,
            }:
                expected_terminal_state = (
                    WorkflowEventState.COMPLETED
                    if outcome.status is BrowserAgentExecutionStatus.COMPLETED
                    else WorkflowEventState.FAILED
                )
                started = tuple(
                    event
                    for event in matching_events
                    if event.state is WorkflowEventState.STARTED
                )
                terminal_events = tuple(
                    event
                    for event in matching_events
                    if event.state is expected_terminal_state
                )
                if (
                    len(started) != 1
                    or len(terminal_events) != 1
                    or outcome.started_at != started[0].occurred_at
                    or outcome.completed_at != terminal_events[0].occurred_at
                ):
                    raise ValueError(
                        "agent outcome must match its atomic workflow events"
                    )
            elif matching_events:
                raise ValueError(
                    "unstarted agent outcome cannot have workflow events"
                )
            if outcome.status is BrowserAgentExecutionStatus.SKIPPED_DEPENDENCY:
                dependency_states = {
                    outcome_by_id[item].status for item in outcome.depends_on
                }
                if not dependency_states.intersection(
                    {
                        BrowserAgentExecutionStatus.FAILED,
                        BrowserAgentExecutionStatus.SKIPPED_DEPENDENCY,
                        BrowserAgentExecutionStatus.NOT_STARTED,
                    }
                ):
                    raise ValueError(
                        "dependency-skipped agent requires an unavailable dependency"
                    )
        if self.operation.result_available != (self.result is not None):
            raise ValueError(
                "operation audit retained-result availability must match"
            )
        if self.result is not None and (
            self.result.operation_id != operation_id
            or self.result.session_id != self.operation.request.session_id
            or self.result.kind is not self.operation.request.kind
        ):
            raise ValueError(
                "operation audit result identity must match its operation"
            )
        expected_package = (
            self.result.fundamental_package_reference
            if self.result is not None
            else None
        )
        published_package = self.fundamental_package_publication
        if (published_package is None) != (expected_package is None):
            raise ValueError(
                "operation audit package publication must match its result"
            )
        if published_package is not None:
            if not (
                self.operation.request.fundamentals_requested
                and self.operation.request.refresh_requested
            ):
                raise ValueError(
                    "package publication requires an explicit fundamental refresh"
                )
            if published_package.reference != expected_package:
                raise ValueError(
                    "operation audit package publication changed its reference"
                )
        return self
