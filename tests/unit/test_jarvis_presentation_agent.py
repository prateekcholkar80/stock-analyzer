import unittest
from datetime import UTC, datetime

from tests.unit._debate_fixtures import build_approved_technical_result

from app.agents.jarvis_presentation_agent import (
    JARVIS_MULTI_TIMEFRAME_PERSONA_SYSTEM_PROMPT,
    JARVIS_PERSONA_SYSTEM_PROMPT,
    JarvisPresentationAgent,
)
from app.audit.prompt_audit import InMemoryPromptAuditSink
from app.exceptions import LLMResponseValidationError
from app.llm.audited_gateway import PromptAuditedLLMGateway
from app.llm.config import LLMRole
from app.llm.gateway import StructuredGeneration
from app.models.debate import BullBearArgument, DebateSide, DebateVerdict
from app.models.signals import SignalDirection
from app.models.multi_timeframe_trade import MultiTimeframeTradeDisposition
from app.models.storage import EndToEndSwingAnalysisResult
from app.orchestration.debate_orchestrator import (
    DebateOrchestrator,
    DebateOrchestratorConfig,
)
from app.agents._debate_support import valid_multi_timeframe_evidence_ids
from tests.unit.test_run_end_to_end_multi_timeframe_swing_analysis import (
    _trending_timeframes,
    _use_case as _multi_use_case,
)


class _SideAgent:
    def __init__(self, side):
        self.agent_id = f"stub.{side.value}.v1"
        self.side = side
        self.configuration_fingerprint = side.value[0] * 64

    def generate_argument(
        self,
        *,
        profile,
        transcript_so_far,
        round_number,
        technical_submission_id,
        precedent=(),
    ):
        return BullBearArgument(
            argument_id=f"{self.side.value}:{round_number}",
            side=self.side,
            round_number=round_number,
            thesis=f"{self.side.value} thesis",
            evidence_citations=(
                profile.snapshot.evidence[
                    0 if self.side is DebateSide.BULL else -1
                ].evidence_id,
            ),
            rebuts_argument_id=(
                "bull:1" if self.side is DebateSide.BEAR else None
            ),
            model_id="stub-model",
            generated_at=datetime.now(UTC),
        )


class _JudgeAgent:
    agent_id = "stub.judge.v1"
    configuration_fingerprint = "d" * 64

    def render_verdict(self, *, profile, transcript, technical_submission_id):
        evidence_id = transcript.rounds[0].bull_argument.evidence_citations[0]
        return DebateVerdict(
            verdict_id=f"verdict:{technical_submission_id}",
            judge_model_id="stub-judge",
            winner=SignalDirection.BULLISH,
            confidence_percentage=71.0,
            decisive_evidence_ids=(evidence_id,),
            bull_case_summary="The bull cited the stronger trend evidence.",
            bear_case_summary="The bear identified a technical risk.",
            rationale="Bull evidence was more persuasive.",
            generated_at=datetime.now(UTC),
        )


class _FakeGateway:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "f" * 64

    def generate(self, *, system, messages, response_model):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "response_model": response_model,
            }
        )
        return StructuredGeneration[response_model](
            value=response_model(**self.payloads.pop(0)),
            provider="fake-provider",
            model="fake-jarvis",
            attempt_count=1,
        )


def _result():
    technical = build_approved_technical_result()
    debate = DebateOrchestrator(
        bull_agent=_SideAgent(DebateSide.BULL),
        bear_agent=_SideAgent(DebateSide.BEAR),
        judge_agent=_JudgeAgent(),
        config=DebateOrchestratorConfig(max_rounds=1),
    ).run_debate(technical)
    return EndToEndSwingAnalysisResult.model_construct(
        use_case_id="jarvis.run_end_to_end_swing_analysis.v1",
        market_dataset_id="market:test",
        fetch=object(),
        technical_result=technical,
        debate_result=debate,
    )


