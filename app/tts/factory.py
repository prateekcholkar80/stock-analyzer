from collections.abc import Callable
from threading import RLock

from app.exceptions import TTSConfigurationError
from app.tts.adapters.elevenlabs_tts import ElevenLabsTextToSpeechSynthesizer
from app.tts.adapters.google_tts import GoogleTextToSpeechSynthesizer
from app.tts.config import TextToSpeechSettings
from app.tts.gateway import TextToSpeechSynthesizer


SynthesizerBuilder = Callable[[TextToSpeechSettings], TextToSpeechSynthesizer]

# Registered adapters, keyed by TextToSpeechSettings.provider. This is the
# literal mechanism that makes JARVIS_TTS_PROVIDER a config-only switch:
# adding a new provider means adding one entry here (and its adapter
# module), never touching app/conversation/, app/api/http.py, or the
# frontend. An explicit synthesizer_builder passed to the factory still
# overrides this lookup, for tests and one-off composition.
_BUILDER_BY_PROVIDER: dict[str, SynthesizerBuilder] = {
    "google": GoogleTextToSpeechSynthesizer,
    "elevenlabs": ElevenLabsTextToSpeechSynthesizer,
}


class TextToSpeechSynthesizerFactory:
    """Build and cache a single provider-neutral TTS synthesizer.

    Unlike LLMGatewayFactory, there is no per-role cache dimension --
    TTS has one swappable provider for the whole application. Swapping
    providers is a matter of changing TextToSpeechSettings.provider (a
    config change) to select a different registered adapter, never a
    change to app/conversation/ or app/tts/gateway.py.
    """

    def __init__(
        self,
        settings: TextToSpeechSettings,
        synthesizer_builder: SynthesizerBuilder | None = None,
    ) -> None:
        if not isinstance(settings, TextToSpeechSettings):
            raise ValueError(
                "TTS synthesizer factory requires validated settings"
            )
        if synthesizer_builder is not None and not callable(synthesizer_builder):
            raise ValueError("TTS synthesizer builder must be callable")

        self.settings = settings
        self._synthesizer_builder = synthesizer_builder
        self._synthesizer: TextToSpeechSynthesizer | None = None
        self._lock = RLock()

    def get(self) -> TextToSpeechSynthesizer:
        with self._lock:
            if self._synthesizer is not None:
                return self._synthesizer

            builder = self._synthesizer_builder
            if builder is None:
                builder = _BUILDER_BY_PROVIDER.get(self.settings.provider)
                if builder is None:
                    raise TTSConfigurationError(
                        "No TTS adapter is registered for the configured "
                        "provider",
                        provider=self.settings.provider,
                    )

            try:
                synthesizer = builder(self.settings)
            except TTSConfigurationError:
                raise
            except Exception as exc:
                raise TTSConfigurationError(
                    "The TTS synthesizer could not be constructed",
                    provider=self.settings.provider,
                ) from exc
            if not isinstance(synthesizer, TextToSpeechSynthesizer):
                raise TTSConfigurationError(
                    "The configured TTS adapter does not implement the "
                    "synthesizer contract",
                    provider=self.settings.provider,
                )

            self._synthesizer = synthesizer
            return self._synthesizer
