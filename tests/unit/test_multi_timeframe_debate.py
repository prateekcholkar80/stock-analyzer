import unittest

from app.agents._debate_support import (
    serialize_multi_timeframe_evidence,
    valid_multi_timeframe_evidence_ids,
)
from app.agents.bear_agent import BearDebateAgent
from app.agents.bull_agent import BullDebateAgent
from app.agents.debate_judge_agent import DebateJudgeAgent
from app.exceptions import AgentSubmissionRejectedError
from app.llm.gateway import StructuredGeneration
from app.models.agentic import JarvisJudgeDecision, JarvisJudgeVerdict
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import (
    DebateOrchestrator,
    DebateOrchestratorConfig,
)
from app.use_cases.ask_jarvis_judge_follow_up import (
    AskJarvisJudgeFollowUp,
)
from tests.unit.test_build_multi_timeframe_evidence import (
    _cyclical_timeframes,
)


class _Gateway:
    def __init__(self, payloads, *, model, fingerprint):
        self.payloads = list(payloads)
        self.model = model
        self.fingerprint = fingerprint
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return self.fingerprint

    def generate(self, *, system, messages, response_model):
        self.calls.append({"system": system, "messages": messages})
        payload = self.payloads.pop(0)
        return StructuredGeneration[response_model](
            value=response_model(**payload),
            provider="fake-provider",
            model=self.model,
            attempt_count=1,
        )


class MultiTimeframeDebateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.technical_review = AgentOrchestrator().run_multi_timeframe_analysis(
            _cyclical_timeframes()
        )
        cls.package = cls.technical_review.released_evidence
        cls.weekly_signal_id = cls.package.weekly.evidence[0].qualified_evidence_id
        cls.daily_signal_id = cls.package.daily.evidence[0].qualified_evidence_id
        cls.weekly_support_id = cls.package.weekly.nearest_support.qualified_zone_id

    def test_serializer_separates_weekly_structure_and_daily_timing(self):
        serialized = serialize_multi_timeframe_evidence(self.package)

        self.assertLess(serialized.index("## WEEKLY"), serialized.index("## DAILY"))
        self.assertIn("WEEKLY defines structural direction", serialized)
        self.assertIn(self.weekly_support_id, serialized)
        self.assertIn("Immediate support", serialized)
        self.assertIn("Latest confirmed high", serialized)
        for identifier in valid_multi_timeframe_evidence_ids(self.package):
            self.assertIn(identifier, serialized)

    def test_bull_and_bear_prompts_require_conflict_analysis(self):
        bull_gateway = _Gateway(
            [
                {
                    "thesis": (
                        "Timeframes are conflicted: weekly structure is "
                        "constructive but daily timing needs confirmation."
                    ),
                    "evidence_citations": [
                        self.weekly_signal_id,
                        self.daily_signal_id,
                    ],
                    "rebuts_argument_id": None,
                    "timeframe_relationship": "conflicted",
                }
            ],
            model="fake-bull",
            fingerprint="a" * 64,
        )
        bear_gateway = _Gateway(
            [
                {
                    "thesis": (
                        "The conflict makes an early entry vulnerable."
                    ),
                    "evidence_citations": [
                        self.daily_signal_id,
                        self.weekly_support_id,
                    ],
                    "rebuts_argument_id": (
                        f"jarvis.bull_debate_agent.v1:"
                        f"{self.package.package_fingerprint}:1"
                    ),
                    "timeframe_relationship": "conflicted",
                }
            ],
            model="fake-bear",
            fingerprint="b" * 64,
        )
        bull = BullDebateAgent(bull_gateway).generate_multi_timeframe_argument(
            technical_review=self.technical_review,
            transcript_so_far=(),
            round_number=1,
        )
        bear = BearDebateAgent(bear_gateway).generate_multi_timeframe_argument(
            technical_review=self.technical_review,
            transcript_so_far=(bull,),
            round_number=1,
        )

        self.assertTrue(
            all(
                item.startswith(("daily:", "weekly:"))
                for item in bull.evidence_citations
            )
        )
        self.assertEqual(bull.timeframe_relationship.value, "conflicted")
        self.assertTrue(
            all(
                item.startswith(("daily:", "weekly:"))
                for item in bear.evidence_citations
            )
        )
        self.assertEqual(bear.timeframe_relationship.value, "conflicted")
        for gateway in (bull_gateway, bear_gateway):
            system = gateway.calls[0]["system"]
            context = gateway.calls[0]["messages"][0]["content"]
            self.assertIn("aligned, conflicted, mixed, or insufficient", system)
            self.assertIn("## WEEKLY", context)
            self.assertIn("## DAILY", context)
            self.assertIn("Judge-approved", context)

    def test_agents_refuse_evidence_rejected_by_existing_judge(self):
        decision = self.technical_review.decision
        rejected = JarvisJudgeDecision(
            **(
                decision.model_dump(exclude_computed_fields=True)
                | {
                    "verdict": JarvisJudgeVerdict.REJECTED,
                    "reasons": ("test rejection",),
                }
            )
        )
        review = MultiTimeframeEvidenceReview(
            evidence_package=self.package,
            decision=rejected,
        )
        for agent in (
            BullDebateAgent(_Gateway([], model="bull", fingerprint="a" * 64)),
            BearDebateAgent(_Gateway([], model="bear", fingerprint="b" * 64)),
        ):
            with self.subTest(agent=agent.agent_id):
                with self.assertRaisesRegex(ValueError, "Judge-released"):
                    agent.generate_multi_timeframe_argument(
                        technical_review=review,
                        transcript_so_far=(),
                        round_number=1,
                    )

    def test_multi_timeframe_grounding_retries_invented_qualified_id(self):
        gateway = _Gateway(
            [
                {
                    "thesis": "Invented evidence.",
                    "evidence_citations": ["weekly:invented.signal"],
                    "rebuts_argument_id": None,
                    "timeframe_relationship": "aligned",
                },
                {
                    "thesis": "Corrected evidence.",
                    "evidence_citations": [self.weekly_signal_id],
                    "rebuts_argument_id": None,
                    "timeframe_relationship": "mixed",
                },
            ],
            model="fake-bull",
            fingerprint="a" * 64,
        )

        argument = BullDebateAgent(
            gateway
        ).generate_multi_timeframe_argument(
            technical_review=self.technical_review,
            transcript_so_far=(),
            round_number=1,
        )

        self.assertEqual(argument.thesis, "Corrected evidence.")
        self.assertEqual(len(gateway.calls), 2)
        self.assertIn(
            "weekly:invented.signal",
            gateway.calls[1]["messages"][0]["content"],
        )

    def test_full_debate_and_follow_up_use_the_same_judge(self):
        bull_gateway = _Gateway(
            [
                {
                    "thesis": "Weekly structure supports the bull case.",
                    "evidence_citations": [self.weekly_signal_id],
                    "rebuts_argument_id": None,
                    "timeframe_relationship": "mixed",
                }
            ],
            model="fake-bull",
            fingerprint="a" * 64,
        )
        bear_gateway = _Gateway(
            [
                {
                    "thesis": "Daily timing remains the main risk.",
                    "evidence_citations": [self.daily_signal_id],
                    "rebuts_argument_id": (
                        f"jarvis.bull_debate_agent.v1:"
                        f"{self.package.package_fingerprint}:1"
                    ),
                    "timeframe_relationship": "mixed",
                }
            ],
            model="fake-bear",
            fingerprint="b" * 64,
        )
        judge_gateway = _Gateway(
            [
                {
                    "winner": "bullish",
                    "confidence_percentage": 70.0,
                    "decisive_evidence_ids": [self.weekly_signal_id],
                    "bull_case_summary": "Weekly structure supports Bull.",
                    "bear_case_summary": "Daily timing supports caution.",
                    "rationale": (
                        "The timeframes conflict, so the bull case is "
                        "conditional on daily confirmation."
                    ),
                },
                {
                    "answer": (
                        "The nearest confirmed weekly support is the "
                        "supplied weekly support zone."
                    ),
                    "evidence_citations": [self.weekly_support_id],
                },
            ],
            model="fake-judge",
            fingerprint="c" * 64,
        )
        judge_agent = DebateJudgeAgent(judge_gateway)
        orchestrator = DebateOrchestrator(
            bull_agent=BullDebateAgent(bull_gateway),
            bear_agent=BearDebateAgent(bear_gateway),
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=1),
        )

        debate = orchestrator.run_multi_timeframe_debate(
            self.technical_review
        )
        follow_up = AskJarvisJudgeFollowUp(orchestrator).execute(
            "Tell me where support is on a weekly basis.",
            technical_review=self.technical_review,
            debate_result=debate,
        )

        self.assertTrue(debate.decision.accepted)
        self.assertEqual(follow_up.judge_agent_id, judge_agent.agent_id)
        self.assertEqual(
            follow_up.technical_package_fingerprint,
            self.package.package_fingerprint,
        )
        self.assertEqual(
            follow_up.debate_verdict_id,
            debate.submission.verdict.verdict_id,
        )
        self.assertEqual(
            follow_up.evidence_citations,
            (self.weekly_support_id,),
        )
        follow_up_prompt = judge_gateway.calls[1]["messages"][0]["content"]
        self.assertIn("CEO follow-up question", follow_up_prompt)
        self.assertIn(self.weekly_support_id, follow_up_prompt)

    def test_follow_up_rejects_a_debate_for_another_context(self):
        class _RejectedDebate:
            pass

        with self.assertRaisesRegex(ValueError, "debate result"):
            DebateOrchestrator(
                bull_agent=BullDebateAgent(
                    _Gateway([], model="bull", fingerprint="a" * 64)
                ),
                bear_agent=BearDebateAgent(
                    _Gateway([], model="bear", fingerprint="b" * 64)
                ),
                judge_agent=DebateJudgeAgent(
                    _Gateway([], model="judge", fingerprint="c" * 64)
                ),
            ).answer_multi_timeframe_follow_up(
                "Where is support?",
                self.technical_review,
                _RejectedDebate(),
            )


if __name__ == "__main__":
    unittest.main()
