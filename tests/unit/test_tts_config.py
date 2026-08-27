import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.exceptions import TTSConfigurationError
from app.tts.config import TextToSpeechSettings, get_tts_settings


class TextToSpeechSettingsTests(unittest.TestCase):
    def tearDown(self):
        get_tts_settings.cache_clear()

    def test_defaults_require_no_configuration(self):
        settings = TextToSpeechSettings.from_environment({})

        self.assertEqual(settings.provider, "google")
        self.assertIsNone(settings.voice_name)
        self.assertEqual(settings.language_code, "en-GB")
        self.assertEqual(settings.audio_encoding, "MP3")
        self.assertEqual(settings.speaking_rate, 1.0)
        self.assertIsNone(settings.model_id)

    def test_reads_overrides_from_environment(self):
        settings = TextToSpeechSettings.from_environment(
            {
                "JARVIS_TTS_PROVIDER": "  elevenlabs  ",
                "JARVIS_TTS_VOICE_NAME": "  7yOhoTp3hxlERyEyTQBt  ",
                "JARVIS_TTS_LANGUAGE_CODE": "en-US",
                "JARVIS_TTS_AUDIO_ENCODING": "OGG_OPUS",
                "JARVIS_TTS_SPEAKING_RATE": "1.5",
                "JARVIS_TTS_MODEL_ID": "  eleven_v3  ",
            }
        )

        self.assertEqual(settings.provider, "elevenlabs")
        self.assertEqual(settings.voice_name, "7yOhoTp3hxlERyEyTQBt")
        self.assertEqual(settings.language_code, "en-US")
        self.assertEqual(settings.audio_encoding, "OGG_OPUS")
        self.assertEqual(settings.speaking_rate, 1.5)
        self.assertEqual(settings.model_id, "eleven_v3")

    def test_blank_voice_name_normalizes_to_none(self):
        settings = TextToSpeechSettings.from_environment(
            {"JARVIS_TTS_VOICE_NAME": "   "}
        )

        self.assertIsNone(settings.voice_name)

    def test_blank_model_id_normalizes_to_none(self):
        settings = TextToSpeechSettings.from_environment(
            {"JARVIS_TTS_MODEL_ID": "   "}
        )

        self.assertIsNone(settings.model_id)

    def test_rejects_non_numeric_speaking_rate(self):
        with self.assertRaises(ValueError):
            TextToSpeechSettings.from_environment(
                {"JARVIS_TTS_SPEAKING_RATE": "not-a-number"}
            )

    def test_rejects_speaking_rate_outside_bounds(self):
        for value in ("0.1", "4.5"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    TextToSpeechSettings.from_environment(
                        {"JARVIS_TTS_SPEAKING_RATE": value}
                    )

    def test_rejects_blank_provider(self):
        with self.assertRaises(ValidationError):
            TextToSpeechSettings(provider="   ")

    def test_configuration_is_immutable(self):
        settings = TextToSpeechSettings.from_environment({})

        with self.assertRaises(ValidationError):
            settings.provider = "other"

    def test_get_tts_settings_wraps_environment_validation_failure(self):
        get_tts_settings.cache_clear()

        with patch.dict(
            os.environ,
            {"JARVIS_TTS_SPEAKING_RATE": "9.9"},
            clear=True,
        ):
            with self.assertRaises(TTSConfigurationError) as context:
                get_tts_settings()

        self.assertIsInstance(context.exception.__cause__, ValidationError)


if __name__ == "__main__":
    unittest.main()
