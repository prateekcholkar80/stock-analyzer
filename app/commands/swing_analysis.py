from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.exceptions import LLMError
from app.logging_config import get_logger, operation_context
from app.models.interaction import (
    JarvisSwingAnalysisResponse,
    SwingAnalysisCommand,
)
from app.models.storage import (
    EndToEndSwingAnalysisResult,
    MultiTimeframeEndToEndSwingAnalysisResult,
)
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.presentation.llm_failures import present_llm_failure
from app.workflow.events import WorkflowEventEmitter


@runtime_checkable
class SwingAnalysisExecutor(Protocol):
    """Narrow execution port implemented by the end-to-end use case."""

    def execute(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str = "ONE_HOUR",
        *,
        to_date: datetime | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> (
        EndToEndSwingAnalysisResult
        | MultiTimeframeEndToEndSwingAnalysisResult
    ):
        ...


SwingAnalysisExecutorFactory = Callable[[], SwingAnalysisExecutor]


class JarvisSwingAnalysisCommandHandler:
    """Execute one resolved swing request behind a UI-safe boundary."""

    def __init__(
        self,
        executor_factory: SwingAnalysisExecutorFactory,
    ) -> None:
        if not callable(executor_factory):
            raise ValueError(
                "swing-analysis command requires an executor factory"
            )
        self._executor_factory = executor_factory
        self._logger = get_logger("commands.swing_analysis")

    def execute(
        self,
        command: SwingAnalysisCommand,
        *,
        operation_id: str | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> JarvisSwingAnalysisResponse:
        if not isinstance(command, SwingAnalysisCommand):
            raise ValueError(
                "swing-analysis handler requires a validated command"
            )
        if operation_id is not None and (
            not isinstance(operation_id, str) or not operation_id.strip()
        ):
            raise ValueError("command operation ID must be a non-blank string")

        with operation_context(operation_id) as active_operation_id:
            active_emitter = (
                event_emitter
                if event_emitter is not None
                else WorkflowEventEmitter(active_operation_id)
            )
            if not isinstance(active_emitter, WorkflowEventEmitter):
                raise ValueError(
                    "command event emitter must be a workflow emitter"
                )
            if active_emitter.operation_id != active_operation_id:
                raise ValueError(
                    "command event emitter operation ID does not match"
                )
            try:
                executor = self._executor_factory()
                if not isinstance(executor, SwingAnalysisExecutor):
                    raise ValueError(
                        "swing-analysis factory returned an invalid executor"
                    )
                result = executor.execute(
                    exchange=command.exchange,
                    symbol_token=command.symbol_token,
                    symbol=command.symbol,
                    interval=command.interval,
                    to_date=command.to_date,
                    event_emitter=active_emitter,
                )
                if not isinstance(
                    result,
                    (
                        EndToEndSwingAnalysisResult,
                        MultiTimeframeEndToEndSwingAnalysisResult,
                    ),
                ):
                    raise ValueError(
                        "swing-analysis executor returned an invalid result"
                    )
            except LLMError as error:
                active_emitter.emit(
                    WorkflowStage.FAILED,
                    WorkflowEventState.FAILED,
                    exchange=command.exchange,
                    symbol=command.symbol,
                )
                failure = present_llm_failure(
                    error,
                    operation_id=active_operation_id,
                )
                self._logger.warning(
                    "Jarvis swing-analysis LLM workflow failed",
                    extra={
                        "event": "jarvis.swing_analysis.llm_failed",
                        "failure_code": failure.code.value,
                        "failed_role": (
                            failure.failed_role.value
                            if failure.failed_role is not None
                            else None
                        ),
                        "retryable": failure.retryable,
                    },
                )
                return JarvisSwingAnalysisResponse.llm_failure(
                    operation_id=active_operation_id,
                    failure=failure,
                )
            except Exception:
                active_emitter.emit(
                    WorkflowStage.FAILED,
                    WorkflowEventState.FAILED,
                    exchange=command.exchange,
                    symbol=command.symbol,
                )
                raise

            active_emitter.emit(
                WorkflowStage.COMPLETED,
                WorkflowEventState.COMPLETED,
                exchange=command.exchange,
                symbol=command.symbol,
            )
            self._logger.info(
                "Jarvis swing-analysis workflow completed",
                extra={
                    "event": "jarvis.swing_analysis.completed",
                    "exchange": command.exchange,
                    "symbol": command.symbol,
                    "interval": command.interval,
                },
            )
            response_context = {}
            if isinstance(
                result,
                MultiTimeframeEndToEndSwingAnalysisResult,
            ):
                response_context = {
                    "multi_timeframe_review": result.technical_review,
                    "multi_timeframe_debate": result.debate_result,
                }
            return JarvisSwingAnalysisResponse.completed(
                operation_id=active_operation_id,
                result=result,
                **response_context,
            )
