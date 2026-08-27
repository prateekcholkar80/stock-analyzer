from app.audit.prompt_audit import (
    PromptAuditActor,
    PromptAuditEventType,
    PromptAuditRecorder,
    PromptAuditSink,
)
from app.tts.gateway import SpeechSynthesis, TextToSpeechSynthesizer


class PromptAuditedTextToSpeechSynthesizer:
    """Audit TTS calls without ever recording the synthesized audio or
    the synthesized text -- the text is already fully audited via
    CONVERSATION_OUTPUT records, and audio bytes don't belong in a JSONL
    review trail. Only length/provider/voice/duration-style metadata is
    recorded.
    """

    def __init__(
        self,
        synthesizer: TextToSpeechSynthesizer,
        sink: PromptAuditSink | None = None,
    ) -> None:
        if not isinstance(synthesizer, TextToSpeechSynthesizer):
            raise ValueError("audited TTS synthesizer requires a synthesizer")
        self._synthesizer = synthesizer
        self._recorder = PromptAuditRecorder(sink)

    @property
    def configuration_fingerprint(self) -> str:
        return self._synthesizer.configuration_fingerprint

    def synthesize(self, *, text: str) -> SpeechSynthesis:
        self._recorder.record(
            PromptAuditEventType.TTS_REQUEST,
            PromptAuditActor.JARVIS,
            {"text_length": len(text) if isinstance(text, str) else None},
        )
        try:
            synthesis = self._synthesizer.synthesize(text=text)
        except Exception as exc:
            self._recorder.record(
                PromptAuditEventType.TTS_FAILURE,
                PromptAuditActor.JARVIS,
                {"error_type": type(exc).__name__},
            )
            raise
        self._recorder.record(
            PromptAuditEventType.TTS_RESPONSE,
            PromptAuditActor.JARVIS,
            {
                "provider": synthesis.provider,
                "voice": synthesis.voice,
                "audio_encoding": synthesis.audio_encoding,
                "audio_byte_count": len(synthesis.audio),
                "generated_at": synthesis.generated_at.isoformat(),
            },
        )
        return synthesis
