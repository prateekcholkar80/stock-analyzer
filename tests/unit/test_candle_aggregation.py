import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.analytics.candle_aggregation import aggregate_candles
from app.models.market import Candle, HistoricalCandleSeries


IST = ZoneInfo("Asia/Kolkata")
_SESSION_HOURS = (9, 10, 11, 12, 13, 14, 15)


def _series(candles, interval):
    return HistoricalCandleSeries(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval=interval,
        candles=candles,
        retrieved_at=datetime(2026, 1, 1, tzinfo=IST),
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
