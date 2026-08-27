import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.tts.gateway import SpeechSynthesis, TextToSpeechSynthesizer


class FakeTextToSpeechSynthesizer:
    def __init__(self):
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def synthesize(self, *, text):
        self.calls.append(text)
        return SpeechSynthesis(
            audio=b"fake-audio-bytes",
            provider="fake-provider",
            voice="fake-voice",
            audio_encoding="MP3",
            media_type="audio/mpeg",
            generated_at=datetime.now(UTC),
        )


class IncompleteSynthesizer:
    pass


def _valid_kwargs(**overrides):
    values = {
        "audio": b"some-audio-bytes",
        "provider": "google",
        "voice": "en-GB-Neural2-B",
        "audio_encoding": "MP3",
        "media_type": "audio/mpeg",
        "generated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return values


class SpeechSynthesisTests(unittest.TestCase):
    def test_preserves_audio_and_metadata(self):
        synthesis = SpeechSynthesis(**_valid_kwargs())

        self.assertEqual(synthesis.audio, b"some-audio-bytes")
        self.assertEqual(synthesis.provider, "google")
        self.assertEqual(synthesis.voice, "en-GB-Neural2-B")
        self.assertEqual(synthesis.audio_encoding, "MP3")
        self.assertEqual(synthesis.media_type, "audio/mpeg")

    def test_normalizes_identity_fields(self):
        synthesis = SpeechSynthesis(
            **_valid_kwargs(provider="  google  ", voice="  en-GB-B  ")
        )

        self.assertEqual(synthesis.provider, "google")
        self.assertEqual(synthesis.voice, "en-GB-B")

    def test_rejects_blank_provider(self):
        with self.assertRaises(ValidationError):
            SpeechSynthesis(**_valid_kwargs(provider="   "))

    def test_rejects_blank_voice(self):
        with self.assertRaises(ValidationError):
            SpeechSynthesis(**_valid_kwargs(voice="   "))

    def test_rejects_blank_audio_encoding(self):
        with self.assertRaises(ValidationError):
            SpeechSynthesis(**_valid_kwargs(audio_encoding="   "))

    def test_rejects_blank_media_type(self):
        with self.assertRaises(ValidationError):
            SpeechSynthesis(**_valid_kwargs(media_type="   "))

    def test_rejects_empty_audio(self):
        with self.assertRaises(ValidationError):
            SpeechSynthesis(**_valid_kwargs(audio=b""))

    def test_rejects_naive_generated_at(self):
        with self.assertRaises(ValidationError):
            SpeechSynthesis(**_valid_kwargs(generated_at=datetime.now()))

    def test_is_frozen(self):
        synthesis = SpeechSynthesis(**_valid_kwargs())

        with self.assertRaises(ValidationError):
            synthesis.provider = "other-provider"


class TextToSpeechSynthesizerTests(unittest.TestCase):
    def test_plain_fake_satisfies_runtime_contract(self):
        synthesizer = FakeTextToSpeechSynthesizer()

        self.assertIsInstance(synthesizer, TextToSpeechSynthesizer)

    def test_object_without_synthesize_does_not_satisfy_contract(self):
        self.assertNotIsInstance(IncompleteSynthesizer(), TextToSpeechSynthesizer)

    def test_fake_synthesizer_can_synthesize_speech(self):
        synthesizer = FakeTextToSpeechSynthesizer()

        synthesis = synthesizer.synthesize(text="Analysis complete.")

        self.assertIsInstance(synthesis, SpeechSynthesis)
        self.assertEqual(synthesizer.calls, ["Analysis complete."])


if __name__ == "__main__":
    unittest.main()
