import unittest
from unittest.mock import patch

from app.exceptions import AuthenticationError
from app.runtime import run_entrypoint


class RuntimeTests(unittest.TestCase):
    def test_configures_logging_and_returns_zero_on_success(self):
        with patch(
            "app.runtime.configure_logging"
        ) as configure_logging:
            result = run_entrypoint(
                lambda: None,
                logger_name="tests.runtime",
            )

        configure_logging.assert_called_once_with()
        self.assertEqual(result, 0)

    def test_returns_one_without_logging_exception_details(self):
        def failing_entrypoint():
            try:
                raise RuntimeError(
                    "api_key=vendor-secret"
                )
            except RuntimeError as exc:
                raise AuthenticationError(
                    "Unable to authenticate"
                ) from exc

        with patch("app.runtime.configure_logging"):
            with self.assertLogs(
                "jarvis.tests.runtime",
                level="ERROR",
            ) as captured:
                result = run_entrypoint(
                    failing_entrypoint,
                    logger_name="tests.runtime",
                )

        self.assertEqual(result, 1)
        self.assertEqual(
            captured.records[0].event,
            "application.entrypoint.failed",
        )
        self.assertEqual(
            captured.records[0].error_type,
            "AuthenticationError",
        )
        self.assertNotIn(
            "vendor-secret",
            captured.records[0].getMessage(),
        )

    def test_does_not_hide_unexpected_programming_errors(self):
        def failing_entrypoint():
            raise ValueError("Programming error")

        with patch("app.runtime.configure_logging"):
            with self.assertRaisesRegex(
                ValueError,
                "Programming error",
            ):
                run_entrypoint(
                    failing_entrypoint,
                    logger_name="tests.runtime",
                )


if __name__ == "__main__":
    unittest.main()
