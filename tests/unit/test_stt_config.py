import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.exceptions import STTConfigurationError
from app.stt.config import SpeechToTextSettings, get_stt_settings


class SpeechToTextSettingsTests(unittest.TestCase):
    def tearDown(self):
        get_stt_settings.cache_clear()

    def test_defaults_require_no_configuration(self):
        settings = SpeechToTextSettings.from_environment({})

        self.assertEqual(settings.provider, "google")
        self.assertEqual(settings.language_code, "en-IN")
        self.assertEqual(settings.audio_encoding, "WEBM_OPUS")
        self.assertIsNone(settings.sample_rate_hertz)
        self.assertIsNone(settings.model_id)

    def test_reads_overrides_from_environment(self):
        settings = SpeechToTextSettings.from_environment(
            {
                "JARVIS_STT_PROVIDER": "  elevenlabs  ",
                "JARVIS_STT_LANGUAGE_CODE": "en-US",
                "JARVIS_STT_AUDIO_ENCODING": "LINEAR16",
                "JARVIS_STT_SAMPLE_RATE_HERTZ": "16000",
                "JARVIS_STT_MODEL_ID": "  scribe_v1  ",
            }
        )

        self.assertEqual(settings.provider, "elevenlabs")
        self.assertEqual(settings.language_code, "en-US")
        self.assertEqual(settings.audio_encoding, "LINEAR16")
        self.assertEqual(settings.sample_rate_hertz, 16000)
        self.assertEqual(settings.model_id, "scribe_v1")

    def test_blank_model_id_normalizes_to_none(self):
        settings = SpeechToTextSettings.from_environment(
            {"JARVIS_STT_MODEL_ID": "   "}
        )

        self.assertIsNone(settings.model_id)

    def test_rejects_non_integer_sample_rate(self):
        with self.assertRaises(ValueError):
            SpeechToTextSettings.from_environment(
                {"JARVIS_STT_SAMPLE_RATE_HERTZ": "not-a-number"}
            )

    def test_rejects_non_positive_sample_rate(self):
        with self.assertRaises(ValidationError):
            SpeechToTextSettings.from_environment(
                {"JARVIS_STT_SAMPLE_RATE_HERTZ": "0"}
            )

    def test_rejects_blank_provider(self):
        with self.assertRaises(ValidationError):
            SpeechToTextSettings(provider="   ")

    def test_configuration_is_immutable(self):
        settings = SpeechToTextSettings.from_environment({})

        with self.assertRaises(ValidationError):
            settings.provider = "other"

    def test_get_stt_settings_wraps_environment_validation_failure(self):
        get_stt_settings.cache_clear()

        with patch.dict(
            os.environ,
            {"JARVIS_STT_SAMPLE_RATE_HERTZ": "-1"},
            clear=True,
        ):
            with self.assertRaises(STTConfigurationError) as context:
                get_stt_settings()

        self.assertIsInstance(context.exception.__cause__, ValidationError)


if __name__ == "__main__":
    unittest.main()
