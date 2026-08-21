import unittest
from datetime import UTC, datetime, timedelta

from tests.unit._debate_fixtures import build_approved_technical_result

from app.agents.debate_judge_agent import DebateJudgeAgent
from app.exceptions import LLMResponseValidationError
from app.models.debate import (
    BullBearArgument,
    DebateRound,
    DebateSide,
    DebateTerminationReason,
    DebateTranscript,
)


class FakeClient:
    def __init__(self, draft_payloads):
        self.draft_payloads = list(draft_payloads)
        self.calls = []

    def complete_structured(self, *, config, system, messages, response_model):
        self.calls.append({"system": system, "messages": messages})
        payload = self.draft_payloads.pop(0)
        return response_model(**payload)


class DebateJudgeAgentTests(unittest.TestCase):
    def setUp(self):
        technical_result = build_approved_technical_result()
        self.profile = technical_result.submission.profile
        self.evidence_ids = [
            item.evidence_id for item in self.profile.snapshot.evidence
        ]
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        bull_argument = BullBearArgument(
            argument_id="bull:1",
            side=DebateSide.BULL,
            round_number=1,
            thesis="Bullish thesis.",
            evidence_citations=(self.evidence_ids[0],),
            rebuts_argument_id=None,
            model_id="anthropic/claude-sonnet-4-5",
            generated_at=base_time,
        )
        bear_argument = BullBearArgument(
            argument_id="bear:1",
            side=DebateSide.BEAR,
            round_number=1,
            thesis="Bearish thesis.",
            evidence_citations=(self.evidence_ids[1],),
            rebuts_argument_id="bull:1",
            model_id="anthropic/claude-sonnet-4-5",
            generated_at=base_time + timedelta(seconds=1),
        )
        self.transcript = DebateTranscript(
            rounds=(
                DebateRound(
                    round_number=1,
                    bull_argument=bull_argument,
                    bear_argument=bear_argument,
                ),
            ),
            termination_reason=DebateTerminationReason.MAX_ROUNDS_REACHED,
        )

    def test_config_rejects_non_instance(self):
        with self.assertRaisesRegex(ValueError, "validated"):
            DebateJudgeAgent(config=object())

    def test_prompt_includes_evidence_and_transcript(self):
        client = FakeClient(
            [
                {
                    "winner": "bullish",
                    "confidence_percentage": 55.0,
                    "decisive_evidence_ids": [self.evidence_ids[0]],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Trend evidence was stronger.",
                }
            ]
        )
        agent = DebateJudgeAgent(client=client)

        verdict = agent.render_verdict(
            profile=self.profile,
            transcript=self.transcript,
            technical_submission_id="sub-1",
        )

        self.assertEqual(verdict.decisive_evidence_ids, (self.evidence_ids[0],))
        self.assertEqual(
            verdict.bull_case_summary, "Bull leaned on trend evidence."
        )
        self.assertEqual(
            verdict.bear_case_summary, "Bear leaned on momentum evidence."
        )
        prompt_text = client.calls[0]["messages"][0]["content"]
        self.assertIn("Bullish thesis.", prompt_text)
        self.assertIn("Bearish thesis.", prompt_text)
        for evidence_id in self.evidence_ids:
            self.assertIn(evidence_id, prompt_text)

    def test_render_verdict_has_no_precedent_parameter(self):
        # The judge must never see precedent -- verdicts are grounded
        # solely in this debate's own evidence and transcript. Bull/Bear
        # get precedent (see test_bull_agent.py/test_bear_agent.py); the
        # judge structurally cannot accept it.
        client = FakeClient(
            [
                {
                    "winner": "bullish",
                    "confidence_percentage": 55.0,
                    "decisive_evidence_ids": [self.evidence_ids[0]],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Trend evidence was stronger.",
                }
            ]
        )
        agent = DebateJudgeAgent(client=client)

        with self.assertRaises(TypeError):
            agent.render_verdict(
                profile=self.profile,
                transcript=self.transcript,
                technical_submission_id="sub-1",
                precedent=(),
            )

    def test_prompt_has_role_context_system_prompt_and_feedback_sections(self):
        client = FakeClient(
            [
                {
                    "winner": "bearish",
                    "confidence_percentage": 40.0,
                    "decisive_evidence_ids": ["nonexistent.signal"],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Bad rationale.",
                },
                {
                    "winner": "bearish",
                    "confidence_percentage": 40.0,
                    "decisive_evidence_ids": [self.evidence_ids[1]],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Fixed rationale.",
                },
            ]
        )
        agent = DebateJudgeAgent(client=client)

        agent.render_verdict(
            profile=self.profile,
            transcript=self.transcript,
            technical_submission_id="sub-1",
        )

        self.assertEqual(len(client.calls), 2)

        first_system = client.calls[0]["system"]
        self.assertIn("# Role", first_system)
        self.assertIn("# System Prompt", first_system)

        first_user = client.calls[0]["messages"][0]["content"]
        self.assertIn("# Context", first_user)
        self.assertIn("# Feedback", first_user)
        self.assertIn("None yet", first_user)

        second_user = client.calls[1]["messages"][0]["content"]
        self.assertIn("# Context", second_user)
        self.assertIn("# Feedback", second_user)
        self.assertNotIn("None yet", second_user)
        self.assertIn("nonexistent.signal", second_user)

    def test_retries_once_on_hallucinated_decisive_evidence(self):
        client = FakeClient(
            [
                {
                    "winner": "bearish",
                    "confidence_percentage": 40.0,
                    "decisive_evidence_ids": ["nonexistent.signal"],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Bad rationale.",
                },
                {
                    "winner": "bearish",
                    "confidence_percentage": 40.0,
                    "decisive_evidence_ids": [self.evidence_ids[1]],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Fixed rationale.",
                },
            ]
        )
        agent = DebateJudgeAgent(client=client)

        verdict = agent.render_verdict(
            profile=self.profile,
            transcript=self.transcript,
            technical_submission_id="sub-1",
        )

        self.assertEqual(verdict.rationale, "Fixed rationale.")
        self.assertEqual(len(client.calls), 2)

    def test_raises_after_two_hallucinated_attempts(self):
        client = FakeClient(
            [
                {
                    "winner": "bearish",
                    "confidence_percentage": 40.0,
                    "decisive_evidence_ids": ["nonexistent.signal"],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Bad rationale.",
                },
                {
                    "winner": "bearish",
                    "confidence_percentage": 40.0,
                    "decisive_evidence_ids": ["still.nonexistent"],
                    "bull_case_summary": "Bull leaned on trend evidence.",
                    "bear_case_summary": "Bear leaned on momentum evidence.",
                    "rationale": "Still bad.",
                },
            ]
        )
        agent = DebateJudgeAgent(client=client)

        with self.assertRaises(LLMResponseValidationError):
            agent.render_verdict(
                profile=self.profile,
                transcript=self.transcript,
                technical_submission_id="sub-1",
            )


if __name__ == "__main__":
    unittest.main()
