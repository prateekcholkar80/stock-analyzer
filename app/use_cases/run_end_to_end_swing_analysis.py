from datetime import datetime

from app.exceptions import AgentSubmissionRejectedError
from app.models.llm import LLMPreflightResult
from app.models.storage import EndToEndSwingAnalysisResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import DebateOrchestrator
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.workflow.events import WorkflowEventEmitter


class RunEndToEndSwingAnalysis:
    """Chain a rolling data pull into technical evaluation and a debate."""

    use_case_id = "jarvis.run_end_to_end_swing_analysis.v1"

    def __init__(
        self,
        rolling_fetch: PullRollingMarketSeries,
        agent_orchestrator: AgentOrchestrator | None = None,
        debate_orchestrator: DebateOrchestrator | None = None,
        llm_preflight: LLMPreflightResult | None = None,
    ) -> None:
        self.rolling_fetch = rolling_fetch
        self.agent_orchestrator = agent_orchestrator or AgentOrchestrator()
        if debate_orchestrator is None:
            raise ValueError(
                "end-to-end swing analysis requires a configured "
                "full-debate orchestrator"
            )
        self.debate_orchestrator = debate_orchestrator
        if not isinstance(llm_preflight, LLMPreflightResult):
            raise ValueError(
                "end-to-end swing analysis requires a successful LLM "
                "preflight result"
            )
        self.llm_preflight = llm_preflight

    def execute(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str = "ONE_HOUR",
        *,
        to_date: datetime | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> EndToEndSwingAnalysisResult:
        _validate_event_emitter(event_emitter)
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
            technical_result = self.agent_orchestrator.run_swing_analysis(
                fetch_receipt.stored.series
            )
            if not technical_result.decision.accepted:
                reasons = "; ".join(technical_result.decision.reasons)
                raise AgentSubmissionRejectedError(
                    f"Jarvis rejected the technical submission: {reasons}"
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

        debate_result = self.debate_orchestrator.run_debate(
            technical_result,
            event_emitter=event_emitter,
        )

        return EndToEndSwingAnalysisResult(
            use_case_id=self.use_case_id,
            market_dataset_id=fetch_receipt.dataset_id,
            fetch=fetch_receipt,
            technical_result=technical_result,
            debate_result=debate_result,
        )


def _validate_event_emitter(
    event_emitter: WorkflowEventEmitter | None,
) -> None:
    if event_emitter is not None and not isinstance(
        event_emitter,
        WorkflowEventEmitter,
    ):
        raise ValueError("swing analysis requires a workflow event emitter")


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
