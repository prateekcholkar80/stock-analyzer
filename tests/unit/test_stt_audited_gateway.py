import unittest
from datetime import UTC, datetime

from app.audit.prompt_audit import InMemoryPromptAuditSink
from app.stt.audited_gateway import PromptAuditedSpeechToTextTranscriber
from app.stt.gateway import Transcription


class RecordingTranscriber:
    def __init__(self, transcription=None, failure=None):
        self.transcription = transcription
        self.failure = failure
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def transcribe(self, *, audio, media_type):
        self.calls.append((audio, media_type))
        if self.failure is not None:
            raise self.failure
        return self.transcription


def _transcription(**overrides):
    values = {
        "transcript": "Analyze Reliance for a swing trade, this must never be logged",
        "provider": "google",
        "language_code": "en-IN",
        "confidence": 0.92,
        "generated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return Transcription(**values)


class PromptAuditedSpeechToTextTranscriberTests(unittest.TestCase):
    def test_records_request_and_response_without_audio_or_transcript(self):
        sink = InMemoryPromptAuditSink()
        gateway = PromptAuditedSpeechToTextTranscriber(
            RecordingTranscriber(transcription=_transcription()), sink
        )

        spoken_audio = b"real-audio-bytes-that-must-never-be-logged"
        transcription = gateway.transcribe(audio=spoken_audio, media_type="audio/webm")

        self.assertEqual(transcription.provider, "google")
        self.assertEqual(
            [record["event_type"] for record in sink.records],
            ["stt_request", "stt_response"],
        )
        request = sink.records[0]
        response = sink.records[1]
        self.assertEqual(request["payload"]["audio_byte_count"], len(spoken_audio))
        self.assertEqual(request["payload"]["media_type"], "audio/webm")
        self.assertNotIn("audio", request["payload"])
        self.assertNotIn(
            "real-audio-bytes-that-must-never-be-logged", str(request["payload"])
        )
        self.assertEqual(response["payload"]["provider"], "google")
        self.assertEqual(response["payload"]["language_code"], "en-IN")
        self.assertEqual(response["payload"]["confidence"], 0.92)
        self.assertEqual(
            response["payload"]["transcript_length"],
            len(transcription.transcript),
        )
        self.assertNotIn("transcript", response["payload"])
        self.assertNotIn(
            "Analyze Reliance", str(response["payload"])
        )
        self.assertTrue(
            all(record["actor"] == "jarvis" for record in sink.records)
        )

    def test_records_only_failure_type_and_propagates_error(self):
        sink = InMemoryPromptAuditSink()
        gateway = PromptAuditedSpeechToTextTranscriber(
            RecordingTranscriber(failure=RuntimeError("api_key=secret")),
            sink,
        )

        with self.assertRaises(RuntimeError):
            gateway.transcribe(audio=b"some-audio", media_type="audio/webm")

        self.assertEqual(
            [record["event_type"] for record in sink.records],
            ["stt_request", "stt_failure"],
        )
        failure_payload = sink.records[1]["payload"]
        self.assertEqual(failure_payload["error_type"], "RuntimeError")
        self.assertNotIn("secret", str(failure_payload))

    def test_delegates_configuration_fingerprint(self):
        underlying = RecordingTranscriber(transcription=_transcription())
        gateway = PromptAuditedSpeechToTextTranscriber(underlying)

        self.assertEqual(gateway.configuration_fingerprint, "a" * 64)

    def test_rejects_invalid_dependency(self):
        with self.assertRaises(ValueError):
            PromptAuditedSpeechToTextTranscriber("invalid")


if __name__ == "__main__":
    unittest.main()
