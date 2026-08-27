from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import ConfigDict, Field, field_validator

from app.models.technical import TechnicalModel


class Transcription(TechnicalModel):
    """A validated transcription result and its execution metadata.

    Unlike SpeechSynthesis.audio, ``transcript`` has no non-empty
    constraint -- an empty transcript is valid "no speech detected" data
    (a VAD false positive, silence, a cough), not corrupted output. Only
    a genuinely malformed provider response should raise, never an empty
    result.
    """

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    transcript: str
    provider: str = Field(min_length=1)
    language_code: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    generated_at: datetime

    @field_validator("provider", "language_code")
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("transcription identity must not be blank")
        return normalized

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "transcription generated_at must include timezone information"
            )
        return value


@runtime_checkable
class SpeechToTextTranscriber(Protocol):
    """Provider-neutral boundary for speech-to-text transcription.

    ``transcribe`` takes only the raw audio and its media type -- language
    and provider-specific recognition settings are bound into the
    transcriber at construction time via SpeechToTextSettings, so the
    caller-facing contract never varies by provider. This is what makes
    swapping providers a configuration change rather than a code change,
    mirroring TextToSpeechSynthesizer.
    """

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def transcribe(self, *, audio: bytes, media_type: str) -> Transcription:
        ...