def _valid_payload(result):
    profile = result.technical_result.submission.profile
    verdict = result.debate_result.submission.verdict
    transcript = result.debate_result.submission.transcript
    return {
        "executive_briefing": "Reliance has a bullish technical verdict.",
        "executive_evidence_ids": verdict.decisive_evidence_ids,
        "judge_conclusion_explanation": "The Judge preferred the bull case.",
        "judge_evidence_ids": verdict.decisive_evidence_ids,
        "technical_findings": [
            {
                "evidence_id": item.evidence_id,
                "inference": "This contributes to the bounded swing view.",
            }
            for item in profile.snapshot.evidence
        ],
        "bull_case": {
            "summary": "The bull case follows its cited evidence.",
            "argument_ids": tuple(
                round_.bull_argument.argument_id
                for round_ in transcript.rounds
            ),
            "evidence_ids": tuple(
                round_.bull_argument.evidence_citations[0]
                for round_ in transcript.rounds
            ),
        },
        "bear_case": {
            "summary": "The bear case follows its cited evidence.",
            "argument_ids": tuple(
                round_.bear_argument.argument_id
                for round_ in transcript.rounds
            ),
            "evidence_ids": tuple(
                round_.bear_argument.evidence_citations[0]
                for round_ in transcript.rounds
            ),
        },
    }


def _valid_multi_payload(result):
    package = result.technical_review.evidence_package
    verdict = result.debate_result.submission.verdict
    transcript = result.debate_result.submission.transcript
    return {
        "executive_briefing": (
            "Reliance has a conditional multi-timeframe swing setup."
        ),
        "executive_evidence_ids": verdict.decisive_evidence_ids,
        "weekly_analysis": "Weekly structure defines the primary regime.",
        "weekly_analysis_evidence_ids": (
            package.weekly.evidence[0].qualified_evidence_id,
        ),
        "daily_analysis": "Daily evidence defines tactical timing.",
        "daily_analysis_evidence_ids": (
            package.daily.evidence[0].qualified_evidence_id,
        ),
        "judge_conclusion_explanation": (
            "The Judge weighed weekly structure against daily timing."
        ),
        "judge_evidence_ids": verdict.decisive_evidence_ids,
        "bull_case": {
            "summary": "The Bull case follows its cited evidence.",
            "argument_ids": tuple(
                round_.bull_argument.argument_id
                for round_ in transcript.rounds
            ),
            "evidence_ids": tuple(
                dict.fromkeys(
                    citation
                    for round_ in transcript.rounds
                    for citation in round_.bull_argument.evidence_citations
                )
            ),
        },
        "bear_case": {
            "summary": "The Bear case follows its cited evidence.",
            "argument_ids": tuple(
                round_.bear_argument.argument_id
                for round_ in transcript.rounds
            ),
            "evidence_ids": tuple(
                dict.fromkeys(
                    citation
                    for round_ in transcript.rounds
                    for citation in round_.bear_argument.evidence_citations
                )
            ),
        },
    }


