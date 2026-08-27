from threading import RLock

from app.audit.prompt_audit import PromptAuditSink
from app.stt.audited_gateway import PromptAuditedSpeechToTextTranscriber
from app.stt.config import SpeechToTextSettings, get_stt_settings
from app.stt.factory import SpeechToTextTranscriberFactory, TranscriberBuilder
from app.stt.gateway import Transcription
from app.tts.audited_gateway import PromptAuditedTextToSpeechSynthesizer
from app.tts.config import TextToSpeechSettings, get_tts_settings
from app.tts.factory import SynthesizerBuilder, TextToSpeechSynthesizerFactory
from app.tts.gateway import SpeechSynthesis


class _LazyTextToSpeechSynthesizer:
    """Build the TTS synthesizer only on first use, mirroring
    _LazyTickerResolutionExecutor's lazy-build pattern
    (app/composition/research.py).
    """

    def __init__(
        self,
        *,
        settings: TextToSpeechSettings | None = None,
        synthesizer_builder: SynthesizerBuilder | None = None,
        prompt_audit_sink: PromptAuditSink | None = None,
    ) -> None:
        self._settings = settings
        self._synthesizer_builder = synthesizer_builder
        self._prompt_audit_sink = prompt_audit_sink
        self._synthesizer: PromptAuditedTextToSpeechSynthesizer | None = None
        self._lock = RLock()

    def synthesize(self, *, text: str) -> SpeechSynthesis:
        return self._get_synthesizer().synthesize(text=text)

    @property
    def configuration_fingerprint(self) -> str:
        return self._get_synthesizer().configuration_fingerprint

    def _get_synthesizer(self) -> PromptAuditedTextToSpeechSynthesizer:
        with self._lock:
            if self._synthesizer is None:
                settings = self._settings or get_tts_settings()
                factory = (
                    TextToSpeechSynthesizerFactory(settings)
                    if self._synthesizer_builder is None
                    else TextToSpeechSynthesizerFactory(
                        settings, self._synthesizer_builder
                    )
                )
                self._synthesizer = PromptAuditedTextToSpeechSynthesizer(
                    factory.get(), self._prompt_audit_sink
                )
            return self._synthesizer


def compose_jarvis_speech_synthesis(
    *,
    settings: TextToSpeechSettings | None = None,
    synthesizer_builder: SynthesizerBuilder | None = None,
    prompt_audit_sink: PromptAuditSink | None = None,
) -> _LazyTextToSpeechSynthesizer:
    """Compose a provider-neutral TTS synthesizer without eager I/O.

    Deliberately not threaded through compose_jarvis_conversation or
    compose_jarvis_swing_research: TTS never eagerly fires as part of
    producing a conversation turn, so it does not belong in that
    dependency graph. Compose it independently, alongside
    BrowserConversationCoordinator, at the same composition-root call
    site (see examples/browser_api.py).
    """
    return _LazyTextToSpeechSynthesizer(
        settings=settings,
        synthesizer_builder=synthesizer_builder,
        prompt_audit_sink=prompt_audit_sink,
    )


class _LazySpeechToTextTranscriber:
    """Build the STT transcriber only on first use, mirroring
    _LazyTextToSpeechSynthesizer above.
    """

    def __init__(
        self,
        *,
        settings: SpeechToTextSettings | None = None,
        transcriber_builder: TranscriberBuilder | None = None,
        prompt_audit_sink: PromptAuditSink | None = None,
    ) -> None:
        self._settings = settings
        self._transcriber_builder = transcriber_builder
        self._prompt_audit_sink = prompt_audit_sink
        self._transcriber: PromptAuditedSpeechToTextTranscriber | None = None
        self._lock = RLock()

    def transcribe(self, *, audio: bytes, media_type: str) -> Transcription:
        return self._get_transcriber().transcribe(audio=audio, media_type=media_type)

    @property
    def configuration_fingerprint(self) -> str:
        return self._get_transcriber().configuration_fingerprint

    def _get_transcriber(self) -> PromptAuditedSpeechToTextTranscriber:
        with self._lock:
            if self._transcriber is None:
                settings = self._settings or get_stt_settings()
                factory = (
                    SpeechToTextTranscriberFactory(settings)
                    if self._transcriber_builder is None
                    else SpeechToTextTranscriberFactory(
                        settings, self._transcriber_builder
                    )
                )
                self._transcriber = PromptAuditedSpeechToTextTranscriber(
                    factory.get(), self._prompt_audit_sink
                )
            return self._transcriber


def compose_jarvis_speech_transcription(
    *,
    settings: SpeechToTextSettings | None = None,
    transcriber_builder: TranscriberBuilder | None = None,
    prompt_audit_sink: PromptAuditSink | None = None,
) -> _LazySpeechToTextTranscriber:
    """Compose a provider-neutral STT transcriber without eager I/O.

    Deliberately not threaded through compose_jarvis_conversation or
    compose_jarvis_swing_research: STT never eagerly fires as part of
    producing a conversation turn, so it does not belong in that
    dependency graph. Compose it independently, alongside
    BrowserConversationCoordinator and compose_jarvis_speech_synthesis,
    at the same composition-root call site (see examples/browser_api.py).
    """
    return _LazySpeechToTextTranscriber(
        settings=settings,
        transcriber_builder=transcriber_builder,
        prompt_audit_sink=prompt_audit_sink,
    )
