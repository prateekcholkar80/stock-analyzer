from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from app.logging_config import get_logger
from app.models.workflow import (
    JarvisWorkflowEvent,
    WorkflowActivityDescriptor,
    WorkflowEventState,
    WorkflowParticipantKind,
    WorkflowStage,
)


IST = ZoneInfo("Asia/Kolkata")
EventClock = Callable[[], datetime]
logger = get_logger(__name__)


_STAGE_MESSAGES = {
    (WorkflowStage.REQUEST_RECEIVED, WorkflowEventState.COMPLETED): (
        "Jarvis received the swing-research request."
    ),
    (WorkflowStage.INSTRUMENT_RESOLVED, WorkflowEventState.COMPLETED): (
        "The requested instrument was resolved."
    ),
    (WorkflowStage.MARKET_DATA_LOADING, WorkflowEventState.STARTED): (
        "Historical market data is loading."
    ),
    (WorkflowStage.MARKET_DATA_LOADING, WorkflowEventState.COMPLETED): (
        "Historical market data is ready."
    ),
    (WorkflowStage.MARKET_DATA_LOADING, WorkflowEventState.FAILED): (
        "Historical market data could not be prepared."
    ),
    (WorkflowStage.TECHNICAL_ANALYSIS, WorkflowEventState.STARTED): (
        "The technical agent is evaluating price action and signals."
    ),
    (WorkflowStage.TECHNICAL_ANALYSIS, WorkflowEventState.COMPLETED): (
        "The technical evidence package is ready."
    ),
    (WorkflowStage.TECHNICAL_ANALYSIS, WorkflowEventState.FAILED): (
        "The technical evidence package could not be completed."
    ),
    (WorkflowStage.DATA_PREPARATION, WorkflowEventState.STARTED): (
        "Market data is being prepared for analysis."
    ),
    (WorkflowStage.DATA_PREPARATION, WorkflowEventState.COMPLETED): (
        "Market data preparation is complete."
    ),
    (WorkflowStage.DATA_PREPARATION, WorkflowEventState.FAILED): (
        "Market data preparation could not be completed."
    ),
    (WorkflowStage.ANALYSIS, WorkflowEventState.STARTED): (
        "An analyst started evaluating the approved data."
    ),
    (WorkflowStage.ANALYSIS, WorkflowEventState.COMPLETED): (
        "An analyst completed its evidence submission."
    ),
    (WorkflowStage.ANALYSIS, WorkflowEventState.FAILED): (
        "An analyst could not complete its evidence submission."
    ),
    (WorkflowStage.EVIDENCE_REVIEW, WorkflowEventState.STARTED): (
        "The Jarvis Judge is validating the combined evidence package."
    ),
    (WorkflowStage.EVIDENCE_REVIEW, WorkflowEventState.COMPLETED): (
        "The Jarvis Judge released the validated evidence package."
    ),
    (WorkflowStage.EVIDENCE_REVIEW, WorkflowEventState.FAILED): (
        "The combined evidence package did not pass review."
    ),
    (WorkflowStage.BULL_DEBATING, WorkflowEventState.STARTED): (
        "The Bull agent is building its evidence-grounded case."
    ),
    (WorkflowStage.BULL_DEBATING, WorkflowEventState.COMPLETED): (
        "The Bull agent submitted its case."
    ),
    (WorkflowStage.BULL_DEBATING, WorkflowEventState.FAILED): (
        "The Bull agent could not complete its case."
    ),
    (WorkflowStage.BEAR_DEBATING, WorkflowEventState.STARTED): (
        "The Bear agent is challenging the bullish case."
    ),
    (WorkflowStage.BEAR_DEBATING, WorkflowEventState.COMPLETED): (
        "The Bear agent submitted its case."
    ),
    (WorkflowStage.BEAR_DEBATING, WorkflowEventState.FAILED): (
        "The Bear agent could not complete its case."
    ),
    (WorkflowStage.JUDGE_REVIEWING, WorkflowEventState.STARTED): (
        "The Jarvis Judge is reviewing both grounded cases."
    ),
    (WorkflowStage.JUDGE_REVIEWING, WorkflowEventState.COMPLETED): (
        "The Jarvis Judge completed its review."
    ),
    (WorkflowStage.JUDGE_REVIEWING, WorkflowEventState.FAILED): (
        "The Jarvis Judge could not complete its review."
    ),
    (WorkflowStage.TRADE_PLANNING, WorkflowEventState.STARTED): (
        "The deterministic long-only trade policy is evaluating the verdict."
    ),
    (WorkflowStage.TRADE_PLANNING, WorkflowEventState.COMPLETED): (
        "The long-only trade policy produced its validated outcome."
    ),
    (WorkflowStage.TRADE_PLANNING, WorkflowEventState.FAILED): (
        "The long-only trade policy could not produce a valid outcome."
    ),
    (WorkflowStage.PRESENTATION, WorkflowEventState.STARTED): (
        "Jarvis is preparing the evidence-grounded CEO briefing."
    ),
    (WorkflowStage.PRESENTATION, WorkflowEventState.COMPLETED): (
        "Jarvis completed the evidence-grounded CEO briefing."
    ),
    (WorkflowStage.PRESENTATION, WorkflowEventState.FAILED): (
        "Jarvis could not prepare the CEO briefing."
    ),
    (WorkflowStage.FOLLOW_UP, WorkflowEventState.STARTED): (
        "The Jarvis Judge is reviewing the grounded follow-up question."
    ),
    (WorkflowStage.FOLLOW_UP, WorkflowEventState.COMPLETED): (
        "The Jarvis Judge completed the grounded follow-up response."
    ),
    (WorkflowStage.FOLLOW_UP, WorkflowEventState.FAILED): (
        "The Jarvis Judge could not complete the grounded follow-up response."
    ),
    (WorkflowStage.COMPLETED, WorkflowEventState.COMPLETED): (
        "Jarvis completed the swing-research workflow."
    ),
    (WorkflowStage.FAILED, WorkflowEventState.FAILED): (
        "Jarvis stopped the swing-research workflow without a conclusion."
    ),
}


