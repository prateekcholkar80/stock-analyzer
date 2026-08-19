import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import calculate_atr
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class AverageTrueRangeTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 7, 1, tzinfo=UTC)
        closes = [
            100 + index * 0.35 + ((index % 4) - 1.5) * 0.9
            for index in range(20)
        ]
        self.candles = [
            Candle(
                timestamp=first_timestamp + timedelta(days=index),
                open=close,
                high=close + 1.0 + (index % 3) * 0.2,
                low=close - 0.8 - (index % 2) * 0.3,
                close=close,
                volume=1_000 + index,
            )
            for index, close in enumerate(closes)
        ]
        self.series = self.build_series(self.candles)

    def build_series(self, candles):
        return HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
        )

    def valid_mock_result(self):
        return np.asarray([np.nan] * 14 + [2.5] * 6)

    def test_calculates_default_atr_and_preserves_metadata(self):
        result = calculate_atr(self.series)

        self.assertEqual(result.indicator, "ATR")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertIsNone(result.price_field)
        self.assertEqual(
            result.input_fields,
            (
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
        )
        self.assertEqual(result.parameters, {"period": 14})

        expected = (
            2.61428571428571,
            2.588265306122444,
            2.628389212827983,
            2.6299328404831277,
            2.6027947804486185,
            2.5918808675594316,
        )
        for point, expected_value in zip(result.points, expected):
            self.assertAlmostEqual(point.value, expected_value)

    def test_aligns_atr_values_after_wilder_lookback(self):
        result = calculate_atr(self.series)

        self.assertEqual(
            [point.timestamp for point in result.points],
            [candle.timestamp for candle in self.candles[14:]],
        )

    def test_supports_custom_atr_period(self):
        result = calculate_atr(self.series, period=5)

        self.assertEqual(result.parameters, {"period": 5})
        self.assertEqual(len(result.points), 15)
        self.assertEqual(
            result.points[0].timestamp,
            self.candles[5].timestamp,
        )

    @patch("app.analytics.indicators.talib.ATR")
    def test_supplies_high_low_and_close_values(self, mock_atr):
        mock_atr.return_value = self.valid_mock_result()

        calculate_atr(self.series)

        supplied_highs, supplied_lows, supplied_closes = (
            mock_atr.call_args.args
        )
        np.testing.assert_array_equal(
            supplied_highs,
            [candle.high for candle in self.candles],
        )
        np.testing.assert_array_equal(
            supplied_lows,
            [candle.low for candle in self.candles],
        )
        np.testing.assert_array_equal(
            supplied_closes,
            [candle.close for candle in self.candles],
        )
        self.assertEqual(mock_atr.call_args.kwargs, {"timeperiod": 14})

    def test_rejects_invalid_period(self):
        invalid_periods = (True, 2.5, "14", None, 1, 0, -1)

        for period in invalid_periods:
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    calculate_atr(self.series, period=period)

    def test_rejects_insufficient_candle_data(self):
        short_series = self.build_series(self.candles[:14])

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 15 candles; received 14",
        ):
            calculate_atr(short_series)

    @patch("app.analytics.indicators.talib.ATR")
    def test_wraps_talib_failure(self, mock_atr):
        provider_error = RuntimeError("calculation failed")
        mock_atr.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate ATR",
        ) as raised:
            calculate_atr(self.series)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.ATR")
    def test_rejects_invalid_atr_shape(self, mock_atr):
        mock_atr.return_value = np.asarray([1.0, 2.0])

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid ATR result shape",
        ):
            calculate_atr(self.series)

    @patch("app.analytics.indicators.talib.ATR")
    def test_rejects_non_finite_atr_values(self, mock_atr):
        calculated = self.valid_mock_result()
        calculated[15] = np.inf
        mock_atr.return_value = calculated

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite ATR values",
        ):
            calculate_atr(self.series)

    @patch("app.analytics.indicators.talib.ATR")
    def test_rejects_negative_atr_values(self, mock_atr):
        calculated = self.valid_mock_result()
        calculated[15] = -0.1
        mock_atr.return_value = calculated

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "negative ATR values",
        ):
            calculate_atr(self.series)


if __name__ == "__main__":
    unittest.main()
