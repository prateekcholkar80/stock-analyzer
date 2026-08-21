import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models.workflow import (
    JarvisWorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
)
from app.workflow.events import (
    InMemoryWorkflowEventSink,
    WorkflowEventEmitter,
)


IST = ZoneInfo("Asia/Kolkata")
FIXED_TIME = datetime(2026, 8, 21, 15, 30, tzinfo=IST)


class FailingEventSink:
    def publish(self, event):
        raise RuntimeError("event-sink-secret")


class WorkflowEventEmitterTests(unittest.TestCase):
    def test_sequences_typed_ist_events_for_one_operation(self):
        sink = InMemoryWorkflowEventSink()
        emitter = WorkflowEventEmitter(
            "operation-123",
            sink,
            clock=lambda: FIXED_TIME,
        )

        first = emitter.emit(
            WorkflowStage.REQUEST_RECEIVED,
            WorkflowEventState.COMPLETED,
        )
        second = emitter.emit(
            WorkflowStage.INSTRUMENT_RESOLVED,
            WorkflowEventState.COMPLETED,
            exchange="NSE",
            symbol="RELIANCE-EQ",
        )

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.event_id, "operation-123:2")
        self.assertEqual(second.occurred_at.utcoffset(), FIXED_TIME.utcoffset())
        self.assertEqual(sink.events, (first, second))

    def test_bull_and_bear_events_require_round_number(self):
        emitter = WorkflowEventEmitter(
            "operation",
            InMemoryWorkflowEventSink(),
            clock=lambda: FIXED_TIME,
        )

        event = emitter.emit(
            WorkflowStage.BULL_DEBATING,
            WorkflowEventState.STARTED,
            round_number=2,
        )

        self.assertEqual(event.round_number, 2)
        with self.assertRaises(ValidationError):
            JarvisWorkflowEvent(
                event_id="operation:1",
                operation_id="operation",
                sequence=1,
                stage=WorkflowStage.BEAR_DEBATING,
                state=WorkflowEventState.STARTED,
                occurred_at=FIXED_TIME,
                message="message",
            )

    def test_rejects_unsupported_stage_state_and_invalid_dependencies(self):
        emitter = WorkflowEventEmitter(
            "operation",
            clock=lambda: FIXED_TIME,
        )

        with self.assertRaisesRegex(ValueError, "unsupported"):
            emitter.emit(
                WorkflowStage.REQUEST_RECEIVED,
                WorkflowEventState.STARTED,
            )
        with self.assertRaises(ValueError):
            WorkflowEventEmitter(" ")
        with self.assertRaises(ValueError):
            WorkflowEventEmitter("operation", sink=object())
        with self.assertRaises(ValueError):
            WorkflowEventEmitter("operation", clock="invalid")

    def test_rejects_non_ist_timestamp_and_inconsistent_terminal_event(self):
        common = {
            "event_id": "operation:1",
            "operation_id": "operation",
            "sequence": 1,
            "message": "message",
        }
        with self.assertRaisesRegex(ValidationError, "must be in IST"):
            JarvisWorkflowEvent(
                **common,
                stage=WorkflowStage.COMPLETED,
                state=WorkflowEventState.COMPLETED,
                occurred_at=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
            )
        with self.assertRaisesRegex(ValidationError, "must be completed"):
            JarvisWorkflowEvent(
                **common,
                stage=WorkflowStage.COMPLETED,
                state=WorkflowEventState.FAILED,
                occurred_at=FIXED_TIME,
            )

    def test_sink_failure_is_secret_safe_and_does_not_abort(self):
        emitter = WorkflowEventEmitter(
            "operation",
            FailingEventSink(),
            clock=lambda: FIXED_TIME,
        )

        with self.assertLogs("jarvis.workflow.events", level="ERROR") as logs:
            event = emitter.emit(
                WorkflowStage.REQUEST_RECEIVED,
                WorkflowEventState.COMPLETED,
            )

        self.assertEqual(event.sequence, 1)
        self.assertNotIn("event-sink-secret", " ".join(logs.output))

    def test_concurrent_emission_keeps_contiguous_sequence(self):
        sink = InMemoryWorkflowEventSink()
        emitter = WorkflowEventEmitter(
            "operation",
            sink,
            clock=lambda: FIXED_TIME,
        )

        with ThreadPoolExecutor(max_workers=4) as pool:
            tuple(
                pool.map(
                    lambda _: emitter.emit(
                        WorkflowStage.MARKET_DATA_LOADING,
                        WorkflowEventState.STARTED,
                    ),
                    range(20),
                )
            )

        self.assertEqual(
            [event.sequence for event in sink.events],
            list(range(1, 21)),
        )


if __name__ == "__main__":
    unittest.main()
