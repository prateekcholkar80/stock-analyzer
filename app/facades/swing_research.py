from datetime import datetime
from collections.abc import Callable
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
        emit_terminal_event: bool = True,
    ) -> JarvisSwingAnalysisResponse:
        ...


class JarvisSwingResearchFacade:
    """Route natural language through resolution and complete analysis."""

    def __init__(
        self,
        request_resolver: SwingRequestResolver,
        command_handler: SwingCommandHandler,
        event_sink: WorkflowEventSink | None = None,
        judge_follow_up_executor=None,
        ticker_resolution_executor=None,
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
        self._judge_follow_up_executor = judge_follow_up_executor
        self._ticker_resolution_executor = ticker_resolution_executor
        self._logger = get_logger("facades.swing_research")

    @property
    def judge_follow_up_executor(self):
        return self._judge_follow_up_executor

    @property
    def ticker_resolution_executor(self):
        return self._ticker_resolution_executor

    def execute(
        self,
        text: str,
        *,
        to_date: datetime | None = None,
        operation_id: str | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
        manage_terminal_events: bool = True,
        instrument_resolved_callback: (
            Callable[[SwingAnalysisCommand], None] | None
        ) = None,
    ) -> JarvisSwingAnalysisResponse:
        if not isinstance(manage_terminal_events, bool):
            raise ValueError("terminal-event policy must be a boolean")
        if instrument_resolved_callback is not None and not callable(
            instrument_resolved_callback
        ):
            raise ValueError(
                "instrument-resolved callback must be callable"
            )
        with operation_context(operation_id) as active_operation_id:
            emitter = (
                event_emitter
                if event_emitter is not None
                else WorkflowEventEmitter(
                    active_operation_id,
                    self._event_sink,
                )
            )
            if not isinstance(emitter, WorkflowEventEmitter):
                raise ValueError("research façade requires a workflow emitter")
            if emitter.operation_id != active_operation_id:
                raise ValueError(
                    "research emitter operation ID does not match"
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
                if manage_terminal_events:
                    emitter.emit(
                        WorkflowStage.FAILED,
                        WorkflowEventState.FAILED,
                    )
                raise
            if not isinstance(command, SwingAnalysisCommand):
                if manage_terminal_events:
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
            if instrument_resolved_callback is not None:
                try:
                    instrument_resolved_callback(command)
                except Exception:
                    if manage_terminal_events:
                        emitter.emit(
                            WorkflowStage.FAILED,
                            WorkflowEventState.FAILED,
                            exchange=command.exchange,
                            symbol=command.symbol,
                        )
                    raise
            command_kwargs = {
                "operation_id": active_operation_id,
                "event_emitter": emitter,
            }
            if not manage_terminal_events:
                command_kwargs["emit_terminal_event"] = False
            response = self._command_handler.execute(command, **command_kwargs)
            if not isinstance(response, JarvisSwingAnalysisResponse):
                if manage_terminal_events:
                    emitter.emit(
                        WorkflowStage.FAILED,
                        WorkflowEventState.FAILED,
                        exchange=command.exchange,
                        symbol=command.symbol,
                    )
                raise ValueError(
                    "swing command handler returned an invalid response"
                )
            if response.operation_id != active_operation_id:
                if manage_terminal_events:
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
