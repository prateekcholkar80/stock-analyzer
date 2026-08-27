import unittest

from app.exceptions import TTSConfigurationError
from app.tts.adapters.elevenlabs_tts import ElevenLabsTextToSpeechSynthesizer
from app.tts.adapters.google_tts import GoogleTextToSpeechSynthesizer
from app.tts.config import TextToSpeechSettings
from app.tts.factory import _BUILDER_BY_PROVIDER, TextToSpeechSynthesizerFactory
from app.tts.gateway import TextToSpeechSynthesizer


class FakeSynthesizer:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def synthesize(self, *, text):
        raise AssertionError("synthesis is not used by factory tests")


class InvalidSynthesizer:
    pass


class RecordingBuilder:
    def __init__(self):
        self.settings = []

    def __call__(self, settings):
        self.settings.append(settings)
        return FakeSynthesizer(settings)


class TextToSpeechSynthesizerFactoryTests(unittest.TestCase):
    def test_builds_synthesizer_with_resolved_settings(self):
        settings = TextToSpeechSettings.from_environment(
            {"JARVIS_TTS_VOICE_NAME": "en-GB-Neural2-B"}
        )
        builder = RecordingBuilder()
        factory = TextToSpeechSynthesizerFactory(settings, builder)

        synthesizer = factory.get()

        self.assertIsInstance(synthesizer, TextToSpeechSynthesizer)
        self.assertEqual(synthesizer.settings.voice_name, "en-GB-Neural2-B")
        self.assertEqual(len(builder.settings), 1)

    def test_caches_one_synthesizer(self):
        builder = RecordingBuilder()
        factory = TextToSpeechSynthesizerFactory(
            TextToSpeechSettings.from_environment({}), builder
        )

        first = factory.get()
        second = factory.get()

        self.assertIs(first, second)
        self.assertEqual(len(builder.settings), 1)

    def test_default_provider_dispatches_to_google_adapter(self):
        # "google" is TextToSpeechSettings' default provider; confirm the
        # registry -- the literal config-only-swap mechanism -- resolves
        # it to the real Google adapter class, not a fake.
        self.assertIs(_BUILDER_BY_PROVIDER["google"], GoogleTextToSpeechSynthesizer)

    def test_elevenlabs_provider_dispatches_to_elevenlabs_adapter(self):
        # Confirms the second registered provider resolves to the real
        # ElevenLabs adapter class -- the literal proof that swapping
        # JARVIS_TTS_PROVIDER="elevenlabs" is a config-only change.
        self.assertIs(
            _BUILDER_BY_PROVIDER["elevenlabs"], ElevenLabsTextToSpeechSynthesizer
        )

    def test_no_explicit_builder_dispatches_by_provider_name(self):
        settings = TextToSpeechSettings.from_environment({})
        factory = TextToSpeechSynthesizerFactory(settings)

        self.assertIsNone(factory._synthesizer_builder)

    def test_unregistered_provider_raises_configuration_error(self):
        settings = TextToSpeechSettings(provider="not-a-real-provider")
        factory = TextToSpeechSynthesizerFactory(settings)

        with self.assertRaises(TTSConfigurationError):
            factory.get()

    def test_explicit_builder_overrides_provider_dispatch(self):
        settings = TextToSpeechSettings(provider="not-a-real-provider")
        builder = RecordingBuilder()
        factory = TextToSpeechSynthesizerFactory(settings, builder)

        synthesizer = factory.get()

        self.assertIsInstance(synthesizer, FakeSynthesizer)
        self.assertEqual(len(builder.settings), 1)

    def test_rejects_invalid_factory_dependencies(self):
        settings = TextToSpeechSettings.from_environment({})

        with self.assertRaises(ValueError):
            TextToSpeechSynthesizerFactory("invalid-settings", RecordingBuilder())
        with self.assertRaises(ValueError):
            TextToSpeechSynthesizerFactory(settings, "not-callable")

    def test_rejects_builder_result_that_does_not_match_contract(self):
        factory = TextToSpeechSynthesizerFactory(
            TextToSpeechSettings.from_environment({}),
            lambda settings: InvalidSynthesizer(),
        )

        with self.assertRaises(TTSConfigurationError):
            factory.get()

    def test_sanitizes_and_chains_unexpected_builder_failure(self):
        secret = "builder-secret-must-not-leak"

        def failing_builder(settings):
            raise RuntimeError(secret)

        factory = TextToSpeechSynthesizerFactory(
            TextToSpeechSettings.from_environment({}), failing_builder
        )

        with self.assertRaises(TTSConfigurationError) as context:
            factory.get()

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))

    def test_preserves_typed_configuration_failure_from_builder(self):
        expected = TTSConfigurationError(
            "Safe adapter configuration failure", provider="google"
        )

        def failing_builder(settings):
            raise expected

        factory = TextToSpeechSynthesizerFactory(
            TextToSpeechSettings.from_environment({}), failing_builder
        )

        with self.assertRaises(TTSConfigurationError) as context:
            factory.get()

        self.assertIs(context.exception, expected)


if __name__ == "__main__":
    unittest.main()
