import json
import logging
import unittest

from app.logging_config import (
    JsonLogFormatter,
    OperationContextFilter,
    REDACTED_VALUE,
    get_operation_id,
    operation_context,
)


class JsonLogFormatterTests(unittest.TestCase):
    def format_record(
        self,
        message,
        **extra,
    ):
        record = logging.LogRecord(
            name="jarvis.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )

        for key, value in extra.items():
            setattr(record, key, value)

        return json.loads(
            JsonLogFormatter().format(record)
        )

    def test_formats_structured_log_record(self):
        payload = self.format_record(
            "Quote retrieved",
            event="market.quote.retrieved",
            symbol="RELIANCE-EQ",
            symbol_token="2885",
        )

        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(
            payload["logger"],
            "jarvis.test",
        )
        self.assertEqual(
            payload["message"],
            "Quote retrieved",
        )
        self.assertEqual(
            payload["context"]["event"],
            "market.quote.retrieved",
        )
        self.assertEqual(
            payload["context"]["symbol_token"],
            "2885",
        )

    def test_redacts_nested_sensitive_context(self):
        payload = self.format_record(
            "Authentication failed",
            api_key="real-api-key",
            credentials={
                "jwtToken": "real-jwt-token",
                "refresh_token": "real-refresh-token",
                "symbol_token": "2885",
            },
        )

        self.assertEqual(
            payload["context"]["api_key"],
            REDACTED_VALUE,
        )
        self.assertEqual(
            payload["context"]["credentials"]["jwtToken"],
            REDACTED_VALUE,
        )
        self.assertEqual(
            payload["context"]["credentials"]["refresh_token"],
            REDACTED_VALUE,
        )
        self.assertEqual(
            payload["context"]["credentials"]["symbol_token"],
            "2885",
        )

    def test_redacts_secrets_embedded_in_message(self):
        payload = self.format_record(
            "Login failed api_key=real-secret "
            "Bearer abc.def.ghi"
        )

        self.assertNotIn(
            "real-secret",
            payload["message"],
        )
        self.assertNotIn(
            "abc.def.ghi",
            payload["message"],
        )
        self.assertIn(
            REDACTED_VALUE,
            payload["message"],
        )


class OperationContextTests(unittest.TestCase):
    def test_sets_and_restores_operation_id(self):
        self.assertIsNone(get_operation_id())

        with operation_context(
            "market-operation-123"
        ) as operation_id:
            self.assertEqual(
                operation_id,
                "market-operation-123",
            )
            self.assertEqual(
                get_operation_id(),
                "market-operation-123",
            )

        self.assertIsNone(get_operation_id())

    def test_nested_context_restores_parent_id(self):
        with operation_context("parent-operation"):
            self.assertEqual(
                get_operation_id(),
                "parent-operation",
            )

            with operation_context("child-operation"):
                self.assertEqual(
                    get_operation_id(),
                    "child-operation",
                )

            self.assertEqual(
                get_operation_id(),
                "parent-operation",
            )

        self.assertIsNone(get_operation_id())

    def test_filter_adds_operation_id_to_log_record(self):
        record = logging.LogRecord(
            name="jarvis.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Quote retrieved",
            args=(),
            exc_info=None,
        )

        with operation_context("quote-operation-123"):
            log_filter = OperationContextFilter()

            self.assertTrue(
                log_filter.filter(record)
            )

        payload = json.loads(
            JsonLogFormatter().format(record)
        )

        self.assertEqual(
            payload["operation_id"],
            "quote-operation-123",
        )


if __name__ == "__main__":
    unittest.main()
