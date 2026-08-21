import unittest

from tests.unit._debate_fixtures import (
    build_approved_technical_result,
    build_precedent_summary,
)

from app.agents.bull_agent import BullDebateAgent, BullDebateAgentConfig
from app.exceptions import LLMResponseValidationError
from app.llm.gateway import StructuredGeneration
from app.models.debate import DebateSide


class FakeGateway:
    def __init__(
        self,
        draft_payloads,
        model="fake-bull-model",
        fingerprint="a" * 64,
    ):
        self.draft_payloads = list(draft_payloads)
        self.calls = []
        self.model = model
        self.fingerprint = fingerprint

    @property
    def configuration_fingerprint(self):
        return self.fingerprint

    def generate(self, *, system, messages, response_model):
        self.calls.append({"system": system, "messages": messages})
        payload = self.draft_payloads.pop(0)
        return StructuredGeneration[response_model](
            value=response_model(**payload),
            provider="fake-provider",
            model=self.model,
            attempt_count=1,
        )


class BullDebateAgentTests(unittest.TestCase):
    def setUp(self):
        technical_result = build_approved_technical_result()
        self.profile = technical_result.submission.profile
        self.evidence_ids = [
            item.evidence_id for item in self.profile.snapshot.evidence
        ]

    def test_config_rejects_non_instance(self):
        with self.assertRaisesRegex(ValueError, "validated configuration"):
            BullDebateAgent(FakeGateway([]), config=object())

    def test_rejects_non_gateway_dependency(self):
        with self.assertRaisesRegex(ValueError, "structured LLM gateway"):
            BullDebateAgent(object())

    def test_configuration_fingerprint_is_sha256_hex(self):
        agent = BullDebateAgent(FakeGateway([]))
        fingerprint = agent.configuration_fingerprint
        self.assertEqual(len(fingerprint), 64)
        int(fingerprint, 16)

    def test_configuration_fingerprint_includes_bound_gateway(self):
        first = BullDebateAgent(
            FakeGateway([], fingerprint="a" * 64)
        )
        second = BullDebateAgent(
            FakeGateway([], fingerprint="b" * 64)
        )

        self.assertNotEqual(
            first.configuration_fingerprint,
            second.configuration_fingerprint,
        )

    def test_config_rejects_blank_prompt_version(self):
        with self.assertRaises(ValueError):
            BullDebateAgentConfig(prompt_version="   ")

    def test_prompt_includes_all_evidence_ids(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Bullish momentum is confirmed.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                }
            ]
        )
        agent = BullDebateAgent(gateway)

        argument = agent.generate_argument(
            profile=self.profile,
            transcript_so_far=(),
            round_number=1,
            technical_submission_id="sub-1",
        )

        self.assertEqual(argument.side, DebateSide.BULL)
        self.assertEqual(argument.round_number, 1)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(argument.model_id, "fake-bull-model")
        prompt_text = gateway.calls[0]["messages"][0]["content"]
        for evidence_id in self.evidence_ids:
            self.assertIn(evidence_id, prompt_text)

    def test_precedent_included_in_prompt_when_provided(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Bullish momentum is confirmed.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                }
            ]
        )
        agent = BullDebateAgent(gateway)
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
        self.assertIn("context only", prompt_text)

    def test_precedent_section_absent_when_not_provided(self):
        gateway = FakeGateway(
            [
                {
                    "thesis": "Bullish momentum is confirmed.",
                    "evidence_citations": [self.evidence_ids[0]],
                    "rebuts_argument_id": None,
                }
            ]
        )
        agent = BullDebateAgent(gateway)

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
        agent = BullDebateAgent(gateway)

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
        agent = BullDebateAgent(gateway)

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
        agent = BullDebateAgent(gateway)

        with self.assertRaises(LLMResponseValidationError):
            agent.generate_argument(
                profile=self.profile,
                transcript_so_far=(),
                round_number=1,
                technical_submission_id="sub-1",
            )


if __name__ == "__main__":
    unittest.main()
