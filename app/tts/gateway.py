from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import ConfigDict, Field, field_validator

from app.models.technical import TechnicalModel


class SpeechSynthesis(TechnicalModel):
    """A validated synthesized-audio result and its execution metadata.

    Callers may record this metadata for audit purposes, but provider and
    voice selection remain the responsibility of the synthesizer
    composition layer -- the same separation StructuredGeneration draws
    for text generation.
    """

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    audio: bytes
    provider: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    audio_encoding: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    generated_at: datetime

    @field_validator("provider", "voice", "audio_encoding", "media_type")
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("synthesis identity must not be blank")
        return normalized

    @field_validator("audio")
    @classmethod
    def require_non_empty_audio(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("synthesized audio must not be empty")
        return value

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "synthesis generated_at must include timezone information"
            )
        return value


@runtime_checkable
class TextToSpeechSynthesizer(Protocol):
    """Provider-neutral boundary for text-to-speech synthesis.

    ``synthesize`` takes only the text to speak -- voice, language, and
    encoding are bound into the synthesizer at construction time, so the
    caller-facing contract never varies by provider. This is what makes
    swapping providers a configuration change rather than a code change.
    """

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def synthesize(self, *, text: str) -> SpeechSynthesis:
        ...
