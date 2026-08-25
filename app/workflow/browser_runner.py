from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Event, RLock
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from app.exceptions import (
    AmbiguousInstrumentError,
    ApplicationError,
    BrowserOperationConflictError,
    InstrumentMasterDownloadError,
    InstrumentNotFoundError,
    InstrumentResolutionError,
    IntentRecognitionError,
    LLMError,
    MarketDataError,
)
from app.logging_config import get_logger
from app.models.browser_operations import (
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    WorkflowEventBatch,
    WorkflowEventReplayCursor,
)
from app.models.debate import AgenticDebateResult, JudgeFollowUpAnswer
from app.models.interaction import (
    JarvisCommandStatus,
    JarvisSwingAnalysisResponse,
)
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
from app.models.presentation import (
    JarvisMultiTimeframeResearchExplanation,
    JarvisResearchExplanation,
)
from app.models.storage import (
    EndToEndSwingAnalysisResult,
    MultiTimeframeEndToEndSwingAnalysisResult,
)
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.workflow.events import WorkflowEventEmitter
from app.workflow.operations import BrowserOperationRegistry


IST = ZoneInfo("Asia/Kolkata")
RunnerClock = Callable[[], datetime]
logger = get_logger("workflow.browser_runner")


@runtime_checkable
class BrowserOperationResultStore(Protocol):
    def save(self, output: BrowserOperationOutput) -> None:
        ...

    def get(self, operation_id: str) -> BrowserOperationOutput | None:
        ...


class InMemoryBrowserOperationResultStore:
    """Thread-safe immutable result store for local browser execution."""

    def __init__(self) -> None:
        self._outputs: dict[str, BrowserOperationOutput] = {}
        self._lock = RLock()

    def save(self, output: BrowserOperationOutput) -> None:
        if not isinstance(output, BrowserOperationOutput):
            raise ValueError("browser result store requires validated output")
        with self._lock:
            existing = self._outputs.get(output.operation_id)
            if existing is not None and existing != output:
                raise BrowserOperationConflictError(
                    "browser operation result cannot be rewritten"
                )
            self._outputs[output.operation_id] = output

    def get(self, operation_id: str) -> BrowserOperationOutput | None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("browser operation ID must not be blank")
        with self._lock:
            return self._outputs.get(operation_id.strip())


