from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.llm.config import LLMRole
from app.models.technical import TechnicalModel


class LLMFailureCode(str, Enum):
    """Stable failure codes consumed by UI and voice clients."""

    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class JarvisLLMFailureResponse(TechnicalModel):
    """Secret-safe response shown when mandatory LLM debate cannot run."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.llm_failure.v1"] = (
        "jarvis.llm_failure.v1"
    )
    code: LLMFailureCode
    title: str = Field(min_length=1)
    display_message: str = Field(min_length=1)
    spoken_message: str = Field(min_length=1)
    recovery_action: str = Field(min_length=1)
    retryable: bool
    analysis_available: Literal[False] = False
    failed_role: LLMRole | None = None
    operation_id: str | None = Field(default=None, min_length=1)

    @field_validator(
        "title",
        "display_message",
        "spoken_message",
        "recovery_action",
        "operation_id",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Jarvis failure response text must not be blank")
        return normalized


class LLMRolePreflight(TechnicalModel):
    """Sanitized readiness metadata for one mandatory debate role."""

    model_config = ConfigDict(frozen=True, strict=True)

    role: LLMRole
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    credential_required: bool
    credential_ready: bool
    structured_gateway_ready: bool

    @field_validator("provider", "model")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("LLM preflight identity must not be blank")
        return normalized


class LLMPreflightResult(TechnicalModel):
    """Successful local readiness result for the complete debate panel."""

    model_config = ConfigDict(frozen=True, strict=True)

    configuration_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    roles: tuple[LLMRolePreflight, ...] = Field(min_length=3, max_length=3)
    checked_at: datetime
    ready: bool

    @field_validator("checked_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("LLM preflight time must include timezone")
        return value

    @model_validator(mode="after")
    def require_complete_ready_panel(self) -> "LLMPreflightResult":
        expected = set(LLMRole)
        observed = {item.role for item in self.roles}
        if observed != expected or len(observed) != len(self.roles):
            raise ValueError("LLM preflight must contain each debate role")
        if not self.ready:
            raise ValueError("successful LLM preflight result must be ready")
        if any(
            not item.credential_ready
            or not item.structured_gateway_ready
            for item in self.roles
        ):
            raise ValueError("all LLM debate roles must be ready")
        return self
