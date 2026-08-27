import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.conversation.browser import BrowserConversationCoordinator
from app.conversation.config import JarvisConversationConfig
from app.exceptions import BrowserOperationConflictError
from app.models.browser_conversation import ConversationEventReplayCursor
from app.models.browser_operations import (
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
)
from app.models.conversation import (
    ConversationOutcome,
    ConversationState,
    InputChannel,
    JarvisUtterance,
)
from tests.unit.test_jarvis_conversation import FakeTickerResolutionExecutor


IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2026, 8, 23, 12, 0, tzinfo=IST)


class _Operations:
    def __init__(self):
        self.operations = {}
        self.outputs = {}
        self.submit_calls = []

    def submit(self, request):
        self.submit_calls.append(request)
        snapshot = BrowserOperationSnapshot(
            request=request,
            status=BrowserOperationStatus.RUNNING,
            updated_at=request.requested_at,
        )
        self.operations[request.operation_id] = snapshot
        return snapshot

    def get_operation(self, operation_id):
        return self.operations.get(operation_id)

    def get_result(self, operation_id):
        return self.outputs.get(operation_id)

    def complete(self, operation_id, *, at, with_context=True):
        current = self.operations[operation_id]
        self.operations[operation_id] = BrowserOperationSnapshot(
            request=current.request,
            status=BrowserOperationStatus.COMPLETED,
            updated_at=at,
            result_available=True,
        )
        response = SimpleNamespace(
            multi_timeframe_review=(object() if with_context else None),
            multi_timeframe_debate=(object() if with_context else None),
        )
        self.outputs[operation_id] = BrowserOperationOutput.model_construct(
            operation_id=operation_id,
            session_id=current.request.session_id,
            kind=current.request.kind,
            completed_at=at,
            research_response=response,
            research_explanation=SimpleNamespace(
                executive_briefing="The evidence supports a cautious bullish view."
            ),
            judge_follow_up=None,
            presentation_failure=None,
        )

    def fail(
        self,
        operation_id,
        *,
        at,
        code="llm.unavailable",
        message="The debate panel is temporarily unavailable.",
    ):
        current = self.operations[operation_id]
        from app.models.browser_operations import BrowserOperationFailure

        self.operations[operation_id] = BrowserOperationSnapshot(
            request=current.request,
            status=BrowserOperationStatus.FAILED,
            updated_at=at,
            failure=BrowserOperationFailure(
                code=code,
                message=message,
                retryable=True,
            ),
        )


class BrowserConversationCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.operations = _Operations()
        self.conversation = BrowserConversationCoordinator(
            self.operations,
            JarvisConversationConfig(user_name="Prateek"),
        )
        self.conversation.open_session("session-1", at=T0)

    def _turn(self, text, key, *, channel=InputChannel.TEXT, at=T0):
        return self.conversation.handle(
            "session-1",
            JarvisUtterance(text=text, channel=channel),
            idempotency_key=key,
            operation_id_factory=lambda: f"operation-{key}",
            at=at,
        )

    def test_dormant_input_is_ignored_and_wake_greets_configured_user(self):
        ignored = self._turn("Analyze Reliance", "ignored")
        activated = self._turn(
            "hey, jarvis!",
            "wake",
            channel=InputChannel.VOICE,
            at=T0 + timedelta(seconds=1),
        )

        self.assertIs(ignored.outcome, ConversationOutcome.IGNORED)
        self.assertIs(ignored.conversation.state, ConversationState.DORMANT)
        self.assertIs(activated.outcome, ConversationOutcome.ACTIVATED)
        self.assertIs(activated.conversation.state, ConversationState.LISTENING)
        self.assertEqual(
            activated.conversation.display_message,
            "Hello Prateek. How can I help you today?",
        )
        events = self.conversation.replay(
            ConversationEventReplayCursor(session_id="session-1")
        ).events
        self.assertEqual(
            [event.to_state for event in events],
            [ConversationState.GREETING, ConversationState.LISTENING],
        )
        self.assertTrue(
            all(event.input_channel is InputChannel.VOICE for event in events)
        )

    def test_wake_and_command_dispatches_asynchronously_and_is_idempotent(self):
        first = self._turn(
            "Hey Jarvis, analyze Reliance for a swing trade",
            "request-1",
        )
        retry = self._turn(
            "Hey Jarvis, analyze Reliance for a swing trade",
            "request-1",
            at=T0 + timedelta(seconds=1),
        )
        busy = self._turn(
            "Analyze TCS",
            "request-2",
            at=T0 + timedelta(seconds=2),
        )

        self.assertIs(first.outcome, ConversationOutcome.DISPATCHED)
        self.assertIs(first.conversation.state, ConversationState.PROCESSING)
        self.assertIs(
            first.operation.request.kind,
            BrowserOperationKind.SWING_ANALYSIS,
        )
        self.assertEqual(
            first.operation.request.message,
            "analyze Reliance for a swing trade",
        )
        self.assertIs(retry.outcome, ConversationOutcome.DISPATCHED)
        self.assertEqual(len(self.operations.submit_calls), 1)
        self.assertIs(busy.outcome, ConversationOutcome.BUSY)

    def test_completed_analysis_becomes_response_then_support_is_follow_up(self):
        dispatched = self._turn(
            "Hey Jarvis analyze Reliance",
            "request-1",
        )
        operation_id = dispatched.operation.request.operation_id
        self.operations.complete(
            operation_id,
            at=T0 + timedelta(seconds=1),
        )

        responding = self.conversation.get_snapshot(
            "session-1",
            at=T0 + timedelta(seconds=2),
        )
        sleeping = self.conversation.sleep(
            "session-1",
            at=T0 + timedelta(seconds=3),
        )
        follow_up = self._turn(
            "Hey Jarvis, where is weekly support?",
            "request-2",
            at=T0 + timedelta(seconds=4),
        )

        self.assertIs(responding.state, ConversationState.RESPONDING)
        self.assertTrue(responding.has_follow_up_context)
        self.assertIn("cautious bullish", responding.display_message)
        self.assertIs(sleeping.state, ConversationState.DORMANT)
        self.assertTrue(sleeping.has_follow_up_context)
        self.assertIs(
            follow_up.operation.request.kind,
            BrowserOperationKind.JUDGE_FOLLOW_UP,
        )

    def test_new_analysis_clears_old_context_and_failure_is_candid(self):
        first = self._turn("Hey Jarvis analyze Reliance", "request-1")
        self.operations.complete(
            first.operation.request.operation_id,
            at=T0 + timedelta(seconds=1),
        )
        self.conversation.get_snapshot(
            "session-1",
            at=T0 + timedelta(seconds=2),
        )
        self.conversation.sleep(
            "session-1",
            at=T0 + timedelta(seconds=3),
        )
        second = self._turn(
            "Hey Jarvis analyze TCS",
            "request-2",
            at=T0 + timedelta(seconds=4),
        )
        self.assertFalse(second.conversation.has_follow_up_context)
        self.operations.fail(
            second.operation.request.operation_id,
            at=T0 + timedelta(seconds=5),
        )

        failed = self.conversation.get_snapshot(
            "session-1",
            at=T0 + timedelta(seconds=6),
        )

        self.assertIs(failed.state, ConversationState.FAILED)
        self.assertEqual(
            failed.display_message,
            "The debate panel is temporarily unavailable.",
        )
        self.assertFalse(failed.has_follow_up_context)

    def test_sleep_and_replay_reject_invalid_active_or_future_state(self):
        self._turn("Hey Jarvis", "wake")
        sleeping = self._turn(
            "stand by",
            "sleep",
            at=T0 + timedelta(seconds=1),
        )
        self.assertIs(sleeping.conversation.state, ConversationState.DORMANT)

        active = self._turn(
            "Hey Jarvis analyze Reliance",
            "request-1",
            at=T0 + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "cannot sleep",
        ):
            self.conversation.sleep(
                "session-1",
                at=T0 + timedelta(seconds=3),
            )
        with self.assertRaisesRegex(
            BrowserOperationConflictError,
            "beyond",
        ):
            self.conversation.replay(
                ConversationEventReplayCursor(
                    session_id="session-1",
                    after_sequence=999,
                )
            )
        self.assertIsNotNone(active.operation)


