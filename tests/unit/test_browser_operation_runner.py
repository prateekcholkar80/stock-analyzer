import threading
import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.exceptions import IntentRecognitionError, MarketDataError
from app.models.browser_operations import (
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    WorkflowEventReplayCursor,
)
from app.models.conversation import InputChannel
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.llm import JarvisLLMFailureResponse, LLMFailureCode
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.workflow.browser_runner import (
    AsyncBrowserOperationRunner,
    BrowserOperationHandledFailure,
    InMemoryBrowserOperationResultStore,
    JarvisBrowserOperationHandler,
)
from app.workflow.operations import InMemoryBrowserOperationRegistry
from tests.unit.test_jarvis_conversation import (
    RecordingJudgeFollowUpExecutor,
    RecordingPresenter,
    _completed_response,
    _multi_timeframe_response,
)


IST = ZoneInfo("Asia/Kolkata")


def _now():
    return datetime.now(IST)


def _request(
    operation_id="operation-1",
    *,
    kind=BrowserOperationKind.SWING_ANALYSIS,
    idempotency_key="request-1",
    message="Analyze Reliance for me",
):
    return BrowserOperationRequest(
        operation_id=operation_id,
        session_id="session-1",
        idempotency_key=idempotency_key,
        kind=kind,
        input_channel=InputChannel.TEXT,
        message=message,
        requested_at=_now(),
    )


def _wait_for_terminal(runner, operation_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = runner.get_operation(operation_id)
        if snapshot is not None and snapshot.status.terminal:
            return snapshot
        time.sleep(0.005)
    raise AssertionError("browser operation did not become terminal")


class _ResearchExecutor:
    def __init__(self, response=None, *, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def execute(
        self,
        text,
        *,
        operation_id=None,
        event_emitter=None,
        manage_terminal_events=True,
    ):
        self.calls.append(
            (text, operation_id, event_emitter, manage_terminal_events)
        )
        if self.failure is not None:
            raise self.failure
        return self.response


class _SimpleHandler:
    def __init__(self, *, failure=None, blocking=False):
        self.failure = failure
        self.blocking = blocking
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request, *, event_emitter, cancellation_token):
        self.calls += 1
        self.started.set()
        if self.blocking:
            self.release.wait(timeout=2)
        if self.failure is not None:
            raise self.failure
        event_emitter.emit(
            WorkflowStage.REQUEST_RECEIVED,
            WorkflowEventState.COMPLETED,
        )
        return BrowserOperationOutput.model_construct(
            operation_id=request.operation_id,
            session_id=request.session_id,
            kind=request.kind,
            completed_at=_now(),
        )


class BrowserOperationOutputTests(unittest.TestCase):
    def test_requires_kind_specific_payload(self):
        response = _completed_response()
        explanation = RecordingPresenter().explain(
            response.result,
            user_name="Prateek",
        )
        output = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=_now(),
            research_response=response,
            research_explanation=explanation,
        )

        self.assertIs(output.research_response, response)
        with self.assertRaisesRegex(ValueError, "either an explanation"):
            BrowserOperationOutput(
                operation_id="operation-1",
                session_id="session-1",
                kind=BrowserOperationKind.SWING_ANALYSIS,
                completed_at=_now(),
                research_response=response,
                research_explanation=explanation,
                presentation_failure=BrowserOperationFailure(
                    code="presentation.unavailable",
                    message="Unavailable",
                ),
            )


