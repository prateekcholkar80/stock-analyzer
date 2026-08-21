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
    BULL_DEBATING = "bull_debating"
    BEAR_DEBATING = "bear_debating"
    JUDGE_REVIEWING = "judge_reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowEventState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class JarvisWorkflowEvent(TechnicalModel):
    """Secret-safe progress evidence delivered to voice and UI clients."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.workflow_event.v1"] = (
        "jarvis.workflow_event.v1"
    )
    event_id: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    stage: WorkflowStage
    state: WorkflowEventState
    occurred_at: datetime
    message: str = Field(min_length=1, max_length=300)
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
