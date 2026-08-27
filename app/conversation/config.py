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
    consecutive_resolution_failures_before_refresh_prompt: int = Field(
        default=3,
        ge=1,
    )

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
        kwargs = {
            "user_name": values.get("JARVIS_USER_NAME", ""),
            "wake_phrase": values.get("JARVIS_WAKE_PHRASE", "Hey Jarvis"),
        }
        threshold = values.get(
            "JARVIS_RESOLUTION_FAILURE_THRESHOLD",
        )
        if threshold is not None and threshold.strip():
            try:
                kwargs["consecutive_resolution_failures_before_refresh_prompt"] = (
                    int(threshold)
                )
            except ValueError as exc:
                raise ConfigurationError(
                    "JARVIS_RESOLUTION_FAILURE_THRESHOLD must be an integer"
                ) from exc
        try:
            return cls(**kwargs)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                "Jarvis conversation configuration is invalid; set "
                "JARVIS_USER_NAME to a non-blank display name"
            ) from exc

