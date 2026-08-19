import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.agents.technical_swing_agent import market_series_fingerprint
from app.analytics.signal_profiles import (
    build_swing_trading_signal_profile,
)
from app.analytics.walk_forward import (
    WalkForwardBacktestEngine,
    run_walk_forward_backtest,
)
from app.exceptions import InsufficientDataError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
    TechnicalSwingAgentSubmission,
)
from app.models.backtest import (
    WalkForwardBacktestConfig,
    WalkForwardEvaluationOutcome,
)
from app.models.execution import (
    ExecutionSimulationConfig,
    ExecutionTargetPolicy,
    HistoricalExecutionOutcome,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.models.trade_setup import TradeDirection


class _ScriptedTechnicalAgent:
    minimum_candles = 3


class _ScriptedOrchestrator:
    def __init__(self, directions=None, failures=()):
        self.technical_agent = _ScriptedTechnicalAgent()
        self.directions = directions or {}
        self.failures = set(failures)
        self.analyzed_indices = []
        self._planning = AgentOrchestrator()

    def run_swing_analysis(self, series):
        index = len(series.candles) - 1
        self.analyzed_indices.append(index)
        if index in self.failures:
            raise InsufficientDataError("Scripted technical failure.")
        direction = self.directions.get(index, SignalDirection.NEUTRAL)
        profile = _profile(series, direction)
        submission = TechnicalSwingAgentSubmission(
            submission_id=f"scripted-technical-{index}",
            agent_id="test.scripted_technical_agent.v1",
            evaluator_id="test.scripted_evaluator.v1",
            input_fingerprint=market_series_fingerprint(series),
            configuration_fingerprint="1" * 64,
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=series.symbol,
            interval=series.interval,
            source=series.source,
            source_retrieved_at=series.retrieved_at,
            evaluated_at=series.candles[-1].timestamp,
            input_candle_count=len(series.candles),
            profile=profile,
        )
        decision = JarvisJudgeDecision(
            decision_id=f"scripted-technical-decision-{index}",
            judge_id="test.scripted_technical_judge.v1",
            submission_id=submission.submission_id,
            verdict=JarvisJudgeVerdict.ACCEPTED,
            decided_at=submission.evaluated_at,
            passed_checks=("scripted_technical_approval",),
        )
        return AgenticSwingAnalysisResult(
            orchestrator_id="test.scripted_orchestrator.v1",
            submission=submission,
            decision=decision,
        )

    def run_trade_planning(self, technical, series):
        return self._planning.run_trade_planning(technical, series)


def _profile(series, direction):
    evaluated_at = series.candles[-1].timestamp
    evidence = []
    for category in SignalCategory:
        source = f"test.{category.value}"
        evidence_direction = direction
        values = {"value": 1.0}
        parameters = {}
        evidence_id = f"{category.value}_signal"
        if category is SignalCategory.VOLATILITY:
            source = "volatility_signals.atr_regime_and_risk_distance"
            evidence_direction = SignalDirection.NEUTRAL
            values = {"atr": 2.0, "close": series.candles[-1].close}
            evidence_id = "atr_volatility.atr14.hlc"
        if category is SignalCategory.PRICE_ACTION:
            source = "price_action_signals.support_resistance_lifecycle"
            parameters = {
                "pivot_left_strength": 1,
                "pivot_right_strength": 1,
                "zone_tolerance_percentage": 0.1,
                "minimum_touches": 2,
            }
        evidence.append(
            TechnicalSignalEvidence(
                evidence_id=evidence_id,
                name=f"{category.value} scripted evidence",
                category=category,
                direction=evidence_direction,
                strength=SignalStrength.STRONG,
                source=source,
                explanation="Deterministic walk-forward test evidence.",
                observed_at=evaluated_at,
                available_at=evaluated_at,
                observed_values=values,
                parameters=parameters,
            )
        )
    snapshot = TechnicalSignalSnapshot(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        source=series.source,
        source_retrieved_at=series.retrieved_at,
        evaluated_at=evaluated_at,
        evidence=evidence,
    )
    return build_swing_trading_signal_profile(snapshot)


class WalkForwardBacktestTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 1, 1, tzinfo=UTC)
        self.execution = ExecutionSimulationConfig(
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
            timestamp=self.base_time + timedelta(days=day),
            open=open,
            high=high,
            low=low,
            close=close,
            volume=1_000 + day,
        )

    def market(self, candles=None, count=11, **overrides):
        if candles is None:
            candles = [self.candle(day) for day in range(count)]
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.base_time + timedelta(days=30),
            "source": "test",
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def config(self, **overrides):
        values = {
            "warmup_candles": 2,
            "execution": self.execution,
        }
        values.update(overrides)
        return WalkForwardBacktestConfig(**values)

    def test_compounds_capital_and_builds_complete_performance_report(self):
        candles = [self.candle(day) for day in range(11)]
        candles[3] = self.candle(3, high=109, low=99)
        candles[4] = self.candle(4, high=101, low=91)
        candles[5] = self.candle(5, high=101, low=95)
        candles[9] = self.candle(9, high=109, low=99)
        directions = {
            2: SignalDirection.BULLISH,
            3: SignalDirection.BEARISH,
            4: SignalDirection.BULLISH,
            6: SignalDirection.BULLISH,
        }
        orchestrator = _ScriptedOrchestrator(directions)

        result = run_walk_forward_backtest(
            self.market(candles=candles),
            orchestrator=orchestrator,
            config=self.config(),
        )

        self.assertEqual(len(result.evaluations), 9)
        self.assertEqual(len(result.equity_curve), 11)
        self.assertEqual(len(result.trades), 4)
        self.assertEqual(
            [trade.capital_after for trade in result.trades],
            [102_000, 104_040, 103_000, 105_056],
        )
        self.assertEqual(
            [trade.outcome for trade in result.trades],
            [
                HistoricalExecutionOutcome.MINIMUM_TARGET,
                HistoricalExecutionOutcome.MINIMUM_TARGET,
                HistoricalExecutionOutcome.STOP_LOSS,
                HistoricalExecutionOutcome.MINIMUM_TARGET,
            ],
        )
        performance = result.performance
        self.assertEqual(performance.final_equity, 105_056)
        self.assertEqual(performance.net_profit, 5_056)
        self.assertEqual(performance.total_return_percentage, 5.056)
        self.assertEqual(performance.entered_trades, 4)
        self.assertEqual(performance.winning_trades, 3)
        self.assertEqual(performance.losing_trades, 1)
        self.assertEqual(performance.win_rate_percentage, 75)
        self.assertAlmostEqual(performance.gross_profit, 6_096)
        self.assertAlmostEqual(performance.gross_loss, 1_040)
        self.assertAlmostEqual(performance.profit_factor, 6_096 / 1_040)
        self.assertEqual(performance.expectancy_per_closed_trade, 1_264)
        self.assertEqual(performance.maximum_consecutive_losses, 1)
        self.assertGreater(performance.maximum_drawdown_amount, 0)
        self.assertEqual(performance.interval, "ONE_DAY")
        self.assertEqual(
            performance.direction_breakdown[
                TradeDirection.LONG
            ].entered_trades,
            3,
        )
        self.assertEqual(
            performance.direction_breakdown[
                TradeDirection.SHORT
            ].entered_trades,
            1,
        )
        self.assertEqual(
            sum(
                segment.entered_trades
                for segment in performance.stance_breakdown.values()
            ),
            4,
        )

    def test_open_position_blocks_overlapping_evaluations(self):
        candles = [self.candle(day) for day in range(8)]
        candles[6] = self.candle(6, high=109, low=99)
        orchestrator = _ScriptedOrchestrator(
            {
                2: SignalDirection.BULLISH,
                3: SignalDirection.BEARISH,
                4: SignalDirection.BEARISH,
                5: SignalDirection.BEARISH,
            }
        )

        result = run_walk_forward_backtest(
            self.market(candles=candles),
            orchestrator=orchestrator,
            config=self.config(),
        )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_index, 3)
        self.assertEqual(result.trades[0].exit_index, 6)
        self.assertEqual(orchestrator.analyzed_indices, [2, 6, 7])
        self.assertEqual(
            [item.outcome for item in result.evaluations[1:4]],
            [WalkForwardEvaluationOutcome.POSITION_ALREADY_OPEN] * 3,
        )
        self.assertEqual(
            result.performance.skipped_open_position_evaluations,
            3,
        )

    def test_every_analysis_receives_only_its_historical_prefix(self):
        orchestrator = _ScriptedOrchestrator()
        result = WalkForwardBacktestEngine(
            orchestrator,
            self.config(evaluation_stride=2),
        ).run(self.market(count=9))

        self.assertEqual(orchestrator.analyzed_indices, [2, 4, 6, 8])
        self.assertEqual(
            [item.candle_index for item in result.evaluations],
            [2, 4, 6, 8],
        )
        self.assertTrue(
            all(
                item.outcome is WalkForwardEvaluationOutcome.NO_TRADE
                for item in result.evaluations
            )
        )

    def test_end_bound_truncates_both_analysis_and_execution(self):
        candles = [self.candle(day) for day in range(9)]
        candles[8] = self.candle(8, high=109, low=99)
        orchestrator = _ScriptedOrchestrator(
            {2: SignalDirection.BULLISH}
        )
        end_at = self.base_time + timedelta(days=6)

        result = run_walk_forward_backtest(
            self.market(candles=candles),
            orchestrator=orchestrator,
            config=self.config(end_at=end_at),
        )

        self.assertEqual(len(result.market_series.candles), 7)
        self.assertEqual(result.trades[0].exit_index, 6)
        self.assertIs(
            result.trades[0].outcome,
            HistoricalExecutionOutcome.END_OF_DATA,
        )
        self.assertTrue(
            all(point.timestamp <= end_at for point in result.equity_curve)
        )

    def test_technical_failure_is_recorded_and_next_date_continues(self):
        orchestrator = _ScriptedOrchestrator(failures={2})

        result = run_walk_forward_backtest(
            self.market(count=5),
            orchestrator=orchestrator,
            config=self.config(),
        )

        self.assertIs(
            result.evaluations[0].outcome,
            WalkForwardEvaluationOutcome.TECHNICAL_FAILURE,
        )
        self.assertIs(
            result.evaluations[1].outcome,
            WalkForwardEvaluationOutcome.NO_TRADE,
        )
        self.assertEqual(result.performance.technical_failures, 1)

    def test_open_end_position_is_marked_to_market_not_realized(self):
        execution = self.execution.model_copy(
            update={"close_open_position_at_end": False}
        )
        candles = [self.candle(day) for day in range(6)]
        candles[5] = self.candle(5, close=103, high=104, low=99)
        orchestrator = _ScriptedOrchestrator(
            {2: SignalDirection.BULLISH}
        )

        result = run_walk_forward_backtest(
            self.market(candles=candles),
            orchestrator=orchestrator,
            config=self.config(execution=execution),
        )

        self.assertEqual(result.performance.open_trades, 1)
        self.assertIs(
            result.trades[0].outcome,
            HistoricalExecutionOutcome.OPEN,
        )
        self.assertIsNone(result.trades[0].net_pnl)
        self.assertEqual(result.performance.final_equity, 100_750)
        self.assertEqual(result.performance.net_profit, 750)

    def test_capital_depletion_stops_new_analysis(self):
        execution = self.execution.model_copy(
            update={"risk_per_trade_percentage": 100.0}
        )
        candles = [self.candle(day) for day in range(7)]
        candles[4] = self.candle(
            4,
            open=300,
            high=301,
            low=299,
            close=300,
        )
        orchestrator = _ScriptedOrchestrator(
            {2: SignalDirection.BEARISH}
        )

        result = run_walk_forward_backtest(
            self.market(candles=candles),
            orchestrator=orchestrator,
            config=self.config(execution=execution),
        )

        self.assertEqual(result.trades[0].capital_after, -100_000)
        self.assertIs(
            result.evaluations[2].outcome,
            WalkForwardEvaluationOutcome.CAPITAL_DEPLETED,
        )
        self.assertEqual(orchestrator.analyzed_indices, [2])
        self.assertEqual(result.performance.final_equity, -100_000)
        self.assertEqual(
            result.performance.maximum_drawdown_percentage,
            200,
        )

    def test_costs_flow_into_trade_and_strategy_metrics(self):
        execution = self.execution.model_copy(
            update={
                "commission_basis_points": 10.0,
                "fixed_fee_per_order": 10.0,
            }
        )
        candles = [self.candle(day) for day in range(5)]
        candles[3] = self.candle(3, high=109, low=99)
        orchestrator = _ScriptedOrchestrator(
            {2: SignalDirection.BULLISH}
        )

        result = run_walk_forward_backtest(
            self.market(candles=candles),
            orchestrator=orchestrator,
            config=self.config(execution=execution),
        )

        trade = result.trades[0]
        self.assertGreater(trade.total_costs, 0)
        self.assertEqual(
            result.performance.total_costs,
            trade.total_costs,
        )
        self.assertLess(trade.net_pnl, trade.gross_pnl)

    def test_config_is_immutable_and_rejects_invalid_values(self):
        config = WalkForwardBacktestConfig()
        with self.assertRaises(ValidationError):
            config.evaluation_stride = 2

        cases = (
            {"warmup_candles": -1},
            {"warmup_candles": True},
            {"evaluation_stride": 0},
            {"evaluation_stride": 1.5},
            {
                "start_at": self.base_time + timedelta(days=2),
                "end_at": self.base_time + timedelta(days=1),
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    WalkForwardBacktestConfig(**values)

    def test_empty_history_produces_empty_zero_performance_report(self):
        result = run_walk_forward_backtest(
            self.market(candles=[], count=0),
            orchestrator=_ScriptedOrchestrator(),
            config=self.config(),
        )

        self.assertEqual(result.evaluations, [])
        self.assertEqual(result.trades, [])
        self.assertEqual(result.equity_curve, [])
        self.assertEqual(result.performance.final_equity, 100_000)
        self.assertEqual(result.performance.entered_trades, 0)

    def test_result_rejects_configuration_fingerprint_tampering(self):
        result = run_walk_forward_backtest(
            self.market(count=4),
            orchestrator=_ScriptedOrchestrator(),
            config=self.config(),
        )
        values = result.model_dump()
        values["configuration_fingerprint"] = "0" * 64

        with self.assertRaisesRegex(
            ValidationError,
            "fingerprint does not match",
        ):
            type(result).model_validate(values)

    def test_rejects_unordered_and_post_retrieval_history(self):
        candles = [self.candle(day) for day in range(3)]
        cases = (
            self.market(candles=[candles[1], candles[0]]),
            self.market(
                candles=candles,
                retrieved_at=self.base_time + timedelta(days=1),
            ),
        )
        patterns = ("unique timestamps", "post-retrieval")
        for series, pattern in zip(cases, patterns):
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ValueError, pattern):
                    run_walk_forward_backtest(
                        series,
                        orchestrator=_ScriptedOrchestrator(),
                        config=self.config(),
                    )

    def test_default_real_agent_pipeline_runs_end_to_end(self):
        candles = []
        for day in range(60):
            close = 100 + day * 0.5
            candles.append(
                self.candle(
                    day,
                    open=close - 0.1,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                )
            )
        config = WalkForwardBacktestConfig(
            evaluation_stride=5,
            execution=self.execution,
        )

        result = run_walk_forward_backtest(
            self.market(
                candles=candles,
                retrieved_at=self.base_time + timedelta(days=70),
            ),
            config=config,
        )

        self.assertEqual(result.resolved_warmup_candles, 49)
        self.assertEqual(
            [item.candle_index for item in result.evaluations],
            [49, 54, 59],
        )
        self.assertEqual(len(result.equity_curve), 60)
        self.assertTrue(result.configuration_fingerprint)


if __name__ == "__main__":
    unittest.main()
