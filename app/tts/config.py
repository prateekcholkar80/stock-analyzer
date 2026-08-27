import os
from collections.abc import Mapping
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.exceptions import TTSConfigurationError


class TextToSpeechSettings(BaseModel):
    """Provider-neutral text-to-speech configuration.

    Unlike LLMSettings, there is no per-role dimension -- TTS has a
    single swappable provider for the whole application, not multiple
    roles needing potentially different models. Provider credentials
    deliberately do not belong to this model; the adapter is responsible
    for obtaining the credential required by its configured provider.
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
        alias="JARVIS_TTS_PROVIDER",
    )
    voice_name: str | None = Field(
        default=None,
        alias="JARVIS_TTS_VOICE_NAME",
    )
    language_code: str = Field(
        default="en-GB",
        min_length=1,
        alias="JARVIS_TTS_LANGUAGE_CODE",
    )
    audio_encoding: str = Field(
        default="MP3",
        min_length=1,
        alias="JARVIS_TTS_AUDIO_ENCODING",
    )
    speaking_rate: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        alias="JARVIS_TTS_SPEAKING_RATE",
    )
    model_id: str | None = Field(
        default=None,
        alias="JARVIS_TTS_MODEL_ID",
    )

    @field_validator("provider", "language_code", "audio_encoding")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("TTS setting must not be blank")
        return normalized

    @field_validator("voice_name", "model_id", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
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
    ) -> "TextToSpeechSettings":
        source = environment if environment is not None else os.environ
        values: dict[str, object] = {}
        text_fields = {
            "JARVIS_TTS_PROVIDER": "provider",
            "JARVIS_TTS_VOICE_NAME": "voice_name",
            "JARVIS_TTS_LANGUAGE_CODE": "language_code",
            "JARVIS_TTS_AUDIO_ENCODING": "audio_encoding",
            "JARVIS_TTS_MODEL_ID": "model_id",
        }
        for environment_name, field_name in text_fields.items():
            value = source.get(environment_name)
            if value is not None and value.strip():
                values[field_name] = value
        speaking_rate = source.get("JARVIS_TTS_SPEAKING_RATE")
        if speaking_rate is not None and speaking_rate.strip():
            try:
                values["speaking_rate"] = float(speaking_rate)
            except ValueError as exc:
                raise ValueError(
                    "JARVIS_TTS_SPEAKING_RATE must be numeric"
                ) from exc
        return cls.model_validate(values)


@lru_cache
def get_tts_settings() -> TextToSpeechSettings:
    try:
        return TextToSpeechSettings.from_environment()
    except ValidationError as exc:
        raise TTSConfigurationError(
            "TTS configuration is invalid"
        ) from exc
