import os
from collections.abc import Mapping
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.exceptions import STTConfigurationError


class SpeechToTextSettings(BaseModel):
    """Provider-neutral speech-to-text configuration.

    Like TextToSpeechSettings, there is no per-role dimension -- STT has a
    single swappable provider for the whole application. Provider
    credentials deliberately do not belong to this model; the adapter is
    responsible for obtaining the credential required by its configured
    provider.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
        allow_inf_nan=False,
    )

    provider: str = Field(
        default="google",
        min_length=1,
        alias="JARVIS_STT_PROVIDER",
    )
    language_code: str = Field(
        default="en-IN",
        min_length=1,
        alias="JARVIS_STT_LANGUAGE_CODE",
    )
    audio_encoding: str = Field(
        default="WEBM_OPUS",
        min_length=1,
        alias="JARVIS_STT_AUDIO_ENCODING",
    )
    sample_rate_hertz: int | None = Field(
        default=None,
        gt=0,
        alias="JARVIS_STT_SAMPLE_RATE_HERTZ",
    )
    model_id: str | None = Field(
        default=None,
        alias="JARVIS_STT_MODEL_ID",
    )

    @field_validator("provider", "language_code", "audio_encoding")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("STT setting must not be blank")
        return normalized

    @field_validator("model_id", mode="before")
    @classmethod
    def normalize_optional_model_id(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "SpeechToTextSettings":
        source = environment if environment is not None else os.environ
        values: dict[str, object] = {}
        text_fields = {
            "JARVIS_STT_PROVIDER": "provider",
            "JARVIS_STT_LANGUAGE_CODE": "language_code",
            "JARVIS_STT_AUDIO_ENCODING": "audio_encoding",
            "JARVIS_STT_MODEL_ID": "model_id",
        }
        for environment_name, field_name in text_fields.items():
            value = source.get(environment_name)
            if value is not None and value.strip():
                values[field_name] = value
        sample_rate = source.get("JARVIS_STT_SAMPLE_RATE_HERTZ")
        if sample_rate is not None and sample_rate.strip():
            try:
                values["sample_rate_hertz"] = int(sample_rate)
            except ValueError as exc:
                raise ValueError(
                    "JARVIS_STT_SAMPLE_RATE_HERTZ must be an integer"
                ) from exc
        return cls.model_validate(values)


@lru_cache
def get_stt_settings() -> SpeechToTextSettings:
    try:
        return SpeechToTextSettings.from_environment()
    except ValidationError as exc:
        raise STTConfigurationError(
            "STT configuration is invalid"
        ) from exc
