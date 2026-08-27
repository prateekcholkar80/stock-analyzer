from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.presentation import (
    JarvisMultiTimeframeResearchExplanation,
    JarvisResearchExplanation,
)
from app.models.technical import TechnicalModel
from app.models.debate import JudgeFollowUpAnswer


IST_OFFSET = timedelta(hours=5, minutes=30)


class InputChannel(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class ConversationState(StrEnum):
    DORMANT = "dormant"
    GREETING = "greeting"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESPONDING = "responding"
    FAILED = "failed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


class ConversationOutcome(StrEnum):
    IGNORED = "ignored"
    ACTIVATED = "activated"
    COMPLETED = "completed"
    FAILED = "failed"
    CLARIFICATION_REQUIRED = "clarification_required"
    BUSY = "busy"
    DISPATCHED = "dispatched"
    CONFIRMATION_REQUESTED = "confirmation_requested"


class JarvisUtterance(TechnicalModel):
    """Text understood by Jarvis, either typed or transcribed from voice."""

    model_config = ConfigDict(frozen=True, strict=True)

    text: str = Field(min_length=1, max_length=2_000)
    channel: InputChannel

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Jarvis utterance must not be blank")
        return normalized


class JarvisConversationTurn(TechnicalModel):
    """Stable response envelope consumed by text, voice, and dashboard UIs."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.conversation_turn.v1"] = (
        "jarvis.conversation_turn.v1"
    )
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    outcome: ConversationOutcome
    state_before: ConversationState
    state_after: ConversationState
    input_channel: InputChannel
    display_message: str | None = Field(default=None, min_length=1)
    spoken_message: str | None = Field(default=None, min_length=1)
    research_response: JarvisSwingAnalysisResponse | None = None
    research_explanation: (
        JarvisResearchExplanation
        | JarvisMultiTimeframeResearchExplanation
        | None
    ) = None
    judge_follow_up: JudgeFollowUpAnswer | None = None

    @field_validator("session_id", "display_message", "spoken_message")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("conversation response text must not be blank")
        return normalized


class JarvisConversationEvent(TechnicalModel):
    """One safe, ordered conversation transition for the dynamic UI."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.conversation_event.v1"] = (
        "jarvis.conversation_event.v1"
    )
    event_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    from_state: ConversationState
    to_state: ConversationState
    input_channel: InputChannel
    occurred_at: datetime
    message: str = Field(min_length=1, max_length=300)

    @field_validator("event_id", "session_id", "message")
    @classmethod
    def normalize_event_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("conversation event text must not be blank")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def require_ist_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != IST_OFFSET:
            raise ValueError("conversation event timestamp must be in IST")
        return value
