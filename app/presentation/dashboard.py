from collections.abc import Sequence

from app.models.browser_operations import (
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
)
from app.models.dashboard import (
    DashboardActivity,
    DashboardCandle,
    DashboardDebateArgument,
    DashboardDebatePanel,
    DashboardDebateRound,
    DashboardEvidence,
    DashboardMultiTimeframeInterpretation,
    DashboardPivot,
    DashboardPriceZone,
    DashboardQuote,
    DashboardRefreshGap,
    DashboardRefreshProvenance,
    DashboardRewardRiskTarget,
    DashboardRiskRewardInterpretation,
    DashboardSetupStep,
    DashboardSwingSetup,
    DashboardTimeframePanel,
    DashboardTimeframeInterpretation,
    DashboardTradeDecision,
    DashboardTradePlan,
    JarvisDashboardView,
)
from app.models.multi_timeframe_evidence import (
    NearestPriceZoneSummary,
    SwingAnalysisTimeframe,
    TimeframeTechnicalEvidenceContext,
)
from app.models.storage import MultiTimeframeEndToEndSwingAnalysisResult
from app.models.timeframes import MultiTimeframeTechnicalAnalysis
from app.models.technical_setup import TimeframeSwingSetup
from app.models.timeframe_interpretation import (
    MultiTimeframeSwingInterpretation,
    RewardRiskTargetInterpretation,
    TimeframeMarketInterpretation,
)
from app.models.workflow import JarvisWorkflowEvent
from app.presentation.technical_chart import project_technical_chart


class DashboardProjectionError(ValueError):
    """The operation cannot safely be represented by the dashboard schema."""


