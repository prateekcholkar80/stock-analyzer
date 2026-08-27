import os
from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.exceptions import LLMConfigurationError
from app.models.technical import TechnicalModel


class LLMRole(StrEnum):
    BULL = "bull"
    BEAR = "bear"
    JUDGE = "judge"
    JARVIS = "jarvis"
    TICKER_RESOLVER = "ticker_resolver"


DEBATE_LLM_ROLES = (
    LLMRole.BULL,
    LLMRole.BEAR,
    LLMRole.JUDGE,
)


class LLMRoleSettings(TechnicalModel):
    """Resolved generation settings for one debate role."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    role: LLMRole
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("LLM model must not be blank")
        return normalized


class LLMSettings(BaseModel):
    """Provider-neutral model selection for the mandatory debate roles.

    Provider credentials deliberately do not belong to this model. The
    eventual gateway adapter is responsible for obtaining the credential
    required by its configured model/provider.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
        allow_inf_nan=False,
    )

    shared_model: str | None = Field(
        default=None,
        alias="JARVIS_LLM_MODEL",
    )
    bull_model: str | None = Field(
        default=None,
        alias="JARVIS_BULL_LLM_MODEL",
    )
    bear_model: str | None = Field(
        default=None,
        alias="JARVIS_BEAR_LLM_MODEL",
    )
    judge_model: str | None = Field(
        default=None,
        alias="JARVIS_JUDGE_LLM_MODEL",
    )
    jarvis_model: str | None = Field(
        default=None,
        alias="JARVIS_PERSONA_LLM_MODEL",
    )
    ticker_resolver_model: str | None = Field(
        default=None,
        alias="JARVIS_TICKER_RESOLVER_LLM_MODEL",
    )

    bull_temperature: float = Field(
        default=0.4,
        ge=0,
        le=2,
        alias="JARVIS_BULL_LLM_TEMPERATURE",
    )
    bear_temperature: float = Field(
        default=0.4,
        ge=0,
        le=2,
        alias="JARVIS_BEAR_LLM_TEMPERATURE",
    )
    judge_temperature: float = Field(
        default=0.0,
        ge=0,
        le=2,
        alias="JARVIS_JUDGE_LLM_TEMPERATURE",
    )
    jarvis_temperature: float = Field(
        default=0.2,
        ge=0,
        le=2,
        alias="JARVIS_PERSONA_LLM_TEMPERATURE",
    )
    ticker_resolver_temperature: float = Field(
        default=0.0,
        ge=0,
        le=2,
        alias="JARVIS_TICKER_RESOLVER_LLM_TEMPERATURE",
    )

    bull_max_tokens: int = Field(
        default=800,
        gt=0,
        alias="JARVIS_BULL_LLM_MAX_TOKENS",
    )
    bear_max_tokens: int = Field(
        default=800,
        gt=0,
        alias="JARVIS_BEAR_LLM_MAX_TOKENS",
    )
    judge_max_tokens: int = Field(
        default=600,
        gt=0,
        alias="JARVIS_JUDGE_LLM_MAX_TOKENS",
    )
    jarvis_max_tokens: int = Field(
        default=5_000,
        gt=0,
        alias="JARVIS_PERSONA_LLM_MAX_TOKENS",
    )
    ticker_resolver_max_tokens: int = Field(
        default=300,
        gt=0,
        alias="JARVIS_TICKER_RESOLVER_LLM_MAX_TOKENS",
    )

    @field_validator(
        "shared_model",
        "bull_model",
        "bear_model",
        "judge_model",
        "jarvis_model",
        "ticker_resolver_model",
        mode="before",
    )
    @classmethod
    def normalize_optional_model(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_model_for_every_role(self) -> "LLMSettings":
        missing = [
            role.value
            for role in DEBATE_LLM_ROLES
            if self._model_for(role) is None
        ]
        if missing:
            raise ValueError(
                "LLM model configuration is required for debate roles: "
                + ", ".join(missing)
            )
        return self

    def _model_for(self, role: LLMRole) -> str | None:
        override = {
            LLMRole.BULL: self.bull_model,
            LLMRole.BEAR: self.bear_model,
            LLMRole.JUDGE: self.judge_model,
            LLMRole.JARVIS: self.jarvis_model or self.judge_model,
            LLMRole.TICKER_RESOLVER: self.ticker_resolver_model,
        }[role]
        return override or self.shared_model

    def for_role(self, role: LLMRole) -> LLMRoleSettings:
        if not isinstance(role, LLMRole):
            raise ValueError("LLM role must be a validated LLMRole")

        model = self._model_for(role)
        if model is None:  # Protected by model validation.
            raise ValueError(f"LLM model is not configured for {role.value}")

        temperature = {
            LLMRole.BULL: self.bull_temperature,
            LLMRole.BEAR: self.bear_temperature,
            LLMRole.JUDGE: self.judge_temperature,
            LLMRole.JARVIS: self.jarvis_temperature,
            LLMRole.TICKER_RESOLVER: self.ticker_resolver_temperature,
        }[role]
        max_tokens = {
            LLMRole.BULL: self.bull_max_tokens,
            LLMRole.BEAR: self.bear_max_tokens,
            LLMRole.JUDGE: self.judge_max_tokens,
            LLMRole.JARVIS: self.jarvis_max_tokens,
            LLMRole.TICKER_RESOLVER: self.ticker_resolver_max_tokens,
        }[role]
        return LLMRoleSettings(
            role=role,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "LLMSettings":
        source = environment if environment is not None else os.environ
        return cls.model_validate(source)


@lru_cache
def get_llm_settings() -> LLMSettings:
    try:
        return LLMSettings.from_environment()
    except ValidationError as exc:
        raise LLMConfigurationError(
            "LLM configuration is missing or invalid"
        ) from exc
