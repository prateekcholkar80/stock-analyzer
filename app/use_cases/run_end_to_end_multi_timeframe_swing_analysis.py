from datetime import datetime

from app.exceptions import AgentSubmissionRejectedError
from app.models.llm import LLMPreflightResult
from app.models.storage import MultiTimeframeEndToEndSwingAnalysisResult
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import DebateOrchestrator
from app.use_cases.derive_swing_timeframes import DeriveSwingTimeframes
from app.use_cases.build_multi_timeframe_long_trade_plan import (
    BuildMultiTimeframeLongTradePlan,
)
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries
from app.workflow.events import WorkflowEventEmitter


class RunEndToEndMultiTimeframeSwingAnalysis:
    """Run hourly pull, parallel daily/weekly analysis, and full debate."""

    use_case_id = "jarvis.run_end_to_end_multi_timeframe_swing_analysis.v1"

    def __init__(
        self,
        rolling_fetch: PullRollingMarketSeries,
        agent_orchestrator: AgentOrchestrator | None = None,
        debate_orchestrator: DebateOrchestrator | None = None,
        llm_preflight: LLMPreflightResult | None = None,
        timeframe_deriver: DeriveSwingTimeframes | None = None,
        trade_plan_builder: BuildMultiTimeframeLongTradePlan | None = None,
    ) -> None:
        self.rolling_fetch = rolling_fetch
        self.agent_orchestrator = agent_orchestrator or AgentOrchestrator()
        if debate_orchestrator is None:
            raise ValueError(
                "multi-timeframe analysis requires a configured debate "
                "orchestrator"
            )
        self.debate_orchestrator = debate_orchestrator
        if not isinstance(llm_preflight, LLMPreflightResult):
            raise ValueError(
                "multi-timeframe analysis requires successful LLM preflight"
            )
        self.llm_preflight = llm_preflight
        self.timeframe_deriver = timeframe_deriver or DeriveSwingTimeframes()
        if not callable(getattr(self.timeframe_deriver, "execute", None)):
            raise ValueError("timeframe deriver must provide execute()")
        self.trade_plan_builder = (
            trade_plan_builder
            or BuildMultiTimeframeLongTradePlan(self.agent_orchestrator)
        )
        if not callable(getattr(self.trade_plan_builder, "execute", None)):
            raise ValueError("trade-plan builder must provide execute()")

    def execute(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str = "ONE_HOUR",
        *,
        to_date: datetime | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> MultiTimeframeEndToEndSwingAnalysisResult:
        if event_emitter is not None and not isinstance(
            event_emitter,
            WorkflowEventEmitter,
        ):
            raise ValueError("analysis requires a workflow event emitter")
        if interval != "ONE_HOUR":
            raise ValueError(
                "multi-timeframe swing analysis requires ONE_HOUR input"
            )

        _emit(
            event_emitter,
            WorkflowStage.MARKET_DATA_LOADING,
            WorkflowEventState.STARTED,
            exchange,
            symbol,
        )
        try:
            fetch_receipt = self.rolling_fetch.execute(
                exchange,
                symbol_token,
                symbol,
                interval,
                to_date=to_date,
            )
        except Exception:
            _emit(
                event_emitter,
                WorkflowStage.MARKET_DATA_LOADING,
                WorkflowEventState.FAILED,
                exchange,
                symbol,
            )
            raise
        _emit(
            event_emitter,
            WorkflowStage.MARKET_DATA_LOADING,
            WorkflowEventState.COMPLETED,
            exchange,
            symbol,
        )

        _emit(
            event_emitter,
            WorkflowStage.TECHNICAL_ANALYSIS,
            WorkflowEventState.STARTED,
            exchange,
            symbol,
        )
        try:
            timeframes = self.timeframe_deriver.execute(
                fetch_receipt.stored.series
            )
            technical_review = (
                self.agent_orchestrator.run_multi_timeframe_analysis(
                    timeframes
                )
            )
            if technical_review.released_evidence is None:
                reasons = "; ".join(technical_review.decision.reasons)
                raise AgentSubmissionRejectedError(
                    "Jarvis rejected multi-timeframe evidence: " + reasons
                )
        except Exception:
            _emit(
                event_emitter,
                WorkflowStage.TECHNICAL_ANALYSIS,
                WorkflowEventState.FAILED,
                exchange,
                symbol,
            )
            raise
        _emit(
            event_emitter,
            WorkflowStage.TECHNICAL_ANALYSIS,
            WorkflowEventState.COMPLETED,
            exchange,
            symbol,
        )

        debate_result = self.debate_orchestrator.run_multi_timeframe_debate(
            technical_review,
            event_emitter=event_emitter,
        )
        if not debate_result.decision.accepted:
            reasons = "; ".join(debate_result.decision.reasons)
            raise AgentSubmissionRejectedError(
                "Jarvis rejected multi-timeframe debate: " + reasons
            )
        trade_plan_result = self.trade_plan_builder.execute(
            technical_review,
            debate_result,
        )
        return MultiTimeframeEndToEndSwingAnalysisResult(
            use_case_id=self.use_case_id,
            market_dataset_id=fetch_receipt.dataset_id,
            fetch=fetch_receipt,
            technical_review=technical_review,
            debate_result=debate_result,
            trade_plan_result=trade_plan_result,
        )


def _emit(
    emitter: WorkflowEventEmitter | None,
    stage: WorkflowStage,
    state: WorkflowEventState,
    exchange: str,
    symbol: str,
) -> None:
    if emitter is not None:
        emitter.emit(
            stage,
            state,
            exchange=exchange,
            symbol=symbol,
        )
