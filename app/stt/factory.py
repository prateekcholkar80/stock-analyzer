from collections.abc import Callable
from threading import RLock

from app.exceptions import STTConfigurationError
from app.stt.adapters.elevenlabs_stt import ElevenLabsSpeechToTextTranscriber
from app.stt.adapters.google_stt import GoogleSpeechToTextTranscriber
from app.stt.config import SpeechToTextSettings
from app.stt.gateway import SpeechToTextTranscriber


TranscriberBuilder = Callable[[SpeechToTextSettings], SpeechToTextTranscriber]

# Registered adapters, keyed by SpeechToTextSettings.provider. This is the
# literal mechanism that makes JARVIS_STT_PROVIDER a config-only switch:
# adding a new provider means adding one entry here (and its adapter
# module), never touching app/conversation/, app/api/http.py, or the
# frontend. An explicit transcriber_builder passed to the factory still
# overrides this lookup, for tests and one-off composition.
_BUILDER_BY_PROVIDER: dict[str, TranscriberBuilder] = {
    "google": GoogleSpeechToTextTranscriber,
    "elevenlabs": ElevenLabsSpeechToTextTranscriber,
}


class SpeechToTextTranscriberFactory:
    """Build and cache a single provider-neutral STT transcriber.

    Unlike LLMGatewayFactory, there is no per-role cache dimension --
    STT has one swappable provider for the whole application. Swapping
    providers is a matter of changing SpeechToTextSettings.provider (a
    config change) to select a different registered adapter, never a
    change to app/conversation/ or app/stt/gateway.py.
    """

    def __init__(
        self,
        settings: SpeechToTextSettings,
        transcriber_builder: TranscriberBuilder | None = None,
    ) -> None:
        if not isinstance(settings, SpeechToTextSettings):
            raise ValueError(
                "STT transcriber factory requires validated settings"
            )
        if transcriber_builder is not None and not callable(transcriber_builder):
            raise ValueError("STT transcriber builder must be callable")

        self.settings = settings
        self._transcriber_builder = transcriber_builder
        self._transcriber: SpeechToTextTranscriber | None = None
        self._lock = RLock()

    def get(self) -> SpeechToTextTranscriber:
        with self._lock:
            if self._transcriber is not None:
                return self._transcriber

            builder = self._transcriber_builder
            if builder is None:
                builder = _BUILDER_BY_PROVIDER.get(self.settings.provider)
                if builder is None:
                    raise STTConfigurationError(
                        "No STT adapter is registered for the configured "
                        "provider",
                        provider=self.settings.provider,
                    )

            try:
                transcriber = builder(self.settings)
            except STTConfigurationError:
                raise
            except Exception as exc:
                raise STTConfigurationError(
                    "The STT transcriber could not be constructed",
                    provider=self.settings.provider,
                ) from exc
            if not isinstance(transcriber, SpeechToTextTranscriber):
                raise STTConfigurationError(
                    "The configured STT adapter does not implement the "
                    "transcriber contract",
                    provider=self.settings.provider,
                )

            self._transcriber = transcriber
            return self._transcriber