class JarvisPresentationAgentTests(unittest.TestCase):
    def test_persona_requires_beginner_friendly_structured_briefing(self):
        for prompt in (
            JARVIS_PERSONA_SYSTEM_PROMPT,
            JARVIS_MULTI_TIMEFRAME_PERSONA_SYSTEM_PROMPT,
        ):
            self.assertIn("beginner", prompt)
            self.assertIn("three to five sentences", prompt)
            self.assertIn("not a probability", prompt)
            self.assertIn("Do not write Markdown headings", prompt)

        self.assertEqual(
            JarvisPresentationAgent(_FakeGateway([])).config.prompt_version,
            "jarvis.chief_investment_research_assistant_prompt.v2",
        )

    def test_persona_explains_complete_result_without_changing_evidence(self):
        result = _result()
        gateway = _FakeGateway([_valid_payload(result)])

        explanation = JarvisPresentationAgent(gateway).explain(
            result,
            user_name="Prateek",
        )

        profile = result.technical_result.submission.profile
        verdict = result.debate_result.submission.verdict
        self.assertEqual(explanation.judge_winner, verdict.winner)
        self.assertEqual(explanation.technical_score, profile.score)
        self.assertEqual(
            {item.evidence_id for item in explanation.technical_findings},
            {item.evidence_id for item in profile.snapshot.evidence},
        )
        self.assertIn("Chief Investment Research Assistant", gateway.calls[0]["system"])
        self.assertIn("Prateek", gateway.calls[0]["messages"][0]["content"])
        self.assertIn("not guaranteed investment advice", explanation.disclaimer)

    def test_retries_then_rejects_hallucinated_argument_reference(self):
        result = _result()
        invalid = _valid_payload(result)
        invalid["bull_case"] = invalid["bull_case"] | {
            "argument_ids": ("invented-argument",)
        }
        gateway = _FakeGateway([invalid, invalid])

        with self.assertRaisesRegex(
            LLMResponseValidationError,
            "altered or omitted",
        ):
            JarvisPresentationAgent(gateway).explain(
                result,
                user_name="Prateek",
            )

        self.assertEqual(len(gateway.calls), 2)
        self.assertIn(
            "bull_case",
            gateway.calls[1]["messages"][0]["content"],
        )
    def test_rejects_missing_or_hallucinated_technical_evidence(self):
        result = _result()
        invalid = _valid_payload(result)
        invalid["technical_findings"] = invalid["technical_findings"][:-1]
        gateway = _FakeGateway([invalid, invalid])

        with self.assertRaises(LLMResponseValidationError):
            JarvisPresentationAgent(gateway).explain(
                result,
                user_name="Prateek",
            )

    def test_persona_system_prompt_and_response_are_audited_as_jarvis(self):
        result = _result()
        sink = InMemoryPromptAuditSink()
        gateway = PromptAuditedLLMGateway(
            _FakeGateway([_valid_payload(result)]),
            LLMRole.JARVIS,
            sink,
        )

        JarvisPresentationAgent(gateway).explain(
            result,
            user_name="Prateek",
        )

        self.assertEqual(
            [record["event_type"] for record in sink.records],
            ["llm_request", "llm_response"],
        )
        self.assertTrue(all(record["actor"] == "jarvis" for record in sink.records))
        self.assertEqual(
            sink.records[0]["payload"]["system_prompt"],
            JARVIS_PERSONA_SYSTEM_PROMPT,
        )

    def test_multi_timeframe_briefing_exposes_exact_long_two_r_plan(self):
        result = _multi_use_case(
            timeframes=_trending_timeframes()
        ).execute("NSE", "2885", "RELIANCE-EQ", "ONE_HOUR")
        gateway = _FakeGateway([_valid_multi_payload(result)])

        explanation = JarvisPresentationAgent(gateway).explain(
            result,
            user_name="Prateek",
        )

        expected = (
            result.trade_plan_result.daily_planning_result
            .approved_trade_intent.evaluation
        )
        self.assertIs(
            explanation.trade_plan.disposition,
            MultiTimeframeTradeDisposition.ACTIONABLE,
        )
        self.assertEqual(
            explanation.trade_plan.reference_entry_price,
            expected.entry_price,
        )
        self.assertEqual(
            explanation.trade_plan.stop_loss_price,
            expected.stop_loss_price,
        )
        self.assertEqual(
            explanation.trade_plan.minimum_target_price,
            expected.minimum_target.target_price,
        )
        self.assertIn("1:2 target", explanation.executive_briefing)
        self.assertIn(
            "latest completed daily close",
            explanation.trade_plan.execution_note,
        )
        self.assertIn("Prateek", gateway.calls[0]["messages"][0]["content"])
        self.assertEqual(
            {item.evidence_id for item in explanation.technical_findings},
            set(
                valid_multi_timeframe_evidence_ids(
                    result.technical_review.evidence_package
                )
            ),
        )

    def test_multi_timeframe_bearish_verdict_is_presented_as_no_trade(self):
        result = _multi_use_case(winner="bearish").execute(
            "NSE", "2885", "RELIANCE-EQ", "ONE_HOUR"
        )
        gateway = _FakeGateway([_valid_multi_payload(result)])

        explanation = JarvisPresentationAgent(gateway).explain(
            result,
            user_name="Prateek",
        )

        self.assertIs(
            explanation.trade_plan.disposition,
            MultiTimeframeTradeDisposition.NO_TRADE,
        )
        self.assertIsNone(explanation.trade_plan.reference_entry_price)
        self.assertIn("Trade plan: NO TRADE", explanation.executive_briefing)

    def test_no_trade_briefing_rejects_hypothetical_numeric_plan(self):
        result = _multi_use_case(winner="bearish").execute(
            "NSE", "2885", "RELIANCE-EQ", "ONE_HOUR"
        )
        invalid = _valid_multi_payload(result) | {
            "executive_briefing": (
                "No trade, but use stop below 1298 and target 1345."
            )
        }
        gateway = _FakeGateway([invalid, invalid])

        with self.assertRaisesRegex(
            LLMResponseValidationError,
            "altered or omitted",
        ):
            JarvisPresentationAgent(gateway).explain(
                result,
                user_name="Prateek",
            )


if __name__ == "__main__":
    unittest.main()
