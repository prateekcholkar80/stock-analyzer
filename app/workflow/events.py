from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from app.logging_config import get_logger
from app.models.workflow import (
    JarvisWorkflowEvent,
    WorkflowEventState,
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
    (WorkflowStage.COMPLETED, WorkflowEventState.COMPLETED): (
        "Jarvis completed the swing-research workflow."
    ),
    (WorkflowStage.FAILED, WorkflowEventState.FAILED): (
        "Jarvis stopped the swing-research workflow without a conclusion."
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
    ) -> None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("workflow emitter operation ID must not be blank")
        resolved_sink = sink if sink is not None else NullWorkflowEventSink()
        if not isinstance(resolved_sink, WorkflowEventSink):
            raise ValueError("workflow emitter requires an event sink")
        resolved_clock = clock or _ist_now
        if not callable(resolved_clock):
            raise ValueError("workflow emitter clock must be callable")
        self.operation_id = operation_id.strip()
        self._sink = resolved_sink
        self._clock = resolved_clock
        self._sequence = 0
        self._lock = RLock()

    def emit(
        self,
        stage: WorkflowStage,
        state: WorkflowEventState,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        round_number: int | None = None,
    ) -> JarvisWorkflowEvent:
        if not isinstance(stage, WorkflowStage):
            raise ValueError("workflow stage must be validated")
        if not isinstance(state, WorkflowEventState):
            raise ValueError("workflow event state must be validated")
        try:
            message = _STAGE_MESSAGES[(stage, state)]
        except KeyError as exc:
            raise ValueError(
                "workflow stage and state combination is unsupported"
            ) from exc
        with self._lock:
            self._sequence += 1
            event = JarvisWorkflowEvent(
                event_id=f"{self.operation_id}:{self._sequence}",
                operation_id=self.operation_id,
                sequence=self._sequence,
                stage=stage,
                state=state,
                occurred_at=self._clock(),
                message=message,
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
