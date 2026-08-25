import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.analytics.candle_aggregation import aggregate_candles
from app.models.market import Candle, HistoricalCandleSeries


IST = ZoneInfo("Asia/Kolkata")
_SESSION_HOURS = (9, 10, 11, 12, 13, 14, 15)


def _series(candles, interval):
    retrieved_at = (
        candles[-1].timestamp + timedelta(days=1)
        if candles
        else datetime(2026, 1, 1, tzinfo=IST)
    )
    return HistoricalCandleSeries(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval=interval,
        candles=candles,
        retrieved_at=retrieved_at,
        source="test_market",
    )


def _hourly_day(day: datetime, base_price: float, hours=_SESSION_HOURS):
    """One session's worth of hourly candles for the given IST date."""
    candles = []
    for offset, hour in enumerate(hours):
        minute = 30 if hour == 15 else 15
        close = base_price + offset
        candles.append(
            Candle(
                timestamp=day.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                ),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1_000 + offset * 10,
            )
        )
    return candles


def _hourly_series(day_count: int, start_day: datetime, hours=_SESSION_HOURS):
    candles = []
    for day_index in range(day_count):
        day = start_day + timedelta(days=day_index)
        candles.extend(_hourly_day(day, base_price=100 + day_index, hours=hours))
    return _series(candles, "ONE_HOUR")


def _angel_hourly_day(day: datetime, base_price: float):
    """Angel ONE_HOUR bars are stamped at their interval start."""
    candles = []
    for offset, hour in enumerate(_SESSION_HOURS):
        close = base_price + offset
        candles.append(
            Candle(
                timestamp=day.replace(
                    hour=hour,
                    minute=15,
                    second=0,
                    microsecond=0,
                ),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1_000 + offset * 10,
            )
        )
    return candles


