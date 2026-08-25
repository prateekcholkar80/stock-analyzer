from collections.abc import Callable
from hashlib import sha256
from math import isclose

from app.agents.technical_swing_agent import (
    TechnicalSwingAgent,
    market_series_fingerprint,
)
from app.agents.historical_execution_agent import (
    HistoricalExecutionAgent,
    HistoricalExecutionAgentConfig,
)
from app.agents.trade_planning_agent import (
    TradePlanningAgent,
    TradePlanningAgentConfig,
)
from app.analytics.trade_execution import simulate_historical_trade
from app.analytics.accumulation import detect_accumulation_zones
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticHistoricalExecutionResult,
    AgenticSwingAnalysisResult,
    AgenticTradePlanningResult,
    HistoricalExecutionAgentSubmission,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
    TechnicalSwingAgentSubmission,
    TradePlanningAgentSubmission,
)
from app.models.execution import HistoricalTradeExecution
from app.models.market import HistoricalCandleSeries
from app.models.multi_timeframe_evidence import (
    MultiTimeframeEvidencePackage,
    MultiTimeframeEvidenceReview,
)
from app.models.signals import (
    SignalCategory,
    SignalProvenance,
    SwingTradingSignalProfile,
)
from app.models.trade_setup import SwingTradePlan
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.workflow.events import WorkflowEventEmitter


UNIFIED_SWING_EVIDENCE_SOURCES = frozenset(
    {
        "trend_signals.moving_average_alignment",
        "trend_signals.adx_directional_strength",
        "momentum_signals.rsi_mean_reversion",
        "momentum_signals.macd_momentum",
        "momentum_signals.stochastic_zone_crossover",
        "volatility_signals.bollinger_price_and_bandwidth",
        "volatility_signals.atr_regime_and_risk_distance",
        "volume_signals.obv_price_confirmation",
        "candlestick_signals.candlestick_pattern",
        "price_action_signals.fair_value_gap_context",
        "price_action_signals.support_resistance_lifecycle",
        "price_action_signals.market_structure",
    }
)


