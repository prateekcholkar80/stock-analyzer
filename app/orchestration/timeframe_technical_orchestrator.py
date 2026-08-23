from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, runtime_checkable

from app.agents.technical_swing_agent import (
    DailyTechnicalSwingAgent,
    WeeklyTechnicalSwingAgent,
)
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import TechnicalSwingAgentSubmission
from app.models.market import HistoricalCandleSeries
from app.models.timeframes import (
    MultiTimeframeTechnicalAnalysis,
    SwingTimeframeSeries,
    TechnicalSubmissionValidationReceipt,
    multi_timeframe_technical_fingerprint,
)
from app.orchestration.agent_orchestrator import technical_submission_checks


@runtime_checkable
class TimeframeTechnicalAgent(Protocol):
    agent_id: str

    @property
    def evaluator_id(self) -> str:
        ...

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def execute(
        self,
        market_series: HistoricalCandleSeries,
    ) -> TechnicalSwingAgentSubmission:
        ...


class ParallelTimeframeTechnicalOrchestrator:
    """Execute independent daily and weekly technical assignments in parallel."""

    orchestrator_id = "jarvis.parallel_timeframe_technical_orchestrator.v1"
    validator_id = "jarvis.technical_submission_guard.v1"

    def __init__(
        self,
        daily_agent: TimeframeTechnicalAgent | None = None,
        weekly_agent: TimeframeTechnicalAgent | None = None,
    ) -> None:
        self.daily_agent = daily_agent or DailyTechnicalSwingAgent()
        self.weekly_agent = weekly_agent or WeeklyTechnicalSwingAgent()
        for agent in (self.daily_agent, self.weekly_agent):
            if not isinstance(agent, TimeframeTechnicalAgent):
                raise ValueError(
                    "parallel technical orchestration requires valid agents"
                )
        if self.daily_agent.agent_id == self.weekly_agent.agent_id:
            raise ValueError("daily and weekly agent identities must differ")

    def execute(
        self,
        timeframes: SwingTimeframeSeries,
    ) -> MultiTimeframeTechnicalAnalysis:
        if not isinstance(timeframes, SwingTimeframeSeries):
            raise ValueError(
                "parallel technical orchestration requires swing timeframes"
            )

        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="jarvis-timeframe",
        ) as executor:
            daily_future = executor.submit(
                self.daily_agent.execute,
                timeframes.daily,
            )
            weekly_future = executor.submit(
                self.weekly_agent.execute,
                timeframes.weekly,
            )
            daily_submission = daily_future.result()
            weekly_submission = weekly_future.result()

        daily_validation = self._validate(
            daily_submission,
            timeframes.daily,
            self.daily_agent,
            label="daily",
        )
        weekly_validation = self._validate(
            weekly_submission,
            timeframes.weekly,
            self.weekly_agent,
            label="weekly",
        )
        return MultiTimeframeTechnicalAnalysis(
            timeframes=timeframes,
            daily_submission=daily_submission,
            weekly_submission=weekly_submission,
            daily_validation=daily_validation,
            weekly_validation=weekly_validation,
            combined_fingerprint=multi_timeframe_technical_fingerprint(
                timeframes,
                daily_submission,
                weekly_submission,
            ),
        )

    def _validate(
        self,
        submission: TechnicalSwingAgentSubmission,
        series: HistoricalCandleSeries,
        agent: TimeframeTechnicalAgent,
        *,
        label: str,
    ) -> TechnicalSubmissionValidationReceipt:
        submission, passed_checks, reasons = technical_submission_checks(
            submission,
            series,
            expected_agent_id=agent.agent_id,
            expected_evaluator_id=agent.evaluator_id,
            expected_configuration_fingerprint=(
                agent.configuration_fingerprint
            ),
        )
        if reasons:
            raise AgentSubmissionRejectedError(
                f"{label} technical submission failed validation: "
                + "; ".join(reasons)
            )
        return TechnicalSubmissionValidationReceipt(
            validation_id=(
                f"{self.validator_id}:{label}:"
                f"{submission.input_fingerprint}"
            ),
            submission_id=submission.submission_id,
            passed_checks=tuple(passed_checks),
        )
