import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import calculate_bollinger_bands
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class BollingerBandsTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 7, 1, tzinfo=UTC)
        close_values = [
            100 + index * 0.4 + ((index % 4) - 1.5) * 1.1
            for index in range(25)
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
        warmup = [np.nan] * 19
        upper = np.asarray(warmup + [12.0] * 6)
        middle = np.asarray(warmup + [10.0] * 6)
        lower = np.asarray(warmup + [8.0] * 6)
        return upper, middle, lower

    def component_values(self, result):
        return {
            component.name: [
                point.value
                for point in component.points
            ]
            for component in result.components
        }

    def test_calculates_default_bands_and_preserves_metadata(self):
        result = calculate_bollinger_bands(self.series)
        values = self.component_values(result)

        self.assertEqual(result.indicator, "BBANDS")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.price_field, PriceField.CLOSE)
        self.assertEqual(
            result.parameters,
            {
                "period": 20,
                "upper_deviation": 2.0,
                "lower_deviation": 2.0,
                "moving_average_type": "SMA",
            },
        )
        self.assertEqual(
            [component.name for component in result.components],
            ["upper_band", "middle_band", "lower_band"],
        )

        expected_middle = (103.8, 104.2, 104.6, 105.0, 105.4, 105.8)
        expected_upper = (
            109.432938842204,
            109.34295634824923,
            109.5689032995229,
            110.14295634824923,
            111.03293884220271,
            110.94295634824923,
        )
        for actual, expected in zip(
            values["middle_band"],
            expected_middle,
        ):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(
            values["upper_band"],
            expected_upper,
        ):
            self.assertAlmostEqual(actual, expected)

    def test_aligns_all_bands_after_period_lookback(self):
        result = calculate_bollinger_bands(self.series)
        expected_timestamps = [
            candle.timestamp
            for candle in self.candles[19:]
        ]

        for component in result.components:
            self.assertEqual(
                [point.timestamp for point in component.points],
                expected_timestamps,
            )

    def test_supports_custom_period_and_deviations(self):
        result = calculate_bollinger_bands(
            self.series,
            period=5,
            upper_deviation=1.5,
            lower_deviation=2.5,
        )

        self.assertEqual(
            result.parameters,
            {
                "period": 5,
                "upper_deviation": 1.5,
                "lower_deviation": 2.5,
                "moving_average_type": "SMA",
            },
        )
        self.assertEqual(len(result.components[0].points), 21)
        self.assertEqual(
            result.components[0].points[0].timestamp,
            self.candles[4].timestamp,
        )

    @patch("app.analytics.indicators.talib.BBANDS")
    def test_uses_selected_price_field(self, mock_bbands):
        mock_bbands.return_value = self.valid_mock_result()

        result = calculate_bollinger_bands(
            self.series,
            price_field=PriceField.OPEN,
        )

        supplied_prices = mock_bbands.call_args.args[0]
        np.testing.assert_array_equal(
            supplied_prices,
            [candle.open for candle in self.candles],
        )
        self.assertEqual(result.price_field, PriceField.OPEN)

    def test_rejects_invalid_period(self):
        invalid_periods = (True, 2.5, "20", None, 1, 0, -1)

        for period in invalid_periods:
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    calculate_bollinger_bands(
                        self.series,
                        period=period,
                    )

    def test_rejects_invalid_deviations(self):
        invalid_deviations = (
            True,
            "2",
            None,
            0,
            -1,
            float("nan"),
            float("inf"),
        )

        for deviation in invalid_deviations:
            with self.subTest(deviation=deviation):
                with self.assertRaises(ValueError):
                    calculate_bollinger_bands(
                        self.series,
                        upper_deviation=deviation,
                    )

    def test_rejects_insufficient_candle_data(self):
        short_series = self.build_series(self.candles[:19])

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 20 candles; received 19",
        ):
            calculate_bollinger_bands(short_series)

    @patch("app.analytics.indicators.talib.BBANDS")
    def test_wraps_talib_failure(self, mock_bbands):
        provider_error = RuntimeError("calculation failed")
        mock_bbands.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate Bollinger Bands",
        ) as raised:
            calculate_bollinger_bands(self.series)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.BBANDS")
    def test_rejects_missing_band_output_array(self, mock_bbands):
        mock_bbands.return_value = self.valid_mock_result()[:2]

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "three Bollinger Bands output arrays",
        ):
            calculate_bollinger_bands(self.series)

    @patch("app.analytics.indicators.talib.BBANDS")
    def test_rejects_invalid_band_shape(self, mock_bbands):
        _, middle, lower = self.valid_mock_result()
        mock_bbands.return_value = (
            np.asarray([1.0, 2.0]),
            middle,
            lower,
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid Bollinger Bands upper_band result shape",
        ):
            calculate_bollinger_bands(self.series)

    @patch("app.analytics.indicators.talib.BBANDS")
    def test_rejects_non_finite_band_values(self, mock_bbands):
        upper, middle, lower = self.valid_mock_result()
        upper[20] = np.inf
        mock_bbands.return_value = upper, middle, lower

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite Bollinger Bands upper_band values",
        ):
            calculate_bollinger_bands(self.series)

    @patch("app.analytics.indicators.talib.BBANDS")
    def test_rejects_incorrectly_ordered_bands(self, mock_bbands):
        upper, middle, lower = self.valid_mock_result()
        upper[20] = 9.0
        mock_bbands.return_value = upper, middle, lower

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "incorrectly ordered Bollinger Bands",
        ):
            calculate_bollinger_bands(self.series)


if __name__ == "__main__":
    unittest.main()
