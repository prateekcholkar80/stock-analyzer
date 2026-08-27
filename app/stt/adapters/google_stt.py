from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from google.api_core import exceptions as google_exceptions
from google.cloud import speech

from app.exceptions import (
    STTAuthenticationError,
    STTConfigurationError,
    STTError,
    STTProviderUnavailableError,
    STTTranscriptionError,
)
from app.logging_config import get_operation_id
from app.stt.config import SpeechToTextSettings
from app.stt.gateway import Transcription


ClientFactory = Callable[[], Any]


class GoogleSpeechToTextTranscriber:
    """SpeechToTextTranscriber implementation backed by Google Cloud STT."""

    def __init__(
        self,
        settings: SpeechToTextSettings,
        *,
        client_factory: ClientFactory = speech.SpeechClient,
    ) -> None:
        if not isinstance(settings, SpeechToTextSettings):
            raise ValueError(
                "Google STT transcriber requires validated settings"
            )
        if not callable(client_factory):
            raise ValueError("Google STT client factory must be callable")

        self.settings = settings
        try:
            audio_encoding = speech.RecognitionConfig.AudioEncoding[
                settings.audio_encoding
            ]
        except KeyError as exc:
            raise STTConfigurationError(
                "The configured STT audio encoding is not supported by "
                "Google Cloud STT",
                provider="google",
                language_code=settings.language_code,
                operation_id=get_operation_id(),
            ) from exc
        self._audio_encoding = audio_encoding
        self._client = client_factory()

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.settings.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def transcribe(self, *, audio: bytes, media_type: str) -> Transcription:
        if not isinstance(audio, bytes) or not audio:
            raise ValueError("audio to transcribe must be non-empty bytes")

        config_kwargs: dict[str, Any] = {
            "encoding": self._audio_encoding,
            "language_code": self.settings.language_code,
        }
        if self.settings.sample_rate_hertz is not None:
            config_kwargs["sample_rate_hertz"] = self.settings.sample_rate_hertz
        recognition_config = speech.RecognitionConfig(**config_kwargs)
        recognition_audio = speech.RecognitionAudio(content=audio)

        try:
            response = self._client.recognize(
                config=recognition_config,
                audio=recognition_audio,
            )
        except Exception as exc:
            raise self._translate_provider_error(exc) from exc

        results = getattr(response, "results", None) or []
        if not results:
            return Transcription(
                transcript="",
                provider="google",
                language_code=self.settings.language_code,
                confidence=None,
                generated_at=datetime.now(UTC),
            )

        alternatives = results[0].alternatives
        if not alternatives:
            raise STTTranscriptionError(
                "The STT provider returned a result with no alternatives",
                provider="google",
                language_code=self.settings.language_code,
                operation_id=get_operation_id(),
            )

        best = alternatives[0]
        return Transcription(
            transcript=best.transcript,
            provider="google",
            language_code=self.settings.language_code,
            confidence=best.confidence if best.confidence else None,
            generated_at=datetime.now(UTC),
        )

    def _translate_provider_error(self, error: Exception) -> STTError:
        context = {
            "provider": "google",
            "language_code": self.settings.language_code,
            "operation_id": get_operation_id(),
        }
        if isinstance(
            error,
            (google_exceptions.Unauthenticated, google_exceptions.PermissionDenied),
        ):
            return STTAuthenticationError(
                "The STT provider rejected its configured credential",
                **context,
            )
        if isinstance(
            error,
            (
                google_exceptions.ServiceUnavailable,
                google_exceptions.DeadlineExceeded,
                google_exceptions.RetryError,
            ),
        ):
            return STTProviderUnavailableError(
                "The STT provider is currently unavailable",
                **context,
            )
        if isinstance(
            error,
            (google_exceptions.InvalidArgument, google_exceptions.NotFound),
        ):
            return STTConfigurationError(
                "The configured STT provider or language was rejected",
                **context,
            )
        return STTProviderUnavailableError(
            "The STT provider could not complete the request",
            **context,
        )