class BrowserConversationCoordinatorTickerResolutionTests(unittest.TestCase):
    def setUp(self):
        self.operations = _Operations()

    def _coordinator(self, *, ticker_resolution_executor, threshold=3):
        return BrowserConversationCoordinator(
            self.operations,
            JarvisConversationConfig(
                user_name="Prateek",
                consecutive_resolution_failures_before_refresh_prompt=threshold,
            ),
            ticker_resolution_executor=ticker_resolution_executor,
        )

    def _turn(self, conversation, text, key, *, channel=InputChannel.TEXT, at=T0):
        return conversation.handle(
            "session-1",
            JarvisUtterance(text=text, channel=channel),
            idempotency_key=key,
            operation_id_factory=lambda: f"operation-{key}",
            at=at,
        )

    def test_resolved_guess_awaits_confirmation_then_dispatches_resolved_symbol(
        self,
    ):
        from app.use_cases.resolve_ticker_conversationally import (
            TickerConversationalResolutionResult,
        )

        executor = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(
                    outcome="resolved_needs_confirmation",
                    chosen_symbol="INFY-EQ",
                    exchange="NSE",
                    candidate_display_names=("Infosys Limited",),
                )
            ]
        )
        conversation = self._coordinator(ticker_resolution_executor=executor)
        conversation.open_session("session-1", at=T0)

        dispatched = self._turn(
            conversation, "Hey Jarvis analyze Infosys", "request-1"
        )
        self.operations.fail(
            dispatched.operation.request.operation_id,
            at=T0 + timedelta(seconds=1),
            code="instrument.not_found",
            message=(
                "I could not match that company to one NSE cash-market "
                "instrument."
            ),
        )

        awaiting = conversation.get_snapshot(
            "session-1", at=T0 + timedelta(seconds=2)
        )
        self.assertIs(awaiting.state, ConversationState.AWAITING_CONFIRMATION)
        self.assertIn('Did you mean "INFY-EQ"', awaiting.display_message)
        self.assertEqual(executor.attempt_calls, ["analyze Infosys"])

        confirmed = self._turn(
            conversation, "yes", "request-2", at=T0 + timedelta(seconds=3)
        )
        self.assertIs(confirmed.outcome, ConversationOutcome.DISPATCHED)
        self.assertIs(confirmed.conversation.state, ConversationState.PROCESSING)
        self.assertEqual(
            confirmed.operation.request.message,
            "Analyze INFY-EQ for a swing trade",
        )

    def test_declining_the_guess_returns_to_listening(self):
        from app.use_cases.resolve_ticker_conversationally import (
            TickerConversationalResolutionResult,
        )

        executor = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(
                    outcome="resolved_needs_confirmation",
                    chosen_symbol="INFY-EQ",
                    exchange="NSE",
                )
            ]
        )
        conversation = self._coordinator(ticker_resolution_executor=executor)
        conversation.open_session("session-1", at=T0)

        dispatched = self._turn(
            conversation, "Hey Jarvis analyze Infosys", "request-1"
        )
        self.operations.fail(
            dispatched.operation.request.operation_id,
            at=T0 + timedelta(seconds=1),
            code="instrument.not_found",
        )
        conversation.get_snapshot("session-1", at=T0 + timedelta(seconds=2))

        declined = self._turn(
            conversation, "no", "request-2", at=T0 + timedelta(seconds=3)
        )

        self.assertIs(
            declined.outcome, ConversationOutcome.CLARIFICATION_REQUIRED
        )
        self.assertIs(declined.conversation.state, ConversationState.LISTENING)
        self.assertIsNone(declined.operation)

    def test_repeated_failures_reach_threshold_and_confirm_catalog_refresh(self):
        from app.use_cases.resolve_ticker_conversationally import (
            TickerConversationalResolutionResult,
        )

        not_found = TickerConversationalResolutionResult(outcome="not_found")
        executor = FakeTickerResolutionExecutor(
            results=[not_found, not_found],
            refresh_count=42,
        )
        conversation = self._coordinator(
            ticker_resolution_executor=executor, threshold=2
        )
        conversation.open_session("session-1", at=T0)

        first = self._turn(
            conversation, "Hey Jarvis analyze Zzz Corp", "request-1"
        )
        self.operations.fail(
            first.operation.request.operation_id,
            at=T0 + timedelta(seconds=1),
            code="instrument.not_found",
        )
        after_first = conversation.get_snapshot(
            "session-1", at=T0 + timedelta(seconds=2)
        )
        self.assertIs(after_first.state, ConversationState.FAILED)

        second = self._turn(
            conversation,
            "Hey Jarvis analyze Zzz Corp",
            "request-2",
            at=T0 + timedelta(seconds=3),
        )
        self.operations.fail(
            second.operation.request.operation_id,
            at=T0 + timedelta(seconds=4),
            code="instrument.not_found",
        )
        awaiting = conversation.get_snapshot(
            "session-1", at=T0 + timedelta(seconds=5)
        )
        self.assertIs(awaiting.state, ConversationState.AWAITING_CONFIRMATION)
        self.assertIn("refresh", awaiting.display_message.lower())

        confirmed = self._turn(
            conversation, "yes", "request-3", at=T0 + timedelta(seconds=6)
        )
        self.assertEqual(executor.refresh_calls, 1)
        self.assertIs(confirmed.outcome, ConversationOutcome.DISPATCHED)
        self.assertEqual(
            confirmed.operation.request.message, "analyze Zzz Corp"
        )

    def test_without_executor_configured_falls_back_to_flat_failure(self):
        conversation = self._coordinator(ticker_resolution_executor=None)
        conversation.open_session("session-1", at=T0)

        dispatched = self._turn(
            conversation, "Hey Jarvis analyze Zzz Corp", "request-1"
        )
        self.operations.fail(
            dispatched.operation.request.operation_id,
            at=T0 + timedelta(seconds=1),
            code="instrument.not_found",
            message="I could not match that company to one NSE instrument.",
        )

        failed = conversation.get_snapshot(
            "session-1", at=T0 + timedelta(seconds=2)
        )
        self.assertIs(failed.state, ConversationState.FAILED)
        self.assertEqual(
            failed.display_message,
            "I could not match that company to one NSE instrument.",
        )


if __name__ == "__main__":
    unittest.main()
