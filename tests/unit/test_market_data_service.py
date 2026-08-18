from datetime import datetime, timedelta, timezone
import unittest

from app.exceptions import (
    ClientNotInitializedError,
    DataValidationError,
    MarketDataError,
)
from app.models.market import (
    HistoricalCandleSeries,
    MarketQuote,
)
from app.services.market_data import MarketDataService


class FakeMarketDataGateway:
    def __init__(self):
        self.initialized = False
        self.initialization_error = None
        self.last_quote_request = None
        self.last_history_request = None

        self.quote_response = {
            "status": True,
            "data": {
                "ltp": 1320.0,
                "open": 1314.0,
                "high": 1320.8,
                "low": 1298.1,
                "close": 1305.0,
            },
        }
        self.history_response = {
            "status": True,
            "data": [],
        }

    def initialize(self):
        if self.initialization_error is not None:
            raise self.initialization_error

        self.initialized = True

    def get_ltp(
        self,
        exchange,
        symbol_token,
        symbol,
    ):
        self.last_quote_request = {
            "exchange": exchange,
            "symbol_token": symbol_token,
            "symbol": symbol,
        }

        return self.quote_response

    def get_historical_candles(
        self,
        exchange,
        symbol_token,
        interval,
        from_date,
        to_date,
    ):
        self.last_history_request = {
            "exchange": exchange,
            "symbol_token": symbol_token,
            "interval": interval,
            "from_date": from_date,
            "to_date": to_date,
        }

        return self.history_response


class MarketDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeMarketDataGateway()
        self.service = MarketDataService(gateway=self.gateway)
        self.observed_at = datetime(
            2026,
            8,
            17,
            10,
            0,
            tzinfo=timezone.utc,
        )

    def test_initializes_injected_gateway(self):
        self.service.initialize()

        self.assertTrue(self.gateway.initialized)
        self.assertTrue(self.service.initialized)

    def test_logs_initialization_lifecycle(self):
        with self.assertLogs(
            "jarvis.services.market_data",
            level="INFO",
        ) as captured:
            self.service.initialize()

        events = [
            getattr(record, "event", None)
            for record in captured.records
        ]

        self.assertEqual(
            events,
            [
                "market.service.initialization.started",
                "market.service.initialization.succeeded",
            ],
        )
        self.assertTrue(
            hasattr(captured.records[-1], "duration_ms")
        )

    def test_preserves_gateway_initialization_failure(self):
        failure = RuntimeError(
            "api_key=vendor-secret"
        )
        self.gateway.initialization_error = failure

        with self.assertLogs(
            "jarvis.services.market_data",
            level="ERROR",
        ) as captured:
            with self.assertRaises(RuntimeError) as context:
                self.service.initialize()

        self.assertIs(
            context.exception,
            failure,
        )
        self.assertFalse(
            self.service.initialized
        )
        self.assertEqual(
            captured.records[0].event,
            "market.service.initialization.failed",
        )
        self.assertEqual(
            captured.records[0].error_type,
            "RuntimeError",
        )
        self.assertNotIn(
            "vendor-secret",
            captured.records[0].getMessage(),
        )

    def test_rejects_request_before_initialization(self):
        with self.assertRaises(ClientNotInitializedError):
            self.service.get_quote(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                observed_at=self.observed_at,
            )

    def test_rejects_historical_request_before_initialization(self):
        with self.assertLogs(
            "jarvis.services.market_data",
            level="ERROR",
        ) as captured:
            with self.assertRaises(ClientNotInitializedError):
                self.service.get_historical_series(
                    exchange="NSE",
                    symbol_token="2885",
                    symbol="RELIANCE-EQ",
                    interval="ONE_DAY",
                    from_date="2026-08-01 09:15",
                    to_date="2026-08-17 15:30",
                )

        self.assertEqual(
            captured.records[0].event,
            "market.history.failed",
        )
        self.assertEqual(
            captured.records[0].error_type,
            "ClientNotInitializedError",
        )

    def test_delegates_quote_request_to_gateway(self):
        self.service.initialize()

        quote = self.service.get_quote(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            observed_at=self.observed_at,
        )

        self.assertEqual(quote.price, 1320.0)
        self.assertEqual(
            self.gateway.last_quote_request,
            {
                "exchange": "NSE",
                "symbol_token": "2885",
                "symbol": "RELIANCE-EQ",
            },
        )

    def test_logs_quote_lifecycle(self):
        self.service.initialize()

        with self.assertLogs(
            "jarvis.services.market_data",
            level="DEBUG",
        ) as captured:
            self.service.get_quote(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                observed_at=self.observed_at,
            )

        events = [
            getattr(record, "event", None)
            for record in captured.records
        ]

        self.assertEqual(
            events,
            [
                "market.quote.started",
                "market.quote.succeeded",
            ],
        )
        self.assertTrue(
            hasattr(captured.records[-1], "duration_ms")
        )

    def test_logs_quote_failure_without_raw_response(self):
        self.gateway.quote_response = {
            "status": False,
            "message": "api_key=vendor-secret",
        }
        self.service.initialize()

        with self.assertLogs(
            "jarvis.services.market_data",
            level="ERROR",
        ) as captured:
            with self.assertRaises(MarketDataError):
                self.service.get_quote(
                    exchange="NSE",
                    symbol_token="2885",
                    symbol="RELIANCE-EQ",
                    observed_at=self.observed_at,
                )

        record = captured.records[0]

        self.assertEqual(
            record.event,
            "market.quote.failed",
        )
        self.assertEqual(
            record.error_type,
            "MarketDataError",
        )
        self.assertNotIn(
            "vendor-secret",
            record.getMessage(),
        )

    def test_returns_normalized_market_quote(self):
        self.service.initialize()

        quote = self.service.get_quote(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            observed_at=self.observed_at,
        )

        self.assertIsInstance(quote, MarketQuote)
        self.assertEqual(quote.price, 1320.0)
        self.assertEqual(quote.previous_close, 1305.0)
        self.assertEqual(quote.exchange, "NSE")
        self.assertEqual(quote.symbol_token, "2885")
        self.assertEqual(quote.symbol, "RELIANCE-EQ")
        self.assertEqual(quote.observed_at, self.observed_at)
        self.assertEqual(quote.source, "angel_one")

    def test_quote_uses_current_utc_time_by_default(self):
        self.service.initialize()
        before_request = datetime.now(timezone.utc)

        quote = self.service.get_quote(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
        )

        after_request = datetime.now(timezone.utc)

        self.assertLessEqual(
            before_request,
            quote.observed_at,
        )
        self.assertLessEqual(
            quote.observed_at,
            after_request,
        )
        self.assertEqual(
            quote.observed_at.utcoffset(),
            timedelta(0),
        )

    def test_rejects_unsuccessful_quote_response(self):
        self.gateway.quote_response = {
            "status": False,
            "message": "Quote unavailable",
        }
        self.service.initialize()

        with self.assertRaises(MarketDataError):
            self.service.get_quote(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                observed_at=self.observed_at,
            )

    def test_rejects_malformed_quote_data(self):
        self.gateway.quote_response = {
            "status": True,
            "data": {
                "ltp": 1320.0,
            },
        }
        self.service.initialize()

        with self.assertRaises(DataValidationError) as context:
            self.service.get_quote(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                observed_at=self.observed_at,
            )

        self.assertIsInstance(
            context.exception.__cause__,
            KeyError,
        )

    def test_delegates_history_request_to_gateway(self):
        self.service.initialize()

        series = self.service.get_historical_series(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            from_date="2026-08-01 09:15",
            to_date="2026-08-17 15:30",
            retrieved_at=self.observed_at,
        )

        self.assertEqual(series.candles, [])
        self.assertEqual(
            self.gateway.last_history_request,
            {
                "exchange": "NSE",
                "symbol_token": "2885",
                "interval": "ONE_DAY",
                "from_date": "2026-08-01 09:15",
                "to_date": "2026-08-17 15:30",
            },
        )

    def test_logs_historical_series_lifecycle(self):
        self.service.initialize()

        with self.assertLogs(
            "jarvis.services.market_data",
            level="DEBUG",
        ) as captured:
            self.service.get_historical_series(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                interval="ONE_DAY",
                from_date="2026-08-01 09:15",
                to_date="2026-08-17 15:30",
                retrieved_at=self.observed_at,
            )

        events = [
            getattr(record, "event", None)
            for record in captured.records
        ]

        self.assertEqual(
            events,
            [
                "market.history.started",
                "market.history.succeeded",
            ],
        )
        self.assertEqual(
            captured.records[-1].candle_count,
            0,
        )
        self.assertTrue(
            hasattr(captured.records[-1], "duration_ms")
        )

    def test_logs_historical_failure_without_raw_response(self):
        self.gateway.history_response = {
            "status": False,
            "message": "authorization=vendor-secret",
        }
        self.service.initialize()

        with self.assertLogs(
            "jarvis.services.market_data",
            level="ERROR",
        ) as captured:
            with self.assertRaises(MarketDataError):
                self.service.get_historical_series(
                    exchange="NSE",
                    symbol_token="2885",
                    symbol="RELIANCE-EQ",
                    interval="ONE_DAY",
                    from_date="2026-08-01 09:15",
                    to_date="2026-08-17 15:30",
                    retrieved_at=self.observed_at,
                )

        record = captured.records[0]

        self.assertEqual(
            record.event,
            "market.history.failed",
        )
        self.assertEqual(
            record.error_type,
            "MarketDataError",
        )
        self.assertNotIn(
            "vendor-secret",
            record.getMessage(),
        )

    def test_returns_normalized_historical_series(self):
            self.gateway.history_response = {
            "status": True,
            "data": [
                [
                    "2026-08-17T00:00:00+05:30",
                    1314.0,
                    1320.8,
                    1298.1,
                    1320.0,
                    13_090_231,
                ]
            ],
        }
            self.service.initialize()

            series = self.service.get_historical_series(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                interval="ONE_DAY",
                from_date="2026-08-01 09:15",
                to_date="2026-08-17 15:30",
                retrieved_at=self.observed_at,
        )

            self.assertIsInstance(
                series,
                HistoricalCandleSeries,
        )
            self.assertEqual(series.exchange, "NSE")
            self.assertEqual(series.symbol_token, "2885")
            self.assertEqual(series.symbol, "RELIANCE-EQ")
            self.assertEqual(series.interval, "ONE_DAY")
            self.assertEqual(len(series.candles), 1)
            self.assertEqual(series.candles[0].close, 1320.0)
            self.assertEqual(series.retrieved_at, self.observed_at)
            self.assertEqual(series.source, "angel_one")

    def test_historical_series_uses_current_utc_time_by_default(self):
        self.service.initialize()
        before_request = datetime.now(timezone.utc)

        series = self.service.get_historical_series(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            from_date="2026-08-01 09:15",
            to_date="2026-08-17 15:30",
        )

        after_request = datetime.now(timezone.utc)

        self.assertLessEqual(
            before_request,
            series.retrieved_at,
        )
        self.assertLessEqual(
            series.retrieved_at,
            after_request,
        )
        self.assertEqual(
            series.retrieved_at.utcoffset(),
            timedelta(0),
        )

    def test_rejects_unsuccessful_historical_response(self):
        self.gateway.history_response = {
            "status": False,
            "message": "Historical data unavailable",
        }
        self.service.initialize()

        with self.assertRaises(MarketDataError):
            self.service.get_historical_series(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                interval="ONE_DAY",
                from_date="2026-08-01 09:15",
                to_date="2026-08-17 15:30",
                retrieved_at=self.observed_at,
            )

    def test_rejects_historical_response_without_candle_list(self):
        self.gateway.history_response = {
            "status": True,
            "data": {
                "unexpected": "value",
            },
        }
        self.service.initialize()

        with self.assertRaises(DataValidationError):
            self.service.get_historical_series(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                interval="ONE_DAY",
                from_date="2026-08-01 09:15",
                to_date="2026-08-17 15:30",
                retrieved_at=self.observed_at,
            )

    def test_rejects_invalid_historical_candle_row(self):
        self.gateway.history_response = {
            "status": True,
            "data": [
                [
                    "2026-08-17T00:00:00+05:30",
                    1314.0,
                    1320.8,
                    1298.1,
                    1320.0,
                ]
            ],
        }
        self.service.initialize()

        with self.assertRaises(DataValidationError) as context:
            self.service.get_historical_series(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                interval="ONE_DAY",
                from_date="2026-08-01 09:15",
                to_date="2026-08-17 15:30",
                retrieved_at=self.observed_at,
            )

        self.assertIsInstance(
            context.exception.__cause__,
            IndexError,
        )

if __name__ == "__main__":
    unittest.main()
