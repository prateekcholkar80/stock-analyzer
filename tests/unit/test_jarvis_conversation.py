import threading
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from pydantic import ValidationError

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
from app.models.debate import (
    AgenticDebateResult,
    JudgeFollowUpAnswer,
)
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.presentation import (
    JarvisCaseExplanation,
    JarvisResearchExplanation,
    JarvisTechnicalExplanation,
)
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    SwingTradingStance,
)
from app.models.storage import EndToEndSwingAnalysisResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.presentation.llm_failures import present_llm_failure
from app.use_cases.resolve_ticker_conversationally import (
    TickerConversationalResolutionResult,
)
from tests.unit.test_build_multi_timeframe_evidence import (
    _cyclical_timeframes,
)
from tests.unit.test_run_end_to_end_multi_timeframe_swing_analysis import (
    _use_case as _multi_end_to_end_use_case,
)


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


class RecordingPresenter:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def explain(self, result, *, user_name):
        self.calls.append((result, user_name))
        if self.failure is not None:
            raise self.failure
        return JarvisResearchExplanation(
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            technical_stance=SwingTradingStance.BULLISH,
            technical_score=20.0,
            verdict_id="verdict-1",
            judge_winner=SignalDirection.BULLISH,
            judge_confidence_percentage=70.0,
            decisive_evidence_ids=("trend-1",),
            executive_evidence_ids=("trend-1",),
            executive_briefing="CEO briefing: the evidence leans bullish.",
            judge_conclusion_explanation="The bull case was stronger.",
            technical_findings=(
                JarvisTechnicalExplanation(
                    evidence_id="trend-1",
                    name="Trend",
                    category=SignalCategory.TREND,
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    fact_explanation="The trend evidence is bullish.",
                    inference="This supports the bullish swing case.",
                ),
            ),
            bull_case=JarvisCaseExplanation(
                summary="Bull case.",
                argument_ids=("bull-1",),
                evidence_ids=("trend-1",),
            ),
            bear_case=JarvisCaseExplanation(
                summary="Bear case.",
                argument_ids=("bear-1",),
                evidence_ids=("trend-1",),
            ),
            limitations=("Technical evidence only.",),
            disclaimer="Research, not guaranteed advice.",
            provider="fake-provider",
            model_id="fake-jarvis",
            generated_at=datetime.now(UTC),
        )


class FakeTickerResolutionExecutor:
    def __init__(self, results=(), refresh_count=1):
        self.results = list(results)
        self.attempt_calls = []
        self.refresh_calls = 0
        self._refresh_count = refresh_count

    def attempt(self, command):
        self.attempt_calls.append(command)
        return self.results.pop(0)

    def refresh_catalog(self):
        self.refresh_calls += 1
        return self._refresh_count


class RecordingJudgeFollowUpExecutor:
    def __init__(self):
        self.calls = []

    def execute(
        self,
        question,
        *,
        technical_review,
        debate_result,
    ):
        self.calls.append(
            (question, technical_review, debate_result)
        )
        return JudgeFollowUpAnswer(
            question=question,
            answer="Weekly support is the approved weekly support zone.",
            evidence_citations=(
                technical_review.evidence_package.weekly.nearest_support
                .qualified_zone_id,
            ),
            technical_package_fingerprint=(
                technical_review.evidence_package.package_fingerprint
            ),
            debate_verdict_id="verdict-1",
            judge_agent_id="jarvis.debate_judge_agent.v1",
            model_id="fake-judge",
            generated_at=datetime.now(UTC),
        )


