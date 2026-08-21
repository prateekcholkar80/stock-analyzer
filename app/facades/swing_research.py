from datetime import datetime
from typing import Protocol, runtime_checkable

from app.logging_config import get_logger, operation_context
from app.models.interaction import (
    JarvisSwingAnalysisResponse,
    SwingAnalysisCommand,
)
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.workflow.events import WorkflowEventEmitter, WorkflowEventSink


@runtime_checkable
class SwingRequestResolver(Protocol):
    def execute(
        self,
        text: str,
        *,
        to_date: datetime | None = None,
    ) -> SwingAnalysisCommand:
        ...


@runtime_checkable
class SwingCommandHandler(Protocol):
    def execute(
        self,
        command: SwingAnalysisCommand,
        *,
        operation_id: str | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> JarvisSwingAnalysisResponse:
        ...


class JarvisSwingResearchFacade:
    """Route natural language through resolution and complete analysis."""

    def __init__(
        self,
        request_resolver: SwingRequestResolver,
        command_handler: SwingCommandHandler,
        event_sink: WorkflowEventSink | None = None,
    ) -> None:
        if not isinstance(request_resolver, SwingRequestResolver):
            raise ValueError(
                "Jarvis swing façade requires a request resolver"
            )
        if not isinstance(command_handler, SwingCommandHandler):
            raise ValueError(
                "Jarvis swing façade requires a command handler"
            )
        if event_sink is not None and not isinstance(
            event_sink,
            WorkflowEventSink,
        ):
            raise ValueError("Jarvis swing façade requires an event sink")
        self._request_resolver = request_resolver
        self._command_handler = command_handler
        self._event_sink = event_sink
        self._logger = get_logger("facades.swing_research")

    def execute(
        self,
        text: str,
        *,
        to_date: datetime | None = None,
    ) -> JarvisSwingAnalysisResponse:
        with operation_context() as operation_id:
            emitter = WorkflowEventEmitter(
                operation_id,
                self._event_sink,
            )
            emitter.emit(
                WorkflowStage.REQUEST_RECEIVED,
                WorkflowEventState.COMPLETED,
            )
            self._logger.info(
                "Jarvis swing research request received",
                extra={"event": "jarvis.swing_research.received"},
            )
            try:
                command = self._request_resolver.execute(
                    text,
                    to_date=to_date,
                )
            except Exception:
                emitter.emit(
                    WorkflowStage.FAILED,
                    WorkflowEventState.FAILED,
                )
                raise
            if not isinstance(command, SwingAnalysisCommand):
                emitter.emit(
                    WorkflowStage.FAILED,
                    WorkflowEventState.FAILED,
                )
                raise ValueError(
                    "swing request resolver returned an invalid command"
                )
            emitter.emit(
                WorkflowStage.INSTRUMENT_RESOLVED,
                WorkflowEventState.COMPLETED,
                exchange=command.exchange,
                symbol=command.symbol,
            )
            response = self._command_handler.execute(
                command,
                operation_id=operation_id,
                event_emitter=emitter,
            )
            if not isinstance(response, JarvisSwingAnalysisResponse):
                emitter.emit(
                    WorkflowStage.FAILED,
                    WorkflowEventState.FAILED,
                    exchange=command.exchange,
                    symbol=command.symbol,
                )
                raise ValueError(
                    "swing command handler returned an invalid response"
                )
            if response.operation_id != operation_id:
                emitter.emit(
                    WorkflowStage.FAILED,
                    WorkflowEventState.FAILED,
                    exchange=command.exchange,
                    symbol=command.symbol,
                )
                raise ValueError(
                    "swing command response operation ID does not match"
                )
            return response
