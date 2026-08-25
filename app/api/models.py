from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from app.models.browser_operations import (
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationSnapshot,
    BrowserSessionSnapshot,
)
from app.models.conversation import InputChannel
from app.models.technical import TechnicalModel


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"


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


class BrowserOperationResultResponse(TechnicalModel):
    """Polling response that never claims a result before completion."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.http_operation_result.v1"] = (
        "jarvis.http_operation_result.v1"
    )
    operation: BrowserOperationSnapshot
    output: BrowserOperationOutput | None = None
