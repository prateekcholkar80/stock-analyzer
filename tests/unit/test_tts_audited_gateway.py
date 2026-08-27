import unittest
from datetime import UTC, datetime

from app.audit.prompt_audit import InMemoryPromptAuditSink
from app.tts.audited_gateway import PromptAuditedTextToSpeechSynthesizer
from app.tts.gateway import SpeechSynthesis


class RecordingSynthesizer:
    def __init__(self, synthesis=None, failure=None):
        self.synthesis = synthesis
        self.failure = failure
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def synthesize(self, *, text):
        self.calls.append(text)
        if self.failure is not None:
            raise self.failure
        return self.synthesis


def _synthesis(**overrides):
    values = {
        "audio": b"real-audio-bytes-that-must-never-be-logged",
        "provider": "google",
        "voice": "en-GB-Neural2-B",
        "audio_encoding": "MP3",
        "media_type": "audio/mpeg",
        "generated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return SpeechSynthesis(**values)


class PromptAuditedTextToSpeechSynthesizerTests(unittest.TestCase):
    def test_records_request_and_response_without_audio_or_text(self):
        sink = InMemoryPromptAuditSink()
        gateway = PromptAuditedTextToSpeechSynthesizer(
            RecordingSynthesizer(synthesis=_synthesis()), sink
        )

        spoken_text = "Analysis complete, sir."
        synthesis = gateway.synthesize(text=spoken_text)

        self.assertEqual(synthesis.provider, "google")
        self.assertEqual(
            [record["event_type"] for record in sink.records],
            ["tts_request", "tts_response"],
        )
        request = sink.records[0]
        response = sink.records[1]
        self.assertEqual(request["payload"]["text_length"], len(spoken_text))
        self.assertNotIn("text", request["payload"])
        self.assertNotIn("Analysis complete", str(request["payload"]))
        self.assertEqual(response["payload"]["provider"], "google")
        self.assertEqual(response["payload"]["voice"], "en-GB-Neural2-B")
        self.assertEqual(response["payload"]["audio_encoding"], "MP3")
        self.assertEqual(
            response["payload"]["audio_byte_count"],
            len(b"real-audio-bytes-that-must-never-be-logged"),
        )
        self.assertNotIn("audio", response["payload"])
        self.assertNotIn(
            "real-audio-bytes-that-must-never-be-logged",
            str(response["payload"]),
        )
        self.assertTrue(
            all(record["actor"] == "jarvis" for record in sink.records)
        )

    def test_records_only_failure_type_and_propagates_error(self):
        sink = InMemoryPromptAuditSink()
        gateway = PromptAuditedTextToSpeechSynthesizer(
            RecordingSynthesizer(failure=RuntimeError("api_key=secret")),
            sink,
        )

        with self.assertRaises(RuntimeError):
            gateway.synthesize(text="Analysis complete.")

        self.assertEqual(
            [record["event_type"] for record in sink.records],
            ["tts_request", "tts_failure"],
        )
        failure_payload = sink.records[1]["payload"]
        self.assertEqual(failure_payload["error_type"], "RuntimeError")
        self.assertNotIn("secret", str(failure_payload))

    def test_delegates_configuration_fingerprint(self):
        underlying = RecordingSynthesizer(synthesis=_synthesis())
        gateway = PromptAuditedTextToSpeechSynthesizer(underlying)

        self.assertEqual(gateway.configuration_fingerprint, "a" * 64)

    def test_rejects_invalid_dependency(self):
        with self.assertRaises(ValueError):
            PromptAuditedTextToSpeechSynthesizer("invalid")


if __name__ == "__main__":
    unittest.main()
