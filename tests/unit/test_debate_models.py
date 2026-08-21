import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.agentic import JarvisJudgeDecision, JarvisJudgeVerdict
from app.models.debate import (
    AgenticDebateResult,
    BullBearArgument,
    BullBearDebateSubmission,
    DebateRound,
    DebateSide,
    DebateTerminationReason,
    DebateTranscript,
    DebateVerdict,
)
from app.models.signals import SignalDirection


class DebateModelsTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def argument(self, **overrides):
        values = {
            "argument_id": "jarvis.bull_debate_agent.v1:sub-1:1",
            "side": DebateSide.BULL,
            "round_number": 1,
            "thesis": "Price is holding above the 50-day moving average.",
            "evidence_citations": (
                "trend_signals.moving_average_alignment",
            ),
            "rebuts_argument_id": None,
            "model_id": "anthropic/claude-sonnet-4-5",
            "generated_at": self.base_time,
        }
        values.update(overrides)
        return BullBearArgument(**values)

    def bear_argument(self, **overrides):
        values = {
            "argument_id": "jarvis.bear_debate_agent.v1:sub-1:1",
            "side": DebateSide.BEAR,
            "round_number": 1,
            "thesis": "RSI shows overbought conditions.",
            "evidence_citations": (
                "momentum_signals.rsi_mean_reversion",
            ),
            "rebuts_argument_id": (
                "jarvis.bull_debate_agent.v1:sub-1:1"
            ),
            "model_id": "anthropic/claude-sonnet-4-5",
            "generated_at": self.base_time + timedelta(seconds=1),
        }
        values.update(overrides)
        return BullBearArgument(**values)

    def transcript(self, *, round_count=1, termination_reason=None):
        rounds = []
        for round_number in range(1, round_count + 1):
            offset = timedelta(seconds=round_number * 10)
            rounds.append(
                DebateRound(
                    round_number=round_number,
                    bull_argument=self.argument(
                        argument_id=f"bull:{round_number}",
                        round_number=round_number,
                        generated_at=self.base_time + offset,
                        rebuts_argument_id=(
                            f"bear:{round_number - 1}"
                            if round_number > 1
                            else None
                        ),
                    ),
                    bear_argument=self.bear_argument(
                        argument_id=f"bear:{round_number}",
                        round_number=round_number,
                        generated_at=(
                            self.base_time + offset + timedelta(seconds=1)
                        ),
                        rebuts_argument_id=f"bull:{round_number}",
                    ),
                )
            )
        return DebateTranscript(
            rounds=tuple(rounds),
            termination_reason=(
                termination_reason
                or DebateTerminationReason.MAX_ROUNDS_REACHED
            ),
        )

    def verdict(self, **overrides):
        values = {
            "verdict_id": "jarvis.debate_judge_agent.v1:sub-1:verdict",
            "judge_model_id": "anthropic/claude-sonnet-4-5",
            "winner": SignalDirection.BULLISH,
            "confidence_percentage": 62.5,
            "decisive_evidence_ids": (
                "trend_signals.moving_average_alignment",
            ),
            "bull_case_summary": "Bull leaned on trend alignment.",
            "bear_case_summary": "Bear leaned on momentum concerns.",
            "rationale": "Trend evidence outweighed momentum concerns.",
            "generated_at": self.base_time + timedelta(minutes=1),
        }
        values.update(overrides)
        return DebateVerdict(**values)

    def submission(self, **overrides):
        values = {
            "submission_id": "jarvis.bull_bear_debate.v1:sub-1:abc",
            "agent_id": "jarvis.bull_bear_debate.v1",
            "technical_submission_id": "sub-1",
            "technical_decision_id": "decision-1",
            "input_fingerprint": "a" * 64,
            "configuration_fingerprint": "b" * 64,
            "transcript": self.transcript(),
            "verdict": self.verdict(),
            "evaluated_at": self.base_time + timedelta(minutes=1),
        }
        values.update(overrides)
        return BullBearDebateSubmission(**values)

    # -- BullBearArgument --

    def test_argument_rejects_blank_thesis(self):
        with self.assertRaisesRegex(ValidationError, "cannot be blank"):
            self.argument(thesis="   ")

    def test_argument_rejects_duplicate_citations(self):
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            self.argument(
                evidence_citations=("a.b", "a.b"),
            )

    def test_argument_rejects_naive_timestamp(self):
        with self.assertRaisesRegex(ValidationError, "timezone"):
            self.argument(generated_at=datetime(2026, 1, 1))

    def test_argument_rejects_non_integer_round(self):
        with self.assertRaisesRegex(ValidationError, "integer"):
            self.argument(round_number=True)

    # -- DebateRound --

    def test_round_rejects_wrong_bull_side(self):
        with self.assertRaisesRegex(ValidationError, "wrong side"):
            DebateRound(
                round_number=1,
                bull_argument=self.bear_argument(round_number=1),
                bear_argument=self.bear_argument(round_number=1),
            )

    def test_round_rejects_mismatched_round_number(self):
        with self.assertRaisesRegex(ValidationError, "match the round"):
            DebateRound(
                round_number=2,
                bull_argument=self.argument(round_number=1),
                bear_argument=self.bear_argument(round_number=1),
            )

    # -- DebateTranscript --

    def test_transcript_requires_sequential_round_numbers(self):
        with self.assertRaisesRegex(ValidationError, "sequentially"):
            DebateTranscript(
                rounds=(
                    DebateRound(
                        round_number=2,
                        bull_argument=self.argument(round_number=2),
                        bear_argument=self.bear_argument(round_number=2),
                    ),
                ),
                termination_reason=(
                    DebateTerminationReason.MAX_ROUNDS_REACHED
                ),
            )

    def test_transcript_round_count(self):
        transcript = self.transcript(round_count=3)
        self.assertEqual(transcript.round_count, 3)

    # -- DebateVerdict --

    def test_verdict_rejects_out_of_range_confidence(self):
        with self.assertRaisesRegex(ValidationError, "less than or equal"):
            self.verdict(confidence_percentage=150.0)

    def test_verdict_rejects_duplicate_decisive_evidence(self):
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            self.verdict(decisive_evidence_ids=("a.b", "a.b"))

    # -- BullBearDebateSubmission --

    def test_submission_builds_from_valid_transcript_and_verdict(self):
        submission = self.submission()
        self.assertEqual(submission.transcript.round_count, 1)

    def test_submission_rejects_duplicate_argument_ids(self):
        duplicated_round = DebateRound(
            round_number=1,
            bull_argument=self.argument(
                argument_id="dup",
                round_number=1,
            ),
            bear_argument=self.bear_argument(
                argument_id="dup",
                round_number=1,
                rebuts_argument_id=None,
            ),
        )
        with self.assertRaisesRegex(ValidationError, "unique"):
            self.submission(
                transcript=DebateTranscript(
                    rounds=(duplicated_round,),
                    termination_reason=(
                        DebateTerminationReason.MAX_ROUNDS_REACHED
                    ),
                )
            )

    def test_submission_rejects_unknown_rebuttal_reference(self):
        bad_round = DebateRound(
            round_number=1,
            bull_argument=self.argument(round_number=1),
            bear_argument=self.bear_argument(
                round_number=1,
                rebuts_argument_id="nonexistent",
            ),
        )
        with self.assertRaisesRegex(ValidationError, "earlier argument"):
            self.submission(
                transcript=DebateTranscript(
                    rounds=(bad_round,),
                    termination_reason=(
                        DebateTerminationReason.MAX_ROUNDS_REACHED
                    ),
                )
            )

    def test_submission_rejects_uncited_decisive_evidence(self):
        with self.assertRaisesRegex(ValidationError, "must have been cited"):
            self.submission(
                verdict=self.verdict(
                    decisive_evidence_ids=("never_cited.signal",)
                )
            )

    def test_submission_rejects_verdict_before_final_argument(self):
        with self.assertRaisesRegex(ValidationError, "cannot precede"):
            self.submission(
                verdict=self.verdict(generated_at=self.base_time)
            )

    # -- AgenticDebateResult --

    def decision(self, *, accepted, submission_id, decided_at):
        if accepted:
            return JarvisJudgeDecision(
                decision_id=f"jarvis.debate_judge.v1:{submission_id}",
                judge_id="jarvis.debate_judge.v1",
                submission_id=submission_id,
                verdict=JarvisJudgeVerdict.ACCEPTED,
                decided_at=decided_at,
                passed_checks=("debate_submission_schema_valid",),
            )
        return JarvisJudgeDecision(
            decision_id=f"jarvis.debate_judge.v1:{submission_id}",
            judge_id="jarvis.debate_judge.v1",
            submission_id=submission_id,
            verdict=JarvisJudgeVerdict.REJECTED,
            decided_at=decided_at,
            passed_checks=("debate_submission_schema_valid",),
            reasons=("submission used an unexpected debate configuration",),
        )

    def test_result_rejects_mismatched_submission_id(self):
        submission = self.submission()
        decision = self.decision(
            accepted=True,
            submission_id="different-id",
            decided_at=submission.evaluated_at,
        )
        with self.assertRaisesRegex(ValidationError, "reviewed submission"):
            AgenticDebateResult(
                orchestrator_id="jarvis.debate_orchestrator.v1",
                submission=submission,
                decision=decision,
            )

    def test_result_approved_verdict_gated_on_acceptance(self):
        submission = self.submission()
        accepted_decision = self.decision(
            accepted=True,
            submission_id=submission.submission_id,
            decided_at=submission.evaluated_at,
        )
        accepted_result = AgenticDebateResult(
            orchestrator_id="jarvis.debate_orchestrator.v1",
            submission=submission,
            decision=accepted_decision,
        )
        self.assertEqual(
            accepted_result.approved_verdict,
            submission.verdict,
        )

        rejected_decision = self.decision(
            accepted=False,
            submission_id=submission.submission_id,
            decided_at=submission.evaluated_at,
        )
        rejected_result = AgenticDebateResult(
            orchestrator_id="jarvis.debate_orchestrator.v1",
            submission=submission,
            decision=rejected_decision,
        )
        self.assertIsNone(rejected_result.approved_verdict)


if __name__ == "__main__":
    unittest.main()
