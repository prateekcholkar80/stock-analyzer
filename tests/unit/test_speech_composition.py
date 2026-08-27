import unittest
from datetime import UTC, datetime

from app.composition.speech import (
    compose_jarvis_speech_synthesis,
    compose_jarvis_speech_transcription,
)
from app.stt.config import SpeechToTextSettings
from app.stt.gateway import Transcription
from app.tts.config import TextToSpeechSettings
from app.tts.gateway import SpeechSynthesis


class FakeSynthesizer:
    def __init__(self, settings):
        self.settings = settings
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "b" * 64

    def synthesize(self, *, text):
        self.calls.append(text)
        return SpeechSynthesis(
            audio=b"fake-audio",
            provider="fake-provider",
            voice="fake-voice",
            audio_encoding="MP3",
            media_type="audio/mpeg",
            generated_at=datetime.now(UTC),
        )


class ComposeJarvisSpeechSynthesisTests(unittest.TestCase):
    def test_builds_nothing_until_first_use(self):
        def eager_builder(settings):
            raise AssertionError(
                "synthesizer must not be built before first use"
            )

        speech = compose_jarvis_speech_synthesis(
            settings=TextToSpeechSettings.from_environment({}),
            synthesizer_builder=eager_builder,
        )

        # Composition itself never touched the builder.
        self.assertIsNotNone(speech)

    def test_synthesize_lazily_builds_and_caches(self):
        calls = []

        def recording_builder(settings):
            calls.append(settings)
            return FakeSynthesizer(settings)

        speech = compose_jarvis_speech_synthesis(
            settings=TextToSpeechSettings.from_environment({}),
            synthesizer_builder=recording_builder,
        )

        first = speech.synthesize(text="Hello")
        second = speech.synthesize(text="World")

        self.assertEqual(first.provider, "fake-provider")
        self.assertEqual(second.provider, "fake-provider")
        self.assertEqual(len(calls), 1)

    def test_configuration_fingerprint_triggers_lazy_build(self):
        def recording_builder(settings):
            return FakeSynthesizer(settings)

        speech = compose_jarvis_speech_synthesis(
            settings=TextToSpeechSettings.from_environment({}),
            synthesizer_builder=recording_builder,
        )

        self.assertEqual(speech.configuration_fingerprint, "b" * 64)


class FakeTranscriber:
    def __init__(self, settings):
        self.settings = settings
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "c" * 64

    def transcribe(self, *, audio, media_type):
        self.calls.append((audio, media_type))
        return Transcription(
            transcript="Analyze Reliance",
            provider="fake-provider",
            language_code="en-IN",
            confidence=0.9,
            generated_at=datetime.now(UTC),
        )


class ComposeJarvisSpeechTranscriptionTests(unittest.TestCase):
    def test_builds_nothing_until_first_use(self):
        def eager_builder(settings):
            raise AssertionError(
                "transcriber must not be built before first use"
            )

        speech = compose_jarvis_speech_transcription(
            settings=SpeechToTextSettings.from_environment({}),
            transcriber_builder=eager_builder,
        )

        # Composition itself never touched the builder.
        self.assertIsNotNone(speech)

    def test_transcribe_lazily_builds_and_caches(self):
        calls = []

        def recording_builder(settings):
            calls.append(settings)
            return FakeTranscriber(settings)

        speech = compose_jarvis_speech_transcription(
            settings=SpeechToTextSettings.from_environment({}),
            transcriber_builder=recording_builder,
        )

        first = speech.transcribe(audio=b"one", media_type="audio/webm")
        second = speech.transcribe(audio=b"two", media_type="audio/webm")

        self.assertEqual(first.provider, "fake-provider")
        self.assertEqual(second.provider, "fake-provider")
        self.assertEqual(len(calls), 1)

    def test_configuration_fingerprint_triggers_lazy_build(self):
        def recording_builder(settings):
            return FakeTranscriber(settings)

        speech = compose_jarvis_speech_transcription(
            settings=SpeechToTextSettings.from_environment({}),
            transcriber_builder=recording_builder,
        )

        self.assertEqual(speech.configuration_fingerprint, "c" * 64)


if __name__ == "__main__":
    unittest.main()
