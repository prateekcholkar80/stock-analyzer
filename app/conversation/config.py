import os
from collections.abc import Mapping

from pydantic import ConfigDict, Field, field_validator

from app.exceptions import ConfigurationError
from app.models.technical import TechnicalModel


class JarvisConversationConfig(TechnicalModel):
    """User-facing activation settings, independent of input channel."""

    model_config = ConfigDict(frozen=True, strict=True)

    user_name: str = Field(min_length=1, max_length=100)
    wake_phrase: str = Field(default="Hey Jarvis", min_length=1, max_length=100)

    @field_validator("user_name", "wake_phrase")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("conversation settings must not be blank")
        return normalized

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "JarvisConversationConfig":
        values = os.environ if environment is None else environment
        try:
            return cls(
                user_name=values.get("JARVIS_USER_NAME", ""),
                wake_phrase=values.get("JARVIS_WAKE_PHRASE", "Hey Jarvis"),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                "Jarvis conversation configuration is invalid; set "
                "JARVIS_USER_NAME to a non-blank display name"
            ) from exc

