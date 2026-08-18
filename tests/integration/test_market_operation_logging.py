from io import StringIO
import json
import logging
import unittest

from app.angel.client import AngelOneClient
from app.config import Settings
from app.logging_config import (
    LOGGER_NAMESPACE,
    JsonLogFormatter,
    OperationContextFilter,
)
from app.models.market import MarketQuote
from app.services.market_data import MarketDataService


class FakeSmartConnect:
    def __init__(self, api_key):
        self.api_key = api_key

    def generateSession(
        self,
        client_code,
        pin,
        totp,
    ):
        return {
            "status": True,
            "data": {
                "jwtToken": "integration-jwt-token",
            },
        }

    def ltpData(
        self,
        exchange,
        symbol,
        symbol_token,
    ):
        return {
            "status": True,
            "data": {
                "ltp": 1320.0,
                "open": 1314.0,
                "high": 1320.8,
                "low": 1298.1,
                "close": 1305.0,
            },
        }


class MarketOperationLoggingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.application_logger = logging.getLogger(
            LOGGER_NAMESPACE
        )
        self.original_handlers = list(
            self.application_logger.handlers
        )
        self.original_level = self.application_logger.level
        self.original_propagate = (
            self.application_logger.propagate
        )

        self.stream = StringIO()
        self.handler = logging.StreamHandler(
            self.stream
        )
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(
            JsonLogFormatter()
        )
        self.handler.addFilter(
            OperationContextFilter()
        )

        self.application_logger.handlers = [
            self.handler
        ]
        self.application_logger.setLevel(
            logging.DEBUG
        )
        self.application_logger.propagate = False

        self.settings = Settings.from_environment(
            {
                "ANGEL_API_KEY": "integration-api-key",
                "ANGEL_CLIENT_CODE": "integration-client-code",
                "ANGEL_PIN": "1234",
                "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
            }
        )

    def tearDown(self):
        self.application_logger.handlers = (
            self.original_handlers
        )
        self.application_logger.setLevel(
            self.original_level
        )
        self.application_logger.propagate = (
            self.original_propagate
        )
        self.handler.close()
        self.stream.close()

    def test_quote_operation_shares_id_across_service_and_gateway(self):
        gateway = AngelOneClient(
            settings=self.settings,
            client_factory=FakeSmartConnect,
        )
        service = MarketDataService(
            gateway=gateway
        )
        service.initialize()

        self.stream.seek(0)
        self.stream.truncate(0)

        quote = service.get_quote(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
        )

        payloads = [
            json.loads(line)
            for line in self.stream.getvalue().splitlines()
        ]
        events = [
            payload["context"]["event"]
            for payload in payloads
        ]
        operation_ids = {
            payload["operation_id"]
            for payload in payloads
        }
        serialized_logs = self.stream.getvalue()

        self.assertIsInstance(
            quote,
            MarketQuote,
        )
        self.assertEqual(
            events,
            [
                "market.quote.started",
                "angel.quote.started",
                "angel.quote.succeeded",
                "market.quote.succeeded",
            ],
        )
        self.assertEqual(
            len(operation_ids),
            1,
        )
        self.assertNotIn(
            "integration-api-key",
            serialized_logs,
        )
        self.assertNotIn(
            "integration-client-code",
            serialized_logs,
        )
        self.assertNotIn(
            "integration-jwt-token",
            serialized_logs,
        )
        self.assertNotIn(
            "JBSWY3DPEHPK3PXP",
            serialized_logs,
        )


if __name__ == "__main__":
    unittest.main()
