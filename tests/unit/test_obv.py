import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np

from app.analytics.indicators import calculate_obv
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import PriceField


class OnBalanceVolumeTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        closes = (100.0, 102.0, 101.0, 101.0, 104.0, 103.0, 105.0)
        volumes = (1_000, 1_200, 800, 900, 1_500, 1_100, 2_000)
        self.candles = [
            Candle(
                timestamp=first_timestamp + timedelta(days=index),
                open=close + (-0.2 if index % 2 == 0 else 0.2),
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=volume,
            )
            for index, (close, volume) in enumerate(
                zip(closes, volumes)
            )
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

    def test_calculates_obv_and_preserves_metadata(self):
        result = calculate_obv(self.series)

        self.assertEqual(result.indicator, "OBV")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertIsNone(result.price_field)
        self.assertEqual(
            result.input_fields,
            (PriceField.CLOSE, PriceField.VOLUME),
        )
        self.assertEqual(result.parameters, {})
        self.assertEqual(
            [point.value for point in result.points],
            [
                1_000.0,
                2_200.0,
                1_400.0,
                1_400.0,
                2_900.0,
                1_800.0,
                3_800.0,
            ],
        )

    def test_aligns_obv_with_every_input_candle(self):
        result = calculate_obv(self.series)

        self.assertEqual(
            [point.timestamp for point in result.points],
            [candle.timestamp for candle in self.candles],
        )

    @patch("app.analytics.indicators.talib.OBV")
    def test_supports_selected_price_field_and_supplies_volume(
        self,
        mock_obv,
    ):
        mock_obv.return_value = np.arange(7, dtype=np.float64)

        result = calculate_obv(
            self.series,
            price_field=PriceField.OPEN,
        )

        supplied_prices, supplied_volumes = mock_obv.call_args.args
        np.testing.assert_array_equal(
            supplied_prices,
            [candle.open for candle in self.candles],
        )
        np.testing.assert_array_equal(
            supplied_volumes,
            [candle.volume for candle in self.candles],
        )
        self.assertEqual(
            result.input_fields,
            (PriceField.OPEN, PriceField.VOLUME),
        )

    def test_rejects_volume_as_direction_field(self):
        with self.assertRaisesRegex(
            ValueError,
            "OBV price field must be an OHLC field",
        ):
            calculate_obv(
                self.series,
                price_field=PriceField.VOLUME,
            )

    def test_rejects_empty_candle_series(self):
        empty_series = self.build_series([])

        with self.assertRaisesRegex(
            InsufficientDataError,
            "OBV requires at least 1 candle; received 0",
        ):
            calculate_obv(empty_series)

    @patch("app.analytics.indicators.talib.OBV")
    def test_wraps_talib_failure(self, mock_obv):
        provider_error = RuntimeError("calculation failed")
        mock_obv.side_effect = provider_error

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "TA-Lib could not calculate OBV",
        ) as raised:
            calculate_obv(self.series)

        self.assertIs(raised.exception.__cause__, provider_error)

    @patch("app.analytics.indicators.talib.OBV")
    def test_rejects_invalid_obv_shape(self, mock_obv):
        mock_obv.return_value = np.asarray([1.0, 2.0])

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "invalid OBV result shape",
        ):
            calculate_obv(self.series)

    @patch("app.analytics.indicators.talib.OBV")
    def test_rejects_non_finite_obv_values(self, mock_obv):
        mock_obv.return_value = np.asarray(
            [1.0, 2.0, np.inf, 4.0, 5.0, 6.0, 7.0]
        )

        with self.assertRaisesRegex(
            IndicatorCalculationError,
            "non-finite OBV values",
        ):
            calculate_obv(self.series)


if __name__ == "__main__":
    unittest.main()
