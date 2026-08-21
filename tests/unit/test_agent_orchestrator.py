import unittest
from datetime import UTC, datetime, timedelta
from math import sin

from pydantic import ValidationError

from app.agents.technical_swing_agent import TechnicalSwingAgent
from app.analytics.signal_profiles import (
    build_swing_trading_signal_profile,
)
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    JarvisJudgeVerdict,
    TechnicalSwingAgentSubmission,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import TechnicalSignalSnapshot
from app.orchestration.agent_orchestrator import (
    AgentOrchestrator,
    JarvisSwingJudge,
    UNIFIED_SWING_EVIDENCE_SOURCES,
)


class AgentOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 1, 1, tzinfo=UTC)
        self.retrieved_at = self.base_time + timedelta(days=100)

    def market(self, count=60, **overrides):
        candles = []
        for index in range(count):
            close = 100 + index * 0.3 + sin(index / 2)
            candles.append(
                Candle(
                    timestamp=self.base_time + timedelta(days=index),
                    open=close - 0.2,
                    high=close + 1.0,
                    low=close - 1.0,
                    close=close,
                    volume=100_000 + index * 1_000,
                )
            )
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.retrieved_at,
            "source": "test_market",
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def agent_and_judge(self):
        agent = TechnicalSwingAgent()
        judge = JarvisSwingJudge(
            expected_agent_id=agent.agent_id,
            expected_evaluator_id=agent.evaluator_id,
            expected_configuration_fingerprint=(
                agent.configuration_fingerprint
            ),
        )
        return agent, judge

    def test_technical_agent_owns_execution_and_builds_submission(self):
        series = self.market()
        agent = TechnicalSwingAgent()

        submission = agent.execute(series)

        self.assertEqual(
            submission.agent_id,
            "jarvis.technical_swing_agent.v1",
        )
        self.assertEqual(
            submission.evaluator_id,
            "jarvis.unified_swing.v1",
        )
        self.assertEqual(submission.input_candle_count, 60)
        self.assertEqual(
            submission.evaluated_at,
            series.candles[-1].timestamp,
        )
        self.assertEqual(submission.evidence_count, 12)
        self.assertEqual(
            {item.source for item in submission.profile.snapshot.evidence},
            UNIFIED_SWING_EVIDENCE_SOURCES,
        )

    def test_jarvis_judge_accepts_complete_agent_submission(self):
        series = self.market()
        agent, judge = self.agent_and_judge()
        submission = agent.execute(series)

        decision = judge.review(submission, series)

        self.assertIs(decision.verdict, JarvisJudgeVerdict.ACCEPTED)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reasons, ())
        self.assertEqual(
            set(decision.passed_checks),
            {
                "submission_schema_valid",
                "expected_agent_identity",
                "expected_evaluator_version",
                "expected_evaluator_configuration",
                "market_source_identity",
                "exact_market_prefix_fingerprint",
                "input_candle_count",
                "point_in_time_boundary",
                "deterministic_evidence",
                "synchronized_evidence",
                "required_evidence_sources",
                "all_signal_categories",
                "full_weighted_coverage",
            },
        )

    def test_orchestrator_returns_the_review_chain(self):
        series = self.market()
        orchestrator = AgentOrchestrator()

        result = orchestrator.run_swing_analysis(series)

        self.assertEqual(
            result.orchestrator_id,
            "jarvis.agent_orchestrator.v1",
        )
        self.assertTrue(result.decision.accepted)
        self.assertIs(result.accepted_profile, result.submission.profile)
        self.assertEqual(
            result.decision.submission_id,
            result.submission.submission_id,
        )

    def test_orchestrator_releases_only_judge_approved_profile(self):
        series = self.market()
        orchestrator = AgentOrchestrator()

        profile = orchestrator.evaluate_swing_prefix(series)

        self.assertEqual(
            profile.snapshot.evaluated_at,
            series.candles[-1].timestamp,
        )
        self.assertEqual(len(profile.snapshot.evidence), 12)

    def test_judge_rejects_submission_for_another_assignment(self):
        series = self.market()
        assigned_series = series.model_copy(
            update={"symbol": "TCS-EQ", "symbol_token": "11536"}
        )
        agent, judge = self.agent_and_judge()
        submission = agent.execute(series)

        decision = judge.review(submission, assigned_series)

        self.assertIs(decision.verdict, JarvisJudgeVerdict.REJECTED)
        self.assertIn(
            "submission does not match the assigned market source",
            decision.reasons,
        )
        self.assertFalse(decision.accepted)

    def test_judge_rejects_incomplete_unified_evidence(self):
        series = self.market()
        agent, judge = self.agent_and_judge()
        submission = agent.execute(series)
        original = submission.profile
        reduced_snapshot = TechnicalSignalSnapshot(
            **{
                **original.snapshot.model_dump(
                    exclude_computed_fields=True
                ),
                "evidence": original.snapshot.evidence[1:],
            }
        )
        reduced_profile = build_swing_trading_signal_profile(
            reduced_snapshot,
            category_weights=original.category_weights,
            minimum_coverage_percentage=(
                original.minimum_coverage_percentage
            ),
            directional_threshold=original.directional_threshold,
            strong_threshold=original.strong_threshold,
        )
        reduced_submission = TechnicalSwingAgentSubmission(
            **{
                **submission.model_dump(exclude_computed_fields=True),
                "profile": reduced_profile,
            }
        )

        decision = judge.review(reduced_submission, series)

        self.assertIs(decision.verdict, JarvisJudgeVerdict.REJECTED)
        self.assertIn(
            "submission does not contain the complete unified evidence "
            "set",
            decision.reasons,
        )

    def test_judge_rejects_an_altered_market_prefix(self):
        series = self.market()
        agent, judge = self.agent_and_judge()
        submission = agent.execute(series)
        altered_candles = list(series.candles)
        candle = altered_candles[10]
        altered_candles[10] = candle.model_copy(
            update={
                "open": candle.open + 0.1,
                "high": candle.high + 0.1,
                "low": candle.low + 0.1,
                "close": candle.close + 0.1,
            }
        )
        altered_series = series.model_copy(
            update={"candles": altered_candles}
        )

        decision = judge.review(submission, altered_series)

        self.assertFalse(decision.accepted)
        self.assertIn(
            "submission fingerprint does not match the assigned market "
            "prefix",
            decision.reasons,
        )

    def test_orchestrator_blocks_a_rejected_submission(self):
        series = self.market()
        agent = TechnicalSwingAgent()
        rejecting_judge = JarvisSwingJudge(
            expected_agent_id=agent.agent_id,
            expected_evaluator_id="jarvis.unexpected_evaluator.v1",
            expected_configuration_fingerprint=(
                agent.configuration_fingerprint
            ),
        )
        orchestrator = AgentOrchestrator(agent, rejecting_judge)

        result = orchestrator.run_swing_analysis(series)
        self.assertFalse(result.decision.accepted)
        self.assertIsNone(result.accepted_profile)

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "unexpected evaluator version",
        ):
            orchestrator.evaluate_swing_prefix(series)

    def test_submission_rejects_profile_identity_tampering(self):
        submission = TechnicalSwingAgent().execute(self.market())
        values = submission.model_dump(exclude_computed_fields=True)
        values["symbol"] = "TCS-EQ"

        with self.assertRaisesRegex(
            ValidationError,
            "profile must match",
        ):
            TechnicalSwingAgentSubmission.model_validate(values)

    def test_orchestrator_rejects_invalid_agent_or_judge_contract(self):
        class InvalidAgent:
            agent_id = "invalid.agent"
            evaluator_id = "invalid.evaluator"
            configuration_fingerprint = "0" * 64

        class InvalidJudge:
            pass

        with self.assertRaisesRegex(ValueError, "execute"):
            AgentOrchestrator(InvalidAgent())
        with self.assertRaisesRegex(ValueError, "review"):
            AgentOrchestrator(TechnicalSwingAgent(), InvalidJudge())


if __name__ == "__main__":
    unittest.main()
