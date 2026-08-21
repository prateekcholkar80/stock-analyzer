from datetime import datetime

from app.exceptions import AgentSubmissionRejectedError
from app.models.storage import EndToEndSwingAnalysisResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import DebateOrchestrator
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries


class RunEndToEndSwingAnalysis:
    """Chain a rolling data pull into technical evaluation and a debate."""

    use_case_id = "jarvis.run_end_to_end_swing_analysis.v1"

    def __init__(
        self,
        rolling_fetch: PullRollingMarketSeries,
        agent_orchestrator: AgentOrchestrator | None = None,
        debate_orchestrator: DebateOrchestrator | None = None,
    ) -> None:
        self.rolling_fetch = rolling_fetch
        self.agent_orchestrator = agent_orchestrator or AgentOrchestrator()
        self.debate_orchestrator = (
            debate_orchestrator or DebateOrchestrator()
        )

    def execute(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str = "ONE_HOUR",
        *,
        to_date: datetime | None = None,
    ) -> EndToEndSwingAnalysisResult:
        fetch_receipt = self.rolling_fetch.execute(
            exchange,
            symbol_token,
            symbol,
            interval,
            to_date=to_date,
        )

        technical_result = self.agent_orchestrator.run_swing_analysis(
            fetch_receipt.stored.series
        )
        if not technical_result.decision.accepted:
            reasons = "; ".join(technical_result.decision.reasons)
            raise AgentSubmissionRejectedError(
                f"Jarvis rejected the technical submission: {reasons}"
            )

        debate_result = self.debate_orchestrator.run_debate(technical_result)

        return EndToEndSwingAnalysisResult(
            use_case_id=self.use_case_id,
            market_dataset_id=fetch_receipt.dataset_id,
            fetch=fetch_receipt,
            technical_result=technical_result,
            debate_result=debate_result,
        )
