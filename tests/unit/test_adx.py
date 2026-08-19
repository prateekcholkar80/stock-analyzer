import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import calculate_adx
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class AverageDirectionalIndexTests(unittest.TestCase):
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

    def valid_mock_results(self):
        warmup = [np.nan] * 27
        return (
            np.asarray(warmup + [30.0] * 9),
            np.asarray(warmup + [40.0] * 9),
            np.asarray(warmup + [20.0] * 9),
        )

    def component_values(self, result):
        return {
            component.name: [
                point.value
                for point in component.points
            ]
            for component in result.components
        }

    def test_calculates_default_adx_and_preserves_metadata(self):
        result = calculate_adx(self.series)
        values = self.component_values(result)

        self.assertEqual(result.indicator, "ADX")
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
        self.assertEqual(
            [component.name for component in result.components],
            ["adx", "plus_di", "minus_di"],
        )

        expected_adx = (
            32.830168005772,
            33.04258460168888,
            33.52891951806599,
            32.415921303663694,
            31.722849246135095,
            31.294031827760968,
            31.218468186866776,
            31.45456145072235,
            31.96412235229425,
        )
        for actual, expected in zip(values["adx"], expected_adx):
            self.assertAlmostEqual(actual, expected)

        self.assertAlmostEqual(
            values["plus_di"][0],
            41.86716575261192,
        )
        self.assertAlmostEqual(
            values["minus_di"][0],
            21.04402813456354,
        )

    def test_aligns_all_components_after_complete_adx_lookback(self):
        result = calculate_adx(self.series)
        expected_timestamps = [
            candle.timestamp
            for candle in self.candles[27:]
        ]

        for component in result.components:
            self.assertEqual(
                [point.timestamp for point in component.points],
                expected_timestamps,
            )

    def test_supports_custom_adx_period(self):
        result = calculate_adx(self.series, period=5)

        self.assertEqual(result.parameters, {"period": 5})
        self.assertEqual(len(result.components[0].points), 27)
        self.assertEqual(
            result.components[0].points[0].timestamp,
            self.candles[9].timestamp,
        )

    @patch("app.analytics.indicators.talib.MINUS_DI")
    @patch("app.analytics.indicators.talib.PLUS_DI")
    @patch("app.analytics.indicators.talib.ADX")
    def test_supplies_hlc_values_to_all_calculators(
        self,
        mock_adx,
        mock_plus_di,
        mock_minus_di,
    ):
        results = self.valid_mock_results()
        mock_adx.return_value = results[0]
        mock_plus_di.return_value = results[1]
        mock_minus_di.return_value = results[2]

        calculate_adx(self.series)

        expected_inputs = (
            [candle.high for candle in self.candles],
            [candle.low for candle in self.candles],
            [candle.close for candle in self.candles],
        )
        for calculator in (mock_adx, mock_plus_di, mock_minus_di):
            for supplied, expected in zip(
                calculator.call_args.args,
                expected_inputs,
            ):
                np.testing.assert_array_equal(supplied, expected)
            self.assertEqual(
                calculator.call_args.kwargs,
                {"timeperiod": 14},
            )

    def test_rejects_invalid_period(self):
        invalid_periods = (True, 2.5, "14", None, 1, 0, -1)

        for period in invalid_periods:
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    calculate_adx(self.series, period=period)

    def test_rejects_insufficient_candle_data(self):
        short_series = self.build_series(self.candles[:27])

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires at least 28 candles; received 27",
        ):
            calculate_adx(short_series)

    @patch("app.analytics.indicators.talib.ADX")
    def test_wraps_talib_failure(self, mock_adx):
        provider_error = RuntimeError("calculation failed")
        mock_adx.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate ADX",
        ) as raised:
            calculate_adx(self.series)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.MINUS_DI")
    @patch("app.analytics.indicators.talib.PLUS_DI")
    @patch("app.analytics.indicators.talib.ADX")
    def test_rejects_invalid_component_shape(
        self,
        mock_adx,
        mock_plus_di,
        mock_minus_di,
    ):
        _, plus_di, minus_di = self.valid_mock_results()
        mock_adx.return_value = np.asarray([1.0, 2.0])
        mock_plus_di.return_value = plus_di
        mock_minus_di.return_value = minus_di

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid ADX adx result shape",
        ):
            calculate_adx(self.series)

    @patch("app.analytics.indicators.talib.MINUS_DI")
    @patch("app.analytics.indicators.talib.PLUS_DI")
    @patch("app.analytics.indicators.talib.ADX")
    def test_rejects_non_finite_component_values(
        self,
        mock_adx,
        mock_plus_di,
        mock_minus_di,
    ):
        adx, plus_di, minus_di = self.valid_mock_results()
        plus_di[28] = np.inf
        mock_adx.return_value = adx
        mock_plus_di.return_value = plus_di
        mock_minus_di.return_value = minus_di

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite ADX plus_di values",
        ):
            calculate_adx(self.series)

    @patch("app.analytics.indicators.talib.MINUS_DI")
    @patch("app.analytics.indicators.talib.PLUS_DI")
    @patch("app.analytics.indicators.talib.ADX")
    def test_rejects_component_values_below_zero(
        self,
        mock_adx,
        mock_plus_di,
        mock_minus_di,
    ):
        adx, plus_di, minus_di = self.valid_mock_results()
        minus_di[28] = -0.1
        mock_adx.return_value = adx
        mock_plus_di.return_value = plus_di
        mock_minus_di.return_value = minus_di

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "ADX minus_di values outside the expected range",
        ):
            calculate_adx(self.series)

    @patch("app.analytics.indicators.talib.MINUS_DI")
    @patch("app.analytics.indicators.talib.PLUS_DI")
    @patch("app.analytics.indicators.talib.ADX")
    def test_rejects_component_values_above_one_hundred(
        self,
        mock_adx,
        mock_plus_di,
        mock_minus_di,
    ):
        adx, plus_di, minus_di = self.valid_mock_results()
        adx[28] = 100.1
        mock_adx.return_value = adx
        mock_plus_di.return_value = plus_di
        mock_minus_di.return_value = minus_di

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "ADX adx values outside the expected range",
        ):
            calculate_adx(self.series)


if __name__ == "__main__":
    unittest.main()
