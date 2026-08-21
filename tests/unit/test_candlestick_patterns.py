import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import (
    CANDLESTICK_PATTERN_NAMES,
    calculate_candlestick_patterns,
)
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class CandlestickPatternCalculationTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 7, 1, tzinfo=UTC)

    def flat_series(self, count=20, **overrides):
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1_000 + index,
            )
            for index in range(count)
        ]
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.first_timestamp
            + timedelta(days=count + 5),
            "source": "test_market",
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def series_with_bullish_engulfing(self):
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1_000 + index,
            )
            for index in range(18)
        ]
        candles.append(
            Candle(
                timestamp=self.first_timestamp + timedelta(days=18),
                open=100.0,
                high=100.2,
                low=97.0,
                close=97.5,
                volume=2_000,
            )
        )
        candles.append(
            Candle(
                timestamp=self.first_timestamp + timedelta(days=19),
                open=97.0,
                high=102.0,
                low=96.5,
                close=101.5,
                volume=2_500,
            )
        )
        return HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )

    def test_calculates_default_bundle_metadata(self):
        result = calculate_candlestick_patterns(self.flat_series())

        self.assertEqual(result.indicator, "CDL_PATTERNS")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertIsNone(result.price_field)
        self.assertEqual(
            result.input_fields,
            (
                PriceField.OPEN,
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
        )
        self.assertEqual(result.parameters, {"penetration": 0.3})
        self.assertEqual(
            {component.name for component in result.components},
            {name for name, _, _ in CANDLESTICK_PATTERN_NAMES},
        )
        self.assertEqual(len(result.components), 61)

    def test_every_component_spans_the_full_candle_prefix(self):
        series = self.flat_series(count=20)

        result = calculate_candlestick_patterns(series)

        expected_timestamps = [
            candle.timestamp for candle in series.candles
        ]
        for component in result.components:
            self.assertEqual(
                [point.timestamp for point in component.points],
                expected_timestamps,
            )

    def test_detects_a_real_bullish_engulfing_candle(self):
        result = calculate_candlestick_patterns(
            self.series_with_bullish_engulfing()
        )

        by_name = {
            component.name: component.points[-1].value
            for component in result.components
        }
        self.assertGreater(by_name["engulfing"], 0)

    def test_supports_custom_penetration(self):
        result = calculate_candlestick_patterns(
            self.flat_series(),
            penetration=0.5,
        )

        self.assertEqual(result.parameters, {"penetration": 0.5})

    def test_rejects_invalid_penetration(self):
        invalid_values = (True, "0.3", None, 0.0, 1.0, -0.1, 1.1)

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate_candlestick_patterns(
                        self.flat_series(),
                        penetration=value,
                    )

    def test_rejects_insufficient_candle_data(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "candlestick patterns require at least 15 candles; "
            "received 14",
        ):
            calculate_candlestick_patterns(self.flat_series(14))

    @patch("app.analytics.indicators.talib.CDLHAMMER")
    def test_wraps_talib_failure(self, mock_hammer):
        provider_error = RuntimeError("calculation failed")
        mock_hammer.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate candlestick pattern hammer",
        ) as raised:
            calculate_candlestick_patterns(self.flat_series())

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.CDLHAMMER")
    def test_rejects_invalid_component_shape(self, mock_hammer):
        mock_hammer.return_value = np.asarray([1.0, 2.0])

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid candlestick pattern hammer result shape",
        ):
            calculate_candlestick_patterns(self.flat_series())

    @patch("app.analytics.indicators.talib.CDLHAMMER")
    def test_rejects_non_finite_component_values(self, mock_hammer):
        series = self.flat_series()
        values = np.zeros(len(series.candles))
        values[-1] = np.inf
        mock_hammer.return_value = values

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite candlestick pattern hammer values",
        ):
            calculate_candlestick_patterns(series)

    def test_uses_penetration_only_for_patterns_that_accept_it(self):
        needs_penetration = {
            name
            for name, _, requires in CANDLESTICK_PATTERN_NAMES
            if requires
        }
        self.assertEqual(
            needs_penetration,
            {
                "abandoned_baby",
                "dark_cloud_cover",
                "evening_doji_star",
                "evening_star",
                "mat_hold",
                "morning_doji_star",
                "morning_star",
            },
        )


if __name__ == "__main__":
    unittest.main()