class JarvisBrowserOperationHandlerTests(unittest.TestCase):
    def test_intent_failure_becomes_candid_safe_browser_failure(self):
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(
                failure=IntentRecognitionError("private parser detail")
            ),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        with self.assertRaises(BrowserOperationHandledFailure) as captured:
            handler.execute(
                _request(),
                event_emitter=WorkflowEventEmitter(
                    "operation-1",
                    InMemoryWorkflowEventSink(),
                ),
                cancellation_token=BrowserCancellationToken(),
            )

        failure = captured.exception.failure
        self.assertEqual(failure.code, "request.intent_unrecognized")
        self.assertIn("Analyse TCS for me", failure.message)
        self.assertNotIn("private parser detail", failure.message)

    def test_llm_failure_becomes_safe_browser_failure(self):
        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-1",
            failure=JarvisLLMFailureResponse(
                code=LLMFailureCode.PROVIDER_UNAVAILABLE,
                title="The panel is unavailable",
                display_message="The debate panel is taking an unscheduled tea break.",
                spoken_message="The panel is unavailable.",
                recovery_action="Please retry shortly.",
                retryable=True,
                operation_id="operation-1",
            ),
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        with self.assertRaises(BrowserOperationHandledFailure) as captured:
            handler.execute(
                _request(),
                event_emitter=WorkflowEventEmitter(
                    "operation-1",
                    InMemoryWorkflowEventSink(),
                ),
                cancellation_token=BrowserCancellationToken(),
            )

        self.assertEqual(
            captured.exception.failure.code,
            "llm.provider_unavailable",
        )
        self.assertTrue(captured.exception.failure.retryable)

    def test_analysis_uses_one_operation_and_presents_result(self):
        research = _ResearchExecutor(_completed_response())
        presenter = RecordingPresenter()
        handler = JarvisBrowserOperationHandler(
            research,
            presenter,
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        registry = InMemoryBrowserOperationRegistry()
        at = _now()
        registry.open_session(
            BrowserSessionSnapshot(
                session_id="session-1",
                created_at=at,
                updated_at=at,
            )
        )
        runner = AsyncBrowserOperationRunner(
            registry,
            handler,
            InMemoryBrowserOperationResultStore(),
            max_workers=1,
        )
        try:
            runner.submit(_request())
            terminal = _wait_for_terminal(runner, "operation-1")
            output = runner.get_result("operation-1")
        finally:
            runner.shutdown()

        self.assertIs(terminal.status, BrowserOperationStatus.COMPLETED)
        self.assertIsNotNone(output.research_explanation)
        self.assertEqual(research.calls[0][1], "operation-1")
        self.assertFalse(research.calls[0][3])
        stages = [
            event.stage
            for event in registry.replay(
                WorkflowEventReplayCursor(operation_id="operation-1")
            ).events
        ]
        self.assertEqual(
            stages,
            [
                WorkflowStage.PRESENTATION,
                WorkflowStage.PRESENTATION,
                WorkflowStage.COMPLETED,
            ],
        )

    def test_presentation_failure_preserves_validated_analysis(self):
        response = _completed_response()
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(failure=MarketDataError("private detail")),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        sink = InMemoryWorkflowEventSink()
        output = handler.execute(
            _request(),
            event_emitter=WorkflowEventEmitter("operation-1", sink),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertIs(output.research_response, response)
        self.assertEqual(
            output.presentation_failure.code,
            "presentation.unavailable",
        )
        self.assertNotIn("private detail", output.presentation_failure.message)

    def test_follow_up_reuses_only_approved_session_context(self):
        response = _multi_timeframe_response().model_copy(
            update={"operation_id": "operation-1"}
        )
        follow_up = RecordingJudgeFollowUpExecutor()
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(),
            follow_up,
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        sink = InMemoryWorkflowEventSink()
        handler.execute(
            _request(),
            event_emitter=WorkflowEventEmitter("operation-1", sink),
            cancellation_token=BrowserCancellationToken(),
        )
        answer = handler.execute(
            _request(
                "operation-2",
                kind=BrowserOperationKind.JUDGE_FOLLOW_UP,
                idempotency_key="request-2",
                message="Where is weekly support?",
            ),
            event_emitter=WorkflowEventEmitter("operation-2", sink),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(answer.judge_follow_up.question, "Where is weekly support?")
        self.assertEqual(len(follow_up.calls), 1)

    def test_follow_up_without_approved_analysis_fails_candidly(self):
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        with self.assertRaises(BrowserOperationHandledFailure) as captured:
            handler.execute(
                _request(
                    kind=BrowserOperationKind.JUDGE_FOLLOW_UP,
                    message="Where is weekly support?",
                ),
                event_emitter=WorkflowEventEmitter(
                    "operation-1",
                    InMemoryWorkflowEventSink(),
                ),
                cancellation_token=BrowserCancellationToken(),
            )

        self.assertEqual(
            captured.exception.failure.code,
            "conversation.context_unavailable",
        )


class AsyncBrowserOperationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.registry = InMemoryBrowserOperationRegistry()
        at = _now()
        self.registry.open_session(
            BrowserSessionSnapshot(
                session_id="session-1",
                created_at=at,
                updated_at=at,
            )
        )
        self.results = InMemoryBrowserOperationResultStore()

    def test_idempotent_submit_executes_once_and_stores_result(self):
        handler = _SimpleHandler()
        runner = AsyncBrowserOperationRunner(
            self.registry,
            handler,
            self.results,
            max_workers=1,
        )
        request = _request()
        try:
            runner.submit(request)
            runner.submit(request)
            terminal = _wait_for_terminal(runner, "operation-1")
        finally:
            runner.shutdown()

        self.assertEqual(handler.calls, 1)
        self.assertIs(terminal.status, BrowserOperationStatus.COMPLETED)
        self.assertTrue(terminal.result_available)
        self.assertIsNotNone(self.results.get("operation-1"))
        events = self.registry.replay(
            WorkflowEventReplayCursor(operation_id="operation-1")
        ).events
        self.assertEqual([event.sequence for event in events], [1, 2])
        self.assertIs(events[-1].stage, WorkflowStage.COMPLETED)

    def test_running_operation_cancels_cooperatively_without_result(self):
        handler = _SimpleHandler(blocking=True)
        runner = AsyncBrowserOperationRunner(
            self.registry,
            handler,
            self.results,
            max_workers=1,
        )
        try:
            runner.submit(_request())
            self.assertTrue(handler.started.wait(timeout=1))
            requested = runner.cancel("operation-1")
            handler.release.set()
            terminal = _wait_for_terminal(runner, "operation-1")
        finally:
            runner.shutdown()

        self.assertIs(
            requested.status,
            BrowserOperationStatus.CANCELLATION_REQUESTED,
        )
        self.assertIs(terminal.status, BrowserOperationStatus.CANCELLED)
        self.assertIsNone(self.results.get("operation-1"))

    def test_safe_failure_is_recorded_and_internal_details_are_hidden(self):
        handler = _SimpleHandler(
            failure=BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="market.unavailable",
                    message="Market history is unavailable.",
                    retryable=True,
                )
            )
        )
        runner = AsyncBrowserOperationRunner(
            self.registry,
            handler,
            self.results,
            max_workers=1,
        )
        try:
            runner.submit(_request())
            terminal = _wait_for_terminal(runner, "operation-1")
        finally:
            runner.shutdown()

        self.assertIs(terminal.status, BrowserOperationStatus.FAILED)
        self.assertEqual(terminal.failure.code, "market.unavailable")
        events = self.registry.replay(
            WorkflowEventReplayCursor(operation_id="operation-1")
        ).events
        self.assertIs(events[-1].stage, WorkflowStage.FAILED)


if __name__ == "__main__":
    unittest.main()