_DEFAULT_ACTIVITIES = {
    WorkflowStage.REQUEST_RECEIVED: WorkflowActivityDescriptor(
        activity_id="request.receive",
        participant_id="jarvis.orchestrator",
        participant_kind=WorkflowParticipantKind.ORCHESTRATOR,
        participant_label="Jarvis",
    ),
    WorkflowStage.INSTRUMENT_RESOLVED: WorkflowActivityDescriptor(
        activity_id="instrument.resolve",
        participant_id="research.instrument_service",
        participant_kind=WorkflowParticipantKind.SERVICE,
        participant_label="Instrument Resolver",
    ),
    WorkflowStage.MARKET_DATA_LOADING: WorkflowActivityDescriptor(
        activity_id="market.hourly.load",
        participant_id="research.market_data_service",
        participant_kind=WorkflowParticipantKind.SERVICE,
        participant_label="Market Data Service",
        timeframe="ONE_HOUR",
    ),
    WorkflowStage.TECHNICAL_ANALYSIS: WorkflowActivityDescriptor(
        activity_id="technical.legacy.evaluate",
        participant_id="technical.swing_analyst",
        participant_kind=WorkflowParticipantKind.ANALYST,
        participant_label="Technical Analyst",
    ),
    WorkflowStage.DATA_PREPARATION: WorkflowActivityDescriptor(
        activity_id="market.timeframes.prepare",
        participant_id="research.market_data_service",
        participant_kind=WorkflowParticipantKind.SERVICE,
        participant_label="Market Data Service",
    ),
    WorkflowStage.ANALYSIS: WorkflowActivityDescriptor(
        activity_id="research.analysis.execute",
        participant_id="research.generic_analyst",
        participant_kind=WorkflowParticipantKind.ANALYST,
        participant_label="Research Analyst",
    ),
    WorkflowStage.EVIDENCE_REVIEW: WorkflowActivityDescriptor(
        activity_id="evidence.release.review",
        participant_id="judge.evidence_release",
        participant_kind=WorkflowParticipantKind.JUDGE,
        participant_label="Jarvis Judge",
    ),
    WorkflowStage.BULL_DEBATING: WorkflowActivityDescriptor(
        activity_id="debate.bull.argue",
        participant_id="debate.bull",
        participant_kind=WorkflowParticipantKind.ADVOCATE,
        participant_label="Bull",
    ),
    WorkflowStage.BEAR_DEBATING: WorkflowActivityDescriptor(
        activity_id="debate.bear.argue",
        participant_id="debate.bear",
        participant_kind=WorkflowParticipantKind.ADVOCATE,
        participant_label="Bear",
    ),
    WorkflowStage.JUDGE_REVIEWING: WorkflowActivityDescriptor(
        activity_id="debate.verdict.review",
        participant_id="judge.debate",
        participant_kind=WorkflowParticipantKind.JUDGE,
        participant_label="Jarvis Judge",
    ),
    WorkflowStage.TRADE_PLANNING: WorkflowActivityDescriptor(
        activity_id="trade.long_only.evaluate",
        participant_id="research.trade_planner",
        participant_kind=WorkflowParticipantKind.ANALYST,
        participant_label="Trade Planning Analyst",
        timeframe="ONE_DAY",
    ),
    WorkflowStage.PRESENTATION: WorkflowActivityDescriptor(
        activity_id="jarvis.ceo_briefing.present",
        participant_id="jarvis.presenter",
        participant_kind=WorkflowParticipantKind.PRESENTER,
        participant_label="Jarvis",
    ),
    WorkflowStage.FOLLOW_UP: WorkflowActivityDescriptor(
        activity_id="judge.follow_up.answer",
        participant_id="judge.debate",
        participant_kind=WorkflowParticipantKind.JUDGE,
        participant_label="Jarvis Judge",
    ),
    WorkflowStage.COMPLETED: WorkflowActivityDescriptor(
        activity_id="workflow.complete",
        participant_id="jarvis.orchestrator",
        participant_kind=WorkflowParticipantKind.ORCHESTRATOR,
        participant_label="Jarvis",
    ),
    WorkflowStage.FAILED: WorkflowActivityDescriptor(
        activity_id="workflow.fail",
        participant_id="jarvis.orchestrator",
        participant_kind=WorkflowParticipantKind.ORCHESTRATOR,
        participant_label="Jarvis",
    ),
}


