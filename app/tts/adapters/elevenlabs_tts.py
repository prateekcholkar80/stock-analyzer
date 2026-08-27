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
from elevenlabs.types.voice_settings import VoiceSettings

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

# Keyed by the same JARVIS_TTS_AUDIO_ENCODING vocabulary the Google adapter
# uses, mapped onto ElevenLabs' own codec_samplerate_bitrate output_format
# strings -- so "MP3" (the shared default) works unchanged across providers.
_OUTPUT_FORMAT_BY_ENCODING: dict[str, tuple[str, str]] = {
    "MP3": ("mp3_44100_128", "audio/mpeg"),
    "LINEAR16": ("pcm_44100", "audio/l16"),
    "OGG_OPUS": ("opus_48000_128", "audio/ogg"),
    "MULAW": ("ulaw_8000", "audio/basic"),
    "ALAW": ("alaw_8000", "audio/basic"),
}


def _default_client() -> ElevenLabs:
    # ElevenLabs authenticates via the ELEVENLABS_API_KEY environment
    # variable, read implicitly by the SDK -- mirrors Google's Application
    # Default Credentials pattern: no Jarvis-specific credential setting.
    return ElevenLabs()


class ElevenLabsTextToSpeechSynthesizer:
    """TextToSpeechSynthesizer implementation backed by ElevenLabs."""

    def __init__(
        self,
        settings: TextToSpeechSettings,
        *,
        client_factory: ClientFactory = _default_client,
    ) -> None:
        if not isinstance(settings, TextToSpeechSettings):
            raise ValueError(
                "ElevenLabs TTS synthesizer requires validated settings"
            )
        if not callable(client_factory):
            raise ValueError("ElevenLabs TTS client factory must be callable")
        if not settings.voice_name:
            raise TTSConfigurationError(
                "ElevenLabs requires a configured voice ID "
                "(JARVIS_TTS_VOICE_NAME)",
                provider="elevenlabs",
                operation_id=get_operation_id(),
            )
        try:
            output_format, media_type = _OUTPUT_FORMAT_BY_ENCODING[
                settings.audio_encoding
            ]
        except KeyError as exc:
            raise TTSConfigurationError(
                "The configured TTS audio encoding is not supported by "
                "the ElevenLabs adapter",
                provider="elevenlabs",
                operation_id=get_operation_id(),
            ) from exc

        self.settings = settings
        self._output_format = output_format
        self._media_type = media_type
        self._client = client_factory()

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.settings.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def synthesize(self, *, text: str) -> SpeechSynthesis:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text to synthesize must be a non-blank string")

        convert_kwargs: dict[str, Any] = {
            "text": text,
            "output_format": self._output_format,
            "voice_settings": VoiceSettings(speed=self.settings.speaking_rate),
        }
        # Omitted entirely (not passed as None) when unset -- the SDK's
        # own default for model_id means "let the account's default model
        # decide," which is different from explicitly requesting one.
        if self.settings.model_id is not None:
            convert_kwargs["model_id"] = self.settings.model_id

        try:
            chunks = self._client.text_to_speech.convert(
                self.settings.voice_name,
                **convert_kwargs,
            )
            audio_content = b"".join(chunks)
        except Exception as exc:
            raise self._translate_provider_error(exc) from exc

        if not audio_content:
            raise TTSSynthesisError(
                "The TTS provider returned empty audio",
                provider="elevenlabs",
                voice=self.settings.voice_name,
                operation_id=get_operation_id(),
            )

        return SpeechSynthesis(
            audio=audio_content,
            provider="elevenlabs",
            voice=self.settings.voice_name,
            audio_encoding=self.settings.audio_encoding,
            media_type=self._media_type,
            generated_at=datetime.now(UTC),
        )

    def _translate_provider_error(self, error: Exception) -> TTSError:
        context = {
            "provider": "elevenlabs",
            "voice": self.settings.voice_name,
            "operation_id": get_operation_id(),
        }
        if isinstance(error, (UnauthorizedError, ForbiddenError)):
            return TTSAuthenticationError(
                "The TTS provider rejected its configured credential",
                **context,
            )
        if isinstance(
            error,
            (BadRequestError, NotFoundError, UnprocessableEntityError),
        ):
            return TTSConfigurationError(
                "The configured TTS provider or voice was rejected",
                **context,
            )
        return TTSProviderUnavailableError(
            "The TTS provider could not complete the request",
            **context,
        )
