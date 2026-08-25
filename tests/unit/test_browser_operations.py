import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.exceptions import (
    BrowserOperationConflictError,
    BrowserOperationNotFoundError,
    BrowserSessionNotFoundError,
)
from app.models.browser_operations import (
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    BrowserSessionState,
    WorkflowEventReplayCursor,
)
from app.models.conversation import InputChannel
from app.models.workflow import (
    JarvisWorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
)
from app.workflow.events import WorkflowEventEmitter
from app.workflow.operations import InMemoryBrowserOperationRegistry


IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2026, 8, 23, 9, 0, tzinfo=IST)


def _session(session_id="session-1", at=T0):
    return BrowserSessionSnapshot(
        session_id=session_id,
        created_at=at,
        updated_at=at,
    )


def _request(
    operation_id="operation-1",
    *,
    session_id="session-1",
    idempotency_key="request-1",
    message="Analyze TCS for me",
    at=T0,
):
    return BrowserOperationRequest(
        operation_id=operation_id,
        session_id=session_id,
        idempotency_key=idempotency_key,
        kind=BrowserOperationKind.SWING_ANALYSIS,
        input_channel=InputChannel.TEXT,
        message=message,
        requested_at=at,
    )


class BrowserOperationModelTests(unittest.TestCase):
    def test_rejects_non_ist_and_inconsistent_terminal_payloads(self):
        with self.assertRaisesRegex(ValidationError, "must be in IST"):
            _session(at=datetime(2026, 8, 23, 9, 0))

        request = _request()
        with self.assertRaisesRegex(ValidationError, "available result"):
            BrowserOperationSnapshot(
                request=request,
                status=BrowserOperationStatus.COMPLETED,
                updated_at=T0,
            )
        with self.assertRaisesRegex(ValidationError, "safe failure"):
            BrowserOperationSnapshot(
                request=request,
                status=BrowserOperationStatus.FAILED,
                updated_at=T0,
            )

    def test_closed_session_and_cancellation_contracts_are_strict(self):
        with self.assertRaisesRegex(ValidationError, "active work"):
            BrowserSessionSnapshot(
                session_id="session-1",
                state=BrowserSessionState.CLOSED,
                created_at=T0,
                updated_at=T0,
                active_operation_id="operation-1",
            )
        with self.assertRaisesRegex(ValidationError, "cancellation time"):
            BrowserOperationSnapshot(
                request=_request(),
                status=BrowserOperationStatus.CANCELLED,
                updated_at=T0,
            )


class InMemoryBrowserOperationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = InMemoryBrowserOperationRegistry()
        self.registry.open_session(_session())

    def test_opens_and_closes_session_idempotently(self):
        self.assertEqual(self.registry.open_session(_session()), _session())
        closed = self.registry.close_session(
            "session-1",
            closed_at=T0 + timedelta(seconds=1),
        )

        self.assertIs(closed.state, BrowserSessionState.CLOSED)
        self.assertEqual(
            self.registry.close_session(
                "session-1",
                closed_at=T0 + timedelta(seconds=2),
            ),
            closed,
        )
        with self.assertRaises(BrowserOperationConflictError):
            self.registry.submit(_request(at=T0 + timedelta(seconds=3)))

        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "unknown active operation",
        ):
            self.registry.open_session(
                _session("session-phantom").model_copy(
                    update={"active_operation_id": "missing-operation"}
                )
            )

    def test_submit_is_idempotent_and_rejects_key_reuse(self):
        first = self.registry.submit(_request())
        retry = self.registry.submit(
            _request(
                operation_id="operation-retry",
                at=T0 + timedelta(seconds=1),
            )
        )

        self.assertEqual(retry, first)
        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "different request",
        ):
            self.registry.submit(
                _request(
                    operation_id="operation-other",
                    message="Analyze Reliance for me",
                )
            )

    def test_per_session_allows_only_one_active_operation(self):
        self.registry.submit(_request())

        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "active operation",
        ):
            self.registry.submit(
                _request(
                    operation_id="operation-2",
                    idempotency_key="request-2",
                )
            )

    def test_runs_publishes_replays_and_completes_one_operation(self):
        self.registry.submit(_request())
        self.registry.mark_running(
            "operation-1",
            at=T0 + timedelta(seconds=1),
        )
        event_times = iter(
            (T0 + timedelta(seconds=2), T0 + timedelta(seconds=3))
        )
        emitter = WorkflowEventEmitter(
            "operation-1",
            self.registry,
            clock=lambda: next(event_times),
        )
        first = emitter.emit(
            WorkflowStage.REQUEST_RECEIVED,
            WorkflowEventState.COMPLETED,
        )
        second = emitter.emit(
            WorkflowStage.INSTRUMENT_RESOLVED,
            WorkflowEventState.COMPLETED,
            exchange="NSE",
            symbol="TCS-EQ",
        )
        completed = self.registry.mark_completed(
            "operation-1",
            at=T0 + timedelta(seconds=4),
        )

        self.assertTrue(completed.result_available)
        self.assertEqual(completed.last_event_sequence, 2)
        self.assertIsNone(
            self.registry.get_session("session-1").active_operation_id
        )
        batch = self.registry.replay(
            WorkflowEventReplayCursor(
                operation_id="operation-1",
                limit=1,
            )
        )
        self.assertEqual(batch.events, (first,))
        self.assertEqual(batch.next_sequence, 1)
        self.assertTrue(batch.has_more)
        remainder = self.registry.replay(
            WorkflowEventReplayCursor(
                operation_id="operation-1",
                after_sequence=batch.next_sequence,
            )
        )
        self.assertEqual(remainder.events, (second,))
        self.assertFalse(remainder.has_more)

    def test_running_cancellation_can_finish_cancelled_or_win_race(self):
        self.registry.submit(_request())
        self.registry.mark_running(
            "operation-1",
            at=T0 + timedelta(seconds=1),
        )
        requested = self.registry.request_cancellation(
            "operation-1",
            at=T0 + timedelta(seconds=2),
        )
        self.assertIs(
            requested.status,
            BrowserOperationStatus.CANCELLATION_REQUESTED,
        )
        completed = self.registry.mark_completed(
            "operation-1",
            at=T0 + timedelta(seconds=3),
        )
        self.assertEqual(
            completed.cancellation_requested_at,
            T0 + timedelta(seconds=2),
        )

        second_session = _session("session-2", T0 + timedelta(seconds=4))
        self.registry.open_session(second_session)
        self.registry.submit(
            _request(
                "operation-2",
                session_id="session-2",
                idempotency_key="request-2",
                at=T0 + timedelta(seconds=4),
            )
        )
        cancelled = self.registry.request_cancellation(
            "operation-2",
            at=T0 + timedelta(seconds=5),
        )
        self.assertIs(cancelled.status, BrowserOperationStatus.CANCELLED)

    def test_failure_is_safe_and_releases_the_session(self):
        self.registry.submit(_request())
        self.registry.mark_running(
            "operation-1",
            at=T0 + timedelta(seconds=1),
        )
        failure = BrowserOperationFailure(
            code="llm.unavailable",
            message="The research provider is temporarily unavailable.",
            retryable=True,
        )

        failed = self.registry.mark_failed(
            "operation-1",
            failure,
            at=T0 + timedelta(seconds=2),
        )

        self.assertEqual(failed.failure, failure)
        self.assertFalse(failed.result_available)
        self.assertIsNone(
            self.registry.get_session("session-1").active_operation_id
        )
        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "cannot be rewritten",
        ):
            self.registry.mark_failed(
                "operation-1",
                failure.model_copy(update={"code": "different.failure"}),
                at=T0 + timedelta(seconds=3),
            )

    def test_rejects_illegal_transitions_time_reversal_and_event_gaps(self):
        self.registry.submit(_request())
        with self.assertRaises(BrowserOperationConflictError):
            self.registry.mark_completed("operation-1", at=T0)
        self.registry.mark_running(
            "operation-1",
            at=T0 + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "backwards",
        ):
            self.registry.mark_failed(
                "operation-1",
                BrowserOperationFailure(
                    code="test.failure",
                    message="Safe failure",
                ),
                at=T0 + timedelta(seconds=1),
            )
        base = WorkflowEventEmitter(
            "operation-1",
            clock=lambda: T0 + timedelta(seconds=3),
        ).emit(
            WorkflowStage.REQUEST_RECEIVED,
            WorkflowEventState.COMPLETED,
        )
        gap = JarvisWorkflowEvent(
            **(
                base.model_dump()
                | {"event_id": "operation-1:2", "sequence": 2}
            )
        )
        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "contiguous",
        ):
            self.registry.publish(gap)

    def test_missing_identity_errors_are_typed(self):
        with self.assertRaises(BrowserSessionNotFoundError):
            self.registry.close_session("missing", closed_at=T0)
        with self.assertRaises(BrowserOperationNotFoundError):
            self.registry.mark_running("missing", at=T0)
        with self.assertRaises(BrowserOperationNotFoundError):
            self.registry.replay(
                WorkflowEventReplayCursor(operation_id="missing")
            )

    def test_rejects_replay_cursor_beyond_recorded_events(self):
        self.registry.submit(_request())

        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "beyond",
        ):
            self.registry.replay(
                WorkflowEventReplayCursor(
                    operation_id="operation-1",
                    after_sequence=1,
                )
            )

    def test_concurrent_idempotent_submission_creates_one_operation(self):
        requests = [
            _request(
                operation_id=f"operation-{index}",
                at=T0 + timedelta(milliseconds=index),
            )
            for index in range(1, 21)
        ]

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = tuple(pool.map(self.registry.submit, requests))

        self.assertEqual(len({item.request.operation_id for item in results}), 1)


if __name__ == "__main__":
    unittest.main()
