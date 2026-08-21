import threading
import unittest
from datetime import timedelta

from app.conversation.config import JarvisConversationConfig
from app.conversation.events import InMemoryConversationEventSink
from app.conversation.session import JarvisConversationSession
from app.conversation.wake_word import NormalizedWakePhraseDetector
from app.exceptions import (
    ConfigurationError,
    InstrumentNotFoundError,
    LLMConfigurationError,
    MarketDataError,
)
from app.models.conversation import (
    ConversationOutcome,
    ConversationState,
    InputChannel,
)
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.storage import EndToEndSwingAnalysisResult
from app.presentation.llm_failures import present_llm_failure


def _result():
    return EndToEndSwingAnalysisResult.model_construct(
        use_case_id="jarvis.run_end_to_end_swing_analysis.v1",
        market_dataset_id="market:test",
        fetch=object(),
        technical_result=object(),
        debate_result=object(),
    )


def _completed_response():
    return JarvisSwingAnalysisResponse.completed(
        operation_id="operation-1",
        result=_result(),
    )


class RecordingResearchExecutor:
    def __init__(self, response=None, failures=()):
        self.response = response or _completed_response()
        self.failures = list(failures)
        self.calls = []

    def execute(self, text, *, to_date=None):
        self.calls.append((text, to_date))
        if self.failures:
            raise self.failures.pop(0)
        return self.response


class BlockingResearchExecutor:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, text, *, to_date=None):
        self.started.set()
        self.release.wait(timeout=2)
        return _completed_response()


class JarvisWakePhraseTests(unittest.TestCase):
    def test_accepts_case_spacing_and_punctuation_at_start(self):
        detector = NormalizedWakePhraseDetector("Hey Jarvis")

        self.assertEqual(
            detector.command_after_wake_phrase(
                "  HEY,   JARVIS! Analyze Reliance"
            ),
            "Analyze Reliance",
        )
        self.assertEqual(detector.command_after_wake_phrase("Hey Jarvis"), "")

    def test_rejects_missing_middle_and_partial_wake_phrases(self):
        detector = NormalizedWakePhraseDetector()

        self.assertIsNone(detector.command_after_wake_phrase("Jarvis, wake up"))
        self.assertIsNone(
            detector.command_after_wake_phrase("Please say Hey Jarvis")
        )
        self.assertIsNone(
            detector.command_after_wake_phrase("Hey Jarvisian analyze this")
        )


class JarvisConversationConfigTests(unittest.TestCase):
    def test_loads_user_and_custom_wake_phrase(self):
        config = JarvisConversationConfig.from_environment(
            {
                "JARVIS_USER_NAME": "  Prateek  ",
                "JARVIS_WAKE_PHRASE": " Hello Jarvis ",
            }
        )

        self.assertEqual(config.user_name, "Prateek")
        self.assertEqual(config.wake_phrase, "Hello Jarvis")

    def test_requires_user_name(self):
        with self.assertRaises(ConfigurationError):
            JarvisConversationConfig.from_environment({})