class JarvisDashboardProjector:
    """Project domain results into a bounded, frontend-specific read model."""

    def __init__(
        self,
        *,
        daily_candle_limit: int = 260,
        weekly_candle_limit: int = 104,
    ) -> None:
        for label, value in (
            ("daily candle limit", daily_candle_limit),
            ("weekly candle limit", weekly_candle_limit),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        self._daily_limit = daily_candle_limit
        self._weekly_limit = weekly_candle_limit

    def project(
        self,
        operation: BrowserOperationSnapshot,
        output: BrowserOperationOutput,
        events: Sequence[JarvisWorkflowEvent] = (),
    ) -> JarvisDashboardView:
        if operation.status is not BrowserOperationStatus.COMPLETED:
            raise DashboardProjectionError(
                "dashboard requires a completed browser operation"
            )
        if output.operation_id != operation.request.operation_id:
            raise DashboardProjectionError("dashboard operation IDs do not match")
        if output.session_id != operation.request.session_id:
            raise DashboardProjectionError("dashboard session IDs do not match")
        if output.kind is not BrowserOperationKind.SWING_ANALYSIS:
            raise DashboardProjectionError(
                "dashboard requires a swing-analysis operation"
            )
        response = output.research_response
        if response is None or not isinstance(
            response.result,
            MultiTimeframeEndToEndSwingAnalysisResult,
        ):
            raise DashboardProjectionError(
                "dashboard requires a multi-timeframe swing-analysis result"
            )
        result = response.result
        package = result.technical_review.released_evidence
        if package is None:
            raise DashboardProjectionError("dashboard evidence is unavailable")
        decisive = set(
            result.debate_result.submission.verdict.decisive_evidence_ids
        )
        technical = package.technical_analysis
        return JarvisDashboardView(
            operation_id=output.operation_id,
            session_id=output.session_id,
            completed_at=output.completed_at,
            exchange=technical.timeframes.hourly.exchange,
            symbol_token=technical.timeframes.hourly.symbol_token,
            symbol=technical.timeframes.hourly.symbol,
            source_retrieved_at=technical.timeframes.hourly.retrieved_at,
            latest_quote=(
                DashboardQuote(**result.latest_quote.model_dump())
                if result.latest_quote is not None
                else None
            ),
            refresh=self._refresh_provenance(result),
            package_fingerprint=package.package_fingerprint,
            daily=self._timeframe_panel(
                SwingAnalysisTimeframe.DAILY,
                package.daily,
                technical,
                decisive,
            ),
            weekly=self._timeframe_panel(
                SwingAnalysisTimeframe.WEEKLY,
                package.weekly,
                technical,
                decisive,
            ),
            debate=self._debate_panel(result),
            trade_plan=self._trade_plan(result),
            interpretation=self._interpretation(result.interpretation),
            activities=tuple(self._activity(event) for event in events),
            presentation=output.research_explanation,
        )

    @staticmethod
    def _refresh_provenance(
        result: MultiTimeframeEndToEndSwingAnalysisResult,
    ) -> DashboardRefreshProvenance:
        fetch = result.fetch
        series = fetch.stored.series
        candles = series.candles
        final_count = len(candles)
        existing_count = final_count - fetch.new_candle_count
        if final_count < 1 or existing_count < 0:
            raise DashboardProjectionError(
                "dashboard refresh counts do not match the stored market series"
            )
        if fetch.reused_existing_dataset:
            mode = "unchanged"
        elif fetch.resumed_from is not None:
            mode = "incremental"
        else:
            mode = "initial"
        return DashboardRefreshProvenance(
            mode=mode,
            dataset_id=fetch.dataset_id,
            adapter_name=fetch.adapter_name,
            source=series.source,
            source_retrieved_at=series.retrieved_at,
            requested_from=fetch.requested_from,
            requested_to=fetch.requested_to,
            resumed_from=fetch.resumed_from,
            checked_at=fetch.checked_at,
            stored_from=candles[0].timestamp,
            stored_to=candles[-1].timestamp,
            existing_candle_count=existing_count,
            final_candle_count=final_count,
            new_candle_count=fetch.new_candle_count,
            corrected_candle_count=fetch.corrected_candle_count,
            deduplicated_fetched_candle_count=(
                fetch.deduplicated_fetched_candle_count
            ),
            chunk_request_count=fetch.chunk_request_count,
            reused_existing_dataset=fetch.reused_existing_dataset,
            intraday_gaps=tuple(
                DashboardRefreshGap(**gap.model_dump())
                for gap in fetch.intraday_gaps
            ),
        )

    def _timeframe_panel(
        self,
        timeframe: SwingAnalysisTimeframe,
        context: TimeframeTechnicalEvidenceContext,
        technical: MultiTimeframeTechnicalAnalysis,
        decisive: set[str],
    ) -> DashboardTimeframePanel:
        if timeframe is SwingAnalysisTimeframe.DAILY:
            submission = technical.daily_submission
            series = technical.timeframes.daily
            candle_limit = self._daily_limit
        else:
            submission = technical.weekly_submission
            series = technical.timeframes.weekly
            candle_limit = self._weekly_limit
        profile = submission.profile
        return DashboardTimeframePanel(
            timeframe=timeframe,
            interval=context.interval,
            agent_id=submission.agent_id,
            evaluated_at=context.evaluated_at,
            current_close=context.current_close,
            stance=profile.stance,
            score=profile.score,
            confidence_percentage=profile.confidence_percentage,
            coverage_percentage=profile.coverage_percentage,
            rationale=profile.rationale,
            source_candle_count=len(series.candles),
            candles=tuple(
                DashboardCandle(**candle.model_dump())
                for candle in series.candles[-candle_limit:]
            ),
            chart=project_technical_chart(
                series,
                context,
                timeframe,
                candle_limit=candle_limit,
                cpr_source_series=(
                    technical.timeframes.daily
                    if timeframe is SwingAnalysisTimeframe.WEEKLY
                    else series
                ),
            ),
            evidence=tuple(
                DashboardEvidence(
                    evidence_id=item.qualified_evidence_id,
                    name=item.evidence.name,
                    category=item.evidence.category,
                    direction=item.evidence.direction,
                    strength=item.evidence.strength,
                    explanation=item.evidence.explanation,
                    observed_at=item.evidence.observed_at,
                    available_at=item.evidence.available_at,
                    observed_values=item.evidence.observed_values,
                    parameters=item.evidence.parameters,
                    decisive=item.qualified_evidence_id in decisive,
                )
                for item in context.evidence
            ),
            pivots=tuple(
                DashboardPivot(
                    pivot_id=item.qualified_pivot_id,
                    pivot_type=item.pivot.pivot_type.value,
                    price=item.pivot.price,
                    pivot_at=item.pivot.pivot_at,
                    confirmed_at=item.pivot.confirmed_at,
                )
                for item in context.recent_confirmed_pivots
            ),
            nearest_support=self._zone(context.nearest_support),
            nearest_resistance=self._zone(context.nearest_resistance),
        )

    @staticmethod
    def _zone(summary: NearestPriceZoneSummary | None) -> DashboardPriceZone | None:
        if summary is None:
            return None
        zone = summary.lifecycle.zone
        return DashboardPriceZone(
            zone_id=summary.qualified_zone_id,
            zone_type=summary.effective_zone_type.value,
            lower_price=zone.lower_price,
            upper_price=zone.upper_price,
            boundary_price=summary.boundary_price,
            distance_percentage=summary.distance_percentage,
            lifecycle_status=summary.lifecycle.status.value,
            confirmed_at=zone.confirmed_at,
        )

    @staticmethod
    def _argument(argument) -> DashboardDebateArgument:
        relationship = argument.timeframe_relationship
        return DashboardDebateArgument(
            argument_id=argument.argument_id,
            thesis=argument.thesis,
            evidence_citations=argument.evidence_citations,
            rebuts_argument_id=argument.rebuts_argument_id,
            model_id=argument.model_id,
            generated_at=argument.generated_at,
            timeframe_relationship=(relationship.value if relationship else None),
        )

    def _debate_panel(
        self,
        result: MultiTimeframeEndToEndSwingAnalysisResult,
    ) -> DashboardDebatePanel:
        submission = result.debate_result.submission
        verdict = submission.verdict
        return DashboardDebatePanel(
            rounds=tuple(
                DashboardDebateRound(
                    round_number=item.round_number,
                    bull=self._argument(item.bull_argument),
                    bear=self._argument(item.bear_argument),
                )
                for item in submission.transcript.rounds
            ),
            termination_reason=submission.transcript.termination_reason.value,
            verdict_id=verdict.verdict_id,
            winner=verdict.winner,
            confidence_percentage=verdict.confidence_percentage,
            decisive_evidence_ids=verdict.decisive_evidence_ids,
            bull_case_summary=verdict.bull_case_summary,
            bear_case_summary=verdict.bear_case_summary,
            judge_rationale=verdict.rationale,
            judge_model_id=verdict.judge_model_id,
            generated_at=verdict.generated_at,
        )

    @staticmethod
    def _trade_plan(
        result: MultiTimeframeEndToEndSwingAnalysisResult,
    ) -> DashboardTradePlan:
        outcome = result.trade_plan_result
        planning = outcome.daily_planning_result
        intent = planning.approved_trade_intent if planning is not None else None
        if intent is None:
            return DashboardTradePlan(
                disposition=outcome.disposition.value,
                reason=outcome.reason.value,
                rationale=outcome.rationale,
            )
        evaluation = intent.evaluation
        return DashboardTradePlan(
            disposition=outcome.disposition.value,
            reason=outcome.reason.value,
            rationale=outcome.rationale,
            direction="long",
            status=evaluation.status.value,
            entry_price=evaluation.entry_price,
            stop_loss_price=evaluation.stop_loss_price,
            risk_per_unit=evaluation.risk_per_unit,
            target_2r_price=evaluation.minimum_target.target_price,
            target_2r_feasibility=evaluation.minimum_target.feasibility.value,
            target_3r_price=evaluation.preferred_target.target_price,
            target_3r_feasibility=evaluation.preferred_target.feasibility.value,
            maximum_structural_reward_to_risk=(
                evaluation.maximum_structural_reward_to_risk
            ),
        )

    @classmethod
    def _interpretation(
        cls,
        interpretation: MultiTimeframeSwingInterpretation | None,
    ) -> DashboardMultiTimeframeInterpretation | None:
        if interpretation is None:
            return None
        risk_reward = interpretation.risk_reward
        decision = interpretation.trade_decision
        return DashboardMultiTimeframeInterpretation(
            daily=cls._timeframe_interpretation(interpretation.daily),
            weekly=cls._timeframe_interpretation(interpretation.weekly),
            alignment=interpretation.alignment,
            tactical_readiness=interpretation.tactical_readiness,
            structural_risk=interpretation.structural_risk,
            risk_reward=DashboardRiskRewardInterpretation(
                reference_entry=risk_reward.reference_entry,
                stop_loss=risk_reward.stop_loss,
                risk_per_unit=risk_reward.risk_per_unit,
                target_2r=cls._reward_risk_target(risk_reward.target_2r),
                target_3r=cls._reward_risk_target(risk_reward.target_3r),
            ),
            trade_decision=DashboardTradeDecision(
                market_condition=decision.market_condition,
                decision=decision.decision,
                no_trade_reasons=decision.no_trade_reasons,
                rationale=decision.rationale,
            ),
            decisive_evidence_ids=interpretation.decisive_evidence_ids,
            decision_change_conditions=(
                interpretation.decision_change_conditions
            ),
            rationale=interpretation.rationale,
            interpreted_at=interpretation.interpreted_at,
        )

    @classmethod
    def _timeframe_interpretation(
        cls,
        interpretation: TimeframeMarketInterpretation,
    ) -> DashboardTimeframeInterpretation:
        return DashboardTimeframeInterpretation(
            timeframe=interpretation.timeframe,
            interval=interpretation.interval,
            evaluated_at=interpretation.evaluated_at,
            market_condition=interpretation.market_condition,
            bullish_setup=cls._swing_setup(interpretation.bullish_setup),
            bearish_setup=cls._swing_setup(interpretation.bearish_setup),
            decisive_evidence_ids=interpretation.decisive_evidence_ids,
            rationale=interpretation.rationale,
        )

    @staticmethod
    def _swing_setup(setup: TimeframeSwingSetup) -> DashboardSwingSetup:
        return DashboardSwingSetup(
            setup_id=setup.setup_id,
            side=setup.side,
            steps=tuple(
                DashboardSetupStep(
                    step_id=step.step_id,
                    label=step.label,
                    sequence=step.sequence,
                    state=step.state,
                    evidence_ids=step.evidence_ids,
                    observed_at=step.observed_at,
                    available_at=step.available_at,
                    confirmed_at=step.confirmed_at,
                    invalidated_at=step.invalidated_at,
                    explanation=step.explanation,
                    observed_values=step.observed_values,
                    thresholds=step.thresholds,
                )
                for step in setup.steps
            ),
        )

    @staticmethod
    def _reward_risk_target(
        target: RewardRiskTargetInterpretation,
    ) -> DashboardRewardRiskTarget:
        return DashboardRewardRiskTarget(
            reward_to_risk=target.reward_to_risk,
            feasibility=target.feasibility,
            target_price=target.target_price,
            blocking_evidence_ids=target.blocking_evidence_ids,
            rationale=target.rationale,
        )

    @staticmethod
    def _activity(event: JarvisWorkflowEvent) -> DashboardActivity:
        activity = event.activity
        return DashboardActivity(
            event_id=event.event_id,
            sequence=event.sequence,
            stage=event.stage,
            state=event.state,
            occurred_at=event.occurred_at,
            message=event.message,
            participant_id=activity.participant_id,
            participant_kind=activity.participant_kind,
            participant_label=activity.participant_label,
            timeframe=activity.timeframe,
            round_number=event.round_number,
        )
