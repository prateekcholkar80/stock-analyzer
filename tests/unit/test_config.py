import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.exceptions import ConfigurationError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.valid_environment = {
            "ANGEL_API_KEY": "test-api-key",
            "ANGEL_CLIENT_CODE": "test-client-code",
            "ANGEL_PIN": "1234",
            "ANGEL_TOTP_SECRET": "test-totp-secret",
        }

    def test_loads_valid_configuration(self):
        settings = Settings.from_environment(self.valid_environment)

        self.assertEqual(
            settings.angel_client_code.get_secret_value(),
            "test-client-code",
        )

    def test_rejects_missing_configuration(self):
        incomplete_environment = {
            "ANGEL_API_KEY": "test-api-key",
        }

        with self.assertRaises(ValidationError):
            Settings.from_environment(incomplete_environment)

    def test_rejects_blank_configuration(self):
        environment = {
            **self.valid_environment,
            "ANGEL_PIN": "   ",
        }

        with self.assertRaises(ValidationError):
            Settings.from_environment(environment)

    def test_masks_secrets_in_representation(self):
        settings = Settings.from_environment(self.valid_environment)
        representation = repr(settings)

        self.assertNotIn("test-api-key", representation)
        self.assertNotIn("test-client-code", representation)
        self.assertNotIn("test-totp-secret", representation)
        self.assertIn("**********", representation)

    def tearDown(self):
        get_settings.cache_clear()

    def test_get_settings_wraps_validation_failure(self):
        get_settings.cache_clear()

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError) as context:
                get_settings()

        self.assertIsInstance(
            context.exception.__cause__,
            ValidationError,
        )

if __name__ == "__main__":
    unittest.main()