class JarvisConversationSessionTests(unittest.TestCase):
    def setUp(self):
        self.executor = RecordingResearchExecutor()
        self.sink = InMemoryConversationEventSink()
        self.session = JarvisConversationSession(
            self.executor,
            JarvisConversationConfig(user_name="Prateek"),
            event_sink=self.sink,
            session_id_factory=lambda: "session-1",
        )

    def test_typed_wake_phrase_activates_and_greets_by_name(self):
        turn = self.session.handle_text("Hey Jarvis")

        self.assertEqual(turn.outcome, ConversationOutcome.ACTIVATED)
        self.assertEqual(turn.input_channel, InputChannel.TEXT)
        self.assertEqual(turn.state_after, ConversationState.LISTENING)
        self.assertEqual(
            turn.display_message,
            "Hello Prateek. How can I help you today?",
        )
        self.assertEqual(self.executor.calls, [])

    def test_voice_transcript_uses_the_same_activation_path(self):
        turn = self.session.handle_voice_transcript("hey, jarvis!")

        self.assertEqual(turn.outcome, ConversationOutcome.ACTIVATED)
        self.assertEqual(turn.input_channel, InputChannel.VOICE)
        self.assertEqual(turn.state_after, ConversationState.LISTENING)
        self.assertTrue(
            all(
                event.input_channel is InputChannel.VOICE
                for event in self.sink.events
            )
        )

    def test_typed_wake_phrase_and_command_execute_immediately(self):
        turn = self.session.handle_text(
            "Hey Jarvis, how is Reliance looking for a swing trade?"
        )

        self.assertEqual(
            self.executor.calls[0][0],
            "how is Reliance looking for a swing trade?",
        )
        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)
        self.assertEqual(turn.state_after, ConversationState.DORMANT)
        self.assertIsNotNone(turn.research_response)

    def test_voice_wake_phrase_and_command_execute_immediately(self):
        turn = self.session.handle_voice_transcript(
            "Hey Jarvis analyze TCS for a swing trade"
        )

        self.assertEqual(
            self.executor.calls[0][0],
            "analyze TCS for a swing trade",
        )
        self.assertEqual(turn.input_channel, InputChannel.VOICE)
        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)

    def test_non_wake_text_is_ignored_while_dormant(self):
        turn = self.session.handle_text("Analyze Reliance")

        self.assertEqual(turn.outcome, ConversationOutcome.IGNORED)
        self.assertEqual(turn.state_after, ConversationState.DORMANT)
        self.assertIsNone(turn.display_message)
        self.assertEqual(self.sink.events, ())
        self.assertEqual(self.executor.calls, [])

    def test_follow_up_after_wake_does_not_require_second_wake_phrase(self):
        self.session.handle_text("Hey Jarvis")
        turn = self.session.handle_text("Analyze Reliance for a swing trade")

        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)
        self.assertEqual(len(self.executor.calls), 1)

    def test_resolution_failure_requests_clarification_and_keeps_listening(self):
        executor = RecordingResearchExecutor(
            failures=(InstrumentNotFoundError("raw secret detail"),)
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(user_name="Prateek"),
            session_id_factory=lambda: "session-2",
        )

        turn = session.handle_text("Hey Jarvis analyze Mystery Limited")

        self.assertEqual(
            turn.outcome,
            ConversationOutcome.CLARIFICATION_REQUIRED,
        )
        self.assertEqual(turn.state_after, ConversationState.LISTENING)
        self.assertNotIn("raw secret detail", turn.display_message)

    def test_llm_failure_preserves_candid_safe_voice_and_display_copy(self):
        failure = present_llm_failure(
            LLMConfigurationError("secret"),
            operation_id="operation-2",
        )
        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-2",
            failure=failure,
        )
        session = JarvisConversationSession(
            RecordingResearchExecutor(response=response),
            JarvisConversationConfig(user_name="Prateek"),
            session_id_factory=lambda: "session-3",
        )

        turn = session.handle_text("Hey Jarvis analyze Reliance")

        self.assertEqual(turn.outcome, ConversationOutcome.FAILED)
        self.assertEqual(turn.display_message, failure.display_message)
        self.assertEqual(turn.spoken_message, failure.spoken_message)
        self.assertEqual(turn.state_after, ConversationState.DORMANT)

    def test_expected_application_failure_does_not_expose_raw_error(self):
        session = JarvisConversationSession(
            RecordingResearchExecutor(
                failures=(MarketDataError("api-key=secret"),)
            ),
            JarvisConversationConfig(user_name="Prateek"),
            session_id_factory=lambda: "session-4",
        )

        turn = session.handle_text("Hey Jarvis analyze Reliance")

        self.assertEqual(turn.outcome, ConversationOutcome.FAILED)
        self.assertNotIn("secret", turn.display_message)
        self.assertEqual(turn.state_after, ConversationState.DORMANT)

    def test_unexpected_defect_resets_state_and_propagates(self):
        session = JarvisConversationSession(
            RecordingResearchExecutor(failures=(RuntimeError("defect"),)),
            JarvisConversationConfig(user_name="Prateek"),
            session_id_factory=lambda: "session-5",
        )

        with self.assertRaisesRegex(RuntimeError, "defect"):
            session.handle_text("Hey Jarvis analyze Reliance")

        self.assertEqual(session.state, ConversationState.DORMANT)

    def test_concurrent_input_gets_busy_response(self):
        executor = BlockingResearchExecutor()
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(user_name="Prateek"),
            session_id_factory=lambda: "session-6",
        )
        completed = []
        worker = threading.Thread(
            target=lambda: completed.append(
                session.handle_text("Hey Jarvis analyze Reliance")
            )
        )
        worker.start()
        self.assertTrue(executor.started.wait(timeout=1))

        busy = session.handle_text("Hey Jarvis analyze TCS")
        executor.release.set()
        worker.join(timeout=2)

        self.assertEqual(busy.outcome, ConversationOutcome.BUSY)
        self.assertEqual(busy.state_after, ConversationState.PROCESSING)
        self.assertEqual(completed[0].outcome, ConversationOutcome.COMPLETED)

    def test_emits_ordered_ist_transitions_for_dynamic_ui(self):
        self.session.handle_text("Hey Jarvis analyze Reliance")

        self.assertEqual(
            [event.to_state for event in self.sink.events],
            [
                ConversationState.GREETING,
                ConversationState.LISTENING,
                ConversationState.PROCESSING,
                ConversationState.RESPONDING,
                ConversationState.DORMANT,
            ],
        )
        self.assertEqual(
            [event.sequence for event in self.sink.events],
            [1, 2, 3, 4, 5],
        )
        self.assertTrue(
            all(
                event.occurred_at.utcoffset()
                == timedelta(hours=5, minutes=30)
                for event in self.sink.events
            )
        )


if __name__ == "__main__":
    unittest.main()
