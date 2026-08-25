import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models.market import Candle, HistoricalCandleSeries
from app.models.multi_timeframe_evidence import (
    MultiTimeframeEvidencePackage,
    QualifiedTechnicalEvidence,
    SwingAnalysisTimeframe,
    qualified_pivot_id,
)
from app.orchestration.timeframe_technical_orchestrator import (
    ParallelTimeframeTechnicalOrchestrator,
)
from app.use_cases.build_multi_timeframe_evidence import (
    BuildMultiTimeframeEvidence,
)
from app.use_cases.derive_swing_timeframes import DeriveSwingTimeframes
from tests.unit.test_timeframe_technical_orchestrator import _timeframes


IST = ZoneInfo("Asia/Kolkata")


def _cyclical_timeframes(week_count=80):
    day = datetime(2024, 1, 1, tzinfo=IST)
    candles = []
    trading_day = 0
    pattern = (
        100,
        102,
        104,
        108,
        110,
        108,
        104,
        102,
        100,
        98,
        96,
        92,
        90,
        92,
        96,
        98,
        100,
        101,
        100,
        99,
    )
    while trading_day < week_count * 5:
        if day.weekday() < 5:
            cycle = trading_day // len(pattern)
            base = pattern[trading_day % len(pattern)] + cycle * 0.01
            for offset, (hour, minute) in enumerate(
                (
                    (9, 15),
                    (10, 15),
                    (11, 15),
                    (12, 15),
                    (13, 15),
                    (14, 15),
                    (15, 30),
                )
            ):
                close = base + offset * 0.01
                candles.append(
                    Candle(
                        timestamp=day.replace(hour=hour, minute=minute),
                        open=close - 0.2,
                        high=close + 0.6,
                        low=close - 0.6,
                        close=close,
                        volume=100_000 + trading_day * 10 + offset,
                    )
                )
            trading_day += 1
        day += timedelta(days=1)

    hourly = HistoricalCandleSeries(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval="ONE_HOUR",
        candles=candles,
        retrieved_at=day + timedelta(days=1),
        source="test_market",
    )
    return DeriveSwingTimeframes().execute(hourly)


class BuildMultiTimeframeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = ParallelTimeframeTechnicalOrchestrator().execute(
            _cyclical_timeframes()
        )
        cls.package = BuildMultiTimeframeEvidence().execute(cls.analysis)

    def test_preserves_and_qualifies_every_validated_signal(self):
        package = self.package

        self.assertEqual(len(package.daily.evidence), 12)
        self.assertEqual(len(package.weekly.evidence), 12)
        self.assertTrue(
            all(
                item.qualified_evidence_id.startswith("daily:")
                for item in package.daily.evidence
            )
        )
        self.assertTrue(
            all(
                item.qualified_evidence_id.startswith("weekly:")
                for item in package.weekly.evidence
            )
        )
        self.assertEqual(
            [item.evidence for item in package.daily.evidence],
            self.analysis.daily_submission.profile.snapshot.evidence,
        )
        qualified_ids = {
            item.qualified_evidence_id
            for context in (package.daily, package.weekly)
            for item in context.evidence
        }
        self.assertEqual(len(qualified_ids), 24)
        self.assertEqual(
            package.daily.accumulation,
            self.analysis.daily_accumulation,
        )
        self.assertEqual(
            package.weekly.accumulation,
            self.analysis.weekly_accumulation,
        )
        for context in (package.daily, package.weekly):
            self.assertEqual(
                context.accumulation.evaluated_at,
                context.evaluated_at,
            )
            self.assertEqual(
                context.accumulation.interval,
                context.interval,
            )

    def test_exposes_confirmed_pivots_and_immediate_levels(self):
        for context in (self.package.daily, self.package.weekly):
            self.assertIsNotNone(context.latest_confirmed_high)
            self.assertIsNotNone(context.latest_confirmed_low)
            self.assertIsNotNone(context.nearest_support)
            self.assertIsNotNone(context.nearest_resistance)
            self.assertLessEqual(
                context.latest_confirmed_high.pivot.confirmed_at,
                context.evaluated_at,
            )
            self.assertLessEqual(
                context.latest_confirmed_low.pivot.confirmed_at,
                context.evaluated_at,
            )
            self.assertEqual(
                str(context.evaluated_at.tzinfo),
                "Asia/Kolkata",
            )
            self.assertLessEqual(
                context.nearest_support.lifecycle.zone.lower_price,
                context.current_close,
            )
            self.assertGreaterEqual(
                context.nearest_resistance.lifecycle.zone.upper_price,
                context.current_close,
            )

    def test_absent_market_structure_is_explicitly_none(self):
        analysis = ParallelTimeframeTechnicalOrchestrator().execute(
            _timeframes()
        )

        package = BuildMultiTimeframeEvidence().execute(analysis)

        for context in (package.daily, package.weekly):
            self.assertEqual(context.recent_confirmed_pivots, ())
            self.assertIsNone(context.latest_confirmed_high)
            self.assertIsNone(context.latest_confirmed_low)
            self.assertIsNone(context.nearest_support)
            self.assertIsNone(context.nearest_resistance)

    def test_rejects_qualified_id_and_package_fingerprint_tampering(self):
        evidence = self.package.daily.evidence[0]
        with self.assertRaisesRegex(
            ValidationError,
            "must preserve timeframe and source id",
        ):
            QualifiedTechnicalEvidence(
                timeframe=SwingAnalysisTimeframe.DAILY,
                interval="ONE_DAY",
                qualified_evidence_id=(
                    f"daily:tampered.{evidence.evidence.evidence_id}"
                ),
                evidence=evidence.evidence,
            )

        with self.assertRaisesRegex(ValidationError, "fingerprint"):
            MultiTimeframeEvidencePackage(
                **(
                    self.package.model_dump(exclude_computed_fields=True)
                    | {"package_fingerprint": "0" * 64}
                )
            )

        tampered_accumulation = self.package.weekly.accumulation.model_copy(
            update={"zones": ()}
        )
        tampered_weekly = self.package.weekly.model_copy(
            update={"accumulation": tampered_accumulation}
        )
        with self.assertRaisesRegex(
            ValidationError,
            "preserve the assigned accumulation evidence",
        ):
            MultiTimeframeEvidencePackage(
                technical_analysis=self.analysis,
                daily=self.package.daily,
                weekly=tampered_weekly,
                package_fingerprint=self.package.package_fingerprint,
            )

    def test_rejects_future_confirmed_pivot_in_context(self):
        context = self.package.daily
        summary = context.latest_confirmed_high
        future_pivot = summary.pivot.model_copy(
            update={"confirmed_at": context.evaluated_at + timedelta(days=1)}
        )
        future_summary = summary.model_copy(
            update={
                "qualified_pivot_id": qualified_pivot_id(
                    SwingAnalysisTimeframe.DAILY,
                    future_pivot,
                ),
                "pivot": future_pivot,
            }
        )

        with self.assertRaisesRegex(ValidationError, "future"):
            context.__class__(
                **(
                    context.model_dump(exclude_computed_fields=True)
                    | {"latest_confirmed_high": future_summary}
                )
            )

    def test_validates_input_and_pivot_limit(self):
        for invalid in (0, -1, 1.5, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    BuildMultiTimeframeEvidence(recent_pivot_limit=invalid)
        with self.assertRaisesRegex(ValueError, "validated multi-timeframe"):
            BuildMultiTimeframeEvidence().execute(object())


if __name__ == "__main__":
    unittest.main()
