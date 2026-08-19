import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.fair_value_gaps import detect_fair_value_gaps
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import FairValueGapDirection
from app.models.technical import (
    IndicatorPoint,
    IndicatorSeries,
    PriceField,
)


class FairValueGapDetectionTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)

    def candle(
        self,
        day,
        *,
        open_price,
        high,
        low,
        close,
    ):
        return Candle(
            timestamp=self.first_timestamp + timedelta(days=day),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=1_000,
        )

    def bullish_candles(self):
        return [
            self.candle(
                0,
                open_price=100,
                high=102,
                low=99,
                close=101,
            ),
            self.candle(
                1,
                open_price=101,
                high=108,
                low=100,
                close=107,
            ),
            self.candle(
                2,
                open_price=106,
                high=110,
                low=103,
                close=109,
            ),
        ]

    def bearish_candles(self):
        return [
            self.candle(
                0,
                open_price=110,
                high=111,
                low=108,
                close=109,
            ),
            self.candle(
                1,
                open_price=109,
                high=110,
                low=101,
                close=102,
            ),
            self.candle(
                2,
                open_price=106,
                high=107,
                low=100,
                close=101,
            ),
        ]

    def build_series(self, candles, symbol="RELIANCE-EQ"):
        return HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol=symbol,
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
        )

    def build_atr_series(self, market_series, value=2.0):
        return IndicatorSeries(
            exchange=market_series.exchange,
            symbol_token=market_series.symbol_token,
            symbol=market_series.symbol,
            interval=market_series.interval,
            indicator="ATR",
            input_fields=(
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
            parameters={"period": 14},
            points=[
                IndicatorPoint(
                    timestamp=market_series.candles[2].timestamp,
                    value=value,
                )
            ],
        )

    def test_detects_bullish_gap_after_third_candle(self):
        series = self.build_series(self.bullish_candles())

        result = detect_fair_value_gaps(series)

        self.assertEqual(len(result.gaps), 1)
        gap = result.gaps[0]
        self.assertEqual(gap.direction, FairValueGapDirection.BULLISH)
        self.assertEqual(gap.lower_price, 102.0)
        self.assertEqual(gap.upper_price, 103.0)
        self.assertEqual(gap.detected_at, series.candles[2].timestamp)
        self.assertEqual(result.source, "angel_one")

    def test_detects_bearish_gap(self):
        series = self.build_series(self.bearish_candles())

        result = detect_fair_value_gaps(series)

        self.assertEqual(len(result.gaps), 1)
        gap = result.gaps[0]
        self.assertEqual(gap.direction, FairValueGapDirection.BEARISH)
        self.assertEqual(gap.lower_price, 107.0)
        self.assertEqual(gap.upper_price, 108.0)

    def test_returns_empty_result_when_no_strict_gap_exists(self):
        candles = self.bullish_candles()
        candles[2] = self.candle(
            2,
            open_price=102,
            high=105,
            low=102,
            close=104,
        )

        result = detect_fair_value_gaps(self.build_series(candles))

        self.assertEqual(result.gaps, [])

    def test_filters_gap_below_minimum_percentage(self):
        series = self.build_series(self.bullish_candles())

        result = detect_fair_value_gaps(
            series,
            minimum_gap_percentage=1.0,
        )

        self.assertEqual(result.gaps, [])
        self.assertEqual(result.minimum_gap_percentage, 1.0)

    def test_enriches_and_filters_gap_with_atr(self):
        series = self.build_series(self.bullish_candles())
        atr_series = self.build_atr_series(series)

        included = detect_fair_value_gaps(
            series,
            atr_series=atr_series,
            minimum_atr_multiple=0.5,
        )
        excluded = detect_fair_value_gaps(
            series,
            atr_series=atr_series,
            minimum_atr_multiple=0.6,
        )

        self.assertEqual(included.gaps[0].atr_value, 2.0)
        self.assertEqual(included.gaps[0].atr_multiple, 0.5)
        self.assertEqual(excluded.gaps, [])

    def test_requires_atr_series_for_atr_filter(self):
        series = self.build_series(self.bullish_candles())

        with self.assertRaisesRegex(
            ValueError,
            "ATR series is required",
        ):
            detect_fair_value_gaps(
                series,
                minimum_atr_multiple=0.5,
            )

    def test_rejects_atr_for_different_instrument(self):
        series = self.build_series(self.bullish_candles())
        other_series = self.build_series(
            self.bullish_candles(),
            symbol="OTHER-EQ",
        )
        atr_series = self.build_atr_series(other_series)

        with self.assertRaisesRegex(
            ValueError,
            "same instrument",
        ):
            detect_fair_value_gaps(
                series,
                atr_series=atr_series,
            )

    def test_rejects_non_atr_indicator_for_enrichment(self):
        series = self.build_series(self.bullish_candles())
        atr_series = self.build_atr_series(series).model_copy(
            update={"indicator": "SMA"}
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires an ATR indicator series",
        ):
            detect_fair_value_gaps(
                series,
                atr_series=atr_series,
            )

    def test_rejects_invalid_threshold(self):
        series = self.build_series(self.bullish_candles())
        invalid_values = (True, "1", None, -1, float("inf"))

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    detect_fair_value_gaps(
                        series,
                        minimum_gap_percentage=value,
                    )

    def test_rejects_candles_out_of_order(self):
        candles = self.bullish_candles()
        series = self.build_series(
            [candles[0], candles[2], candles[1]]
        )

        with self.assertRaisesRegex(
            ValueError,
            "historical candles must have unique timestamps",
        ):
            detect_fair_value_gaps(series)


if __name__ == "__main__":
    unittest.main()