class AggregateCandlesTests(unittest.TestCase):
    def setUp(self):
        # 2026-01-05 is a Monday.
        self.monday = datetime(2026, 1, 5, tzinfo=IST)

    def test_aggregates_hourly_into_complete_daily_bars(self):
        series = _hourly_series(day_count=2, start_day=self.monday)
        partial_day = self.monday + timedelta(days=2)
        series = _series(
            series.candles + _hourly_day(partial_day, base_price=200, hours=(9, 10)),
            "ONE_HOUR",
        )

        result = aggregate_candles(series, target_interval="ONE_DAY")

        self.assertEqual(len(result.candles), 2)
        self.assertEqual(result.interval, "ONE_DAY")
        first_day_candles = _hourly_day(self.monday, base_price=100)
        first_bar = result.candles[0]
        self.assertEqual(first_bar.timestamp, first_day_candles[-1].timestamp)
        self.assertEqual(first_bar.open, first_day_candles[0].open)
        self.assertEqual(
            first_bar.high,
            max(candle.high for candle in first_day_candles),
        )
        self.assertEqual(
            first_bar.low,
            min(candle.low for candle in first_day_candles),
        )
        self.assertEqual(first_bar.close, first_day_candles[-1].close)
        self.assertEqual(
            first_bar.volume,
            sum(candle.volume for candle in first_day_candles),
        )

    def test_includes_incomplete_final_bucket_when_opted_in(self):
        partial_day = self.monday + timedelta(days=2)
        series = _series(
            _hourly_series(day_count=2, start_day=self.monday).candles
            + _hourly_day(partial_day, base_price=200, hours=(9, 10)),
            "ONE_HOUR",
        )

        result = aggregate_candles(
            series,
            target_interval="ONE_DAY",
            include_incomplete_final_bucket=True,
        )

        self.assertEqual(len(result.candles), 3)
        last_bar = result.candles[-1]
        expected_last_candle = _hourly_day(partial_day, base_price=200, hours=(9, 10))[-1]
        self.assertEqual(last_bar.timestamp, expected_last_candle.timestamp)
        self.assertEqual(last_bar.close, expected_last_candle.close)

    def test_accepts_angel_1515_bar_only_after_nse_session_close(self):
        tuesday = self.monday + timedelta(days=1)
        series = _series(
            _angel_hourly_day(self.monday, 100)
            + _angel_hourly_day(tuesday, 200),
            "ONE_HOUR",
        )

        before_close = aggregate_candles(
            series,
            target_interval="ONE_DAY",
            as_of=tuesday.replace(hour=15, minute=29),
        )
        after_close = aggregate_candles(
            series,
            target_interval="ONE_DAY",
            as_of=tuesday.replace(hour=15, minute=30),
        )

        self.assertEqual(len(before_close.candles), 1)
        self.assertEqual(len(after_close.candles), 2)
        self.assertEqual(
            after_close.candles[-1].timestamp,
            tuesday.replace(hour=15, minute=15),
        )
        self.assertEqual(after_close.candles[-1].open, 199.5)
        self.assertEqual(after_close.candles[-1].high, 207.0)
        self.assertEqual(after_close.candles[-1].low, 199.0)
        self.assertEqual(after_close.candles[-1].close, 206.0)

    def test_angel_start_stamped_friday_completes_weekly_cascade(self):
        hourly = _series(
            [
                candle
                for day_offset in range(5)
                for candle in _angel_hourly_day(
                    self.monday + timedelta(days=day_offset),
                    100 + day_offset,
                )
            ],
            "ONE_HOUR",
        )
        friday_close = (self.monday + timedelta(days=4)).replace(
            hour=15,
            minute=30,
        )

        daily = aggregate_candles(
            hourly,
            target_interval="ONE_DAY",
            as_of=friday_close,
        )
        weekly = aggregate_candles(
            daily,
            target_interval="ONE_WEEK",
            as_of=friday_close,
        )

        self.assertEqual(len(daily.candles), 5)
        self.assertEqual(len(weekly.candles), 1)
        self.assertEqual(weekly.candles[-1].timestamp.weekday(), 4)

    def test_rejects_naive_analysis_cutoff(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            aggregate_candles(
                _hourly_series(day_count=1, start_day=self.monday),
                target_interval="ONE_DAY",
                as_of=datetime(2026, 1, 5, 15, 30),
            )

    def test_aggregates_daily_into_weekly_bars(self):
        # Two full Mon-Fri weeks plus a partial third week (Mon-Wed only).
        daily_series = aggregate_candles(
            _hourly_series(day_count=10, start_day=self.monday),
            target_interval="ONE_DAY",
        )
        third_week_monday = self.monday + timedelta(days=14)
        partial_week = aggregate_candles(
            _series(
                _hourly_day(third_week_monday, base_price=300)
                + _hourly_day(
                    third_week_monday + timedelta(days=1), base_price=301
                )
                + _hourly_day(
                    third_week_monday + timedelta(days=2), base_price=302
                ),
                "ONE_HOUR",
            ),
            target_interval="ONE_DAY",
            include_incomplete_final_bucket=True,
        )
        daily_series = _series(
            list(daily_series.candles) + list(partial_week.candles),
            "ONE_DAY",
        )

        result = aggregate_candles(daily_series, target_interval="ONE_WEEK")

        self.assertEqual(len(result.candles), 2)

    def test_hourly_to_weekly_matches_daily_to_weekly_cascade(self):
        hourly = _hourly_series(day_count=10, start_day=self.monday)

        direct = aggregate_candles(hourly, target_interval="ONE_WEEK")
        cascaded_daily = aggregate_candles(hourly, target_interval="ONE_DAY")
        cascaded = aggregate_candles(
            cascaded_daily,
            target_interval="ONE_WEEK",
        )

        self.assertEqual(
            [candle.model_dump() for candle in direct.candles],
            [candle.model_dump() for candle in cascaded.candles],
        )

    def test_rejects_invalid_target_interval(self):
        series = _hourly_series(day_count=1, start_day=self.monday)
        with self.assertRaises(ValueError):
            aggregate_candles(series, target_interval="ONE_HOUR")

    def test_rejects_same_or_coarser_source_interval(self):
        weekly_like = _series(
            _hourly_day(self.monday, base_price=100),
            "ONE_WEEK",
        )
        with self.assertRaises(ValueError):
            aggregate_candles(weekly_like, target_interval="ONE_DAY")

    def test_rejects_empty_series(self):
        empty = _series([], "ONE_HOUR")
        with self.assertRaises(ValueError):
            aggregate_candles(empty, target_interval="ONE_DAY")


if __name__ == "__main__":
    unittest.main()
