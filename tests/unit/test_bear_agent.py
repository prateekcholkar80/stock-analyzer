import unittest

from tests.unit._debate_fixtures import (
    build_approved_technical_result,
    build_precedent_summary,
)

from app.agents.bear_agent import BearDebateAgent
from app.exceptions import LLMResponseValidationError
from app.llm.gateway import StructuredGeneration
from app.models.debate import DebateSide


class FakeGateway:
    def __init__(self, draft_payloads, model="fake-bear-model"):
        self.draft_payloads = list(draft_payloads)
        self.calls = []
        self.model = model

    @property
    def configuration_fingerprint(self):
        return "b" * 64

    def generate(self, *, system, messages, response_model):
        self.calls.append({"system": system, "messages": messages})
        payload = self.draft_payloads.pop(0)
        return StructuredGeneration[response_model](
            value=response_model(**payload),
            provider="fake-provider",
            model=self.model,
            attempt_count=1,
        )


class BearDebateAgentTests(unittest.TestCase):
    def setUp(self):
        technical_result = build_approved_technical_result()
        self.profile = technical_result.submission.profile
        self.evidence_ids = [
            item.evidence_id for item in self.profile.snapshot.evidence
        ]

    def test_config_rejects_non_instance(self):
        with self.assertRaisesRegex(ValueError, "validated configuration"):
            BearDebateAgent(FakeGateway([]), config=object())

    def test_rejects_non_gateway_dependency(self):
        with self.assertRaisesRegex(ValueError, "structured LLM gateway"):
            BearDebateAgent(object())

    def test_prompt_includes_all_evidence_ids(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Momentum is overextended.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                }
            ]
        )
        agent = BearDebateAgent(gateway)

        argument = agent.generate_argument(
            profile=self.profile,
            transcript_so_far=(),
            round_number=1,
            technical_submission_id="sub-1",
        )

        self.assertEqual(argument.side, DebateSide.BEAR)
        self.assertEqual(argument.model_id, "fake-bear-model")
        prompt_text = gateway.calls[0]["messages"][0]["content"]
        for evidence_id in self.evidence_ids:
            self.assertIn(evidence_id, prompt_text)

    def test_precedent_included_in_prompt_when_provided(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Momentum is overextended.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                }
            ]
        )
        agent = BearDebateAgent(gateway)
        precedent = (build_precedent_summary(),)

        agent.generate_argument(
            profile=self.profile,
            transcript_so_far=(),
            round_number=1,
            technical_submission_id="sub-1",
            precedent=precedent,
        )

        prompt_text = gateway.calls[0]["messages"][0]["content"]
        self.assertIn("Precedent", prompt_text)
        self.assertIn("RELIANCE-EQ", prompt_text)

    def test_precedent_section_absent_when_not_provided(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Momentum is overextended.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                }
            ]
        )
        agent = BearDebateAgent(gateway)

        agent.generate_argument(
            profile=self.profile,
            transcript_so_far=(),
            round_number=1,
            technical_submission_id="sub-1",
        )

        prompt_text = gateway.calls[0]["messages"][0]["content"]
        self.assertNotIn("Precedent", prompt_text)

    def test_prompt_has_role_context_system_prompt_and_feedback_sections(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Bad citation.",
                    "evidence_citations": ["nonexistent.signal"],
                    "rebuts_argument_id": None,
                },
                {
                    "thesis": "Fixed citation.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                },
            ]
        )
        agent = BearDebateAgent(gateway)

        agent.generate_argument(
            profile=self.profile,
            transcript_so_far=(),
            round_number=1,
            technical_submission_id="sub-1",
        )

        self.assertEqual(len(gateway.calls), 2)

        first_system = gateway.calls[0]["system"]
        self.assertIn("# Role", first_system)
        self.assertIn("# System Prompt", first_system)

        first_user = gateway.calls[0]["messages"][0]["content"]
        self.assertIn("# Context", first_user)
        self.assertIn("# Feedback", first_user)
        self.assertIn("None yet", first_user)

        second_user = gateway.calls[1]["messages"][0]["content"]
        self.assertIn("# Context", second_user)
        self.assertIn("# Feedback", second_user)
        self.assertNotIn("None yet", second_user)
        self.assertIn("nonexistent.signal", second_user)

    def test_retries_once_on_hallucinated_citation(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Bad citation.",
                    "evidence_citations": ["nonexistent.signal"],
                    "rebuts_argument_id": None,
                },
                {
                    "thesis": "Fixed citation.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                },
            ]
        )
        agent = BearDebateAgent(gateway)

        argument = agent.generate_argument(
            profile=self.profile,
            transcript_so_far=(),
            round_number=1,
            technical_submission_id="sub-1",
        )

        self.assertEqual(argument.thesis, "Fixed citation.")
        self.assertEqual(len(gateway.calls), 2)

    def test_raises_after_two_hallucinated_attempts(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Bad citation.",
                    "evidence_citations": ["nonexistent.signal"],
                    "rebuts_argument_id": None,
                },
                {
                    "thesis": "Still bad.",
                    "evidence_citations": ["still.nonexistent"],
                    "rebuts_argument_id": None,
                },
            ]
        )
        agent = BearDebateAgent(gateway)

        with self.assertRaises(LLMResponseValidationError):
            agent.generate_argument(
                profile=self.profile,
                transcript_so_far=(),
                round_number=1,
                technical_submission_id="sub-1",
            )


if __name__ == "__main__":
    unittest.main()
