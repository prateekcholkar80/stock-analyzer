import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.browser_operations import (
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
)
from app.models.conversation import InputChannel
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.market_refresh import IntradayCandleGap
from app.models.multi_timeframe_trade import MultiTimeframeTradeReason
from app.models.storage import RollingFetchReceipt
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
    TradeDecisionOutcome,
)
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.presentation.dashboard import (
    DashboardProjectionError,
    JarvisDashboardProjector,
)
from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
from tests.unit.test_http_api import _serializable_result
from tests.unit.test_jarvis_conversation import RecordingPresenter
from tests.unit.test_run_end_to_end_multi_timeframe_swing_analysis import (
    _trending_timeframes,
    _use_case,
)


IST = ZoneInfo("Asia/Kolkata")


class JarvisDashboardProjectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = _serializable_result()
        cls.at = datetime(2026, 8, 23, 12, 0, tzinfo=IST)
        cls.request = BrowserOperationRequest(
            operation_id="operation-dashboard",
            session_id="session-dashboard",
            idempotency_key="dashboard-request",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            input_channel=InputChannel.TEXT,
            message="Analyze Reliance for me",
            requested_at=cls.at,
        )
        cls.operation = BrowserOperationSnapshot(
            request=cls.request,
            status=BrowserOperationStatus.COMPLETED,
            updated_at=cls.at,
            last_event_sequence=1,
            result_available=True,
        )
        cls.output = BrowserOperationOutput(
            operation_id=cls.request.operation_id,
            session_id=cls.request.session_id,
            kind=cls.request.kind,
            completed_at=cls.at,
            research_response=JarvisSwingAnalysisResponse.completed(
                operation_id=cls.request.operation_id,
                result=cls.result,
                multi_timeframe_review=cls.result.technical_review,
                multi_timeframe_debate=cls.result.debate_result,
            ),
            research_explanation=RecordingPresenter().explain(
                cls.result,
                user_name="Prateek",
            ),
        )

    def test_projects_exact_multi_timeframe_evidence_for_the_ui(self):
        sink = InMemoryWorkflowEventSink()
        event = WorkflowEventEmitter(
            self.request.operation_id,
            sink,
            clock=lambda: self.at,
        ).emit(
            WorkflowStage.TECHNICAL_ANALYSIS,
            WorkflowEventState.STARTED,
        )
        view = JarvisDashboardProjector().project(
            self.operation,
            self.output,
            (event,),
        )
        package = self.result.technical_review.released_evidence
        verdict = self.result.debate_result.submission.verdict

        self.assertEqual(view.symbol, "RELIANCE-EQ")
        self.assertEqual(view.source_interval, "ONE_HOUR")
        self.assertEqual(view.latest_quote.price, self.result.latest_quote.price)
        self.assertEqual(view.refresh.mode, "initial")
        self.assertEqual(view.refresh.dataset_id, self.result.fetch.dataset_id)
        self.assertEqual(view.refresh.existing_candle_count, 0)
        self.assertEqual(
            view.refresh.final_candle_count,
            len(self.result.fetch.stored.series.candles),
        )
        self.assertEqual(
            view.refresh.new_candle_count,
            self.result.fetch.new_candle_count,
        )
        self.assertEqual(
            view.refresh.stored_from,
            self.result.fetch.stored.series.candles[0].timestamp,
        )
        self.assertEqual(
            view.refresh.stored_to,
            self.result.fetch.stored.series.candles[-1].timestamp,
        )
        self.assertEqual(view.package_fingerprint, package.package_fingerprint)
        self.assertEqual(view.daily.interval, "ONE_DAY")
        self.assertEqual(view.weekly.interval, "ONE_WEEK")
        self.assertEqual(
            view.daily.score,
            package.technical_analysis.daily_submission.profile.score,
        )
        self.assertEqual(
            view.daily.candles[-1].close,
            package.technical_analysis.timeframes.daily.candles[-1].close,
        )
        self.assertEqual(
            {item.evidence_id for item in view.daily.evidence},
            {item.qualified_evidence_id for item in package.daily.evidence},
        )
        self.assertEqual(
            {item.evidence_id for item in view.weekly.evidence},
            {item.qualified_evidence_id for item in package.weekly.evidence},
        )
        self.assertEqual(view.debate.verdict_id, verdict.verdict_id)
        self.assertEqual(
            view.debate.rounds[0].bull.evidence_citations,
            self.result.debate_result.submission.transcript.rounds[
                0
            ].bull_argument.evidence_citations,
        )
        marked_decisive = {
            item.evidence_id
            for panel in (view.daily, view.weekly)
            for item in panel.evidence
            if item.decisive
        }
        self.assertEqual(marked_decisive, set(verdict.decisive_evidence_ids))
        self.assertEqual(view.activities[0].participant_label, "Technical Analyst")
        self.assertIsNotNone(view.presentation)
        self.assertEqual(
            {item.indicator_id for item in view.daily.chart.indicators},
            {
                "ema_20",
                "ema_50",
                "bollinger_upper",
                "bollinger_middle",
                "bollinger_lower",
                "rsi_14",
            },
        )
        self.assertTrue(view.daily.chart.pivots)
        self.assertTrue(view.daily.chart.structure_points)
        self.assertTrue(
            all(
                marker.timestamp <= view.daily.evaluated_at
                for marker in view.daily.chart.candlestick_patterns
            )
        )
        self.assertEqual(len(view.daily.chart.pattern_catalog), 23)
        self.assertEqual(
            {item.pattern for item in view.daily.chart.pattern_catalog},
            {item.pattern for item in view.weekly.chart.pattern_catalog},
        )
        self.assertTrue(view.daily.chart.central_pivot_ranges)
        self.assertTrue(view.weekly.chart.central_pivot_ranges)
        for panel, context in (
            (view.daily, package.daily),
            (view.weekly, package.weekly),
        ):
            first_visible = panel.candles[0].timestamp
            expected_zones = {
                zone.zone_id
                for zone in context.accumulation.zones
                if (
                    zone.base_last_observed_at >= first_visible
                    or zone.is_active
                    or zone.lifecycle[-1].available_at >= first_visible
                )
            }
            expected_sweeps = {
                sweep.sweep_id
                for zone in context.accumulation.zones
                for sweep in zone.liquidity_sweeps
                if sweep.available_at >= first_visible
            }
            self.assertEqual(
                {zone.zone_id for zone in panel.chart.accumulation_zones},
                expected_zones,
            )
            self.assertEqual(
                {sweep.sweep_id for sweep in panel.chart.liquidity_sweeps},
                expected_sweeps,
            )
            for zone in panel.chart.accumulation_zones:
                source = next(
                    item
                    for item in context.accumulation.zones
                    if item.zone_id == zone.zone_id
                )
                self.assertEqual(zone.lower_price, source.lower_price)
                self.assertEqual(zone.upper_price, source.upper_price)
                self.assertEqual(
                    zone.confidence_percentage,
                    source.metrics.confidence_score,
                )
                self.assertEqual(zone.active, source.is_active)
            for sweep in panel.chart.liquidity_sweeps:
                source = next(
                    item
                    for zone in context.accumulation.zones
                    for item in zone.liquidity_sweeps
                    if item.sweep_id == sweep.sweep_id
                )
                self.assertEqual(sweep.extreme_price, source.extreme_price)
                self.assertEqual(
                    sweep.reclaim_close_price,
                    source.reclaim_close_price,
                )
        self.assertTrue(
            all(
                item.basis == "weekly"
                and item.source_period_ended_at < item.valid_from
                and item.bottom_central <= item.pivot <= item.top_central
                for item in view.daily.chart.central_pivot_ranges
            )
        )
        self.assertTrue(
            all(
                item.basis == "monthly"
                and item.source_period_ended_at < item.valid_from
                and item.bottom_central <= item.pivot <= item.top_central
                for item in view.weekly.chart.central_pivot_ranges
            )
        )
        self.assertIsNotNone(view.interpretation)
        self.assertEqual(
            view.interpretation.trade_decision.decision,
            self.result.interpretation.trade_decision.decision,
        )
        self.assertEqual(
            view.interpretation.trade_decision.market_condition,
            self.result.interpretation.trade_decision.market_condition,
        )

    def test_projects_additive_interpretation_without_changing_v1_dashboard(self):
        view = JarvisDashboardProjector().project(self.operation, self.output)

        self.assertEqual(view.schema_version, "jarvis.dashboard.v1")
        self.assertIsNotNone(view.interpretation)
        projected = view.interpretation
        self.assertEqual(
            projected.schema_version,
            "jarvis.dashboard_interpretation.v1",
        )
        self.assertEqual(
            projected.alignment,
            self.result.interpretation.alignment,
        )
        self.assertEqual(projected.trade_decision.decision.value, "no_trade")
        self.assertIsNone(projected.risk_reward.target_2r.target_price)
        self.assertEqual(len(projected.daily.bullish_setup.steps), 9)
        self.assertEqual(len(projected.daily.bearish_setup.steps), 8)
        self.assertEqual(
            projected.daily.bullish_setup.steps[0].label,
            "Prior downtrend",
        )
        restored = view.__class__.model_validate_json(view.model_dump_json())
        self.assertEqual(restored, view)

    def test_legacy_result_without_interpretation_remains_valid(self):
        legacy_payload = self.result.model_dump(exclude={"interpretation"})
        legacy_decision = TradeDecisionOutcome(
            market_condition=MarketCondition.BULLISH,
            decision=TradeDecision.NO_TRADE,
            no_trade_reasons=(NoTradeReason.DAILY_TRIGGER_INCOMPLETE,),
            rationale=(
                "Legacy result used the bullish Judge verdict as its market "
                "condition while the daily trigger remained incomplete."
            ),
        )
        legacy_payload["trade_plan_result"] = (
            self.result.trade_plan_result.model_copy(
                update={
                    "reason": (
                        MultiTimeframeTradeReason.DAILY_PROFILE_NOT_BULLISH
                    ),
                    "trade_decision": legacy_decision,
                }
            )
        )

        restored = self.result.__class__.model_validate(legacy_payload)

        self.assertIsNone(restored.interpretation)

    def test_result_rejects_interpretation_outside_released_evidence(self):
        interpretation = self.result.interpretation
        daily = interpretation.daily.model_copy(
            update={"decisive_evidence_ids": ("daily:not.released",)}
        )
        invalid = interpretation.model_copy(
            update={
                "daily": daily,
                "decisive_evidence_ids": (
                    "daily:not.released",
                    interpretation.weekly.decisive_evidence_ids[0],
                ),
            }
        )

        with self.assertRaisesRegex(ValueError, "released technical package"):
            self.result.__class__(
                **self.result.model_dump(exclude={"interpretation"}),
                interpretation=invalid,
            )

    def test_cpr_uses_exact_previous_completed_period_ohlc(self):
        view = JarvisDashboardProjector().project(
            self.operation,
            self.output,
        )
        source = (
            self.result.technical_review.released_evidence
            .technical_analysis.timeframes.daily
        )

        for cpr in (
            view.daily.chart.central_pivot_ranges[0],
            view.weekly.chart.central_pivot_ranges[0],
        ):
            candles = [
                candle
                for candle in source.candles
                if cpr.source_period_started_at
                <= candle.timestamp
                <= cpr.source_period_ended_at
            ]
            high = max(candle.high for candle in candles)
            low = min(candle.low for candle in candles)
            close = candles[-1].close
            expected_pivot = (high + low + close) / 3
            raw_bottom = (high + low) / 2
            raw_top = 2 * expected_pivot - raw_bottom

            self.assertAlmostEqual(cpr.pivot, expected_pivot)
            self.assertAlmostEqual(
                cpr.bottom_central,
                min(raw_bottom, raw_top),
            )
            self.assertAlmostEqual(
                cpr.top_central,
                max(raw_bottom, raw_top),
            )

    def test_bounds_chart_payload_without_changing_source_counts(self):
        view = JarvisDashboardProjector(
            daily_candle_limit=2,
            weekly_candle_limit=1,
        ).project(self.operation, self.output)
        package = self.result.technical_review.released_evidence

        self.assertEqual(len(view.daily.candles), 2)
        self.assertEqual(len(view.weekly.candles), 1)
        self.assertEqual(
            view.daily.source_candle_count,
            len(package.technical_analysis.timeframes.daily.candles),
        )

    def test_projects_incremental_and_unchanged_refresh_provenance(self):
        base = self.result.fetch
        last_candle_at = base.stored.series.candles[-1].timestamp
        requested_to = last_candle_at + timedelta(days=1)

        incremental = RollingFetchReceipt(
            use_case_id=base.use_case_id,
            adapter_name=base.adapter_name,
            stored=base.stored,
            new_candle_count=4,
            corrected_candle_count=1,
            deduplicated_fetched_candle_count=2,
            chunk_request_count=1,
            resumed_from=last_candle_at - timedelta(hours=4),
            requested_from=last_candle_at - timedelta(days=7),
            requested_to=requested_to,
            checked_at=requested_to,
            intraday_gaps=(
                IntradayCandleGap(
                    interval="ONE_HOUR",
                    gap_after=last_candle_at - timedelta(hours=2),
                    resumes_at=last_candle_at,
                    cadence_minutes=60,
                    missing_candle_count=1,
                ),
            ),
        )
        incremental_view = self._project_with_fetch(incremental)

        self.assertEqual(incremental_view.refresh.mode, "incremental")
        self.assertEqual(incremental_view.refresh.new_candle_count, 4)
        self.assertEqual(incremental_view.refresh.corrected_candle_count, 1)
        self.assertEqual(
            incremental_view.refresh.deduplicated_fetched_candle_count,
            2,
        )
        self.assertEqual(
            incremental_view.refresh.existing_candle_count,
            len(base.stored.series.candles) - 4,
        )
        self.assertEqual(incremental_view.refresh.requested_to, requested_to)
        self.assertEqual(len(incremental_view.refresh.intraday_gaps), 1)
        self.assertEqual(
            incremental_view.refresh.intraday_gaps[0].missing_candle_count,
            1,
        )

        unchanged = RollingFetchReceipt(
            use_case_id=base.use_case_id,
            adapter_name=base.adapter_name,
            stored=base.stored,
            new_candle_count=0,
            chunk_request_count=1,
            resumed_from=last_candle_at,
            requested_from=last_candle_at - timedelta(days=7),
            requested_to=requested_to,
            checked_at=requested_to,
            reused_existing_dataset=True,
        )
        unchanged_view = self._project_with_fetch(unchanged)

        self.assertEqual(unchanged_view.refresh.mode, "unchanged")
        self.assertTrue(unchanged_view.refresh.reused_existing_dataset)
        self.assertEqual(
            unchanged_view.refresh.existing_candle_count,
            unchanged_view.refresh.final_candle_count,
        )

    def test_rejects_refresh_counts_exceeding_stored_dataset(self):
        base = self.result.fetch
        impossible = RollingFetchReceipt(
            use_case_id=base.use_case_id,
            adapter_name=base.adapter_name,
            stored=base.stored,
            new_candle_count=len(base.stored.series.candles) + 1,
            chunk_request_count=1,
        )

        with self.assertRaisesRegex(
            DashboardProjectionError,
            "counts do not match",
        ):
            self._project_with_fetch(impossible)

    def test_no_trade_does_not_invent_entry_stop_or_targets(self):
        view = JarvisDashboardProjector().project(
            self.operation,
            self.output,
        )

        self.assertEqual(view.trade_plan.disposition, "no_trade")
        self.assertIsNone(view.trade_plan.entry_price)
        self.assertIsNone(view.trade_plan.stop_loss_price)
        self.assertIsNone(view.trade_plan.target_2r_price)
        self.assertIsNone(view.trade_plan.target_3r_price)

    def test_actionable_plan_preserves_exact_two_and_three_r_values(self):
        result = _use_case(timeframes=_trending_timeframes()).execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )
        output = self.output.model_copy(
            update={
                "research_response": JarvisSwingAnalysisResponse.completed(
                    operation_id=self.request.operation_id,
                    result=result,
                    multi_timeframe_review=result.technical_review,
                    multi_timeframe_debate=result.debate_result,
                ),
                "research_explanation": RecordingPresenter().explain(
                    result,
                    user_name="Prateek",
                ),
            }
        )
        view = JarvisDashboardProjector().project(self.operation, output)
        evaluation = (
            result.trade_plan_result.daily_planning_result
            .approved_trade_intent.evaluation
        )

        self.assertEqual(view.trade_plan.disposition, "actionable")
        self.assertEqual(view.trade_plan.direction, "long")
        self.assertEqual(view.trade_plan.entry_price, evaluation.entry_price)
        self.assertEqual(
            view.trade_plan.stop_loss_price,
            evaluation.stop_loss_price,
        )
        self.assertEqual(
            view.trade_plan.target_2r_price,
            evaluation.minimum_target.target_price,
        )
        self.assertEqual(
            view.trade_plan.target_3r_price,
            evaluation.preferred_target.target_price,
        )
        self.assertEqual(
            view.interpretation.risk_reward.target_2r.target_price,
            evaluation.minimum_target.target_price,
        )
        self.assertEqual(
            view.interpretation.trade_decision.decision.value,
            "buy",
        )

    def test_rejects_non_completed_and_mismatched_operations(self):
        queued = self.operation.model_copy(
            update={
                "status": BrowserOperationStatus.QUEUED,
                "result_available": False,
            }
        )
        mismatched = self.output.model_copy(update={"operation_id": "other-op"})

        with self.assertRaisesRegex(DashboardProjectionError, "completed"):
            JarvisDashboardProjector().project(queued, self.output)
        with self.assertRaisesRegex(DashboardProjectionError, "IDs"):
            JarvisDashboardProjector().project(self.operation, mismatched)

    def test_rejects_invalid_chart_limits(self):
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    JarvisDashboardProjector(daily_candle_limit=invalid)

    def _project_with_fetch(self, fetch):
        result = self.result.model_copy(update={"fetch": fetch})
        output = self.output.model_copy(
            update={
                "research_response": JarvisSwingAnalysisResponse.completed(
                    operation_id=self.request.operation_id,
                    result=result,
                    multi_timeframe_review=result.technical_review,
                    multi_timeframe_debate=result.debate_result,
                )
            }
        )
        return JarvisDashboardProjector().project(self.operation, output)



if __name__ == "__main__":
    unittest.main()
