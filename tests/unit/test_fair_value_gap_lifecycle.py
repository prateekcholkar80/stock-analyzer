import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.fair_value_gaps import (
    detect_fair_value_gaps,
    track_fair_value_gap_lifecycle,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import FairValueGapStatus


class FairValueGapLifecycleTests(unittest.TestCase):
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

    def bullish_formation(self):
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

    def bearish_formation(self):
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

    def detect_then_track(
        self,
        formation,
        future_candles,
        *,
        max_age_candles=None,
    ):
        detection_result = detect_fair_value_gaps(
            self.build_series(formation)
        )
        extended_series = self.build_series(
            formation + future_candles
        )
        return track_fair_value_gap_lifecycle(
            detection_result,
            extended_series,
            max_age_candles=max_age_candles,
        )

    def test_formation_candles_do_not_fill_new_gap(self):
        formation = self.bullish_formation()

        result = self.detect_then_track(formation, [])

        gap = result.gaps[0]
        self.assertEqual(gap.status, FairValueGapStatus.OPEN)
        self.assertEqual(gap.fill_percentage, 0)
        self.assertIsNone(gap.first_touched_at)

    def test_bullish_gap_remains_open_without_future_overlap(self):
        future = self.candle(
            3,
            open_price=105,
            high=107,
            low=104,
            close=106,
        )

        result = self.detect_then_track(
            self.bullish_formation(),
            [future],
        )

        self.assertEqual(result.gaps[0].status, FairValueGapStatus.OPEN)

    def test_tracks_bullish_partial_fill(self):
        future = self.candle(
            3,
            open_price=104,
            high=106,
            low=102.5,
            close=105,
        )

        result = self.detect_then_track(
            self.bullish_formation(),
            [future],
        )

        gap = result.gaps[0]
        self.assertEqual(
            gap.status,
            FairValueGapStatus.PARTIALLY_FILLED,
        )
        self.assertEqual(gap.fill_percentage, 50)
        self.assertEqual(gap.first_touched_at, future.timestamp)
        self.assertIsNone(gap.resolved_at)

    def test_marks_bullish_gap_filled_by_full_wick_traversal(self):
        future = self.candle(
            3,
            open_price=103,
            high=105,
            low=101.5,
            close=102.5,
        )

        result = self.detect_then_track(
            self.bullish_formation(),
            [future],
        )

        gap = result.gaps[0]
        self.assertEqual(gap.status, FairValueGapStatus.FILLED)
        self.assertEqual(gap.fill_percentage, 100)
        self.assertEqual(gap.resolved_at, future.timestamp)

    def test_marks_bullish_gap_invalidated_by_close_below_zone(self):
        future = self.candle(
            3,
            open_price=102.5,
            high=103,
            low=101,
            close=101.5,
        )

        result = self.detect_then_track(
            self.bullish_formation(),
            [future],
        )

        self.assertEqual(
            result.gaps[0].status,
            FairValueGapStatus.INVALIDATED,
        )

    def test_tracks_bearish_partial_fill(self):
        future = self.candle(
            3,
            open_price=106,
            high=107.5,
            low=104,
            close=105,
        )

        result = self.detect_then_track(
            self.bearish_formation(),
            [future],
        )

        gap = result.gaps[0]
        self.assertEqual(
            gap.status,
            FairValueGapStatus.PARTIALLY_FILLED,
        )
        self.assertEqual(gap.fill_percentage, 50)

    def test_marks_bearish_gap_filled_by_full_wick_traversal(self):
        future = self.candle(
            3,
            open_price=107,
            high=108.5,
            low=105,
            close=107.5,
        )

        result = self.detect_then_track(
            self.bearish_formation(),
            [future],
        )

        self.assertEqual(
            result.gaps[0].status,
            FairValueGapStatus.FILLED,
        )

    def test_marks_bearish_gap_invalidated_by_close_above_zone(self):
        future = self.candle(
            3,
            open_price=107.5,
            high=109,
            low=106,
            close=108.5,
        )

        result = self.detect_then_track(
            self.bearish_formation(),
            [future],
        )

        self.assertEqual(
            result.gaps[0].status,
            FairValueGapStatus.INVALIDATED,
        )

    def test_expires_open_gap_at_configured_candle_age(self):
        future = [
            self.candle(
                3,
                open_price=105,
                high=107,
                low=104,
                close=106,
            ),
            self.candle(
                4,
                open_price=106,
                high=108,
                low=104,
                close=107,
            ),
        ]

        result = self.detect_then_track(
            self.bullish_formation(),
            future,
            max_age_candles=2,
        )

        gap = result.gaps[0]
        self.assertEqual(gap.status, FairValueGapStatus.EXPIRED)
        self.assertEqual(gap.fill_percentage, 0)
        self.assertEqual(gap.resolved_at, future[1].timestamp)

    def test_expiration_preserves_deepest_fill_and_first_touch(self):
        future = [
            self.candle(
                3,
                open_price=104,
                high=106,
                low=102.5,
                close=105,
            ),
            self.candle(
                4,
                open_price=105,
                high=106,
                low=102.8,
                close=104,
            ),
        ]

        result = self.detect_then_track(
            self.bullish_formation(),
            future,
            max_age_candles=2,
        )

        gap = result.gaps[0]
        self.assertEqual(gap.status, FairValueGapStatus.EXPIRED)
        self.assertEqual(gap.fill_percentage, 50)
        self.assertEqual(gap.first_touched_at, future[0].timestamp)
        self.assertEqual(gap.resolved_at, future[1].timestamp)

    def test_rejects_invalid_maximum_age(self):
        formation = self.bullish_formation()
        result = detect_fair_value_gaps(self.build_series(formation))
        invalid_ages = (True, 1.5, "2", 0, -1)

        for age in invalid_ages:
            with self.subTest(age=age):
                with self.assertRaises(ValueError):
                    track_fair_value_gap_lifecycle(
                        result,
                        self.build_series(formation),
                        max_age_candles=age,
                    )

    def test_rejects_market_series_for_different_instrument(self):
        formation = self.bullish_formation()
        result = detect_fair_value_gaps(self.build_series(formation))

        with self.assertRaisesRegex(
            ValueError,
            "detection result and market series must match",
        ):
            track_fair_value_gap_lifecycle(
                result,
                self.build_series(formation, symbol="OTHER-EQ"),
            )


if __name__ == "__main__":
    unittest.main()