class BrowserCancellationToken:
    """Cooperative cancellation checked between expensive workflow phases."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancellation_requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()

    def raise_if_requested(self) -> None:
        if self.cancellation_requested:
            raise BrowserOperationCancelled()


class BrowserOperationCancelled(Exception):
    """Private control-flow signal; never serialized to the browser."""


class BrowserOperationHandledFailure(Exception):
    """Carry an explicitly safe failure from a workflow boundary."""

    def __init__(self, failure: BrowserOperationFailure) -> None:
        if not isinstance(failure, BrowserOperationFailure):
            raise ValueError("handled browser failure must be validated")
        super().__init__(failure.message)
        self.failure = failure


@runtime_checkable
class BrowserOperationHandler(Protocol):
    def execute(
        self,
        request: BrowserOperationRequest,
        *,
        event_emitter: WorkflowEventEmitter,
        cancellation_token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        ...


@runtime_checkable
class BrowserSwingResearchExecutor(Protocol):
    def execute(
        self,
        text: str,
        *,
        operation_id: str | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
        manage_terminal_events: bool = True,
    ) -> JarvisSwingAnalysisResponse:
        ...


@runtime_checkable
class BrowserResearchPresenter(Protocol):
    def explain(
        self,
        result: (
            EndToEndSwingAnalysisResult
            | MultiTimeframeEndToEndSwingAnalysisResult
        ),
        *,
        user_name: str,
    ) -> JarvisResearchExplanation | JarvisMultiTimeframeResearchExplanation:
        ...


@runtime_checkable
class BrowserJudgeFollowUpExecutor(Protocol):
    def execute(
        self,
        question: str,
        *,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> JudgeFollowUpAnswer:
        ...


@runtime_checkable
class BrowserResearchContextStore(Protocol):
    def replace(
        self,
        session_id: str,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> None:
        ...

    def clear(self, session_id: str) -> None:
        ...

    def get(
        self,
        session_id: str,
    ) -> tuple[MultiTimeframeEvidenceReview, AgenticDebateResult] | None:
        ...


class InMemoryBrowserResearchContextStore:
    """Retain only Judge-approved evidence needed by later session queries."""

    def __init__(self) -> None:
        self._contexts: dict[
            str,
            tuple[MultiTimeframeEvidenceReview, AgenticDebateResult],
        ] = {}
        self._lock = RLock()

    def replace(
        self,
        session_id: str,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> None:
        if not isinstance(technical_review, MultiTimeframeEvidenceReview):
            raise ValueError("browser context requires a technical review")
        if not isinstance(debate_result, AgenticDebateResult):
            raise ValueError("browser context requires a debate result")
        with self._lock:
            self._contexts[_required_text("session ID", session_id)] = (
                technical_review,
                debate_result,
            )

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._contexts.pop(_required_text("session ID", session_id), None)

    def get(
        self,
        session_id: str,
    ) -> tuple[MultiTimeframeEvidenceReview, AgenticDebateResult] | None:
        with self._lock:
            return self._contexts.get(_required_text("session ID", session_id))


class JarvisBrowserOperationHandler:
    """Run research, presentation, and grounded follow-ups under one contract."""

    def __init__(
        self,
        research_executor: BrowserSwingResearchExecutor,
        presenter: BrowserResearchPresenter,
        follow_up_executor: BrowserJudgeFollowUpExecutor,
        *,
        user_name: str,
        context_store: BrowserResearchContextStore | None = None,
        clock: RunnerClock | None = None,
    ) -> None:
        if not isinstance(research_executor, BrowserSwingResearchExecutor):
            raise ValueError("browser handler requires a research executor")
        if not isinstance(presenter, BrowserResearchPresenter):
            raise ValueError("browser handler requires a research presenter")
        if not isinstance(follow_up_executor, BrowserJudgeFollowUpExecutor):
            raise ValueError("browser handler requires a follow-up executor")
        resolved_context_store = (
            context_store
            if context_store is not None
            else InMemoryBrowserResearchContextStore()
        )
        if not isinstance(resolved_context_store, BrowserResearchContextStore):
            raise ValueError("browser handler requires a research context store")
        self._research_executor = research_executor
        self._presenter = presenter
        self._follow_up_executor = follow_up_executor
        self._user_name = _required_text("user name", user_name)
        self._contexts = resolved_context_store
        self._clock = clock or _ist_now

    def execute(
        self,
        request: BrowserOperationRequest,
        *,
        event_emitter: WorkflowEventEmitter,
        cancellation_token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        if not isinstance(request, BrowserOperationRequest):
            raise ValueError("browser handler requires a validated request")
        if not isinstance(event_emitter, WorkflowEventEmitter):
            raise ValueError("browser handler requires an event emitter")
        if event_emitter.operation_id != request.operation_id:
            raise ValueError("browser handler emitter operation ID must match")
        if not isinstance(cancellation_token, BrowserCancellationToken):
            raise ValueError("browser handler requires a cancellation token")
        cancellation_token.raise_if_requested()
        if request.kind is BrowserOperationKind.SWING_ANALYSIS:
            return self._execute_analysis(
                request,
                event_emitter,
                cancellation_token,
            )
        return self._execute_follow_up(
            request,
            event_emitter,
            cancellation_token,
        )

    def _execute_analysis(
        self,
        request: BrowserOperationRequest,
        emitter: WorkflowEventEmitter,
        token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        self._contexts.clear(request.session_id)
        try:
            response = self._research_executor.execute(
                request.message,
                operation_id=request.operation_id,
                event_emitter=emitter,
                manage_terminal_events=False,
            )
        except ApplicationError as exc:
            raise BrowserOperationHandledFailure(
                _safe_research_failure(exc)
            ) from exc
        if not isinstance(response, JarvisSwingAnalysisResponse):
            raise ValueError("research executor returned an invalid response")
        if response.operation_id != request.operation_id:
            raise ValueError("research response operation ID must match")
        if response.status is JarvisCommandStatus.LLM_FAILURE:
            failure = response.failure
            if failure is None:
                raise ValueError("LLM failure response is missing its payload")
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code=f"llm.{failure.code.value}",
                    message=failure.display_message,
                    retryable=failure.retryable,
                )
            )
        if response.result is None:
            raise ValueError("completed research response is missing analysis")
        approved_context = None
        if (
            response.multi_timeframe_review is not None
            and response.multi_timeframe_debate is not None
        ):
            approved_context = (
                response.multi_timeframe_review,
                response.multi_timeframe_debate,
            )
        token.raise_if_requested()
        emitter.emit(WorkflowStage.PRESENTATION, WorkflowEventState.STARTED)
        try:
            explanation = self._presenter.explain(
                response.result,
                user_name=self._user_name,
            )
        except ApplicationError as exc:
            token.raise_if_requested()
            emitter.emit(WorkflowStage.PRESENTATION, WorkflowEventState.FAILED)
            if approved_context is not None:
                self._contexts.replace(request.session_id, *approved_context)
            return BrowserOperationOutput(
                operation_id=request.operation_id,
                session_id=request.session_id,
                kind=request.kind,
                completed_at=self._clock(),
                research_response=response,
                presentation_failure=BrowserOperationFailure(
                    code="presentation.unavailable",
                    message=(
                        "The analysis is complete, but Jarvis could not prepare "
                        "the narrated briefing. The validated result remains "
                        "available."
                    ),
                    retryable=(
                        isinstance(exc, LLMError) and exc.context.retryable
                    ),
                ),
            )
        if not isinstance(
            explanation,
            (JarvisResearchExplanation, JarvisMultiTimeframeResearchExplanation),
        ):
            raise ValueError("research presenter returned an invalid explanation")
        token.raise_if_requested()
        emitter.emit(WorkflowStage.PRESENTATION, WorkflowEventState.COMPLETED)
        if approved_context is not None:
            self._contexts.replace(request.session_id, *approved_context)
        return BrowserOperationOutput(
            operation_id=request.operation_id,
            session_id=request.session_id,
            kind=request.kind,
            completed_at=self._clock(),
            research_response=response,
            research_explanation=explanation,
        )

    def _execute_follow_up(
        self,
        request: BrowserOperationRequest,
        emitter: WorkflowEventEmitter,
        token: BrowserCancellationToken,
    ) -> BrowserOperationOutput:
        context = self._contexts.get(request.session_id)
        if context is None:
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="conversation.context_unavailable",
                    message=(
                        "I need a completed multi-timeframe analysis before "
                        "the Judge can answer that follow-up."
                    ),
                    retryable=False,
                )
            )
        emitter.emit(WorkflowStage.FOLLOW_UP, WorkflowEventState.STARTED)
        token.raise_if_requested()
        try:
            answer = self._follow_up_executor.execute(
                request.message,
                technical_review=context[0],
                debate_result=context[1],
            )
        except ApplicationError as exc:
            emitter.emit(WorkflowStage.FOLLOW_UP, WorkflowEventState.FAILED)
            raise BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="follow_up.unavailable",
                    message=(
                        "The Judge could not answer that grounded follow-up "
                        "just now. The original analysis is unchanged."
                    ),
                    retryable=(
                        isinstance(exc, LLMError) and exc.context.retryable
                    ),
                )
            )
        if not isinstance(answer, JudgeFollowUpAnswer):
            raise ValueError("follow-up executor returned an invalid answer")
        token.raise_if_requested()
        emitter.emit(WorkflowStage.FOLLOW_UP, WorkflowEventState.COMPLETED)
        return BrowserOperationOutput(
            operation_id=request.operation_id,
            session_id=request.session_id,
            kind=request.kind,
            completed_at=self._clock(),
            judge_follow_up=answer,
        )


class AsyncBrowserOperationRunner:
    """Bounded background runner behind future HTTP/WebSocket adapters."""

    def __init__(
        self,
        registry: BrowserOperationRegistry,
        handler: BrowserOperationHandler,
        result_store: BrowserOperationResultStore,
        *,
        max_workers: int = 2,
        clock: RunnerClock | None = None,
    ) -> None:
        if not isinstance(registry, BrowserOperationRegistry):
            raise ValueError("browser runner requires an operation registry")
        if not isinstance(handler, BrowserOperationHandler):
            raise ValueError("browser runner requires an operation handler")
        if not isinstance(result_store, BrowserOperationResultStore):
            raise ValueError("browser runner requires a result store")
        if not isinstance(max_workers, int) or isinstance(max_workers, bool):
            raise ValueError("browser runner worker count must be an integer")
        if max_workers < 1 or max_workers > 16:
            raise ValueError("browser runner worker count must be between 1 and 16")
        self._registry = registry
        self._handler = handler
        self._results = result_store
        self._clock = clock or _ist_now
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="jarvis-browser",
        )
        self._futures: dict[str, Future[None]] = {}
        self._tokens: dict[str, BrowserCancellationToken] = {}
        self._lock = RLock()

    def submit(self, request: BrowserOperationRequest) -> BrowserOperationSnapshot:
        snapshot = self._registry.submit(request)
        with self._lock:
            if (
                snapshot.status is BrowserOperationStatus.QUEUED
                and snapshot.request.operation_id not in self._futures
            ):
                operation_id = snapshot.request.operation_id
                token = BrowserCancellationToken()
                self._tokens[operation_id] = token
                self._futures[operation_id] = self._executor.submit(
                    self._run,
                    operation_id,
                    token,
                )
        return snapshot

    def open_session(
        self,
        session: BrowserSessionSnapshot,
    ) -> BrowserSessionSnapshot:
        return self._registry.open_session(session)

    def get_session(self, session_id: str) -> BrowserSessionSnapshot | None:
        return self._registry.get_session(session_id)

    def close_session(
        self,
        session_id: str,
        *,
        closed_at: datetime,
    ) -> BrowserSessionSnapshot:
        return self._registry.close_session(session_id, closed_at=closed_at)

    def cancel(self, operation_id: str) -> BrowserOperationSnapshot:
        snapshot = self._registry.request_cancellation(
            operation_id,
            at=self._clock(),
        )
        with self._lock:
            token = self._tokens.get(operation_id)
            future = self._futures.get(operation_id)
            if token is not None:
                token.request()
            if (
                future is not None
                and snapshot.status is BrowserOperationStatus.CANCELLED
            ):
                future.cancel()
        return snapshot

    def get_operation(self, operation_id: str) -> BrowserOperationSnapshot | None:
        return self._registry.get_operation(operation_id)

    def get_result(self, operation_id: str) -> BrowserOperationOutput | None:
        return self._results.get(operation_id)

    def replay(self, cursor: WorkflowEventReplayCursor) -> WorkflowEventBatch:
        return self._registry.replay(cursor)

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def _run(
        self,
        operation_id: str,
        token: BrowserCancellationToken,
    ) -> None:
        try:
            snapshot = self._registry.get_operation(operation_id)
            if snapshot is None or snapshot.status is not BrowserOperationStatus.QUEUED:
                return
            self._registry.mark_running(operation_id, at=self._clock())
            token.raise_if_requested()
            emitter = WorkflowEventEmitter(
                operation_id,
                self._registry,
                clock=self._clock,
            )
            output = self._handler.execute(
                snapshot.request,
                event_emitter=emitter,
                cancellation_token=token,
            )
            if not isinstance(output, BrowserOperationOutput):
                raise ValueError("browser handler returned an invalid output")
            if (
                output.operation_id != operation_id
                or output.session_id != snapshot.request.session_id
                or output.kind is not snapshot.request.kind
            ):
                raise ValueError("browser handler output identity must match request")
            token.raise_if_requested()
            self._results.save(output)
            emitter.emit(WorkflowStage.COMPLETED, WorkflowEventState.COMPLETED)
            self._registry.mark_completed(operation_id, at=self._clock())
            logger.info(
                "Browser operation completed",
                extra={
                    "event": "jarvis.browser_operation.completed",
                    "browser_operation_id": operation_id,
                },
            )
        except BrowserOperationCancelled:
            current = self._registry.get_operation(operation_id)
            if (
                current is not None
                and current.status is BrowserOperationStatus.CANCELLATION_REQUESTED
            ):
                self._registry.mark_cancelled(operation_id, at=self._clock())
                logger.info(
                    "Browser operation cancelled",
                    extra={
                        "event": "jarvis.browser_operation.cancelled",
                        "browser_operation_id": operation_id,
                    },
                )
        except BrowserOperationHandledFailure as exc:
            logger.warning(
                "Browser operation stopped with a safe failure",
                extra={
                    "event": "jarvis.browser_operation.failed",
                    "browser_operation_id": operation_id,
                    "failure_code": exc.failure.code,
                    "retryable": exc.failure.retryable,
                },
            )
            self._fail(operation_id, exc.failure)
        except Exception as exc:
            logger.error(
                "Browser operation stopped unexpectedly",
                extra={
                    "event": "jarvis.browser_operation.unexpected_failure",
                    "browser_operation_id": operation_id,
                    "error_type": type(exc).__name__,
                },
            )
            self._fail(
                operation_id,
                BrowserOperationFailure(
                    code="workflow.unexpected",
                    message=(
                        "Jarvis encountered an unexpected workflow error. "
                        "No unverified conclusion was produced."
                    ),
                    retryable=False,
                ),
            )
        finally:
            with self._lock:
                self._tokens.pop(operation_id, None)

    def _fail(
        self,
        operation_id: str,
        failure: BrowserOperationFailure,
    ) -> None:
        current = self._registry.get_operation(operation_id)
        if current is None or current.status.terminal:
            return
        emitter = WorkflowEventEmitter(
            operation_id,
            self._registry,
            clock=self._clock,
            initial_sequence=current.last_event_sequence,
        )
        emitter.emit(WorkflowStage.FAILED, WorkflowEventState.FAILED)
        self._registry.mark_failed(operation_id, failure, at=self._clock())


def _required_text(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"browser {label} must not be blank")
    return value.strip()


def _safe_research_failure(exc: ApplicationError) -> BrowserOperationFailure:
    """Translate expected research failures without exposing provider details."""
    if isinstance(exc, IntentRecognitionError):
        return BrowserOperationFailure(
            code="request.intent_unrecognized",
            message=(
                "I could not identify one company to analyse. Try, for "
                "example, 'Analyse TCS for me'."
            ),
            retryable=False,
        )
    if isinstance(exc, InstrumentNotFoundError):
        return BrowserOperationFailure(
            code="instrument.not_found",
            message=(
                "I could not match that company to one NSE cash-market "
                "instrument. Please use its company name or trading symbol."
            ),
            retryable=False,
        )
    if isinstance(exc, AmbiguousInstrumentError):
        return BrowserOperationFailure(
            code="instrument.ambiguous",
            message=(
                "That name matches more than one instrument. Please use the "
                "exact NSE trading symbol."
            ),
            retryable=False,
        )
    if isinstance(exc, InstrumentResolutionError):
        return BrowserOperationFailure(
            code="instrument.catalog_unavailable",
            message=(
                "The NSE instrument catalogue is unavailable just now. "
                "Jarvis will not guess the symbol token."
            ),
            retryable=isinstance(exc, InstrumentMasterDownloadError),
        )
    if isinstance(exc, MarketDataError):
        return BrowserOperationFailure(
            code="market.unavailable",
            message=(
                "Angel One could not provide the required market history. "
                "No analysis was fabricated; please try again shortly."
            ),
            retryable=True,
        )
    return BrowserOperationFailure(
        code="workflow.unavailable",
        message=(
            "Jarvis could not complete the validated research workflow. "
            "No unverified conclusion was produced."
        ),
        retryable=False,
    )


def _ist_now() -> datetime:
    return datetime.now(IST)
