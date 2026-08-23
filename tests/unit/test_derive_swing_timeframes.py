import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.timeframes import (
    SwingTimeframeSeries,
    market_series_fingerprint,
)
from app.use_cases.derive_swing_timeframes import DeriveSwingTimeframes


IST = ZoneInfo("Asia/Kolkata")


def _session(day: datetime, base: float) -> list[Candle]:
    candles = []
    for offset, (hour, minute) in enumerate(
        ((9, 15), (10, 15), (11, 15), (12, 15), (13, 15), (14, 15), (15, 30))
    ):
        close = base + offset
        candles.append(
            Candle(
                timestamp=day.replace(hour=hour, minute=minute),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1_000 + offset,
            )
        )
    return candles


def _hourly_series(*, complete_days: int = 10, partial_final=False):
    day = datetime(2026, 1, 5, tzinfo=IST)
    candles = []
    added = 0
    while added < complete_days:
        if day.weekday() < 5:
            candles.extend(_session(day, 100 + added))
            added += 1
        day += timedelta(days=1)
    if partial_final:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        candles.extend(_session(day, 200)[:2])
    return HistoricalCandleSeries(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval="ONE_HOUR",
        candles=candles,
        retrieved_at=datetime(2026, 1, 20, tzinfo=IST),
        source="test_market",
    )


class DeriveSwingTimeframesTests(unittest.TestCase):
    def setUp(self):
        self.use_case = DeriveSwingTimeframes()

    def test_derives_daily_then_weekly_from_one_hourly_source(self):
        hourly = _hourly_series()

        result = self.use_case.execute(hourly)

        self.assertIs(result.hourly, hourly)
        self.assertEqual(result.daily.interval, "ONE_DAY")
        self.assertEqual(result.weekly.interval, "ONE_WEEK")
        self.assertEqual(len(result.daily.candles), 10)
        self.assertEqual(len(result.weekly.candles), 2)
        self.assertEqual(result.weekly.candles[0].open, 99.5)
        self.assertEqual(result.weekly.candles[0].close, 110.0)

    def test_excludes_incomplete_final_day_and_week(self):
        hourly = _hourly_series(complete_days=7, partial_final=True)

        result = self.use_case.execute(hourly)

        self.assertEqual(len(result.daily.candles), 7)
        self.assertEqual(len(result.weekly.candles), 1)
        self.assertEqual(
            result.daily.candles[-1].timestamp.weekday(),
            1,
        )

    def test_records_exact_immutable_lineage_fingerprints(self):
        result = self.use_case.execute(_hourly_series())

        self.assertEqual(
            result.lineage.hourly_fingerprint,
            market_series_fingerprint(result.hourly),
        )
        self.assertEqual(
            result.lineage.daily_fingerprint,
            market_series_fingerprint(result.daily),
        )
        self.assertEqual(
            result.lineage.weekly_fingerprint,
            market_series_fingerprint(result.weekly),
        )
        self.assertEqual(
            result.lineage.aggregation_path,
            ("ONE_HOUR", "ONE_DAY", "ONE_WEEK"),
        )

    def test_rejects_non_hourly_or_unvalidated_input(self):
        hourly = _hourly_series()
        daily = hourly.model_copy(update={"interval": "ONE_DAY"})

        with self.assertRaisesRegex(ValueError, "ONE_HOUR"):
            self.use_case.execute(daily)
        with self.assertRaisesRegex(ValueError, "validated series"):
            self.use_case.execute(object())

    def test_requires_at_least_one_complete_week(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "complete daily and weekly",
        ):
            self.use_case.execute(_hourly_series(complete_days=2))

    def test_model_rejects_identity_or_fingerprint_tampering(self):
        result = self.use_case.execute(_hourly_series())
        different_symbol = result.daily.model_copy(
            update={"symbol": "TCS-EQ"}
        )
        with self.assertRaisesRegex(ValidationError, "match hourly identity"):
            SwingTimeframeSeries(
                hourly=result.hourly,
                daily=different_symbol,
                weekly=result.weekly,
                lineage=result.lineage,
            )

        bad_lineage = result.lineage.model_copy(
            update={"weekly_fingerprint": "0" * 64}
        )
        with self.assertRaisesRegex(ValidationError, "fingerprints"):
            SwingTimeframeSeries(
                hourly=result.hourly,
                daily=result.daily,
                weekly=result.weekly,
                lineage=bad_lineage,
            )

    def test_derivation_is_deterministic(self):
        hourly = _hourly_series()

        first = self.use_case.execute(hourly)
        second = self.use_case.execute(hourly)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
