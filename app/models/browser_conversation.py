from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.browser_operations import BrowserOperationSnapshot
from app.models.conversation import (
    ConversationOutcome,
    ConversationState,
    InputChannel,
    JarvisConversationEvent,
)
from app.models.technical import TechnicalModel


IST_OFFSET = timedelta(hours=5, minutes=30)
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"


class BrowserConversationSnapshot(TechnicalModel):
    """Current wake/conversation state for one browser session."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_conversation.v1"] = (
        "jarvis.browser_conversation.v1"
    )
    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    state: ConversationState = ConversationState.DORMANT
    updated_at: datetime
    last_event_sequence: int = Field(default=0, ge=0)
    active_operation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    has_follow_up_context: bool = False
    display_message: str | None = Field(default=None, min_length=1)
    spoken_message: str | None = Field(default=None, min_length=1)

    @field_validator("updated_at")
    @classmethod
    def require_ist(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != IST_OFFSET:
            raise ValueError("browser conversation timestamp must be in IST")
        return value

    @field_validator("display_message", "spoken_message")
    @classmethod
    def normalize_optional_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("browser conversation message must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_active_operation(self) -> Self:
        if self.state is ConversationState.PROCESSING:
            if self.active_operation_id is None:
                raise ValueError("processing conversation requires an operation")
        elif self.active_operation_id is not None:
            raise ValueError("only processing conversation may own an operation")
        return self


class BrowserConversationTurn(TechnicalModel):
    """Immediate browser response to one typed or transcribed utterance."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.browser_conversation_turn.v1"] = (
        "jarvis.browser_conversation_turn.v1"
    )
    outcome: ConversationOutcome
    conversation: BrowserConversationSnapshot
    input_channel: InputChannel
    operation: BrowserOperationSnapshot | None = None

    @model_validator(mode="after")
    def validate_dispatched_operation(self) -> Self:
        if self.outcome is ConversationOutcome.DISPATCHED:
            if self.operation is None:
                raise ValueError("dispatched turn requires an operation")
            if (
                self.operation.request.session_id
                != self.conversation.session_id
                or self.operation.request.operation_id
                != self.conversation.active_operation_id
            ):
                raise ValueError("dispatched operation must match conversation")
        elif (
            self.operation is not None
            and self.outcome is not ConversationOutcome.BUSY
        ):
            raise ValueError("only dispatched or busy turns may include work")
        return self


class ConversationEventReplayCursor(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class ConversationEventBatch(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.conversation_event_batch.v1"] = (
        "jarvis.conversation_event_batch.v1"
    )
    session_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    after_sequence: int = Field(ge=0)
    events: tuple[JarvisConversationEvent, ...] = ()
    next_sequence: int = Field(ge=0)
    has_more: bool

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        expected = self.after_sequence
        for event in self.events:
            if event.session_id != self.session_id or event.sequence <= expected:
                raise ValueError("conversation events must be ordered for one session")
            expected = event.sequence
        if self.next_sequence != expected:
            raise ValueError("conversation cursor must match its last event")
        return self
