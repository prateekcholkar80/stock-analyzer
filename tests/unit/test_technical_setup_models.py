import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models.multi_timeframe_evidence import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)
from app.models.technical_setup import (
    BEARISH_SETUP_SEQUENCE,
    BULLISH_SETUP_SEQUENCE,
    SwingSetupSide,
    SwingSetupStep,
    SwingSetupStepId,
    SwingSetupStepState,
    TimeframeSwingSetup,
)


IST = ZoneInfo("Asia/Kolkata")


class SwingSetupContractTests(unittest.TestCase):
    def setUp(self):
        self.evaluated_at = datetime(2026, 8, 24, 15, 30, tzinfo=IST)

    def build_step(
        self,
        *,
        step_id=SwingSetupStepId.BULLISH_CHANGE_OF_CHARACTER,
        side=SwingSetupSide.BULLISH,
        timeframe=SwingAnalysisTimeframe.DAILY,
        state=SwingSetupStepState.PENDING,
        **overrides,
    ):
        sequence = (
            BULLISH_SETUP_SEQUENCE
            if side is SwingSetupSide.BULLISH
            else BEARISH_SETUP_SEQUENCE
        )
        sequence_number = (
            sequence.index(step_id) + 1 if step_id in sequence else 1
        )
        values = {
            "step_id": step_id,
            "side": side,
            "timeframe": timeframe,
            "interval": timeframe_interval(timeframe),
            "sequence": sequence_number,
            "state": state,
            "evaluated_at": self.evaluated_at,
            "explanation": "The condition has not yet been confirmed.",
            "thresholds": {"confirmation_closes": 1},
        }
        if state in {
            SwingSetupStepState.CONFIRMED,
            SwingSetupStepState.DEVELOPING,
            SwingSetupStepState.CONTRADICTED,
            SwingSetupStepState.INVALIDATED,
        }:
            values.update(
                {
                    "evidence_ids": (
                        f"{timeframe.value}:structure.confirmation",
                    ),
                    "observed_at": self.evaluated_at - timedelta(days=2),
                    "available_at": self.evaluated_at - timedelta(days=1),
                    "observed_values": {"close": 2500.0},
                }
            )
        if state is SwingSetupStepState.CONFIRMED:
            values["confirmed_at"] = self.evaluated_at - timedelta(days=1)
        if state is SwingSetupStepState.INVALIDATED:
            values["invalidated_at"] = self.evaluated_at
        values.update(overrides)
        return SwingSetupStep(**values)

    def build_matrix(
        self,
        side=SwingSetupSide.BULLISH,
        timeframe=SwingAnalysisTimeframe.DAILY,
        *,
        steps=None,
        **overrides,
    ):
        sequence = (
            BULLISH_SETUP_SEQUENCE
            if side is SwingSetupSide.BULLISH
            else BEARISH_SETUP_SEQUENCE
        )
        resolved_steps = steps or tuple(
            self.build_step(
                step_id=step_id,
                side=side,
                timeframe=timeframe,
            )
            for step_id in sequence
        )
        values = {
            "setup_id": f"{timeframe.value}:{side.value}_setup",
            "side": side,
            "timeframe": timeframe,
            "interval": timeframe_interval(timeframe),
            "evaluated_at": self.evaluated_at,
            "steps": resolved_steps,
        }
        values.update(overrides)
        return TimeframeSwingSetup(**values)

    def test_canonical_sequences_cover_all_declared_steps(self):
        self.assertEqual(len(BULLISH_SETUP_SEQUENCE), 9)
        self.assertEqual(len(BEARISH_SETUP_SEQUENCE), 8)
        self.assertEqual(
            set(BULLISH_SETUP_SEQUENCE + BEARISH_SETUP_SEQUENCE),
            set(SwingSetupStepId),
        )

    def test_builds_confirmed_step_with_canonical_label(self):
        step = self.build_step(state=SwingSetupStepState.CONFIRMED)

        self.assertEqual(step.label, "Bullish CHOCH")
        self.assertEqual(step.sequence, 2)
        self.assertEqual(
            step.model_dump()["label"],
            "Bullish CHOCH",
        )

    def test_supports_previously_confirmed_then_invalidated_step(self):
        step = self.build_step(
            state=SwingSetupStepState.INVALIDATED,
            confirmed_at=self.evaluated_at - timedelta(hours=12),
            invalidated_at=self.evaluated_at,
        )

        self.assertEqual(step.state, SwingSetupStepState.INVALIDATED)
        self.assertIsNotNone(step.confirmed_at)
        self.assertIsNotNone(step.invalidated_at)

    def test_every_canonical_step_builds_for_its_assigned_side(self):
        for side, sequence in (
            (SwingSetupSide.BULLISH, BULLISH_SETUP_SEQUENCE),
            (SwingSetupSide.BEARISH, BEARISH_SETUP_SEQUENCE),
        ):
            for step_id in sequence:
                with self.subTest(side=side, step_id=step_id):
                    step = self.build_step(step_id=step_id, side=side)
                    self.assertEqual(step.side, side)

    def test_rejects_step_side_sequence_and_interval_mismatch(self):
        cases = (
            {
                "step_id": SwingSetupStepId.BEARISH_LIQUIDITY_SWEEP,
                "side": SwingSetupSide.BULLISH,
            },
            {"sequence": 9},
            {"interval": "ONE_WEEK"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.build_step(**overrides)

    def test_rejects_wrong_timeframe_blank_and_duplicate_evidence_ids(self):
        evidence_cases = (
            ("weekly:structure.confirmation",),
            ("   ",),
            (
                "daily:structure.confirmation",
                "daily:structure.confirmation",
            ),
        )
        for evidence_ids in evidence_cases:
            with self.subTest(evidence_ids=evidence_ids):
                with self.assertRaises(ValidationError):
                    self.build_step(
                        state=SwingSetupStepState.DEVELOPING,
                        evidence_ids=evidence_ids,
                    )

    def test_rejects_naive_future_and_reversed_timestamps(self):
        cases = (
            {"evaluated_at": datetime(2026, 8, 24, 15, 30)},
            {
                "available_at": self.evaluated_at + timedelta(minutes=1),
            },
            {
                "observed_at": self.evaluated_at - timedelta(hours=1),
                "available_at": self.evaluated_at - timedelta(hours=2),
            },
            {"observed_at": None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.build_step(
                        state=SwingSetupStepState.DEVELOPING,
                        **overrides,
                    )

    def test_enforces_state_specific_evidence_and_terminal_times(self):
        invalid_cases = (
            {
                "state": SwingSetupStepState.CONFIRMED,
                "confirmed_at": None,
            },
            {
                "state": SwingSetupStepState.DEVELOPING,
                "confirmed_at": self.evaluated_at,
            },
            {
                "state": SwingSetupStepState.INVALIDATED,
                "invalidated_at": None,
            },
            {
                "state": SwingSetupStepState.UNAVAILABLE,
                "evidence_ids": ("daily:unavailable.claim",),
            },
        )
        for overrides in invalid_cases:
            state = overrides.pop("state")
            with self.subTest(state=state, overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.build_step(state=state, **overrides)

        with self.assertRaisesRegex(
            ValidationError,
            "require available evidence",
        ):
            self.build_step(
                state=SwingSetupStepState.DEVELOPING,
                evidence_ids=(),
            )

    def test_rejects_invalid_observed_values_and_thresholds(self):
        cases = (
            {"observed_values": {" ": 1}},
            {"observed_values": {"close": float("nan")}},
            {"thresholds": {"minimum": "   "}},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.build_step(**overrides)

    def test_step_is_immutable(self):
        step = self.build_step()

        with self.assertRaises(ValidationError):
            step.state = SwingSetupStepState.CONFIRMED

    def test_builds_complete_daily_and_weekly_matrices_for_both_sides(self):
        for side in SwingSetupSide:
            for timeframe in SwingAnalysisTimeframe:
                with self.subTest(side=side, timeframe=timeframe):
                    matrix = self.build_matrix(side, timeframe)
                    self.assertEqual(matrix.side, side)
                    self.assertEqual(matrix.timeframe, timeframe)
                    self.assertEqual(
                        len(matrix.steps),
                        9 if side is SwingSetupSide.BULLISH else 8,
                    )

    def test_rejects_incomplete_reordered_or_cross_assigned_matrix(self):
        complete = self.build_matrix().steps
        invalid_steps = (
            complete[:-1],
            tuple(reversed(complete)),
            complete[:-1]
            + (
                self.build_step(
                    step_id=SwingSetupStepId.BULLISH_HIGHER_HIGH,
                    timeframe=SwingAnalysisTimeframe.WEEKLY,
                ),
            ),
        )
        for steps in invalid_steps:
            with self.subTest(step_count=len(steps)):
                with self.assertRaises(ValidationError):
                    self.build_matrix(steps=steps)

    def test_rejects_parent_id_interval_and_evaluation_mismatch(self):
        cases = (
            {"setup_id": "weekly:bullish_setup"},
            {"interval": "ONE_WEEK"},
            {
                "evaluated_at": self.evaluated_at + timedelta(days=1),
            },
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.build_matrix(**overrides)


if __name__ == "__main__":
    unittest.main()
