from datetime import timedelta
import unittest

from pydantic import ValidationError

from app.models.market import (
    Candle,
    HistoricalCandleSeries,
    MarketQuote,
)


class CandleModelTests(unittest.TestCase):
    def test_builds_candle_from_valid_market_data(self):
        candle = Candle(
            timestamp="2026-08-17T00:00:00+05:30",
            open=1314.0,
            high=1320.8,
            low=1298.1,
            close=1320.0,
            volume=13_090_231,
        )

        self.assertEqual(
            candle.timestamp.utcoffset(),
            timedelta(hours=5, minutes=30),
        )
        self.assertEqual(candle.close, 1320.0)
        self.assertEqual(candle.volume, 13_090_231)

    def test_rejects_missing_required_market_data(self):
        with self.assertRaises(ValidationError):
            Candle(
                timestamp="2026-08-17T00:00:00+05:30",
                open=1314.0,
                high=1320.8,
                low=1298.1,
                close=1320.0,
            )

    def test_rejects_invalid_ohlc_range(self):
        with self.assertRaises(ValidationError):
            Candle(
                timestamp="2026-08-17T00:00:00+05:30",
                open=1314.0,
                high=1300.0,
                low=1298.1,
                close=1320.0,
                volume=13_090_231,
            )

    def test_rejects_negative_volume(self):
        with self.assertRaises(ValidationError):
            Candle(
                timestamp="2026-08-17T00:00:00+05:30",
                open=1314.0,
                high=1320.8,
                low=1298.1,
                close=1320.0,
                volume=-1,
            )


class MarketQuoteModelTests(unittest.TestCase):
    def test_builds_valid_market_quote(self):
        quote = MarketQuote(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            price=1320.0,
            open=1314.0,
            high=1320.8,
            low=1298.1,
            previous_close=1305.0,
            observed_at="2026-08-17T15:30:00+05:30",
        )

        self.assertEqual(quote.price, 1320.0)
        self.assertEqual(quote.source, "angel_one")
        self.assertEqual(
            quote.observed_at.utcoffset(),
            timedelta(hours=5, minutes=30),
        )

    def test_rejects_quote_when_high_is_below_low(self):
        with self.assertRaises(ValidationError):
            MarketQuote(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                price=1320.0,
                open=1314.0,
                high=1290.0,
                low=1300.0,
                previous_close=1305.0,
                observed_at="2026-08-17T15:30:00+05:30",
            )

    def test_rejects_quote_without_observation_timezone(self):
        with self.assertRaises(ValidationError):
            MarketQuote(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                price=1320.0,
                open=1314.0,
                high=1320.8,
                low=1298.1,
                previous_close=1305.0,
                observed_at="2026-08-17T15:30:00",
            )


class HistoricalCandleSeriesModelTests(unittest.TestCase):
    def test_builds_series_with_market_metadata(self):
        candle = Candle(
            timestamp="2026-08-17T00:00:00+05:30",
            open=1314.0,
            high=1320.8,
            low=1298.1,
            close=1320.0,
            volume=13_090_231,
        )

        series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=[candle],
            retrieved_at="2026-08-17T15:31:00+05:30",
        )

        self.assertEqual(len(series.candles), 1)
        self.assertEqual(series.candles[0], candle)
        self.assertEqual(series.source, "angel_one")


if __name__ == "__main__":
    unittest.main()