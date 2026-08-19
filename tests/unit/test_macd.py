import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import calculate_macd
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class MovingAverageConvergenceDivergenceTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 7, 1, tzinfo=UTC)
        close_values = [
            100 + index * 0.5 + ((index % 5) - 2) * 1.2
            for index in range(40)
        ]
        self.candles = [
            Candle(
                timestamp=first_timestamp + timedelta(days=index),
                open=close + (-0.2 if index % 2 == 0 else 0.2),
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1_000 + index,
            )
            for index, close in enumerate(close_values)
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
        warmup = [np.nan] * 33
        macd = np.asarray(warmup + [1.0] * 7)
        signal = np.asarray(warmup + [0.75] * 7)
        histogram = np.asarray(warmup + [0.25] * 7)
        return macd, signal, histogram

    def component_values(self, result):
        return {
            component.name: [
                point.value
                for point in component.points
            ]
            for component in result.components
        }

    def test_calculates_default_macd_and_preserves_metadata(self):
        result = calculate_macd(self.series)
        values = self.component_values(result)

        self.assertEqual(result.indicator, "MACD")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.price_field, PriceField.CLOSE)
        self.assertEqual(
            result.parameters,
            {
                "fast_period": 12,
                "slow_period": 26,
                "signal_period": 9,
            },
        )
        self.assertEqual(
            [component.name for component in result.components],
            ["macd", "signal", "histogram"],
        )

        expected_macd = (
            3.5797787311589104,
            3.762070241098982,
            3.518998686310155,
            3.4240679959621048,
            3.446283916675597,
            3.560028197386444,
            3.7441865005372676,
        )
        for actual, expected in zip(values["macd"], expected_macd):
            self.assertAlmostEqual(actual, expected)

    def test_aligns_all_components_after_complete_macd_lookback(self):
        result = calculate_macd(self.series)
        expected_timestamps = [
            candle.timestamp
            for candle in self.candles[33:]
        ]

        for component in result.components:
            self.assertEqual(
                [point.timestamp for point in component.points],
                expected_timestamps,
            )

    def test_supports_custom_macd_periods(self):
        result = calculate_macd(
            self.series,
            fast_period=3,
            slow_period=6,
            signal_period=3,
        )

        self.assertEqual(
            result.parameters,
            {
                "fast_period": 3,
                "slow_period": 6,
                "signal_period": 3,
            },
        )
        self.assertEqual(len(result.components[0].points), 33)
        self.assertEqual(
            result.components[0].points[0].timestamp,
            self.candles[7].timestamp,
        )

    @patch("app.analytics.indicators.talib.MACD")
    def test_uses_selected_price_field(self, mock_macd):
        mock_macd.return_value = self.valid_mock_result()

        result = calculate_macd(
            self.series,
            price_field=PriceField.OPEN,
        )

        supplied_prices = mock_macd.call_args.args[0]
        np.testing.assert_array_equal(
            supplied_prices,
            [candle.open for candle in self.candles],
        )
        self.assertEqual(result.price_field, PriceField.OPEN)

    def test_rejects_invalid_periods(self):
        invalid_arguments = (
            {"fast_period": True},
            {"fast_period": 1},
            {"slow_period": "26"},
            {"signal_period": None},
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    calculate_macd(self.series, **arguments)

    def test_rejects_fast_period_not_below_slow_period(self):
        with self.assertRaisesRegex(
            ValueError,
            "fast period must be less than slow period",
        ):
            calculate_macd(
                self.series,
                fast_period=26,
                slow_period=26,
            )

    def test_rejects_insufficient_candle_data(self):
        short_series = self.build_series(self.candles[:33])

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 34 candles; received 33",
        ):
            calculate_macd(short_series)

    @patch("app.analytics.indicators.talib.MACD")
    def test_wraps_talib_failure(self, mock_macd):
        provider_error = RuntimeError("calculation failed")
        mock_macd.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate MACD",
        ) as raised:
            calculate_macd(self.series)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.MACD")
    def test_rejects_missing_macd_output_array(self, mock_macd):
        mock_macd.return_value = self.valid_mock_result()[:2]

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "three MACD output arrays",
        ):
            calculate_macd(self.series)

    @patch("app.analytics.indicators.talib.MACD")
    def test_rejects_invalid_component_shape(self, mock_macd):
        _, signal, histogram = self.valid_mock_result()
        mock_macd.return_value = (
            np.asarray([1.0, 2.0]),
            signal,
            histogram,
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid MACD macd result shape",
        ):
            calculate_macd(self.series)

    @patch("app.analytics.indicators.talib.MACD")
    def test_rejects_non_finite_component_values(self, mock_macd):
        macd, signal, histogram = self.valid_mock_result()
        macd[34] = np.inf
        mock_macd.return_value = macd, signal, histogram

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite MACD macd values",
        ):
            calculate_macd(self.series)

    @patch("app.analytics.indicators.talib.MACD")
    def test_rejects_inconsistent_histogram(self, mock_macd):
        macd, signal, histogram = self.valid_mock_result()
        histogram[34] = 0.5
        mock_macd.return_value = macd, signal, histogram

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "inconsistent MACD histogram",
        ):
            calculate_macd(self.series)


if __name__ == "__main__":
    unittest.main()
