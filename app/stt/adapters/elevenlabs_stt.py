import math
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from elevenlabs.client import ElevenLabs
from elevenlabs.errors.bad_request_error import BadRequestError
from elevenlabs.errors.forbidden_error import ForbiddenError
from elevenlabs.errors.not_found_error import NotFoundError
from elevenlabs.errors.unauthorized_error import UnauthorizedError
from elevenlabs.errors.unprocessable_entity_error import UnprocessableEntityError

from app.exceptions import (
    STTAuthenticationError,
    STTConfigurationError,
    STTError,
    STTProviderUnavailableError,
)
from app.logging_config import get_operation_id
from app.stt.config import SpeechToTextSettings
from app.stt.gateway import Transcription


ClientFactory = Callable[[], Any]

_DEFAULT_MODEL_ID = "scribe_v1"


def _default_client() -> ElevenLabs:
    # ElevenLabs authenticates via the ELEVENLABS_API_KEY environment
    # variable, read implicitly by the SDK -- mirrors the TTS adapter's
    # credential handling exactly (same account, same key).
    return ElevenLabs()


class ElevenLabsSpeechToTextTranscriber:
    """SpeechToTextTranscriber implementation backed by ElevenLabs Scribe."""

    def __init__(
        self,
        settings: SpeechToTextSettings,
        *,
        client_factory: ClientFactory = _default_client,
    ) -> None:
        if not isinstance(settings, SpeechToTextSettings):
            raise ValueError(
                "ElevenLabs STT transcriber requires validated settings"
            )
        if not callable(client_factory):
            raise ValueError("ElevenLabs STT client factory must be callable")

        self.settings = settings
        self._model_id = settings.model_id or _DEFAULT_MODEL_ID
        # ElevenLabs expects a bare ISO-639-1/3 code (e.g. "en"), not a
        # BCP-47 locale tag (e.g. "en-IN") -- take the primary subtag so
        # the shared, provider-neutral setting still degrades sensibly.
        self._language_hint = settings.language_code.split("-")[0].lower()
        self._client = client_factory()

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.settings.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def transcribe(self, *, audio: bytes, media_type: str) -> Transcription:
        if not isinstance(audio, bytes) or not audio:
            raise ValueError("audio to transcribe must be non-empty bytes")

        try:
            response = self._client.speech_to_text.convert(
                model_id=self._model_id,
                file=audio,
                language_code=self._language_hint,
            )
        except Exception as exc:
            raise self._translate_provider_error(exc) from exc

        transcript = getattr(response, "text", None) or ""
        language_code = getattr(response, "language_code", None) or (
            self.settings.language_code
        )
        return Transcription(
            transcript=transcript,
            provider="elevenlabs",
            language_code=language_code,
            confidence=_aggregate_confidence(response),
            generated_at=datetime.now(UTC),
        )

    def _translate_provider_error(self, error: Exception) -> STTError:
        context = {
            "provider": "elevenlabs",
            "language_code": self.settings.language_code,
            "operation_id": get_operation_id(),
        }
        if isinstance(error, (UnauthorizedError, ForbiddenError)):
            return STTAuthenticationError(
                "The STT provider rejected its configured credential",
                **context,
            )
        if isinstance(
            error,
            (BadRequestError, NotFoundError, UnprocessableEntityError),
        ):
            return STTConfigurationError(
                "The configured STT provider or language was rejected",
                **context,
            )
        return STTProviderUnavailableError(
            "The STT provider could not complete the request",
            **context,
        )


def _aggregate_confidence(response: Any) -> float | None:
    """Average per-word log-probabilities into one transcript-level
    confidence estimate. ElevenLabs does not return a transcription
    confidence score directly (only a language-detection probability,
    which measures something different and must not be substituted here
    -- callers gate real commands on this value).
    """
    words = getattr(response, "words", None) or []
    logprobs = [
        word.logprob
        for word in words
        if getattr(word, "type", None) == "word"
        and getattr(word, "logprob", None) is not None
    ]
    if not logprobs:
        return None
    mean_logprob = sum(logprobs) / len(logprobs)
    return min(1.0, max(0.0, math.exp(mean_logprob)))