class JarvisSwingJudge:
    """Validate technical-agent evidence before Jarvis accepts it."""

    judge_id = "jarvis.swing_judge.v1"

    def __init__(
        self,
        *,
        expected_agent_id: str,
        expected_evaluator_id: str,
        expected_configuration_fingerprint: str,
    ) -> None:
        self.expected_agent_id = _validate_identifier(
            "expected agent",
            expected_agent_id,
        )
        self.expected_evaluator_id = _validate_identifier(
            "expected evaluator",
            expected_evaluator_id,
        )
        self.expected_configuration_fingerprint = _validate_fingerprint(
            "expected evaluator configuration",
            expected_configuration_fingerprint,
        )

    def review(
        self,
        submission: TechnicalSwingAgentSubmission,
        market_series: HistoricalCandleSeries,
    ) -> JarvisJudgeDecision:
        """Return an auditable acceptance or rejection verdict."""
        submission, passed_checks, reasons = technical_submission_checks(
            submission,
            market_series,
            expected_agent_id=self.expected_agent_id,
            expected_evaluator_id=self.expected_evaluator_id,
            expected_configuration_fingerprint=(
                self.expected_configuration_fingerprint
            ),
        )

        verdict = (
            JarvisJudgeVerdict.REJECTED
            if reasons
            else JarvisJudgeVerdict.ACCEPTED
        )
        return JarvisJudgeDecision(
            decision_id=(
                f"{self.judge_id}:{submission.submission_id}"
            ),
            judge_id=self.judge_id,
            submission_id=submission.submission_id,
            verdict=verdict,
            decided_at=submission.evaluated_at,
            passed_checks=tuple(passed_checks),
            reasons=tuple(reasons),
        )

    def review_multi_timeframe(
        self,
        evidence_package: MultiTimeframeEvidencePackage,
        assigned_analysis: object,
    ) -> JarvisJudgeDecision:
        """Gate a complete daily/weekly package without interpreting it."""
        from app.models.timeframes import MultiTimeframeTechnicalAnalysis

        if not isinstance(
            evidence_package,
            MultiTimeframeEvidencePackage,
        ):
            raise ValueError(
                "multi-timeframe review requires a validated evidence package"
            )
        if not isinstance(
            assigned_analysis,
            MultiTimeframeTechnicalAnalysis,
        ):
            raise ValueError(
                "multi-timeframe review requires its assigned analysis"
            )
        evidence_package = MultiTimeframeEvidencePackage.model_validate(
            evidence_package.model_dump(exclude_computed_fields=True)
        )
        assigned_analysis = MultiTimeframeTechnicalAnalysis.model_validate(
            assigned_analysis.model_dump(exclude_computed_fields=True)
        )

        passed_checks = ["multi_timeframe_package_schema_valid"]
        reasons: list[str] = []
        if evidence_package.technical_analysis == assigned_analysis:
            passed_checks.append("exact_multi_timeframe_assignment")
        else:
            reasons.append(
                "evidence package does not match the assigned analysis"
            )

        analysis = evidence_package.technical_analysis
        if analysis.execution_mode == "parallel":
            passed_checks.append("parallel_timeframe_execution_complete")
        else:
            reasons.append("daily and weekly execution was not parallel")
        accumulation_verified = True
        for label, submission, receipt, series, accumulation in (
            (
                "daily",
                analysis.daily_submission,
                analysis.daily_validation,
                analysis.timeframes.daily,
                analysis.daily_accumulation,
            ),
            (
                "weekly",
                analysis.weekly_submission,
                analysis.weekly_validation,
                analysis.timeframes.weekly,
                analysis.weekly_accumulation,
            ),
        ):
            _, recomputed_checks, submission_reasons = (
                technical_submission_checks(
                    submission,
                    series,
                    expected_agent_id=submission.agent_id,
                    expected_evaluator_id=submission.evaluator_id,
                    expected_configuration_fingerprint=(
                        submission.configuration_fingerprint
                    ),
                )
            )
            expected_validation_id = (
                f"{receipt.validator_id}:{label}:"
                f"{submission.input_fingerprint}"
            )
            receipt_matches = (
                receipt.accepted
                and receipt.validation_id == expected_validation_id
                and receipt.passed_checks == tuple(recomputed_checks)
            )
            if not submission_reasons and receipt_matches:
                passed_checks.append(
                    f"{label}_technical_submission_approved"
                )
            else:
                reasons.append(
                    f"{label} technical validation receipt is invalid"
                )
            expected_accumulation = detect_accumulation_zones(
                series,
                as_of=submission.evaluated_at,
            )
            if accumulation != expected_accumulation:
                accumulation_verified = False
                reasons.append(
                    f"{label} accumulation evidence does not match "
                    "deterministic recalculation"
                )

        if accumulation_verified:
            passed_checks.append(
                "daily_and_weekly_accumulation_verified"
            )

        daily_ids = {
            item.qualified_evidence_id
            for item in evidence_package.daily.evidence
        }
        weekly_ids = {
            item.qualified_evidence_id
            for item in evidence_package.weekly.evidence
        }
        expected_count = len(UNIFIED_SWING_EVIDENCE_SOURCES)
        if (
            len(daily_ids) == expected_count
            and len(weekly_ids) == expected_count
        ):
            passed_checks.append("complete_daily_and_weekly_evidence")
        else:
            reasons.append(
                "daily and weekly evidence sets must both be complete"
            )
        if daily_ids.isdisjoint(weekly_ids):
            passed_checks.append("timeframe_evidence_ids_disjoint")
        else:
            reasons.append("daily and weekly evidence ids must be disjoint")

        passed_checks.extend(
            (
                "timeframe_lineage_verified",
                "package_fingerprint_verified",
                "point_in_time_structure_verified",
                "evidence_preserved_without_reinterpretation",
            )
        )
        verdict = (
            JarvisJudgeVerdict.REJECTED
            if reasons
            else JarvisJudgeVerdict.ACCEPTED
        )
        decided_at = max(
            evidence_package.daily.evaluated_at,
            evidence_package.weekly.evaluated_at,
        )
        return JarvisJudgeDecision(
            decision_id=(
                f"{self.judge_id}:multi_timeframe:"
                f"{evidence_package.package_fingerprint}"
            ),
            judge_id=self.judge_id,
            submission_id=evidence_package.package_fingerprint,
            verdict=verdict,
            decided_at=decided_at,
            passed_checks=tuple(passed_checks),
            reasons=tuple(reasons),
        )


