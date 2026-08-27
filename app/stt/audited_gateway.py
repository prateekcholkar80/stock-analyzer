from app.audit.prompt_audit import (
    PromptAuditActor,
    PromptAuditEventType,
    PromptAuditRecorder,
    PromptAuditSink,
)
from app.stt.gateway import SpeechToTextTranscriber, Transcription


class PromptAuditedSpeechToTextTranscriber:
    """Audit STT calls without ever recording the raw audio or the
    transcribed text -- any transcript that becomes a real turn is
    already fully audited via CONVERSATION_INPUT records moments later,
    and audio bytes don't belong in a JSONL review trail. Recording the
    transcript here too would duplicate that same sensitive content in a
    second trail, and would create an orphan text record for VAD
    false-positives the frontend never actually submits. Only
    length/provider/language/confidence-style metadata is recorded.
    """

    def __init__(
        self,
        transcriber: SpeechToTextTranscriber,
        sink: PromptAuditSink | None = None,
    ) -> None:
        if not isinstance(transcriber, SpeechToTextTranscriber):
            raise ValueError("audited STT transcriber requires a transcriber")
        self._transcriber = transcriber
        self._recorder = PromptAuditRecorder(sink)

    @property
    def configuration_fingerprint(self) -> str:
        return self._transcriber.configuration_fingerprint

    def transcribe(self, *, audio: bytes, media_type: str) -> Transcription:
        self._recorder.record(
            PromptAuditEventType.STT_REQUEST,
            PromptAuditActor.JARVIS,
            {
                "audio_byte_count": len(audio) if isinstance(audio, bytes) else None,
                "media_type": media_type,
            },
        )
        try:
            transcription = self._transcriber.transcribe(
                audio=audio, media_type=media_type
            )
        except Exception as exc:
            self._recorder.record(
                PromptAuditEventType.STT_FAILURE,
                PromptAuditActor.JARVIS,
                {"error_type": type(exc).__name__},
            )
            raise
        self._recorder.record(
            PromptAuditEventType.STT_RESPONSE,
            PromptAuditActor.JARVIS,
            {
                "provider": transcription.provider,
                "language_code": transcription.language_code,
                "confidence": transcription.confidence,
                "transcript_length": len(transcription.transcript),
                "generated_at": transcription.generated_at.isoformat(),
            },
        )
        return transcription
