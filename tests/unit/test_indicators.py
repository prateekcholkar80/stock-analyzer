import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import (
    calculate_ema,
    calculate_rsi,
    calculate_sma,
)
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class SimpleMovingAverageTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.candles = [
            Candle(
                timestamp=first_timestamp + timedelta(days=index),
                open=float(index + 1),
                high=float(index + 2),
                low=float(index),
                close=float(index + 1),
                volume=1_000 + index,
            )
            for index in range(5)
        ]
        self.series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=self.candles,
            retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
        )

    def test_calculates_sma_and_preserves_metadata(self):
        result = calculate_sma(self.series, period=3)

        self.assertEqual(result.indicator, "SMA")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.price_field, PriceField.CLOSE)
        self.assertEqual(result.parameters, {"period": 3})
        self.assertEqual(
            [point.value for point in result.points],
            [2.0, 3.0, 4.0],
        )

    def test_aligns_sma_values_with_first_valid_candle(self):
        result = calculate_sma(self.series, period=3)

        self.assertEqual(
            [point.timestamp for point in result.points],
            [candle.timestamp for candle in self.candles[2:]],
        )

    def test_calculates_sma_from_selected_price_field(self):
        result = calculate_sma(
            self.series,
            period=2,
            price_field=PriceField.HIGH,
        )

        self.assertEqual(result.price_field, PriceField.HIGH)
        self.assertEqual(
            [point.value for point in result.points],
            [2.5, 3.5, 4.5, 5.5],
        )

    def test_rejects_invalid_period(self):
        invalid_periods = (True, 2.5, "14", None, 1, 0, -1)

        for period in invalid_periods:
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    calculate_sma(self.series, period=period)

    def test_rejects_insufficient_candle_data(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 6 candles; received 5",
        ):
            calculate_sma(self.series, period=6)

    @patch("app.analytics.indicators.talib.SMA")
    def test_wraps_talib_failure(self, mock_sma):
        provider_error = RuntimeError("calculation failed")
        mock_sma.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate SMA",
        ) as raised:
            calculate_sma(self.series, period=3)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.SMA")
    def test_rejects_invalid_talib_result_shape(self, mock_sma):
        mock_sma.return_value = np.asarray([1.0, 2.0])

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid SMA result shape",
        ):
            calculate_sma(self.series, period=3)

    @patch("app.analytics.indicators.talib.SMA")
    def test_rejects_non_finite_calculated_values(self, mock_sma):
        mock_sma.return_value = np.asarray(
            [np.nan, np.nan, 2.0, np.inf, 4.0]
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite SMA values",
        ):
            calculate_sma(self.series, period=3)


class ExponentialMovingAverageTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        close_values = (1.0, 2.0, 3.0, 10.0, 5.0)
        self.candles = [
            Candle(
                timestamp=first_timestamp + timedelta(days=index),
                open=close,
                high=close + 1.0,
                low=max(close - 1.0, 0.0),
                close=close,
                volume=1_000 + index,
            )
            for index, close in enumerate(close_values)
        ]
        self.series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=self.candles,
            retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
        )

    def test_calculates_ema_and_preserves_metadata(self):
        result = calculate_ema(self.series, period=3)

        self.assertEqual(result.indicator, "EMA")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.price_field, PriceField.CLOSE)
        self.assertEqual(result.parameters, {"period": 3})
        self.assertEqual(
            [point.value for point in result.points],
            [2.0, 6.0, 5.5],
        )

    def test_aligns_ema_values_with_first_valid_candle(self):
        result = calculate_ema(self.series, period=3)

        self.assertEqual(
            [point.timestamp for point in result.points],
            [candle.timestamp for candle in self.candles[2:]],
        )

    def test_calculates_ema_from_selected_price_field(self):
        result = calculate_ema(
            self.series,
            period=2,
            price_field=PriceField.HIGH,
        )

        self.assertEqual(result.price_field, PriceField.HIGH)
        expected = (2.5, 3.5, 8.5, 6.833333333333333)
        for point, expected_value in zip(result.points, expected):
            self.assertAlmostEqual(point.value, expected_value)

    def test_rejects_invalid_period(self):
        invalid_periods = (True, 2.5, "14", None, 1, 0, -1)

        for period in invalid_periods:
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    calculate_ema(self.series, period=period)

    def test_rejects_insufficient_candle_data(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 6 candles; received 5",
        ):
            calculate_ema(self.series, period=6)

    @patch("app.analytics.indicators.talib.EMA")
    def test_wraps_talib_failure(self, mock_ema):
        provider_error = RuntimeError("calculation failed")
        mock_ema.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate EMA",
        ) as raised:
            calculate_ema(self.series, period=3)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.EMA")
    def test_rejects_invalid_talib_result_shape(self, mock_ema):
        mock_ema.return_value = np.asarray([1.0, 2.0])

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid EMA result shape",
        ):
            calculate_ema(self.series, period=3)

    @patch("app.analytics.indicators.talib.EMA")
    def test_rejects_non_finite_calculated_values(self, mock_ema):
        mock_ema.return_value = np.asarray(
            [np.nan, np.nan, 2.0, np.inf, 4.0]
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite EMA values",
        ):
            calculate_ema(self.series, period=3)


class RelativeStrengthIndexTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 7, 1, tzinfo=UTC)
        close_values = (
            44.34,
            44.09,
            44.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.42,
            45.84,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
            46.28,
            46.00,
            46.03,
            46.41,
            46.22,
            45.64,
        )
        self.candles = [
            Candle(
                timestamp=first_timestamp + timedelta(days=index),
                open=close + (0.05 if index % 2 == 0 else -0.05),
                high=close + 0.10,
                low=close - 0.10,
                close=close,
                volume=1_000 + index,
            )
            for index, close in enumerate(close_values)
        ]
        self.series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=self.candles,
            retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
        )

    def test_calculates_rsi_and_preserves_metadata(self):
        result = calculate_rsi(self.series)

        self.assertEqual(result.indicator, "RSI")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.price_field, PriceField.CLOSE)
        self.assertEqual(result.parameters, {"period": 14})
        expected = (
            70.46413502,
            66.24961855,
            66.48094183,
            69.34685316,
            66.29471266,
            57.91502067,
        )
        for point, expected_value in zip(result.points, expected):
            self.assertAlmostEqual(point.value, expected_value)

    def test_aligns_rsi_values_after_wilder_lookback(self):
        result = calculate_rsi(self.series, period=14)

        self.assertEqual(
            [point.timestamp for point in result.points],
            [candle.timestamp for candle in self.candles[14:]],
        )

    @patch("app.analytics.indicators.talib.RSI")
    def test_calculates_rsi_from_selected_price_field(self, mock_rsi):
        mock_rsi.return_value = np.asarray(
            [np.nan] * 14 + [50.0] * 6
        )

        result = calculate_rsi(
            self.series,
            price_field=PriceField.OPEN,
        )

        supplied_prices = mock_rsi.call_args.args[0]
        np.testing.assert_array_equal(
            supplied_prices,
            [candle.open for candle in self.candles],
        )
        self.assertEqual(result.price_field, PriceField.OPEN)

    def test_rejects_invalid_period(self):
        invalid_periods = (True, 2.5, "14", None, 1, 0, -1)

        for period in invalid_periods:
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    calculate_rsi(self.series, period=period)

    def test_rejects_insufficient_candle_data(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 21 candles; received 20",
        ):
            calculate_rsi(self.series, period=20)

    @patch("app.analytics.indicators.talib.RSI")
    def test_wraps_talib_failure(self, mock_rsi):
        provider_error = RuntimeError("calculation failed")
        mock_rsi.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate RSI",
        ) as raised:
            calculate_rsi(self.series)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.RSI")
    def test_rejects_invalid_talib_result_shape(self, mock_rsi):
        mock_rsi.return_value = np.asarray([1.0, 2.0])

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid RSI result shape",
        ):
            calculate_rsi(self.series)

    @patch("app.analytics.indicators.talib.RSI")
    def test_rejects_non_finite_calculated_values(self, mock_rsi):
        mock_rsi.return_value = np.asarray(
            [np.nan] * 14 + [50.0, np.inf, 50.0, 50.0, 50.0, 50.0]
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite RSI values",
        ):
            calculate_rsi(self.series)

    @patch("app.analytics.indicators.talib.RSI")
    def test_rejects_rsi_value_outside_expected_range(self, mock_rsi):
        mock_rsi.return_value = np.asarray(
            [np.nan] * 14 + [50.0, 101.0, 50.0, 50.0, 50.0, 50.0]
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "RSI values outside the expected range",
        ):
            calculate_rsi(self.series)


if __name__ == "__main__":
    unittest.main()
