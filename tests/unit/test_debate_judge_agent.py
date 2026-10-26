import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.unit._debate_fixtures import build_approved_technical_result

from app.agents.debate_judge_agent import DebateJudgeAgent
from app.exceptions import LLMResponseValidationError
from app.llm.gateway import StructuredGeneration
from app.models.debate import (
    BullBearArgument,
    DebateRound,
    DebateSide,
    DebateTerminationReason,
    DebateTranscript,
)
from app.models.runtime_skill import RuntimeAgentRole
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.runtime_skill_loader import RuntimeSkillLoader
from tests.unit.test_build_multi_timeframe_evidence import (
    _cyclical_timeframes,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class FakeGateway:
    def __init__(
        self,
        draft_payloads,
        model="fake-judge-model",
        fingerprint="c" * 64,
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


class DebateJudgeAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skills = RuntimeSkillLoader(
            _REPOSITORY_ROOT / "runtime-skills" / "manifest.yaml"
        ).load()
        cls.judge_runtime_skill = skills.skill_for(RuntimeAgentRole.JUDGE)
        cls.bull_runtime_skill = skills.skill_for(RuntimeAgentRole.BULL)
        cls.technical_review = AgentOrchestrator().run_multi_timeframe_analysis(
            _cyclical_timeframes()
        )

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
            DebateJudgeAgent(FakeGateway([]), config=object())

    def test_rejects_unverified_or_wrong_role_runtime_skill(self):
        with self.assertRaisesRegex(ValueError, "must be verified"):
            DebateJudgeAgent(FakeGateway([]), runtime_skill=object())
        with self.assertRaisesRegex(ValueError, "requires the Judge"):
            DebateJudgeAgent(
                FakeGateway([]),
                runtime_skill=self.bull_runtime_skill,
            )

    def test_rejects_non_gateway_dependency(self):
        with self.assertRaisesRegex(ValueError, "structured LLM gateway"):
            DebateJudgeAgent(object())

    def test_configuration_fingerprint_includes_runtime_skill(self):
        embedded = DebateJudgeAgent(FakeGateway([]))
        static = DebateJudgeAgent(
            FakeGateway([]),
            runtime_skill=self.judge_runtime_skill,
        )

        self.assertNotEqual(
            embedded.configuration_fingerprint,
            static.configuration_fingerprint,
        )

    def test_verified_skill_is_multi_timeframe_system_instruction(self):
        package = self.technical_review.released_evidence
        weekly_id = package.weekly.evidence[0].qualified_evidence_id
        daily_id = package.daily.evidence[0].qualified_evidence_id
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        transcript = DebateTranscript(
            rounds=(
                DebateRound(
                    round_number=1,
                    bull_argument=BullBearArgument(
                        argument_id="bull:multi:1",
                        side=DebateSide.BULL,
                        round_number=1,
                        thesis="Weekly evidence supports the bullish case.",
                        evidence_citations=(weekly_id,),
                        model_id="fake-bull",
                        generated_at=base_time,
                        timeframe_relationship="mixed",
                    ),
                    bear_argument=BullBearArgument(
                        argument_id="bear:multi:1",
                        side=DebateSide.BEAR,
                        round_number=1,
                        thesis="Daily evidence supports caution.",
                        evidence_citations=(daily_id,),
                        rebuts_argument_id="bull:multi:1",
                        model_id="fake-bear",
                        generated_at=base_time + timedelta(seconds=1),
                        timeframe_relationship="mixed",
                    ),
                ),
            ),
            termination_reason=DebateTerminationReason.MAX_ROUNDS_REACHED,
        )
        gateway = FakeGateway(
            [
                {
                    "winner": "neutral",
                    "confidence_percentage": 35.0,
                    "decisive_evidence_ids": [weekly_id, daily_id],
                    "bull_case_summary": "Weekly evidence supports Bull.",
                    "bear_case_summary": "Daily evidence supports caution.",
                    "rationale": "The timeframes remain mixed.",
                }
            ]
        )
        agent = DebateJudgeAgent(
            gateway,
            runtime_skill=self.judge_runtime_skill,
        )

        agent.render_multi_timeframe_verdict(
            technical_review=self.technical_review,
            transcript=transcript,
        )

        self.assertEqual(
            gateway.calls[0]["system"],
            self.judge_runtime_skill.system_instructions,
        )
        self.assertIn(
            "# Jarvis Runtime Evidence Grounding",
            gateway.calls[0]["system"],
        )
        self.assertIn(
            "# Jarvis Judge Runtime Skill",
            gateway.calls[0]["system"],
        )

    def test_prompt_includes_evidence_and_transcript(self):
        gateway = FakeGateway(
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
        agent = DebateJudgeAgent(gateway)

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
        self.assertEqual(verdict.judge_model_id, "fake-judge-model")
        prompt_text = gateway.calls[0]["messages"][0]["content"]
        self.assertIn("Bullish thesis.", prompt_text)
        self.assertIn("Bearish thesis.", prompt_text)
        for evidence_id in self.evidence_ids:
            self.assertIn(evidence_id, prompt_text)

    def test_render_verdict_has_no_precedent_parameter(self):
        # The judge must never see precedent -- verdicts are grounded
        # solely in this debate's own evidence and transcript. Bull/Bear
        # get precedent (see test_bull_agent.py/test_bear_agent.py); the
        # judge structurally cannot accept it.
        gateway = FakeGateway(
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
        agent = DebateJudgeAgent(gateway)

        with self.assertRaises(TypeError):
            agent.render_verdict(
                profile=self.profile,
                transcript=self.transcript,
                technical_submission_id="sub-1",
                precedent=(),
            )

    def test_prompt_has_role_context_system_prompt_and_feedback_sections(self):
        gateway = FakeGateway(
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
        agent = DebateJudgeAgent(gateway)

        agent.render_verdict(
            profile=self.profile,
            transcript=self.transcript,
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

    def test_retries_once_on_hallucinated_decisive_evidence(self):
        gateway = FakeGateway(
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
        agent = DebateJudgeAgent(gateway)

        verdict = agent.render_verdict(
            profile=self.profile,
            transcript=self.transcript,
            technical_submission_id="sub-1",
        )

        self.assertEqual(verdict.rationale, "Fixed rationale.")
        self.assertEqual(len(gateway.calls), 2)

    def test_raises_after_two_hallucinated_attempts(self):
        gateway = FakeGateway(
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
        agent = DebateJudgeAgent(gateway)

        with self.assertRaises(LLMResponseValidationError):
            agent.render_verdict(
                profile=self.profile,
                transcript=self.transcript,
                technical_submission_id="sub-1",
            )


if __name__ == "__main__":
    unittest.main()