@runtime_checkable
class WorkflowEventSink(Protocol):
    def publish(self, event: JarvisWorkflowEvent) -> None:
        ...


class NullWorkflowEventSink:
    def publish(self, event: JarvisWorkflowEvent) -> None:
        return None


class InMemoryWorkflowEventSink:
    """Thread-safe event collector for tests and local interface adapters."""

    def __init__(self) -> None:
        self._events: list[JarvisWorkflowEvent] = []
        self._lock = RLock()

    @property
    def events(self) -> tuple[JarvisWorkflowEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def publish(self, event: JarvisWorkflowEvent) -> None:
        if not isinstance(event, JarvisWorkflowEvent):
            raise ValueError("workflow sink requires a validated event")
        with self._lock:
            self._events.append(event)


class WorkflowEventEmitter:
    """Sequence and safely deliver progress for one Jarvis operation."""

    def __init__(
        self,
        operation_id: str,
        sink: WorkflowEventSink | None = None,
        *,
        clock: EventClock | None = None,
        initial_sequence: int = 0,
    ) -> None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("workflow emitter operation ID must not be blank")
        resolved_sink = sink if sink is not None else NullWorkflowEventSink()
        if not isinstance(resolved_sink, WorkflowEventSink):
            raise ValueError("workflow emitter requires an event sink")
        resolved_clock = clock or _ist_now
        if not callable(resolved_clock):
            raise ValueError("workflow emitter clock must be callable")
        if (
            not isinstance(initial_sequence, int)
            or isinstance(initial_sequence, bool)
            or initial_sequence < 0
        ):
            raise ValueError(
                "workflow emitter initial sequence must be non-negative"
            )
        self.operation_id = operation_id.strip()
        self._sink = resolved_sink
        self._clock = resolved_clock
        self._sequence = initial_sequence
        self._lock = RLock()

    def emit(
        self,
        stage: WorkflowStage,
        state: WorkflowEventState,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        round_number: int | None = None,
        activity: WorkflowActivityDescriptor | None = None,
        message: str | None = None,
    ) -> JarvisWorkflowEvent:
        if not isinstance(stage, WorkflowStage):
            raise ValueError("workflow stage must be validated")
        if not isinstance(state, WorkflowEventState):
            raise ValueError("workflow event state must be validated")
        try:
            default_message = _STAGE_MESSAGES[(stage, state)]
        except KeyError as exc:
            raise ValueError(
                "workflow stage and state combination is unsupported"
            ) from exc
        resolved_activity = activity or _DEFAULT_ACTIVITIES[stage]
        if not isinstance(resolved_activity, WorkflowActivityDescriptor):
            raise ValueError("workflow event requires an activity descriptor")
        resolved_message = default_message if message is None else message.strip()
        if not resolved_message:
            raise ValueError("workflow event message must not be blank")
        with self._lock:
            self._sequence += 1
            event = JarvisWorkflowEvent(
                event_id=f"{self.operation_id}:{self._sequence}",
                operation_id=self.operation_id,
                sequence=self._sequence,
                stage=stage,
                state=state,
                occurred_at=self._clock(),
                message=resolved_message,
                activity=resolved_activity,
                exchange=exchange,
                symbol=symbol,
                round_number=round_number,
            )
            try:
                self._sink.publish(event)
            except Exception as exc:
                logger.error(
                    "Jarvis workflow event delivery failed",
                    extra={
                        "event": "jarvis.workflow_event.delivery_failed",
                        "workflow_stage": stage.value,
                        "workflow_sequence": self._sequence,
                        "error_type": type(exc).__name__,
                    },
                )
            return event


def _ist_now() -> datetime:
    return datetime.now(IST)
