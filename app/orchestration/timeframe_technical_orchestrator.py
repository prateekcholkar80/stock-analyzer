from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.agents.technical_swing_agent import (
    DailyTechnicalSwingAgent,
    WeeklyTechnicalSwingAgent,
)
from app.exceptions import AgentSubmissionRejectedError
from app.analytics.accumulation import detect_accumulation_zones
from app.models.accumulation import TimeframeAccumulationAnalysis
from app.models.agentic import TechnicalSwingAgentSubmission
from app.models.market import HistoricalCandleSeries
from app.models.workflow import (
    WorkflowActivityDescriptor,
    WorkflowEventState,
    WorkflowParticipantKind,
    WorkflowStage,
)
from app.models.timeframes import (
    MultiTimeframeTechnicalAnalysis,
    SwingTimeframeSeries,
    TechnicalSubmissionValidationReceipt,
    multi_timeframe_technical_fingerprint,
)
from app.orchestration.agent_orchestrator import technical_submission_checks
from app.workflow.events import WorkflowEventEmitter


_DAILY_ANALYSIS = WorkflowActivityDescriptor(
    activity_id="technical.daily.evaluate",
    participant_id="technical.daily_analyst",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Daily Technical Analyst",
    timeframe="ONE_DAY",
)
_WEEKLY_ANALYSIS = WorkflowActivityDescriptor(
    activity_id="technical.weekly.evaluate",
    participant_id="technical.weekly_analyst",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Weekly Technical Analyst",
    timeframe="ONE_WEEK",
)


@runtime_checkable
class TimeframeTechnicalAgent(Protocol):
    agent_id: str

    @property
    def evaluator_id(self) -> str:
        ...


class AccumulationDetector(Protocol):
    def __call__(
        self,
        series: HistoricalCandleSeries,
        *,
        as_of: datetime,
    ) -> TimeframeAccumulationAnalysis:
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
        accumulation_detector: AccumulationDetector = (
            detect_accumulation_zones
        ),
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
        if not callable(accumulation_detector):
            raise ValueError("accumulation detector must be callable")
        self.accumulation_detector = accumulation_detector

    def execute(
        self,
        timeframes: SwingTimeframeSeries,
        *,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> MultiTimeframeTechnicalAnalysis:
        if not isinstance(timeframes, SwingTimeframeSeries):
            raise ValueError(
                "parallel technical orchestration requires swing timeframes"
            )
        if event_emitter is not None and not isinstance(
            event_emitter,
            WorkflowEventEmitter,
        ):
            raise ValueError("parallel analysis requires a workflow emitter")

        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="jarvis-timeframe",
        ) as executor:
            daily_future = executor.submit(
                self._execute_assignment,
                self.daily_agent,
                timeframes.daily,
                "daily",
                _DAILY_ANALYSIS,
                event_emitter,
            )
            weekly_future = executor.submit(
                self._execute_assignment,
                self.weekly_agent,
                timeframes.weekly,
                "weekly",
                _WEEKLY_ANALYSIS,
                event_emitter,
            )
            (
                daily_submission,
                daily_validation,
                daily_accumulation,
            ) = daily_future.result()
            (
                weekly_submission,
                weekly_validation,
                weekly_accumulation,
            ) = weekly_future.result()
        return MultiTimeframeTechnicalAnalysis(
            timeframes=timeframes,
            daily_submission=daily_submission,
            weekly_submission=weekly_submission,
            daily_accumulation=daily_accumulation,
            weekly_accumulation=weekly_accumulation,
            daily_validation=daily_validation,
            weekly_validation=weekly_validation,
            combined_fingerprint=multi_timeframe_technical_fingerprint(
                timeframes,
                daily_submission,
                weekly_submission,
                daily_accumulation,
                weekly_accumulation,
            ),
        )

    def _execute_assignment(
        self,
        agent: TimeframeTechnicalAgent,
        series: HistoricalCandleSeries,
        label: str,
        activity: WorkflowActivityDescriptor,
        emitter: WorkflowEventEmitter | None,
    ) -> tuple[
        TechnicalSwingAgentSubmission,
        TechnicalSubmissionValidationReceipt,
        TimeframeAccumulationAnalysis,
    ]:
        common = {
            "exchange": series.exchange,
            "symbol": series.symbol,
            "activity": activity,
        }
        if emitter is not None:
            emitter.emit(
                WorkflowStage.ANALYSIS,
                WorkflowEventState.STARTED,
                **common,
            )
        try:
            submission = agent.execute(series)
            validation = self._validate(
                submission,
                series,
                agent,
                label=label,
            )
            accumulation = self.accumulation_detector(
                series,
                as_of=submission.evaluated_at,
            )
            self._validate_accumulation(
                accumulation,
                series,
                submission,
                label=label,
            )
        except Exception:
            if emitter is not None:
                emitter.emit(
                    WorkflowStage.ANALYSIS,
                    WorkflowEventState.FAILED,
                    **common,
                )
            raise
        if emitter is not None:
            emitter.emit(
                WorkflowStage.ANALYSIS,
                WorkflowEventState.COMPLETED,
                **common,
            )
        return submission, validation, accumulation

    @staticmethod
    def _validate_accumulation(
        accumulation: TimeframeAccumulationAnalysis,
        series: HistoricalCandleSeries,
        submission: TechnicalSwingAgentSubmission,
        *,
        label: str,
    ) -> None:
        if not isinstance(accumulation, TimeframeAccumulationAnalysis):
            raise AgentSubmissionRejectedError(
                f"{label} accumulation analysis has an invalid contract"
            )
        expected = (
            series.exchange,
            series.symbol_token,
            series.symbol,
            series.interval,
            series.source,
            series.retrieved_at,
            submission.evaluated_at,
        )
        actual = (
            accumulation.exchange,
            accumulation.symbol_token,
            accumulation.symbol,
            accumulation.interval,
            accumulation.source,
            accumulation.source_retrieved_at,
            accumulation.evaluated_at,
        )
        if actual != expected:
            raise AgentSubmissionRejectedError(
                f"{label} accumulation analysis failed assignment validation"
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
