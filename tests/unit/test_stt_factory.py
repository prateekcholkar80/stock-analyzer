import unittest

from app.exceptions import STTConfigurationError
from app.stt.adapters.elevenlabs_stt import ElevenLabsSpeechToTextTranscriber
from app.stt.adapters.google_stt import GoogleSpeechToTextTranscriber
from app.stt.config import SpeechToTextSettings
from app.stt.factory import _BUILDER_BY_PROVIDER, SpeechToTextTranscriberFactory
from app.stt.gateway import SpeechToTextTranscriber


class FakeTranscriber:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def transcribe(self, *, audio, media_type):
        raise AssertionError("transcription is not used by factory tests")


class InvalidTranscriber:
    pass


class RecordingBuilder:
    def __init__(self):
        self.settings = []

    def __call__(self, settings):
        self.settings.append(settings)
        return FakeTranscriber(settings)


class SpeechToTextTranscriberFactoryTests(unittest.TestCase):
    def test_builds_transcriber_with_resolved_settings(self):
        settings = SpeechToTextSettings.from_environment(
            {"JARVIS_STT_LANGUAGE_CODE": "en-US"}
        )
        builder = RecordingBuilder()
        factory = SpeechToTextTranscriberFactory(settings, builder)

        transcriber = factory.get()

        self.assertIsInstance(transcriber, SpeechToTextTranscriber)
        self.assertEqual(transcriber.settings.language_code, "en-US")
        self.assertEqual(len(builder.settings), 1)

    def test_caches_one_transcriber(self):
        builder = RecordingBuilder()
        factory = SpeechToTextTranscriberFactory(
            SpeechToTextSettings.from_environment({}), builder
        )

        first = factory.get()
        second = factory.get()

        self.assertIs(first, second)
        self.assertEqual(len(builder.settings), 1)

    def test_default_provider_dispatches_to_google_adapter(self):
        # "google" is SpeechToTextSettings' default provider; confirm the
        # registry -- the literal config-only-swap mechanism -- resolves
        # it to the real Google adapter class, not a fake.
        self.assertIs(
            _BUILDER_BY_PROVIDER["google"], GoogleSpeechToTextTranscriber
        )

    def test_elevenlabs_provider_dispatches_to_elevenlabs_adapter(self):
        # Confirms the second registered provider resolves to the real
        # ElevenLabs adapter class -- the literal proof that swapping
        # JARVIS_STT_PROVIDER="elevenlabs" is a config-only change.
        self.assertIs(
            _BUILDER_BY_PROVIDER["elevenlabs"],
            ElevenLabsSpeechToTextTranscriber,
        )

    def test_no_explicit_builder_dispatches_by_provider_name(self):
        settings = SpeechToTextSettings.from_environment({})
        factory = SpeechToTextTranscriberFactory(settings)

        self.assertIsNone(factory._transcriber_builder)

    def test_unregistered_provider_raises_configuration_error(self):
        settings = SpeechToTextSettings(provider="not-a-real-provider")
        factory = SpeechToTextTranscriberFactory(settings)

        with self.assertRaises(STTConfigurationError):
            factory.get()

    def test_explicit_builder_overrides_provider_dispatch(self):
        settings = SpeechToTextSettings(provider="not-a-real-provider")
        builder = RecordingBuilder()
        factory = SpeechToTextTranscriberFactory(settings, builder)

        transcriber = factory.get()

        self.assertIsInstance(transcriber, FakeTranscriber)
        self.assertEqual(len(builder.settings), 1)

    def test_rejects_invalid_factory_dependencies(self):
        settings = SpeechToTextSettings.from_environment({})

        with self.assertRaises(ValueError):
            SpeechToTextTranscriberFactory("invalid-settings", RecordingBuilder())
        with self.assertRaises(ValueError):
            SpeechToTextTranscriberFactory(settings, "not-callable")

    def test_rejects_builder_result_that_does_not_match_contract(self):
        factory = SpeechToTextTranscriberFactory(
            SpeechToTextSettings.from_environment({}),
            lambda settings: InvalidTranscriber(),
        )

        with self.assertRaises(STTConfigurationError):
            factory.get()

    def test_sanitizes_and_chains_unexpected_builder_failure(self):
        secret = "builder-secret-must-not-leak"

        def failing_builder(settings):
            raise RuntimeError(secret)

        factory = SpeechToTextTranscriberFactory(
            SpeechToTextSettings.from_environment({}), failing_builder
        )

        with self.assertRaises(STTConfigurationError) as context:
            factory.get()

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))

    def test_preserves_typed_configuration_failure_from_builder(self):
        expected = STTConfigurationError(
            "Safe adapter configuration failure", provider="google"
        )

        def failing_builder(settings):
            raise expected

        factory = SpeechToTextTranscriberFactory(
            SpeechToTextSettings.from_environment({}), failing_builder
        )

        with self.assertRaises(STTConfigurationError) as context:
            factory.get()

        self.assertIs(context.exception, expected)


if __name__ == "__main__":
    unittest.main()
