import unittest
from datetime import UTC, datetime, timedelta

from app.models.debate import (
    BullBearArgument,
    DebateRound,
    DebateSide,
    DebateTerminationReason,
    DebateVerdict,
)
from app.models.signals import SignalDirection
from app.orchestration.debate_session import DebateSession, DebateSessionState


def _argument(side, round_number, base_time, offset_seconds):
    return BullBearArgument(
        argument_id=f"{side.value}:{round_number}",
        side=side,
        round_number=round_number,
        thesis=f"{side.value} thesis {round_number}",
        evidence_citations=(f"signal.{round_number}",),
        rebuts_argument_id=None,
        model_id="stub-model",
        generated_at=base_time + timedelta(seconds=offset_seconds),
    )


def _round(round_number, base_time):
    return DebateRound(
        round_number=round_number,
        bull_argument=_argument(
            DebateSide.BULL,
            round_number,
            base_time,
            round_number * 10,
        ),
        bear_argument=_argument(
            DebateSide.BEAR,
            round_number,
            base_time,
            round_number * 10 + 1,
        ),
    )


def _verdict(base_time):
    return DebateVerdict(
        verdict_id="verdict-1",
        judge_model_id="stub-model",
        winner=SignalDirection.NEUTRAL,
        confidence_percentage=0.0,
        decisive_evidence_ids=("signal.1",),
        bull_case_summary="Stub bull summary.",
        bear_case_summary="Stub bear summary.",
        rationale="Stub rationale.",
        generated_at=base_time + timedelta(minutes=5),
    )


class DebateSessionTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def test_starts_in_progress(self):
        session = DebateSession()
        self.assertEqual(session.state, DebateSessionState.IN_PROGRESS)
        self.assertEqual(session.rounds, [])
        self.assertIsNone(session.verdict)
        self.assertEqual(len(session.history), 1)
        self.assertEqual(session.history[0][0], DebateSessionState.IN_PROGRESS)

    def test_record_round_appends(self):
        session = DebateSession()
        round_one = _round(1, self.base_time)
        session.record_round(round_one)
        self.assertEqual(session.rounds, [round_one])

    def test_cannot_record_round_after_terminal_state(self):
        session = DebateSession()
        session.record_round(_round(1, self.base_time))
        session.mark_max_rounds_reached()
        with self.assertRaisesRegex(ValueError, "cannot record a round"):
            session.record_round(_round(2, self.base_time))

    def test_cannot_transition_twice(self):
        session = DebateSession()
        session.record_round(_round(1, self.base_time))
        session.mark_stalled()
        with self.assertRaisesRegex(ValueError, "cannot transition"):
            session.mark_agent_failure()

    def test_attach_verdict_requires_terminal_state(self):
        session = DebateSession()
        session.record_round(_round(1, self.base_time))
        with self.assertRaisesRegex(ValueError, "terminal state"):
            session.attach_verdict(_verdict(self.base_time))

    def test_attach_verdict_transitions_to_judged(self):
        session = DebateSession()
        session.record_round(_round(1, self.base_time))
        session.mark_max_rounds_reached()
        verdict = _verdict(self.base_time)

        session.attach_verdict(verdict)

        self.assertEqual(session.state, DebateSessionState.JUDGED)
        self.assertEqual(session.verdict, verdict)
        self.assertEqual(
            [state for state, _ in session.history],
            [
                DebateSessionState.IN_PROGRESS,
                DebateSessionState.MAX_ROUNDS_REACHED,
                DebateSessionState.JUDGED,
            ],
        )

    def test_to_transcript_requires_terminal_state(self):
        session = DebateSession()
        session.record_round(_round(1, self.base_time))
        with self.assertRaisesRegex(ValueError, "terminal state"):
            session.to_transcript()

    def test_to_transcript_requires_at_least_one_round(self):
        session = DebateSession()
        session.mark_agent_failure()
        with self.assertRaisesRegex(ValueError, "zero completed rounds"):
            session.to_transcript()

    def test_to_transcript_maps_state_to_termination_reason(self):
        session = DebateSession()
        session.record_round(_round(1, self.base_time))
        session.mark_stalled()

        transcript = session.to_transcript()

        self.assertEqual(
            transcript.termination_reason,
            DebateTerminationReason.STALL_DETECTED,
        )
        self.assertEqual(transcript.round_count, 1)

    def test_to_transcript_after_judged_still_reflects_original_reason(self):
        session = DebateSession()
        session.record_round(_round(1, self.base_time))
        session.mark_max_rounds_reached()
        session.attach_verdict(_verdict(self.base_time))

        transcript = session.to_transcript()

        self.assertEqual(
            transcript.termination_reason,
            DebateTerminationReason.MAX_ROUNDS_REACHED,
        )


if __name__ == "__main__":
    unittest.main()
