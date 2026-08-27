import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.stt.gateway import SpeechToTextTranscriber, Transcription


class FakeSpeechToTextTranscriber:
    def __init__(self):
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def transcribe(self, *, audio, media_type):
        self.calls.append((audio, media_type))
        return Transcription(
            transcript="Analyze Reliance for a swing trade",
            provider="fake-provider",
            language_code="en-IN",
            confidence=0.92,
            generated_at=datetime.now(UTC),
        )


class IncompleteTranscriber:
    pass


def _valid_kwargs(**overrides):
    values = {
        "transcript": "Analyze Reliance for a swing trade",
        "provider": "google",
        "language_code": "en-IN",
        "confidence": 0.9,
        "generated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return values


class TranscriptionTests(unittest.TestCase):
    def test_preserves_transcript_and_metadata(self):
        transcription = Transcription(**_valid_kwargs())

        self.assertEqual(
            transcription.transcript, "Analyze Reliance for a swing trade"
        )
        self.assertEqual(transcription.provider, "google")
        self.assertEqual(transcription.language_code, "en-IN")
        self.assertEqual(transcription.confidence, 0.9)

    def test_allows_empty_transcript_as_valid_no_speech_result(self):
        transcription = Transcription(
            **_valid_kwargs(transcript="", confidence=None)
        )

        self.assertEqual(transcription.transcript, "")
        self.assertIsNone(transcription.confidence)

    def test_normalizes_identity_fields(self):
        transcription = Transcription(
            **_valid_kwargs(provider="  google  ", language_code="  en-IN  ")
        )

        self.assertEqual(transcription.provider, "google")
        self.assertEqual(transcription.language_code, "en-IN")

    def test_rejects_blank_provider(self):
        with self.assertRaises(ValidationError):
            Transcription(**_valid_kwargs(provider="   "))

    def test_rejects_blank_language_code(self):
        with self.assertRaises(ValidationError):
            Transcription(**_valid_kwargs(language_code="   "))

    def test_rejects_confidence_outside_bounds(self):
        for value in (-0.1, 1.1):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Transcription(**_valid_kwargs(confidence=value))

    def test_rejects_naive_generated_at(self):
        with self.assertRaises(ValidationError):
            Transcription(**_valid_kwargs(generated_at=datetime.now()))

    def test_is_frozen(self):
        transcription = Transcription(**_valid_kwargs())

        with self.assertRaises(ValidationError):
            transcription.provider = "other-provider"


class SpeechToTextTranscriberTests(unittest.TestCase):
    def test_plain_fake_satisfies_runtime_contract(self):
        transcriber = FakeSpeechToTextTranscriber()

        self.assertIsInstance(transcriber, SpeechToTextTranscriber)

    def test_object_without_transcribe_does_not_satisfy_contract(self):
        self.assertNotIsInstance(IncompleteTranscriber(), SpeechToTextTranscriber)

    def test_fake_transcriber_can_transcribe_audio(self):
        transcriber = FakeSpeechToTextTranscriber()

        transcription = transcriber.transcribe(
            audio=b"fake-audio-bytes", media_type="audio/webm"
        )

        self.assertIsInstance(transcription, Transcription)
        self.assertEqual(
            transcriber.calls, [(b"fake-audio-bytes", "audio/webm")]
        )


if __name__ == "__main__":
    unittest.main()
