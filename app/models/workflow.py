from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.technical import TechnicalModel


IST_OFFSET = timedelta(hours=5, minutes=30)


class WorkflowStage(StrEnum):
    REQUEST_RECEIVED = "request_received"
    INSTRUMENT_RESOLVED = "instrument_resolved"
    MARKET_DATA_LOADING = "market_data_loading"
    TECHNICAL_ANALYSIS = "technical_analysis"
    DATA_PREPARATION = "data_preparation"
    ANALYSIS = "analysis"
    EVIDENCE_REVIEW = "evidence_review"
    BULL_DEBATING = "bull_debating"
    BEAR_DEBATING = "bear_debating"
    JUDGE_REVIEWING = "judge_reviewing"
    TRADE_PLANNING = "trade_planning"
    PRESENTATION = "presentation"
    FOLLOW_UP = "follow_up"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowEventState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowParticipantKind(StrEnum):
    """Stable visual category for current and future workflow participants."""

    SYSTEM = "system"
    ORCHESTRATOR = "orchestrator"
    SERVICE = "service"
    ANALYST = "analyst"
    ADVOCATE = "advocate"
    JUDGE = "judge"
    PRESENTER = "presenter"


class WorkflowActivityDescriptor(TechnicalModel):
    """Extensible identity used by a UI without hard-coding every agent."""

    model_config = ConfigDict(frozen=True, strict=True)

    activity_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$",
    )
    participant_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$",
    )
    participant_kind: WorkflowParticipantKind
    participant_label: str = Field(min_length=1, max_length=80)
    timeframe: str | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("activity_id", "participant_id")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != value:
            raise ValueError("workflow identifiers must already be normalized")
        return normalized

    @field_validator("participant_label", "timeframe")
    @classmethod
    def normalize_descriptor_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("workflow descriptor text must not be blank")
        return normalized


class JarvisWorkflowEvent(TechnicalModel):
    """Secret-safe progress evidence delivered to voice and UI clients."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.workflow_event.v2"] = (
        "jarvis.workflow_event.v2"
    )
    event_id: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    stage: WorkflowStage
    state: WorkflowEventState
    occurred_at: datetime
    message: str = Field(min_length=1, max_length=300)
    activity: WorkflowActivityDescriptor
    exchange: str | None = Field(default=None, min_length=1, max_length=32)
    symbol: str | None = Field(default=None, min_length=1, max_length=100)
    round_number: int | None = Field(default=None, ge=1)

    @field_validator(
        "event_id",
        "operation_id",
        "message",
        "exchange",
        "symbol",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("workflow event text must not be blank")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def require_ist_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != IST_OFFSET:
            raise ValueError("workflow event timestamp must be in IST")
        return value

    @model_validator(mode="after")
    def validate_stage_contract(self) -> Self:
        expected_event_id = f"{self.operation_id}:{self.sequence}"
        if self.event_id != expected_event_id:
            raise ValueError(
                "workflow event ID must match its operation and sequence"
            )
        debate_stages = {
            WorkflowStage.BULL_DEBATING,
            WorkflowStage.BEAR_DEBATING,
        }
        if (self.stage in debate_stages) != (self.round_number is not None):
            raise ValueError(
                "only Bull/Bear workflow events require a round number"
            )
        if (
            self.stage is WorkflowStage.COMPLETED
            and self.state is not WorkflowEventState.COMPLETED
        ):
            raise ValueError("completed workflow stage must be completed")
        if (
            self.stage is WorkflowStage.FAILED
            and self.state is not WorkflowEventState.FAILED
        ):
            raise ValueError("failed workflow stage must be failed")
        return self
