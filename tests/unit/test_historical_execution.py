import unittest
from datetime import timedelta

from pydantic import ValidationError

from app.agents.historical_execution_agent import (
    HistoricalExecutionAgent,
    HistoricalExecutionAgentConfig,
)
from app.analytics.trade_execution import simulate_historical_trade
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticTradePlanningResult,
    JarvisJudgeVerdict,
)
from app.models.execution import (
    ExecutionSimulationConfig,
    ExecutionTargetPolicy,
    HistoricalExecutionOutcome,
    SameCandleExitPolicy,
    SelectedExecutionTarget,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import SignalDirection
from app.orchestration.agent_orchestrator import (
    AgentOrchestrator,
    JarvisHistoricalExecutionJudge,
)
from tests.unit import test_trade_planning_agent as planning_test_support


class HistoricalExecutionTests(unittest.TestCase):
    def setUp(self):
        fixture = planning_test_support.TradePlanningAgentTests(
            methodName="runTest"
        )
        fixture.setUp()
        self.fixture = fixture
        self.prefix = fixture.market()
        self.technical = fixture.technical_result(self.prefix)
        self.orchestrator = AgentOrchestrator()
        self.planning = self.orchestrator.run_trade_planning(
            self.technical,
            self.prefix,
        )
        self.zero_cost_config = ExecutionSimulationConfig(
            initial_capital=100_000.0,
            risk_per_trade_percentage=1.0,
            maximum_position_percentage=100.0,
            slippage_basis_points=0.0,
            commission_basis_points=0.0,
            fixed_fee_per_order=0.0,
            target_policy=ExecutionTargetPolicy.MINIMUM,
        )

    def candle(
        self,
        day,
        *,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
    ):
        return Candle(
            timestamp=self.fixture.base_time + timedelta(days=day),
            open=open,
            high=high,
            low=low,
            close=close,
            volume=2_000 + day,
        )

    def market(self, *future, prefix=None, **overrides):
        prefix = prefix or self.prefix
        values = {
            "exchange": prefix.exchange,
            "symbol_token": prefix.symbol_token,
            "symbol": prefix.symbol,
            "interval": prefix.interval,
            "source": prefix.source,
            "candles": [*prefix.candles, *future],
            "retrieved_at": self.fixture.base_time + timedelta(days=30),
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def simulate(self, series, *, config=None, planning=None):
        planning = planning or self.planning
        return simulate_historical_trade(
            planning.approved_trade_intent,
            series,
            planned_at=planning.submission.evaluated_at,
            config=config or self.zero_cost_config,
        )

    def test_enters_at_next_open_and_exits_at_two_r_target(self):
        series = self.market(
            self.candle(11, open=100, high=109, low=99, close=108),
        )

        result = self.simulate(series)

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.MINIMUM_TARGET,
        )
        self.assertEqual(result.entry_candle.timestamp, series.candles[-1].timestamp)
        self.assertEqual(result.entry_fill_price, 100)
        self.assertEqual(result.stop_loss_price, 96)
        self.assertEqual(result.target_price, 108)
        self.assertEqual(result.quantity, 250)
        self.assertEqual(result.gross_pnl, 2_000)
        self.assertEqual(result.net_pnl, 2_000)
        self.assertEqual(result.realized_r_multiple, 2)

    def test_preferred_policy_exits_at_three_r_target(self):
        config = self.zero_cost_config.model_copy(
            update={"target_policy": ExecutionTargetPolicy.PREFERRED}
        )
        series = self.market(
            self.candle(11, high=113, low=99, close=112),
        )

        result = self.simulate(series, config=config)

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.PREFERRED_TARGET,
        )
        self.assertIs(
            result.selected_target,
            SelectedExecutionTarget.PREFERRED,
        )
        self.assertEqual(result.target_price, 112)
        self.assertEqual(result.realized_r_multiple, 3)

    def test_short_trade_uses_inverse_stop_and_target_rules(self):
        technical = self.fixture.technical_result(
            self.prefix,
            SignalDirection.BEARISH,
        )
        planning = self.orchestrator.run_trade_planning(
            technical,
            self.prefix,
        )
        series = self.market(
            self.candle(11, open=100, high=101, low=91, close=92),
        )

        result = self.simulate(series, planning=planning)

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.MINIMUM_TARGET,
        )
        self.assertEqual(result.stop_loss_price, 104)
        self.assertEqual(result.target_price, 92)
        self.assertEqual(result.gross_pnl, 2_000)
        self.assertEqual(result.realized_r_multiple, 2)

    def test_planning_candle_range_cannot_trigger_an_exit(self):
        changed_prefix = self.fixture.market(highs={10: 500}, lows={10: 1})
        technical = self.fixture.technical_result(changed_prefix)
        planning = self.orchestrator.run_trade_planning(
            technical,
            changed_prefix,
        )
        series = self.market(
            self.candle(11, high=101, low=99, close=101),
            prefix=changed_prefix,
        )

        result = self.simulate(series, planning=planning)

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.END_OF_DATA,
        )
        self.assertEqual(result.entry_candle.timestamp, self.candle(11).timestamp)

    def test_next_open_beyond_stop_cancels_stale_intent(self):
        series = self.market(
            self.candle(11, open=95, high=97, low=94, close=96),
        )

        result = self.simulate(series)

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.INVALIDATED_AT_ENTRY,
        )
        self.assertEqual(result.entry_order_price, 95)
        self.assertEqual(result.quantity, 0)
        self.assertEqual(result.ending_capital, 100_000)

    def test_later_stop_gap_uses_the_worse_open(self):
        series = self.market(
            self.candle(11),
            self.candle(12, open=90, high=92, low=89, close=91),
        )

        result = self.simulate(series)

        self.assertIs(result.outcome, HistoricalExecutionOutcome.STOP_LOSS)
        self.assertEqual(result.exit_fill_price, 90)
        self.assertEqual(result.gross_pnl, -2_500)
        self.assertEqual(result.realized_r_multiple, -2.5)

    def test_same_candle_uses_conservative_stop_first_by_default(self):
        series = self.market(
            self.candle(11, high=109, low=95, close=100),
        )

        result = self.simulate(series)

        self.assertIs(result.outcome, HistoricalExecutionOutcome.STOP_LOSS)
        self.assertEqual(result.realized_r_multiple, -1)

    def test_same_candle_target_first_policy_is_explicit(self):
        config = self.zero_cost_config.model_copy(
            update={
                "same_candle_exit_policy": (
                    SameCandleExitPolicy.TARGET_FIRST
                )
            }
        )
        series = self.market(
            self.candle(11, high=109, low=95, close=100),
        )

        result = self.simulate(series, config=config)

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.MINIMUM_TARGET,
        )

    def test_slippage_fees_and_risk_sizing_are_applied(self):
        config = ExecutionSimulationConfig(
            initial_capital=100_000.0,
            risk_per_trade_percentage=1.0,
            maximum_position_percentage=100.0,
            slippage_basis_points=10.0,
            commission_basis_points=10.0,
            fixed_fee_per_order=10.0,
            target_policy=ExecutionTargetPolicy.MINIMUM,
        )
        series = self.market(
            self.candle(11, high=109, low=99, close=108),
        )

        result = self.simulate(series, config=config)

        self.assertAlmostEqual(result.entry_fill_price, 100.1)
        self.assertAlmostEqual(result.exit_fill_price, 107.892)
        self.assertEqual(result.quantity, 238)
        self.assertAlmostEqual(
            result.total_costs,
            result.entry_fee + result.exit_fee,
        )
        self.assertLess(result.net_pnl, result.gross_pnl)
        self.assertLess(result.realized_r_multiple, 2)

    def test_insufficient_capital_does_not_create_fractional_position(self):
        config = self.zero_cost_config.model_copy(
            update={
                "initial_capital": 50.0,
                "risk_per_trade_percentage": 100.0,
            }
        )
        series = self.market(self.candle(11))

        result = self.simulate(series, config=config)

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.INSUFFICIENT_CAPITAL,
        )
        self.assertEqual(result.quantity, 0)

    def test_end_of_data_closes_position_or_reports_it_open(self):
        series = self.market(
            self.candle(11, high=103, low=99, close=102),
        )
        closed = self.simulate(series)
        open_config = self.zero_cost_config.model_copy(
            update={"close_open_position_at_end": False}
        )
        still_open = self.simulate(series, config=open_config)

        self.assertIs(
            closed.outcome,
            HistoricalExecutionOutcome.END_OF_DATA,
        )
        self.assertEqual(closed.exit_fill_price, 102)
        self.assertIs(still_open.outcome, HistoricalExecutionOutcome.OPEN)
        self.assertIsNone(still_open.net_pnl)
        self.assertIsNone(still_open.ending_capital)

    def test_no_future_candle_and_no_trade_are_auditable(self):
        no_future = self.simulate(self.prefix)
        neutral = self.fixture.technical_result(
            self.prefix,
            SignalDirection.NEUTRAL,
        )
        no_trade_plan = self.orchestrator.run_trade_planning(
            neutral,
            self.prefix,
        )
        no_trade = simulate_historical_trade(
            no_trade_plan.approved_trade_intent,
            self.prefix,
            planned_at=no_trade_plan.submission.evaluated_at,
            config=self.zero_cost_config,
        )

        self.assertIs(
            no_future.outcome,
            HistoricalExecutionOutcome.NO_FUTURE_CANDLE,
        )
        self.assertIs(
            no_trade.outcome,
            HistoricalExecutionOutcome.NO_TRADE_INTENT,
        )

    def test_best_feasible_falls_back_to_two_r_on_marginal_plan(self):
        prefix = self.fixture.market(highs={1: 109, 3: 109})
        technical = self.fixture.technical_result(prefix)
        planning = self.orchestrator.run_trade_planning(technical, prefix)
        series = self.market(
            self.candle(11, high=109, low=99, close=108),
            prefix=prefix,
        )
        best = ExecutionSimulationConfig(
            initial_capital=100_000.0,
            risk_per_trade_percentage=1.0,
            maximum_position_percentage=100.0,
            slippage_basis_points=0.0,
            commission_basis_points=0.0,
            target_policy=ExecutionTargetPolicy.BEST_FEASIBLE,
        )

        result = self.simulate(series, config=best, planning=planning)

        self.assertIs(
            result.selected_target,
            SelectedExecutionTarget.MINIMUM,
        )
        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.MINIMUM_TARGET,
        )

    def test_explicit_blocked_target_is_not_executed(self):
        prefix = self.fixture.market(highs={1: 109, 3: 109})
        technical = self.fixture.technical_result(prefix)
        planning = self.orchestrator.run_trade_planning(technical, prefix)
        series = self.market(self.candle(11), prefix=prefix)
        preferred = self.zero_cost_config.model_copy(
            update={"target_policy": ExecutionTargetPolicy.PREFERRED}
        )

        result = self.simulate(
            series,
            config=preferred,
            planning=planning,
        )

        self.assertIs(
            result.outcome,
            HistoricalExecutionOutcome.TARGET_BLOCKED,
        )
        self.assertEqual(result.quantity, 0)

    def test_agent_and_jarvis_judge_preserve_the_review_chain(self):
        series = self.market(
            self.candle(11, high=109, low=99, close=108),
        )
        config = HistoricalExecutionAgentConfig(
            simulation=self.zero_cost_config
        )
        agent = HistoricalExecutionAgent(config)
        judge = JarvisHistoricalExecutionJudge(
            expected_agent_id=agent.agent_id,
            expected_config=agent.config,
        )

        submission = agent.execute(self.planning, series)
        decision = judge.review(submission, self.planning, series)

        self.assertTrue(decision.accepted)
        self.assertIn(
            "deterministic_execution_recalculation",
            decision.passed_checks,
        )

    def test_agent_records_approved_no_trade_without_a_fill(self):
        neutral = self.fixture.technical_result(
            self.prefix,
            SignalDirection.NEUTRAL,
        )
        planning = self.orchestrator.run_trade_planning(
            neutral,
            self.prefix,
        )
        series = self.market(self.candle(11))

        result = self.orchestrator.run_historical_execution(
            planning,
            series,
        )

        self.assertTrue(result.decision.accepted)
        self.assertIs(
            result.submission.execution.outcome,
            HistoricalExecutionOutcome.NO_TRADE_INTENT,
        )
        self.assertEqual(result.submission.execution.quantity, 0)

    def test_orchestrator_returns_only_judge_approved_execution(self):
        series = self.market(
            self.candle(11, high=113, low=99, close=112),
        )

        result = self.orchestrator.run_historical_execution(
            self.planning,
            series,
        )
        execution = self.orchestrator.require_approved_historical_execution(
            self.planning,
            series,
        )

        self.assertTrue(result.decision.accepted)
        self.assertIsNotNone(result.approved_execution)
        self.assertEqual(execution, result.submission.execution)

    def test_agent_rejects_changed_prefix_and_unapproved_plan(self):
        changed = self.market(prefix=self.fixture.market(closes={10: 101}))
        rejected_decision = self.planning.decision.model_copy(
            update={
                "verdict": JarvisJudgeVerdict.REJECTED,
                "reasons": ("test rejection",),
            }
        )
        rejected = AgenticTradePlanningResult(
            orchestrator_id=self.planning.orchestrator_id,
            submission=self.planning.submission,
            decision=rejected_decision,
        )
        agent = HistoricalExecutionAgent()

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "planning prefix",
        ):
            agent.execute(self.planning, changed)
        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "Jarvis-approved",
        ):
            agent.execute(rejected, self.prefix)

    def test_judge_rejects_tampered_execution_result(self):
        series = self.market(
            self.candle(11, high=109, low=99, close=108),
        )
        agent = HistoricalExecutionAgent()
        judge = JarvisHistoricalExecutionJudge(
            expected_agent_id=agent.agent_id,
            expected_config=agent.config,
        )
        submission = agent.execute(self.planning, series)
        changed_execution = submission.execution.model_copy(
            update={"rationale": "Tampered but structurally valid."}
        )
        changed_submission = submission.model_copy(
            update={"execution": changed_execution}
        )

        decision = judge.review(
            changed_submission,
            self.planning,
            series,
        )

        self.assertFalse(decision.accepted)
        self.assertIn(
            "execution result does not match deterministic replay",
            decision.reasons,
        )

    def test_judge_rejects_changed_assigned_history_without_crashing(self):
        series = self.market(self.candle(11))
        agent = HistoricalExecutionAgent()
        judge = JarvisHistoricalExecutionJudge(
            expected_agent_id=agent.agent_id,
            expected_config=agent.config,
        )
        submission = agent.execute(self.planning, series)
        changed_prefix = self.fixture.market(closes={10: 101})
        changed_series = self.market(
            self.candle(11),
            prefix=changed_prefix,
        )

        decision = judge.review(
            submission,
            self.planning,
            changed_series,
        )

        self.assertFalse(decision.accepted)
        self.assertIn(
            "execution history changed the approved planning prefix",
            decision.reasons,
        )
        self.assertIn(
            "assigned history cannot be deterministically replayed",
            decision.reasons,
        )

    def test_execution_model_rejects_tampered_performance(self):
        series = self.market(
            self.candle(11, high=109, low=99, close=108),
        )
        result = self.simulate(series)
        values = result.model_dump()
        values["net_pnl"] = result.net_pnl + 1

        with self.assertRaisesRegex(
            ValidationError,
            "net P&L must equal",
        ):
            type(result).model_validate(values)

    def test_config_rejects_invalid_values_and_is_immutable(self):
        config = ExecutionSimulationConfig()
        with self.assertRaises(ValidationError):
            config.initial_capital = 1.0

        invalid = (
            {"initial_capital": 0.0},
            {"risk_per_trade_percentage": 101.0},
            {"maximum_position_percentage": 0.0},
            {"slippage_basis_points": 10_000.0},
            {"commission_basis_points": -1.0},
            {"close_open_position_at_end": 1},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    ExecutionSimulationConfig(**values)


if __name__ == "__main__":
    unittest.main()