def _multi_timeframe_response():
    review = AgentOrchestrator().run_multi_timeframe_analysis(
        _cyclical_timeframes()
    )
    package = review.evidence_package
    evaluated_at = datetime.now(UTC)
    debate = AgenticDebateResult.model_construct(
        orchestrator_id="jarvis.debate_orchestrator.v1",
        submission=SimpleNamespace(
            submission_id="debate-submission-1",
            technical_submission_id=package.package_fingerprint,
            technical_decision_id=review.decision.decision_id,
            evaluated_at=evaluated_at,
        ),
        decision=SimpleNamespace(
            accepted=True,
            submission_id="debate-submission-1",
            decided_at=evaluated_at,
        ),
    )
    return JarvisSwingAnalysisResponse.completed(
        operation_id="operation-multi",
        result=_result(),
        multi_timeframe_review=review,
        multi_timeframe_debate=debate,
    )


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

    def test_defaults_resolution_failure_threshold_to_three(self):
        config = JarvisConversationConfig.from_environment(
            {"JARVIS_USER_NAME": "Prateek"}
        )

        self.assertEqual(
            config.consecutive_resolution_failures_before_refresh_prompt, 3
        )

    def test_loads_custom_resolution_failure_threshold(self):
        config = JarvisConversationConfig.from_environment(
            {
                "JARVIS_USER_NAME": "Prateek",
                "JARVIS_RESOLUTION_FAILURE_THRESHOLD": "5",
            }
        )

        self.assertEqual(
            config.consecutive_resolution_failures_before_refresh_prompt, 5
        )

    def test_rejects_non_integer_resolution_failure_threshold(self):
        with self.assertRaises(ConfigurationError):
            JarvisConversationConfig.from_environment(
                {
                    "JARVIS_USER_NAME": "Prateek",
                    "JARVIS_RESOLUTION_FAILURE_THRESHOLD": "not-a-number",
                }
            )


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

    def test_success_uses_persona_briefing_and_preserves_structured_result(self):
        presenter = RecordingPresenter()
        session = JarvisConversationSession(
            self.executor,
            JarvisConversationConfig(user_name="Prateek"),
            research_presenter=presenter,
            session_id_factory=lambda: "session-persona",
        )

        turn = session.handle_text("Hey Jarvis analyze Reliance")

        self.assertEqual(
            turn.display_message,
            "CEO briefing: the evidence leans bullish.",
        )
        self.assertIsNotNone(turn.research_response)
        self.assertIsNotNone(turn.research_explanation)
        self.assertEqual(presenter.calls[0][1], "Prateek")

    def test_multi_timeframe_success_uses_ceo_presenter(self):
        result = _multi_end_to_end_use_case(winner="bearish").execute(
            "NSE", "2885", "RELIANCE-EQ", "ONE_HOUR"
        )
        research = RecordingResearchExecutor(
            response=JarvisSwingAnalysisResponse.completed(
                operation_id="operation-multi-presented",
                result=result,
                multi_timeframe_review=result.technical_review,
                multi_timeframe_debate=result.debate_result,
            )
        )
        presenter = RecordingPresenter()
        session = JarvisConversationSession(
            research,
            JarvisConversationConfig(user_name="Prateek"),
            research_presenter=presenter,
            session_id_factory=lambda: "session-multi-presented",
        )

        turn = session.handle_text("Hey Jarvis analyze Reliance")

        self.assertEqual(
            turn.display_message,
            "CEO briefing: the evidence leans bullish.",
        )
        self.assertIs(presenter.calls[0][0], result)

    def test_persona_failure_is_candid_and_keeps_validated_research(self):
        presenter = RecordingPresenter(
            LLMConfigurationError("provider secret")
        )
        session = JarvisConversationSession(
            self.executor,
            JarvisConversationConfig(user_name="Prateek"),
            research_presenter=presenter,
            session_id_factory=lambda: "session-persona-failure",
        )

        turn = session.handle_text("Hey Jarvis analyze Reliance")

        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)
        self.assertIn("unscheduled chai", turn.display_message)
        self.assertNotIn("secret", turn.display_message)
        self.assertIsNotNone(turn.research_response)
        self.assertIsNone(turn.research_explanation)

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

    def test_retains_multi_timeframe_context_for_judge_follow_up(self):
        research = RecordingResearchExecutor(
            response=_multi_timeframe_response()
        )
        follow_up = RecordingJudgeFollowUpExecutor()
        session = JarvisConversationSession(
            research,
            JarvisConversationConfig(user_name="Prateek"),
            judge_follow_up_executor=follow_up,
            session_id_factory=lambda: "session-follow-up",
        )
        session.handle_text("Hey Jarvis analyze Reliance")

        turn = session.handle_text(
            "Hey Jarvis, tell me where support is on a weekly basis?"
        )

        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)
        self.assertEqual(
            turn.display_message,
            "Weekly support is the approved weekly support zone.",
        )
        self.assertIsNotNone(turn.judge_follow_up)
        self.assertEqual(len(research.calls), 1)
        self.assertEqual(len(follow_up.calls), 1)
        self.assertIs(
            follow_up.calls[0][1],
            research.response.multi_timeframe_review,
        )

    def test_new_analysis_does_not_reuse_retained_judge_context(self):
        research = RecordingResearchExecutor(
            response=_multi_timeframe_response()
        )
        follow_up = RecordingJudgeFollowUpExecutor()
        session = JarvisConversationSession(
            research,
            JarvisConversationConfig(user_name="Prateek"),
            judge_follow_up_executor=follow_up,
            session_id_factory=lambda: "session-new-analysis",
        )
        session.handle_text("Hey Jarvis analyze Reliance")

        turn = session.handle_text(
            "Hey Jarvis analyze TCS for a swing trade"
        )

        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)
        self.assertEqual(len(research.calls), 2)
        self.assertEqual(follow_up.calls, [])

    def test_legacy_success_clears_previous_multi_timeframe_context(self):
        research = RecordingResearchExecutor(
            response=_multi_timeframe_response()
        )
        follow_up = RecordingJudgeFollowUpExecutor()
        session = JarvisConversationSession(
            research,
            JarvisConversationConfig(user_name="Prateek"),
            judge_follow_up_executor=follow_up,
            session_id_factory=lambda: "session-context-replaced",
        )
        session.handle_text("Hey Jarvis analyze Reliance")
        research.response = _completed_response()
        session.handle_text("Hey Jarvis analyze TCS for a swing trade")

        session.handle_text("Hey Jarvis where is weekly support?")

        self.assertEqual(len(research.calls), 3)
        self.assertEqual(follow_up.calls, [])

    def test_failed_new_analysis_clears_previous_context(self):
        research = RecordingResearchExecutor(
            response=_multi_timeframe_response()
        )
        follow_up = RecordingJudgeFollowUpExecutor()
        session = JarvisConversationSession(
            research,
            JarvisConversationConfig(user_name="Prateek"),
            judge_follow_up_executor=follow_up,
            session_id_factory=lambda: "session-failed-replacement",
        )
        session.handle_text("Hey Jarvis analyze Reliance")
        failure = present_llm_failure(
            LLMConfigurationError("secret"),
            operation_id="operation-failed-replacement",
        )
        research.response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-failed-replacement",
            failure=failure,
        )

        failed_turn = session.handle_text(
            "Hey Jarvis analyze TCS for a swing trade"
        )
        session.handle_text("Hey Jarvis where is weekly support?")

        self.assertEqual(failed_turn.outcome, ConversationOutcome.FAILED)
        self.assertEqual(len(research.calls), 3)
        self.assertEqual(follow_up.calls, [])

    def test_response_rejects_partial_follow_up_context(self):
        response = _multi_timeframe_response()

        with self.assertRaisesRegex(ValidationError, "requires both"):
            JarvisSwingAnalysisResponse.completed(
                operation_id="partial-context",
                result=_result(),
                multi_timeframe_review=response.multi_timeframe_review,
            )

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

    def test_resolution_failure_without_ticker_resolver_is_unaffected(self):
        # No ticker_resolution_executor configured: behavior must be
        # byte-for-byte identical to before this feature existed.
        executor = RecordingResearchExecutor(
            failures=(InstrumentNotFoundError("raw secret detail"),)
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(user_name="Prateek"),
            session_id_factory=lambda: "session-2b",
        )

        turn = session.handle_text("Hey Jarvis analyze Mystery Limited")

        self.assertEqual(
            turn.outcome,
            ConversationOutcome.CLARIFICATION_REQUIRED,
        )
        self.assertEqual(turn.state_after, ConversationState.LISTENING)

    def test_ticker_guess_requests_confirmation_and_awaits_reply(self):
        executor = RecordingResearchExecutor(
            failures=(InstrumentNotFoundError("no exact match"),)
        )
        resolver = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(
                    outcome="resolved_needs_confirmation",
                    chosen_symbol="RELIANCE-EQ",
                    exchange="NSE",
                )
            ]
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(user_name="Prateek"),
            ticker_resolution_executor=resolver,
            session_id_factory=lambda: "session-guess",
        )

        turn = session.handle_text("Hey Jarvis analyze Rel for me")

        self.assertEqual(
            turn.outcome, ConversationOutcome.CONFIRMATION_REQUESTED
        )
        self.assertEqual(
            turn.state_after, ConversationState.AWAITING_CONFIRMATION
        )
        self.assertIn("RELIANCE-EQ", turn.display_message)
        self.assertEqual(resolver.attempt_calls, ["analyze Rel for me"])

    def test_yes_to_ticker_guess_resumes_with_confirmed_symbol(self):
        executor = RecordingResearchExecutor(
            failures=(InstrumentNotFoundError("no exact match"),)
        )
        resolver = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(
                    outcome="resolved_needs_confirmation",
                    chosen_symbol="RELIANCE-EQ",
                    exchange="NSE",
                )
            ]
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(user_name="Prateek"),
            ticker_resolution_executor=resolver,
            session_id_factory=lambda: "session-yes",
        )
        session.handle_text("Hey Jarvis analyze Rel for me")

        turn = session.handle_text("yes")

        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)
        self.assertEqual(turn.state_after, ConversationState.DORMANT)
        self.assertEqual(
            executor.calls[-1][0],
            "Analyze RELIANCE-EQ for a swing trade",
        )

    def test_no_to_ticker_guess_returns_to_listening_without_leaking_state(
        self,
    ):
        executor = RecordingResearchExecutor(
            failures=(InstrumentNotFoundError("no exact match"),)
        )
        resolver = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(
                    outcome="resolved_needs_confirmation",
                    chosen_symbol="RELIANCE-EQ",
                    exchange="NSE",
                )
            ]
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(user_name="Prateek"),
            ticker_resolution_executor=resolver,
            session_id_factory=lambda: "session-no",
        )
        session.handle_text("Hey Jarvis analyze Rel for me")

        decline_turn = session.handle_text("no")

        self.assertEqual(
            decline_turn.outcome,
            ConversationOutcome.CLARIFICATION_REQUIRED,
        )
        self.assertEqual(decline_turn.state_after, ConversationState.LISTENING)

        # A fresh command afterward works normally -- no leaked pending state.
        follow_up_turn = session.handle_text("Analyze TCS for a swing trade")
        self.assertEqual(follow_up_turn.outcome, ConversationOutcome.COMPLETED)
        self.assertEqual(executor.calls[-1][0], "Analyze TCS for a swing trade")

    def test_repeated_failures_reach_threshold_and_offer_catalog_refresh(self):
        executor = RecordingResearchExecutor(
            failures=[
                InstrumentNotFoundError("miss 1"),
                InstrumentNotFoundError("miss 2"),
                InstrumentNotFoundError("miss 3"),
            ]
        )
        resolver = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(outcome="not_found"),
                TickerConversationalResolutionResult(outcome="ambiguous"),
                TickerConversationalResolutionResult(outcome="not_found"),
            ]
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(
                user_name="Prateek",
                consecutive_resolution_failures_before_refresh_prompt=3,
            ),
            ticker_resolution_executor=resolver,
            session_id_factory=lambda: "session-threshold",
        )

        first = session.handle_text("Hey Jarvis analyze Zzz for me")
        second = session.handle_text("Analyze Zzz for me")
        third = session.handle_text("Analyze Zzz for me")

        self.assertEqual(first.outcome, ConversationOutcome.CLARIFICATION_REQUIRED)
        self.assertEqual(second.outcome, ConversationOutcome.CLARIFICATION_REQUIRED)
        self.assertEqual(
            third.outcome, ConversationOutcome.CONFIRMATION_REQUESTED
        )
        self.assertEqual(
            third.state_after, ConversationState.AWAITING_CONFIRMATION
        )

    def test_yes_to_catalog_refresh_calls_refresh_then_retries_original(self):
        executor = RecordingResearchExecutor(
            failures=[
                InstrumentNotFoundError("miss 1"),
                InstrumentNotFoundError("miss 2"),
                InstrumentNotFoundError("miss 3"),
            ]
        )
        resolver = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(outcome="not_found"),
                TickerConversationalResolutionResult(outcome="not_found"),
                TickerConversationalResolutionResult(outcome="not_found"),
            ]
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(
                user_name="Prateek",
                consecutive_resolution_failures_before_refresh_prompt=3,
            ),
            ticker_resolution_executor=resolver,
            session_id_factory=lambda: "session-refresh",
        )
        session.handle_text("Hey Jarvis analyze Zzz for me")
        session.handle_text("Analyze Zzz for me")
        session.handle_text("Analyze Zzz for me")

        turn = session.handle_text("yes")

        self.assertEqual(resolver.refresh_calls, 1)
        self.assertEqual(turn.outcome, ConversationOutcome.COMPLETED)
        # The fourth execute() call is a retry of the exact command that
        # triggered the refresh-confirmation (the third failed attempt) --
        # this time it succeeds since no failure is queued for it.
        self.assertEqual(executor.calls[-1][0], "Analyze Zzz for me")

    def test_busy_guard_does_not_apply_while_awaiting_confirmation(self):
        executor = RecordingResearchExecutor(
            failures=(InstrumentNotFoundError("no exact match"),)
        )
        resolver = FakeTickerResolutionExecutor(
            results=[
                TickerConversationalResolutionResult(
                    outcome="resolved_needs_confirmation",
                    chosen_symbol="RELIANCE-EQ",
                    exchange="NSE",
                )
            ]
        )
        session = JarvisConversationSession(
            executor,
            JarvisConversationConfig(user_name="Prateek"),
            ticker_resolution_executor=resolver,
            session_id_factory=lambda: "session-busy-check",
        )
        session.handle_text("Hey Jarvis analyze Rel for me")
        self.assertEqual(
            session.state, ConversationState.AWAITING_CONFIRMATION
        )

        turn = session.handle_text("yes")

        self.assertNotEqual(turn.outcome, ConversationOutcome.BUSY)

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
