import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import calculate_stochastic
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class StochasticOscillatorTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 7, 1, tzinfo=UTC)
        closes = [
            100 + index * 0.45 + ((index % 6) - 2.5) * 1.1
            for index in range(36)
        ]
        self.candles = [
            Candle(
                timestamp=first_timestamp + timedelta(days=index),
                open=close,
                high=close + 1.0 + (index % 4) * 0.15,
                low=close - 0.9 - (index % 3) * 0.2,
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
        warmup = [np.nan] * 17
        percent_k = np.asarray(warmup + [70.0] * 19)
        percent_d = np.asarray(warmup + [65.0] * 19)
        return percent_k, percent_d

    def component_values(self, result):
        return {
            component.name: [
                point.value
                for point in component.points
            ]
            for component in result.components
        }

    def test_calculates_default_stochastic_and_preserves_metadata(self):
        result = calculate_stochastic(self.series)
        values = self.component_values(result)

        self.assertEqual(result.indicator, "STOCH")
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
        self.assertEqual(
            result.parameters,
            {
                "fast_k_period": 14,
                "slow_k_period": 3,
                "slow_d_period": 3,
                "moving_average_type": "SMA",
            },
        )
        self.assertEqual(
            [component.name for component in result.components],
            ["percent_k", "percent_d"],
        )

        expected_percent_k = (
            87.7413030191908,
            77.31358024691356,
            67.99999999999994,
            61.7991031390134,
            73.06032762880932,
            81.55642372490546,
            87.04794558589201,
            75.3929710960961,
            66.40624999999999,
            60.2841839519651,
            71.10565672424235,
            80.90986197115593,
            87.74130301919082,
            77.31358024691359,
            68.0,
            61.7991031390135,
            73.06032762880938,
            81.55642372490549,
            87.04794558589198,
        )
        for actual, expected in zip(
            values["percent_k"],
            expected_percent_k,
        ):
            self.assertAlmostEqual(actual, expected)

        self.assertAlmostEqual(
            values["percent_d"][0],
            79.91894057152963,
        )

    def test_aligns_components_after_complete_stochastic_lookback(self):
        result = calculate_stochastic(self.series)
        expected_timestamps = [
            candle.timestamp
            for candle in self.candles[17:]
        ]

        for component in result.components:
            self.assertEqual(
                [point.timestamp for point in component.points],
                expected_timestamps,
            )

    def test_supports_custom_stochastic_periods(self):
        result = calculate_stochastic(
            self.series,
            fast_k_period=5,
            slow_k_period=2,
            slow_d_period=2,
        )

        self.assertEqual(
            result.parameters,
            {
                "fast_k_period": 5,
                "slow_k_period": 2,
                "slow_d_period": 2,
                "moving_average_type": "SMA",
            },
        )
        self.assertEqual(len(result.components[0].points), 30)
        self.assertEqual(
            result.components[0].points[0].timestamp,
            self.candles[6].timestamp,
        )

    @patch("app.analytics.indicators.talib.STOCH")
    def test_supplies_hlc_values_and_sma_parameters(self, mock_stoch):
        mock_stoch.return_value = self.valid_mock_result()

        calculate_stochastic(self.series)

        supplied_highs, supplied_lows, supplied_closes = (
            mock_stoch.call_args.args
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
        self.assertEqual(
            mock_stoch.call_args.kwargs,
            {
                "fastk_period": 14,
                "slowk_period": 3,
                "slowk_matype": 0,
                "slowd_period": 3,
                "slowd_matype": 0,
            },
        )

    def test_rejects_invalid_periods(self):
        invalid_arguments = (
            {"fast_k_period": True},
            {"fast_k_period": 1},
            {"slow_k_period": 2.5},
            {"slow_k_period": 0},
            {"slow_d_period": "3"},
            {"slow_d_period": None},
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    calculate_stochastic(self.series, **arguments)

    def test_rejects_insufficient_candle_data(self):
        short_series = self.build_series(self.candles[:17])

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 18 candles; received 17",
        ):
            calculate_stochastic(short_series)

    @patch("app.analytics.indicators.talib.STOCH")
    def test_wraps_talib_failure(self, mock_stoch):
        provider_error = RuntimeError("calculation failed")
        mock_stoch.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate Stochastic",
        ) as raised:
            calculate_stochastic(self.series)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.STOCH")
    def test_rejects_missing_output_array(self, mock_stoch):
        mock_stoch.return_value = self.valid_mock_result()[:1]

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "two Stochastic output arrays",
        ):
            calculate_stochastic(self.series)

    @patch("app.analytics.indicators.talib.STOCH")
    def test_rejects_invalid_component_shape(self, mock_stoch):
        _, percent_d = self.valid_mock_result()
        mock_stoch.return_value = (
            np.asarray([1.0, 2.0]),
            percent_d,
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid Stochastic percent_k result shape",
        ):
            calculate_stochastic(self.series)

    @patch("app.analytics.indicators.talib.STOCH")
    def test_rejects_non_finite_component_values(self, mock_stoch):
        percent_k, percent_d = self.valid_mock_result()
        percent_d[18] = np.inf
        mock_stoch.return_value = percent_k, percent_d

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite Stochastic percent_d values",
        ):
            calculate_stochastic(self.series)

    @patch("app.analytics.indicators.talib.STOCH")
    def test_rejects_component_values_below_zero(self, mock_stoch):
        percent_k, percent_d = self.valid_mock_result()
        percent_k[18] = -0.1
        mock_stoch.return_value = percent_k, percent_d

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "Stochastic percent_k values outside the expected range",
        ):
            calculate_stochastic(self.series)

    @patch("app.analytics.indicators.talib.STOCH")
    def test_rejects_component_values_above_one_hundred(
        self,
        mock_stoch,
    ):
        percent_k, percent_d = self.valid_mock_result()
        percent_d[18] = 100.1
        mock_stoch.return_value = percent_k, percent_d

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "Stochastic percent_d values outside the expected range",
        ):
            calculate_stochastic(self.series)


if __name__ == "__main__":
    unittest.main()