def technical_submission_checks(
    submission: TechnicalSwingAgentSubmission,
    market_series: HistoricalCandleSeries,
    *,
    expected_agent_id: str,
    expected_evaluator_id: str,
    expected_configuration_fingerprint: str,
) -> tuple[TechnicalSwingAgentSubmission, list[str], list[str]]:
    """Apply technical chain-of-custody checks without forming an opinion."""
    if not isinstance(submission, TechnicalSwingAgentSubmission):
        raise ValueError(
            "technical validation requires a technical agent submission"
        )
    submission = TechnicalSwingAgentSubmission.model_validate(
        submission.model_dump(exclude_computed_fields=True)
    )
    expected_agent = _validate_identifier(
        "expected agent",
        expected_agent_id,
    )
    expected_evaluator = _validate_identifier(
        "expected evaluator",
        expected_evaluator_id,
    )
    expected_fingerprint = _validate_fingerprint(
        "expected evaluator configuration",
        expected_configuration_fingerprint,
    )

    passed_checks = ["submission_schema_valid"]
    reasons: list[str] = []
    if submission.agent_id == expected_agent:
        passed_checks.append("expected_agent_identity")
    else:
        reasons.append("submission came from an unexpected agent")
    if submission.evaluator_id == expected_evaluator:
        passed_checks.append("expected_evaluator_version")
    else:
        reasons.append("submission used an unexpected evaluator version")
    if submission.configuration_fingerprint == expected_fingerprint:
        passed_checks.append("expected_evaluator_configuration")
    else:
        reasons.append(
            "submission used an unexpected evaluator configuration"
        )

    market_identity = (
        market_series.exchange,
        market_series.symbol_token,
        market_series.symbol,
        market_series.interval,
        market_series.source,
        market_series.retrieved_at,
    )
    submission_identity = (
        submission.exchange,
        submission.symbol_token,
        submission.symbol,
        submission.interval,
        submission.source,
        submission.source_retrieved_at,
    )
    if submission_identity == market_identity:
        passed_checks.append("market_source_identity")
    else:
        reasons.append("submission does not match the assigned market source")
    if submission.input_fingerprint == market_series_fingerprint(
        market_series
    ):
        passed_checks.append("exact_market_prefix_fingerprint")
    else:
        reasons.append(
            "submission fingerprint does not match the assigned market prefix"
        )
    if submission.input_candle_count == len(market_series.candles):
        passed_checks.append("input_candle_count")
    else:
        reasons.append(
            "submission candle count does not match the assigned prefix"
        )

    latest_timestamp = (
        market_series.candles[-1].timestamp
        if market_series.candles
        else None
    )
    if submission.evaluated_at == latest_timestamp:
        passed_checks.append("point_in_time_boundary")
    else:
        reasons.append(
            "submission was not evaluated at the assigned prefix boundary"
        )
    evidence = submission.profile.snapshot.evidence
    if all(
        item.provenance is SignalProvenance.DETERMINISTIC
        for item in evidence
    ):
        passed_checks.append("deterministic_evidence")
    else:
        reasons.append(
            "submission contains non-deterministic technical evidence"
        )
    if {item.available_at for item in evidence} == {
        submission.evaluated_at
    }:
        passed_checks.append("synchronized_evidence")
    else:
        reasons.append(
            "submission evidence is not synchronized to evaluation"
        )
    sources = {item.source for item in evidence}
    if (
        sources == UNIFIED_SWING_EVIDENCE_SOURCES
        and len(evidence) == len(UNIFIED_SWING_EVIDENCE_SOURCES)
    ):
        passed_checks.append("required_evidence_sources")
    else:
        reasons.append(
            "submission does not contain the complete unified evidence set"
        )
    if set(submission.profile.covered_categories) == set(SignalCategory):
        passed_checks.append("all_signal_categories")
    else:
        reasons.append(
            "submission does not cover every technical category"
        )
    if isclose(
        submission.profile.coverage_percentage,
        100.0,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        passed_checks.append("full_weighted_coverage")
    else:
        reasons.append("submission does not provide full weighted coverage")
    return submission, passed_checks, reasons


class JarvisTradePlanJudge:
    """Validate trade intent without executing or simulating a trade."""

    judge_id = "jarvis.trade_plan_judge.v1"

    def __init__(
        self,
        *,
        expected_agent_id: str,
        expected_config: TradePlanningAgentConfig,
    ) -> None:
        self.expected_agent_id = _validate_identifier(
            "expected trade-planning agent",
            expected_agent_id,
        )
        if not isinstance(expected_config, TradePlanningAgentConfig):
            raise ValueError(
                "trade-plan judge requires validated planning config"
            )
        self.expected_config = expected_config
        serialized = expected_config.model_dump_json()
        self.expected_configuration_fingerprint = sha256(
            serialized.encode("utf-8")
        ).hexdigest()

    def review(
        self,
        submission: TradePlanningAgentSubmission,
        technical_result: AgenticSwingAnalysisResult,
        market_series: HistoricalCandleSeries,
    ) -> JarvisJudgeDecision:
        if not isinstance(submission, TradePlanningAgentSubmission):
            raise ValueError(
                "Jarvis trade-plan judge requires an agent submission"
            )
        submission = TradePlanningAgentSubmission.model_validate(
            submission.model_dump(exclude_computed_fields=True)
        )
        technical_result = AgenticSwingAnalysisResult.model_validate(
            technical_result.model_dump(exclude_computed_fields=True)
        )
        passed_checks = ["trade_submission_schema_valid"]
        reasons = []

        if submission.agent_id == self.expected_agent_id:
            passed_checks.append("expected_trade_planning_agent")
        else:
            reasons.append(
                "submission came from an unexpected trade-planning agent"
            )
        if submission.planner_id == self.expected_config.planner_id:
            passed_checks.append("expected_trade_planner_version")
        else:
            reasons.append(
                "submission used an unexpected trade-planner version"
            )
        if (
            submission.configuration_fingerprint
            == self.expected_configuration_fingerprint
        ):
            passed_checks.append("expected_trade_planner_configuration")
        else:
            reasons.append(
                "submission used an unexpected trade-planner "
                "configuration"
            )

        if technical_result.decision.accepted:
            passed_checks.append("technical_submission_approved")
        else:
            reasons.append(
                "trade plan is based on an unapproved technical result"
            )
        if (
            submission.technical_submission_id
            == technical_result.submission.submission_id
            and submission.technical_decision_id
            == technical_result.decision.decision_id
        ):
            passed_checks.append("technical_review_chain")
        else:
            reasons.append(
                "trade plan does not reference the assigned technical "
                "review"
            )
        if submission.profile == technical_result.submission.profile:
            passed_checks.append("approved_technical_profile")
        else:
            reasons.append(
                "trade plan does not use the approved technical profile"
            )

        expected_market_fingerprint = market_series_fingerprint(
            market_series
        )
        if (
            submission.market_fingerprint
            == expected_market_fingerprint
            == technical_result.submission.input_fingerprint
        ):
            passed_checks.append("exact_trade_planning_prefix")
        else:
            reasons.append(
                "trade plan does not match the approved market prefix"
            )

        if submission.plan is None:
            passed_checks.append("explicit_no_trade_outcome")
        else:
            plan = submission.plan
            if plan.market_series == market_series:
                passed_checks.append("trade_plan_market_source")
            else:
                reasons.append(
                    "trade plan contains a different market source"
                )
            if _plan_config_matches(plan, self.expected_config):
                passed_checks.append("trade_plan_risk_configuration")
            else:
                reasons.append(
                    "trade plan risk configuration does not match its "
                    "assignment"
                )
            passed_checks.append("trade_plan_model_validated")

        passed_checks.append("trade_disposition_consistent")
        verdict = (
            JarvisJudgeVerdict.REJECTED
            if reasons
            else JarvisJudgeVerdict.ACCEPTED
        )
        return JarvisJudgeDecision(
            decision_id=(
                f"{self.judge_id}:{submission.submission_id}"
            ),
            judge_id=self.judge_id,
            submission_id=submission.submission_id,
            verdict=verdict,
            decided_at=submission.evaluated_at,
            passed_checks=tuple(passed_checks),
            reasons=tuple(reasons),
        )


class JarvisHistoricalExecutionJudge:
    """Recalculate and review deterministic historical execution."""

    judge_id = "jarvis.historical_execution_judge.v1"

    def __init__(
        self,
        *,
        expected_agent_id: str,
        expected_config: HistoricalExecutionAgentConfig,
    ) -> None:
        self.expected_agent_id = _validate_identifier(
            "expected historical execution agent",
            expected_agent_id,
        )
        if not isinstance(expected_config, HistoricalExecutionAgentConfig):
            raise ValueError(
                "execution judge requires validated agent config"
            )
        self.expected_config = (
            HistoricalExecutionAgentConfig.model_validate(
                expected_config.model_dump()
            )
        )
        self.expected_configuration_fingerprint = sha256(
            self.expected_config.model_dump_json().encode("utf-8")
        ).hexdigest()

    def review(
        self,
        submission: HistoricalExecutionAgentSubmission,
        planning_result: AgenticTradePlanningResult,
        market_series: HistoricalCandleSeries,
    ) -> JarvisJudgeDecision:
        if not isinstance(submission, HistoricalExecutionAgentSubmission):
            raise ValueError(
                "Jarvis execution judge requires an agent submission"
            )
        submission = HistoricalExecutionAgentSubmission.model_validate(
            submission.model_dump(exclude_computed_fields=True)
        )
        planning_result = AgenticTradePlanningResult.model_validate(
            planning_result.model_dump(exclude_computed_fields=True)
        )
        market_series = HistoricalCandleSeries.model_validate(
            market_series.model_dump()
        )
        passed_checks = ["execution_submission_schema_valid"]
        reasons = []

        if submission.agent_id == self.expected_agent_id:
            passed_checks.append("expected_execution_agent")
        else:
            reasons.append(
                "submission came from an unexpected execution agent"
            )
        if (
            submission.execution_engine_id
            == self.expected_config.execution_engine_id
        ):
            passed_checks.append("expected_execution_engine")
        else:
            reasons.append(
                "submission used an unexpected execution engine"
            )
        if submission.configuration_fingerprint == (
            self.expected_configuration_fingerprint
        ):
            passed_checks.append("expected_execution_configuration")
        else:
            reasons.append(
                "submission used an unexpected execution configuration"
            )

        if planning_result.decision.accepted:
            passed_checks.append("trade_plan_approved")
        else:
            reasons.append(
                "historical execution used an unapproved trade plan"
            )
        if (
            submission.planning_submission_id
            == planning_result.submission.submission_id
            and submission.planning_decision_id
            == planning_result.decision.decision_id
        ):
            passed_checks.append("trade_planning_review_chain")
        else:
            reasons.append(
                "execution does not reference the assigned trade plan"
            )

        market_fingerprint = market_series_fingerprint(market_series)
        if submission.market_fingerprint == market_fingerprint:
            passed_checks.append("exact_execution_history")
        else:
            reasons.append(
                "execution submission does not match assigned history"
            )
        expected_prefix = _planning_prefix_fingerprint(
            planning_result,
            market_series,
        )
        if expected_prefix == planning_result.submission.market_fingerprint:
            passed_checks.append("exact_planning_prefix_preserved")
        else:
            reasons.append(
                "execution history changed the approved planning prefix"
            )

        try:
            expected_execution = simulate_historical_trade(
                planning_result.approved_trade_intent,
                market_series,
                planned_at=(
                    planning_result.submission.profile.snapshot.evaluated_at
                ),
                config=self.expected_config.simulation,
            )
        except ValueError:
            reasons.append(
                "assigned history cannot be deterministically replayed"
            )
        else:
            if submission.execution == expected_execution:
                passed_checks.append(
                    "deterministic_execution_recalculation"
                )
            else:
                reasons.append(
                    "execution result does not match deterministic replay"
                )

        verdict = (
            JarvisJudgeVerdict.REJECTED
            if reasons
            else JarvisJudgeVerdict.ACCEPTED
        )
        return JarvisJudgeDecision(
            decision_id=f"{self.judge_id}:{submission.submission_id}",
            judge_id=self.judge_id,
            submission_id=submission.submission_id,
            verdict=verdict,
            decided_at=submission.simulated_at,
            passed_checks=tuple(passed_checks),
            reasons=tuple(reasons),
        )


class AgentOrchestrator:
    """Assign swing analysis to an agent and enforce Jarvis review."""

    orchestrator_id = "jarvis.agent_orchestrator.v1"

    def __init__(
        self,
        technical_agent: TechnicalSwingAgent | None = None,
        judge: JarvisSwingJudge | None = None,
        trade_planning_agent: TradePlanningAgent | None = None,
        trade_plan_judge: JarvisTradePlanJudge | None = None,
        execution_agent: HistoricalExecutionAgent | None = None,
        execution_judge: JarvisHistoricalExecutionJudge | None = None,
    ) -> None:
        self.technical_agent = technical_agent or TechnicalSwingAgent()
        _validate_agent(self.technical_agent)
        self.judge = judge or JarvisSwingJudge(
            expected_agent_id=self.technical_agent.agent_id,
            expected_evaluator_id=self.technical_agent.evaluator_id,
            expected_configuration_fingerprint=(
                self.technical_agent.configuration_fingerprint
            ),
        )
        if not callable(getattr(self.judge, "review", None)):
            raise ValueError(
                "agent orchestrator judge must provide review()"
            )
        self.trade_planning_agent = (
            trade_planning_agent or TradePlanningAgent()
        )
        _validate_trade_planning_agent(self.trade_planning_agent)
        self.trade_plan_judge = (
            trade_plan_judge
            or JarvisTradePlanJudge(
                expected_agent_id=self.trade_planning_agent.agent_id,
                expected_config=self.trade_planning_agent.config,
            )
        )
        if not callable(
            getattr(self.trade_plan_judge, "review", None)
        ):
            raise ValueError(
                "agent orchestrator trade-plan judge must provide "
                "review()"
            )
        self.execution_agent = (
            execution_agent or HistoricalExecutionAgent()
        )
        _validate_execution_agent(self.execution_agent)
        self.execution_judge = (
            execution_judge
            or JarvisHistoricalExecutionJudge(
                expected_agent_id=self.execution_agent.agent_id,
                expected_config=self.execution_agent.config,
            )
        )
        if not callable(getattr(self.execution_judge, "review", None)):
            raise ValueError(
                "agent orchestrator execution judge must provide review()"
            )

    def run_swing_analysis(
        self,
        market_series: HistoricalCandleSeries,
    ) -> AgenticSwingAnalysisResult:
        submission = self.technical_agent.execute(market_series)
        decision = self.judge.review(submission, market_series)
        return AgenticSwingAnalysisResult(
            orchestrator_id=self.orchestrator_id,
            submission=submission,
            decision=decision,
        )

    def run_multi_timeframe_analysis(
        self,
        timeframes: object,
        *,
        timeframe_orchestrator: object | None = None,
        evidence_builder: object | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> MultiTimeframeEvidenceReview:
        """Wait for both technical agents and ask the same Judge to release."""
        from app.models.timeframes import SwingTimeframeSeries
        from app.orchestration.timeframe_technical_orchestrator import (
            ParallelTimeframeTechnicalOrchestrator,
        )
        from app.use_cases.build_multi_timeframe_evidence import (
            BuildMultiTimeframeEvidence,
        )

        if not isinstance(timeframes, SwingTimeframeSeries):
            raise ValueError(
                "multi-timeframe analysis requires swing timeframe series"
            )
        if event_emitter is not None and not isinstance(
            event_emitter,
            WorkflowEventEmitter,
        ):
            raise ValueError("multi-timeframe analysis requires a workflow emitter")
        technical_runner = (
            timeframe_orchestrator
            or ParallelTimeframeTechnicalOrchestrator()
        )
        if not callable(getattr(technical_runner, "execute", None)):
            raise ValueError(
                "multi-timeframe technical orchestrator must provide execute()"
            )
        builder = evidence_builder or BuildMultiTimeframeEvidence()
        if not callable(getattr(builder, "execute", None)):
            raise ValueError(
                "multi-timeframe evidence builder must provide execute()"
            )
        review = getattr(self.judge, "review_multi_timeframe", None)
        if not callable(review):
            raise ValueError(
                "existing Jarvis Judge must provide review_multi_timeframe()"
            )

        if event_emitter is None:
            analysis = technical_runner.execute(timeframes)
        else:
            analysis = technical_runner.execute(
                timeframes,
                event_emitter=event_emitter,
            )
        identity = timeframes.hourly
        event_common = {
            "exchange": identity.exchange,
            "symbol": identity.symbol,
        }
        if event_emitter is not None:
            event_emitter.emit(
                WorkflowStage.EVIDENCE_REVIEW,
                WorkflowEventState.STARTED,
                **event_common,
            )
        try:
            evidence_package = builder.execute(analysis)
            decision = review(evidence_package, analysis)
        except Exception:
            if event_emitter is not None:
                event_emitter.emit(
                    WorkflowStage.EVIDENCE_REVIEW,
                    WorkflowEventState.FAILED,
                    **event_common,
                )
            raise
        if event_emitter is not None:
            event_emitter.emit(
                WorkflowStage.EVIDENCE_REVIEW,
                (
                    WorkflowEventState.COMPLETED
                    if decision.accepted
                    else WorkflowEventState.FAILED
                ),
                **event_common,
            )
        return MultiTimeframeEvidenceReview(
            orchestrator_id=self.orchestrator_id,
            evidence_package=evidence_package,
            decision=decision,
        )

    def require_released_multi_timeframe_evidence(
        self,
        timeframes: object,
        *,
        timeframe_orchestrator: object | None = None,
        evidence_builder: object | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> MultiTimeframeEvidencePackage:
        """Return debate input only when both submissions pass Judge review."""
        result = self.run_multi_timeframe_analysis(
            timeframes,
            timeframe_orchestrator=timeframe_orchestrator,
            evidence_builder=evidence_builder,
            event_emitter=event_emitter,
        )
        if result.released_evidence is None:
            reasons = "; ".join(result.decision.reasons)
            raise AgentSubmissionRejectedError(
                "Jarvis rejected multi-timeframe evidence: " + reasons
            )
        return result.released_evidence

    def evaluate_swing_prefix(
        self,
        market_series: HistoricalCandleSeries,
    ) -> SwingTradingSignalProfile:
        """Return only a judge-approved profile to downstream engines."""
        result = self.run_swing_analysis(market_series)
        if not result.decision.accepted:
            reasons = "; ".join(result.decision.reasons)
            raise AgentSubmissionRejectedError(
                f"Jarvis rejected technical agent submission: {reasons}"
            )
        return result.submission.profile

    def run_trade_planning(
        self,
        technical_result: AgenticSwingAnalysisResult,
        market_series: HistoricalCandleSeries,
    ) -> AgenticTradePlanningResult:
        submission = self.trade_planning_agent.execute(
            technical_result,
            market_series,
        )
        decision = self.trade_plan_judge.review(
            submission,
            technical_result,
            market_series,
        )
        return AgenticTradePlanningResult(
            orchestrator_id=self.orchestrator_id,
            submission=submission,
            decision=decision,
        )

    def require_approved_trade_intent(
        self,
        technical_result: AgenticSwingAnalysisResult,
        market_series: HistoricalCandleSeries,
    ) -> SwingTradePlan | None:
        result = self.run_trade_planning(
            technical_result,
            market_series,
        )
        if not result.decision.accepted:
            reasons = "; ".join(result.decision.reasons)
            raise AgentSubmissionRejectedError(
                f"Jarvis rejected trade-plan submission: {reasons}"
            )
        return result.approved_trade_intent

    def run_historical_execution(
        self,
        planning_result: AgenticTradePlanningResult,
        market_series: HistoricalCandleSeries,
    ) -> AgenticHistoricalExecutionResult:
        submission = self.execution_agent.execute(
            planning_result,
            market_series,
        )
        decision = self.execution_judge.review(
            submission,
            planning_result,
            market_series,
        )
        return AgenticHistoricalExecutionResult(
            orchestrator_id=self.orchestrator_id,
            submission=submission,
            decision=decision,
        )

    def require_approved_historical_execution(
        self,
        planning_result: AgenticTradePlanningResult,
        market_series: HistoricalCandleSeries,
    ) -> HistoricalTradeExecution:
        result = self.run_historical_execution(
            planning_result,
            market_series,
        )
        if not result.decision.accepted:
            reasons = "; ".join(result.decision.reasons)
            raise AgentSubmissionRejectedError(
                f"Jarvis rejected execution submission: {reasons}"
            )
        return result.submission.execution


def _validate_agent(agent) -> None:
    execute: Callable | None = getattr(agent, "execute", None)
    if not callable(execute):
        raise ValueError(
            "agent orchestrator requires an agent with execute()"
        )
    _validate_identifier("technical agent", getattr(agent, "agent_id", None))
    _validate_identifier(
        "technical evaluator",
        getattr(agent, "evaluator_id", None),
    )
    _validate_fingerprint(
        "technical evaluator configuration",
        getattr(agent, "configuration_fingerprint", None),
    )


def _validate_trade_planning_agent(agent) -> None:
    execute: Callable | None = getattr(agent, "execute", None)
    if not callable(execute):
        raise ValueError(
            "agent orchestrator requires a trade agent with execute()"
        )
    _validate_identifier(
        "trade-planning agent",
        getattr(agent, "agent_id", None),
    )
    _validate_identifier(
        "trade planner",
        getattr(agent, "planner_id", None),
    )
    _validate_fingerprint(
        "trade-planning configuration",
        getattr(agent, "configuration_fingerprint", None),
    )
    if not isinstance(
        getattr(agent, "config", None),
        TradePlanningAgentConfig,
    ):
        raise ValueError(
            "trade-planning agent must expose validated config"
        )


def _validate_execution_agent(agent) -> None:
    if not callable(getattr(agent, "execute", None)):
        raise ValueError(
            "agent orchestrator requires an execution agent with execute()"
        )
    _validate_identifier(
        "historical execution agent",
        getattr(agent, "agent_id", None),
    )
    _validate_identifier(
        "historical execution engine",
        getattr(agent, "execution_engine_id", None),
    )
    _validate_fingerprint(
        "historical execution configuration",
        getattr(agent, "configuration_fingerprint", None),
    )
    if not isinstance(
        getattr(agent, "config", None),
        HistoricalExecutionAgentConfig,
    ):
        raise ValueError(
            "historical execution agent must expose validated config"
        )


def _planning_prefix_fingerprint(
    planning_result: AgenticTradePlanningResult,
    market_series: HistoricalCandleSeries,
) -> str:
    snapshot = planning_result.submission.profile.snapshot
    prefix = HistoricalCandleSeries(
        exchange=market_series.exchange,
        symbol_token=market_series.symbol_token,
        symbol=market_series.symbol,
        interval=market_series.interval,
        candles=[
            candle
            for candle in market_series.candles
            if candle.timestamp <= snapshot.evaluated_at
        ],
        retrieved_at=snapshot.source_retrieved_at,
        source=market_series.source,
    )
    return market_series_fingerprint(prefix)


def _plan_config_matches(plan, config: TradePlanningAgentConfig) -> bool:
    return all(
        (
            isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)
            for actual, expected in (
                (
                    plan.structural_buffer_atr_multiplier,
                    config.structural_buffer_atr_multiplier,
                ),
                (
                    plan.minimum_buffer_percentage,
                    config.minimum_buffer_percentage,
                ),
                (
                    plan.fallback_stop_atr_multiplier,
                    config.fallback_stop_atr_multiplier,
                ),
                (
                    plan.evaluation.minimum_reward_to_risk,
                    config.minimum_reward_to_risk,
                ),
                (
                    plan.evaluation.preferred_reward_to_risk,
                    config.preferred_reward_to_risk,
                ),
            )
        )
    )


def _validate_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} identifier cannot be blank")
    return value


def _validate_fingerprint(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} fingerprint must be a SHA-256 digest")
    return value
