import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models.analysis_timeframe import SwingAnalysisTimeframe
from app.models.cpr import (
    CPRAnalysisRecord,
    CPRBasis,
    CPRLifecycleState,
    CPRPricePosition,
    CPRWidthRegime,
)


IST = ZoneInfo("Asia/Kolkata")


class CPRAnalysisRecordTests(unittest.TestCase):
    def test_lifecycle_contract_exposes_full_band_reclaims(self):
        self.assertEqual(
            CPRLifecycleState.BULLISH_RECLAIM.value,
            "bullish_reclaim",
        )
        self.assertEqual(
            CPRLifecycleState.BEARISH_RECLAIM.value,
            "bearish_reclaim",
        )

    def setUp(self):
        self.source_started_at = datetime(2026, 8, 10, 9, 15, tzinfo=IST)
        self.source_ended_at = datetime(2026, 8, 14, 15, 30, tzinfo=IST)
        self.available_at = self.source_ended_at
        self.valid_from = datetime(2026, 8, 17, 9, 15, tzinfo=IST)
        self.valid_to = datetime(2026, 8, 19, 15, 30, tzinfo=IST)
        self.evaluated_at = self.valid_to
        self.retrieved_at = self.evaluated_at + timedelta(minutes=2)

    def record(self, **overrides):
        values = {
            "analysis_id": "daily:cpr:2026-W34",
            "exchange": "NSE",
            "symbol_token": "11536",
            "symbol": "TCS-EQ",
            "timeframe": SwingAnalysisTimeframe.DAILY,
            "interval": "ONE_DAY",
            "basis": CPRBasis.WEEKLY,
            "source": "angel_one",
            "source_period_started_at": self.source_started_at,
            "source_period_ended_at": self.source_ended_at,
            "available_at": self.available_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "evaluated_at": self.evaluated_at,
            "source_retrieved_at": self.retrieved_at,
            "source_high": 110.0,
            "source_low": 90.0,
            "source_close": 100.0,
            "pivot": 100.0,
            "bottom_central": 100.0,
            "top_central": 100.0,
            "width_percentage": 0.0,
            "width_percentile": 20.0,
            "width_sample_count": 20,
            "width_regime": CPRWidthRegime.NARROW,
            "current_price": 105.0,
            "price_position": CPRPricePosition.ABOVE,
            "consecutive_acceptance_candles": 2,
            "lifecycle_state": CPRLifecycleState.ACCEPTED_ABOVE,
            "evidence_ids": (
                "daily:cpr.price_above",
                "daily:cpr.narrow_expansion",
                "daily:cpr.lifecycle.accepted_above",
            ),
            "calculation_fingerprint": "a" * 64,
        }
        values.update(overrides)
        return CPRAnalysisRecord(**values)

    def test_accepts_immutable_daily_record_and_normalizes_time_to_ist(self):
        record = self.record(
            source_period_started_at=self.source_started_at.astimezone(
                timezone.utc
            )
        )

        self.assertEqual(record.schema_version, "jarvis.cpr_analysis.v1")
        self.assertEqual(record.basis, CPRBasis.WEEKLY)
        self.assertEqual(record.source_period_started_at.tzinfo, IST)
        with self.assertRaises(ValidationError):
            record.current_price = 120.0

    def test_accepts_monthly_cpr_for_weekly_analysis(self):
        record = self.record(
            analysis_id="weekly:cpr:2026-08",
            timeframe=SwingAnalysisTimeframe.WEEKLY,
            interval="ONE_WEEK",
            basis=CPRBasis.MONTHLY,
            evidence_ids=(
                "weekly:cpr.price_above",
                "weekly:cpr.lifecycle.accepted_above",
            ),
        )

        self.assertEqual(record.timeframe, SwingAnalysisTimeframe.WEEKLY)
        self.assertEqual(record.basis, CPRBasis.MONTHLY)

    def test_rejects_identity_basis_interval_and_evidence_mismatches(self):
        invalid = (
            {"analysis_id": "weekly:cpr:2026-W34"},
            {"basis": CPRBasis.MONTHLY},
            {"interval": "ONE_WEEK"},
            {"evidence_ids": ("weekly:cpr.price_above",)},
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(
                ValidationError
            ):
                self.record(**override)

    def test_rejects_naive_and_out_of_order_timestamps(self):
        with self.assertRaises(ValidationError):
            self.record(valid_from=self.valid_from.replace(tzinfo=None))

        with self.assertRaises(ValidationError):
            self.record(valid_from=self.source_ended_at)

        with self.assertRaises(ValidationError):
            self.record(source_retrieved_at=self.evaluated_at - timedelta(1))

    def test_rejects_source_and_derived_geometry_drift(self):
        invalid = (
            {"source_close": 120.0},
            {"source_high": 80.0},
            {"pivot": 100.01},
            {"bottom_central": 99.0},
            {"top_central": 101.0},
            {"width_percentage": 0.1},
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(
                ValidationError
            ):
                self.record(**override)

    def test_preserves_nonzero_cpr_geometry(self):
        pivot = (120.0 + 90.0 + 111.0) / 3
        raw_bottom = (120.0 + 90.0) / 2
        raw_top = 2 * pivot - raw_bottom
        bottom = min(raw_bottom, raw_top)
        top = max(raw_bottom, raw_top)
        record = self.record(
            source_high=120.0,
            source_low=90.0,
            source_close=111.0,
            pivot=pivot,
            bottom_central=bottom,
            top_central=top,
            width_percentage=(top - bottom) / pivot * 100,
            current_price=pivot,
            price_position=CPRPricePosition.INSIDE,
            consecutive_acceptance_candles=0,
            lifecycle_state=CPRLifecycleState.TRADING_INSIDE,
            evidence_ids=(
                "daily:cpr.price_inside",
                "daily:cpr.lifecycle.trading_inside",
            ),
        )

        self.assertLess(record.bottom_central, record.pivot)
        self.assertLess(record.pivot, record.top_central)

    def test_rejects_incoherent_width_history(self):
        invalid = (
            {
                "width_regime": CPRWidthRegime.INSUFFICIENT_HISTORY,
                "width_percentile": 10.0,
            },
            {
                "width_regime": CPRWidthRegime.NORMAL,
                "width_percentile": None,
            },
            {"width_percentile": 10.0, "width_sample_count": 0},
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(
                ValidationError
            ):
                self.record(**override)

        record = self.record(
            width_regime=CPRWidthRegime.INSUFFICIENT_HISTORY,
            width_percentile=None,
            width_sample_count=0,
        )
        self.assertIsNone(record.width_percentile)

    def test_rejects_price_position_and_inside_acceptance_mismatches(self):
        with self.assertRaises(ValidationError):
            self.record(price_position=CPRPricePosition.BELOW)

        with self.assertRaises(ValidationError):
            self.record(
                current_price=100.0,
                price_position=CPRPricePosition.INSIDE,
                consecutive_acceptance_candles=1,
            )

    def test_rejects_lifecycle_position_count_and_evidence_mismatches(self):
        invalid = (
            {"lifecycle_state": CPRLifecycleState.BEARISH_BREAKDOWN},
            {
                "lifecycle_state": CPRLifecycleState.BULLISH_RETEST,
                "consecutive_acceptance_candles": 1,
                "evidence_ids": (
                    "daily:cpr.lifecycle.bullish_retest",
                ),
            },
            {"evidence_ids": ("daily:cpr.price_above",)},
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(
                ValidationError
            ):
                self.record(**override)

    def test_rejects_nonfinite_values_bad_fingerprint_and_duplicate_evidence(self):
        invalid = (
            {"current_price": float("nan")},
            {"width_percentage": float("inf")},
            {"calculation_fingerprint": "not-a-fingerprint"},
            {
                "evidence_ids": (
                    "daily:cpr.price_above",
                    "daily:cpr.price_above",
                )
            },
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(
                ValidationError
            ):
                self.record(**override)


if __name__ == "__main__":
    unittest.main()
