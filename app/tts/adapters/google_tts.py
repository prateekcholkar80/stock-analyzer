from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from google.api_core import exceptions as google_exceptions
from google.cloud import texttospeech

from app.exceptions import (
    TTSAuthenticationError,
    TTSConfigurationError,
    TTSError,
    TTSProviderUnavailableError,
    TTSSynthesisError,
)
from app.logging_config import get_operation_id
from app.tts.config import TextToSpeechSettings
from app.tts.gateway import SpeechSynthesis


ClientFactory = Callable[[], Any]

_MEDIA_TYPE_BY_ENCODING = {
    "MP3": "audio/mpeg",
    "LINEAR16": "audio/l16",
    "OGG_OPUS": "audio/ogg",
    "MULAW": "audio/basic",
    "ALAW": "audio/basic",
}


class GoogleTextToSpeechSynthesizer:
    """TextToSpeechSynthesizer implementation backed by Google Cloud TTS."""

    def __init__(
        self,
        settings: TextToSpeechSettings,
        *,
        client_factory: ClientFactory = texttospeech.TextToSpeechClient,
    ) -> None:
        if not isinstance(settings, TextToSpeechSettings):
            raise ValueError(
                "Google TTS synthesizer requires validated settings"
            )
        if not callable(client_factory):
            raise ValueError("Google TTS client factory must be callable")

        self.settings = settings
        try:
            audio_encoding = texttospeech.AudioEncoding[settings.audio_encoding]
        except KeyError as exc:
            raise TTSConfigurationError(
                "The configured TTS audio encoding is not supported by "
                "Google Cloud TTS",
                provider="google",
                operation_id=get_operation_id(),
            ) from exc
        self._audio_encoding = audio_encoding
        self._client = client_factory()

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.settings.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def synthesize(self, *, text: str) -> SpeechSynthesis:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text to synthesize must be a non-blank string")

        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_kwargs: dict[str, str] = {
            "language_code": self.settings.language_code
        }
        if self.settings.voice_name is not None:
            voice_kwargs["name"] = self.settings.voice_name
        voice = texttospeech.VoiceSelectionParams(**voice_kwargs)
        audio_config = texttospeech.AudioConfig(
            audio_encoding=self._audio_encoding,
            speaking_rate=self.settings.speaking_rate,
        )

        try:
            response = self._client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )
        except Exception as exc:
            raise self._translate_provider_error(exc) from exc

        audio_content = getattr(response, "audio_content", None)
        if not audio_content:
            raise TTSSynthesisError(
                "The TTS provider returned empty audio",
                provider="google",
                voice=self.settings.voice_name,
                operation_id=get_operation_id(),
            )

        return SpeechSynthesis(
            audio=audio_content,
            provider="google",
            voice=self.settings.voice_name or self.settings.language_code,
            audio_encoding=self.settings.audio_encoding,
            media_type=_MEDIA_TYPE_BY_ENCODING.get(
                self.settings.audio_encoding,
                "application/octet-stream",
            ),
            generated_at=datetime.now(UTC),
        )

    def _translate_provider_error(self, error: Exception) -> TTSError:
        context = {
            "provider": "google",
            "voice": self.settings.voice_name,
            "operation_id": get_operation_id(),
        }
        if isinstance(
            error,
            (google_exceptions.Unauthenticated, google_exceptions.PermissionDenied),
        ):
            return TTSAuthenticationError(
                "The TTS provider rejected its configured credential",
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
            return TTSProviderUnavailableError(
                "The TTS provider is currently unavailable",
                **context,
            )
        if isinstance(
            error,
            (google_exceptions.InvalidArgument, google_exceptions.NotFound),
        ):
            return TTSConfigurationError(
                "The configured TTS provider or voice was rejected",
                **context,
            )
        return TTSProviderUnavailableError(
            "The TTS provider could not complete the request",
            **context,
        )
