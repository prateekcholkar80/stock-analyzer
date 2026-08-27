from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol, runtime_checkable
from uuid import uuid4

from app.audit.prompt_audit import (
    PromptAuditActor,
    PromptAuditEventType,
    PromptAuditRecorder,
    PromptAuditSink,
    prompt_audit_session_context,
)
from app.conversation.config import JarvisConversationConfig
from app.conversation.follow_up import looks_like_analysis_follow_up
from app.conversation.events import (
    ConversationEventEmitter,
    ConversationEventSink,
)
from app.conversation.pending_confirmation import (
    PendingConfirmation,
    classify_yes_no,
)
from app.conversation.wake_word import (
    NormalizedWakePhraseDetector,
    WakePhraseDetector,
)
from app.exceptions import (
    AmbiguousInstrumentError,
    ApplicationError,
    InstrumentNotFoundError,
    IntentRecognitionError,
)
from app.models.conversation import (
    ConversationOutcome,
    ConversationState,
    InputChannel,
    JarvisConversationTurn,
    JarvisUtterance,
)
from app.models.interaction import (
    JarvisCommandStatus,
    JarvisSwingAnalysisResponse,
)
from app.models.debate import (
    AgenticDebateResult,
    JudgeFollowUpAnswer,
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
from app.use_cases.resolve_ticker_conversationally import (
    TickerConversationalResolutionResult,
)


@runtime_checkable
class SwingResearchExecutor(Protocol):
    def execute(
        self,
        text: str,
        *,
        to_date: datetime | None = None,
    ) -> JarvisSwingAnalysisResponse:
        ...


@runtime_checkable
class ResearchPresentationExecutor(Protocol):
    def explain(
        self,
        result: (
            EndToEndSwingAnalysisResult
            | MultiTimeframeEndToEndSwingAnalysisResult
        ),
        *,
        user_name: str,
    ) -> (
        JarvisResearchExplanation
        | JarvisMultiTimeframeResearchExplanation
    ):
        ...


@runtime_checkable
class JudgeFollowUpExecutor(Protocol):
    def execute(
        self,
        question: str,
        *,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> JudgeFollowUpAnswer:
        ...


@runtime_checkable
class TickerResolutionExecutor(Protocol):
    """Deterministic-shortlist + constrained-LLM ticker resolution, with
    catalog refresh. Optional: when not configured, an unresolved
    instrument falls back to the plain clarification message unchanged.
    """

    def attempt(self, command: str) -> TickerConversationalResolutionResult:
        ...

    def refresh_catalog(self) -> int:
        ...


_ALLOWED_TRANSITIONS = {
    (ConversationState.DORMANT, ConversationState.GREETING),
    (ConversationState.GREETING, ConversationState.LISTENING),
    (ConversationState.LISTENING, ConversationState.PROCESSING),
    (ConversationState.PROCESSING, ConversationState.RESPONDING),
    (ConversationState.PROCESSING, ConversationState.LISTENING),
    (ConversationState.PROCESSING, ConversationState.FAILED),
    (ConversationState.PROCESSING, ConversationState.AWAITING_CONFIRMATION),
    (ConversationState.AWAITING_CONFIRMATION, ConversationState.PROCESSING),
    (ConversationState.AWAITING_CONFIRMATION, ConversationState.LISTENING),
    (ConversationState.RESPONDING, ConversationState.DORMANT),
    (ConversationState.FAILED, ConversationState.DORMANT),
}

class JarvisConversationSession:
    """Coordinate wake activation and one natural-language research session."""

    def __init__(
        self,
        research_executor: SwingResearchExecutor,
        config: JarvisConversationConfig,
        *,
        wake_detector: WakePhraseDetector | None = None,
        event_sink: ConversationEventSink | None = None,
        prompt_audit_sink: PromptAuditSink | None = None,
        research_presenter: ResearchPresentationExecutor | None = None,
        judge_follow_up_executor: JudgeFollowUpExecutor | None = None,
        ticker_resolution_executor: TickerResolutionExecutor | None = None,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(research_executor, SwingResearchExecutor):
            raise ValueError("conversation requires a swing research executor")
        if not isinstance(config, JarvisConversationConfig):
            raise ValueError("conversation requires validated settings")
        if research_presenter is not None and not isinstance(
            research_presenter,
            ResearchPresentationExecutor,
        ):
            raise ValueError("conversation requires a research presenter")
        if judge_follow_up_executor is not None and not isinstance(
            judge_follow_up_executor,
            JudgeFollowUpExecutor,
        ):
            raise ValueError(
                "conversation requires a Judge follow-up executor"
            )
        if ticker_resolution_executor is not None and not isinstance(
            ticker_resolution_executor,
            TickerResolutionExecutor,
        ):
            raise ValueError(
                "conversation requires a ticker resolution executor"
            )
        detector = wake_detector or NormalizedWakePhraseDetector(
            config.wake_phrase
        )
        if not isinstance(detector, WakePhraseDetector):
            raise ValueError("conversation requires a wake phrase detector")
        if event_sink is not None and not isinstance(
            event_sink,
            ConversationEventSink,
        ):
            raise ValueError("conversation requires an event sink")
        id_factory = session_id_factory or (lambda: uuid4().hex)
        if not callable(id_factory):
            raise ValueError("conversation session ID factory must be callable")

        self._research_executor = research_executor
        self._research_presenter = research_presenter
        self._judge_follow_up_executor = judge_follow_up_executor
        self._ticker_resolution_executor = ticker_resolution_executor
        self._config = config
        self._wake_detector = detector
        self._event_sink = event_sink
        self._prompt_audit = PromptAuditRecorder(prompt_audit_sink)
        self._session_id_factory = id_factory
        self._state = ConversationState.DORMANT
        self._session_id: str | None = None
        self._emitter: ConversationEventEmitter | None = None
        self._lock = RLock()
        self._last_multi_timeframe_review: (
            MultiTimeframeEvidenceReview | None
        ) = None
        self._last_multi_timeframe_debate: AgenticDebateResult | None = None
        self._pending_confirmation: PendingConfirmation | None = None
        self._consecutive_resolution_failures = 0

    @property
    def state(self) -> ConversationState:
        with self._lock:
            return self._state

    def handle_text(
        self,
        text: str,
        *,
        to_date: datetime | None = None,
    ) -> JarvisConversationTurn:
        return self.handle(
            JarvisUtterance(text=text, channel=InputChannel.TEXT),
            to_date=to_date,
        )

    def handle_voice_transcript(
        self,
        transcript: str,
        *,
        to_date: datetime | None = None,
    ) -> JarvisConversationTurn:
        return self.handle(
            JarvisUtterance(text=transcript, channel=InputChannel.VOICE),
            to_date=to_date,
        )

    def handle(
        self,
        utterance: JarvisUtterance,
        *,
        to_date: datetime | None = None,
    ) -> JarvisConversationTurn:
        if not isinstance(utterance, JarvisUtterance):
            raise ValueError("conversation input must be a validated utterance")

        refresh_catalog_first = False
        with self._lock:
            state_before = self._state

            if self._state is ConversationState.AWAITING_CONFIRMATION:
                early_turn = self._begin_confirmation_reply(
                    utterance,
                    state_before,
                )
                if early_turn is not None:
                    return early_turn
                pending = self._pending_confirmation
                assert pending is not None
                self._pending_confirmation = None
                if pending.kind == "catalog_refresh":
                    command = pending.original_command
                    refresh_catalog_first = True
                else:
                    command = (
                        f"Analyze {pending.chosen_symbol} for a swing trade"
                    )
                to_date = pending.to_date
                self._transition(
                    ConversationState.PROCESSING,
                    utterance.channel,
                )
                active_session_id = self._session_id
            else:
                if self._state in {
                    ConversationState.PROCESSING,
                    ConversationState.RESPONDING,
                }:
                    self._record_input(utterance, state_before)
                    return self._turn(
                        utterance,
                        state_before=state_before,
                        outcome=ConversationOutcome.BUSY,
                        display_message=(
                            "Jarvis is already working on a request."
                        ),
                        spoken_message="One moment. I'm still working on that.",
                    )

                command = utterance.text
                detected_command = (
                    self._wake_detector.command_after_wake_phrase(
                        utterance.text
                    )
                )
                if self._state is ConversationState.DORMANT:
                    if detected_command is None:
                        return self._turn(
                            utterance,
                            state_before=state_before,
                            outcome=ConversationOutcome.IGNORED,
                        )
                    command = detected_command
                    self._activate(utterance.channel)
                elif detected_command is not None:
                    command = detected_command

                self._record_input(utterance, state_before)

                if not command:
                    greeting = (
                        f"Hello {self._config.user_name}. "
                        "How can I help you today?"
                    )
                    return self._turn(
                        utterance,
                        state_before=state_before,
                        outcome=ConversationOutcome.ACTIVATED,
                        display_message=greeting,
                        spoken_message=greeting,
                    )

                self._transition(
                    ConversationState.PROCESSING,
                    utterance.channel,
                )
                active_session_id = self._session_id

        if refresh_catalog_first and self._ticker_resolution_executor is not None:
            try:
                self._ticker_resolution_executor.refresh_catalog()
            except ApplicationError:
                # Resolution below will simply fail again and route through
                # the normal failure handling; a refresh failure must not
                # crash the turn.
                pass

        return self._execute_command(
            command,
            to_date,
            utterance,
            state_before,
            active_session_id,
        )

    def _begin_confirmation_reply(
        self,
        utterance: JarvisUtterance,
        state_before: ConversationState,
    ) -> JarvisConversationTurn | None:
        """Handle a "yes"/"no" reply while AWAITING_CONFIRMATION. Called
        while holding self._lock. Returns a final turn for "no" (or
        anything not recognized as "yes"); returns None to signal the
        caller should proceed to resume the pending request (with
        self._pending_confirmation left set for the caller to consume).
        """
        self._record_input(utterance, state_before)
        if classify_yes_no(utterance.text) == "yes":
            return None

        self._pending_confirmation = None
        self._consecutive_resolution_failures += 1
        self._transition(ConversationState.LISTENING, utterance.channel)
        message = (
            "No problem. Let me know the company or NSE symbol you'd "
            "like to analyze."
        )
        return self._turn(
            utterance,
            state_before=state_before,
            outcome=ConversationOutcome.CLARIFICATION_REQUIRED,
            display_message=message,
            spoken_message=message,
        )

    def _execute_command(
        self,
        command: str,
        to_date: datetime | None,
        utterance: JarvisUtterance,
        state_before: ConversationState,
        active_session_id: str | None,
    ) -> JarvisConversationTurn:
        try:
            assert active_session_id is not None
            explanation = None
            presentation_failed = False
            response = None
            follow_up_answer = None
            with prompt_audit_session_context(active_session_id):
                follow_up_context = self._follow_up_context(command)
                if follow_up_context is not None:
                    technical_review, debate_result = follow_up_context
                    follow_up_answer = self._judge_follow_up_executor.execute(
                        command,
                        technical_review=technical_review,
                        debate_result=debate_result,
                    )
                    if not isinstance(
                        follow_up_answer,
                        JudgeFollowUpAnswer,
                    ):
                        raise ValueError(
                            "Judge follow-up executor returned an invalid answer"
                        )
                else:
                    # A fresh request supersedes the previous instrument. Do
                    # this before execution so a failed replacement cannot
                    # leave stale evidence available to a later follow-up.
                    self._clear_multi_timeframe_context()
                    response = self._research_executor.execute(
                        command,
                        to_date=to_date,
                    )
                    if not isinstance(response, JarvisSwingAnalysisResponse):
                        raise ValueError(
                            "research executor returned an invalid response"
                        )
                    with self._lock:
                        self._consecutive_resolution_failures = 0
                    if response.status is JarvisCommandStatus.COMPLETED:
                        self._remember_multi_timeframe_context(response)
                        if (
                            self._research_presenter is not None
                            and isinstance(
                                response.result,
                                (
                                    EndToEndSwingAnalysisResult,
                                    MultiTimeframeEndToEndSwingAnalysisResult,
                                ),
                            )
                        ):
                            assert response.result is not None
                            try:
                                explanation = self._research_presenter.explain(
                                    response.result,
                                    user_name=self._config.user_name,
                                )
                            except ApplicationError:
                                presentation_failed = True
        except (
            IntentRecognitionError,
            InstrumentNotFoundError,
            AmbiguousInstrumentError,
        ) as exc:
            return self._handle_resolution_failure(
                command,
                to_date,
                utterance,
                state_before,
                exc,
            )
        except ApplicationError:
            return self._application_failure_turn(utterance, state_before)
        except Exception:
            with self._lock:
                self._prompt_audit.record(
                    PromptAuditEventType.CONVERSATION_OUTPUT,
                    PromptAuditActor.JARVIS,
                    {
                        "outcome": "unexpected_failure",
                        "error_type": "unexpected_application_defect",
                    },
                    session_id=active_session_id,
                )
                self._transition(ConversationState.FAILED, utterance.channel)
                self._transition(ConversationState.DORMANT, utterance.channel)
                self._pending_confirmation = None
                self._clear_session()
            raise

        with self._lock:
            self._transition(ConversationState.RESPONDING, utterance.channel)
            if follow_up_answer is not None:
                outcome = ConversationOutcome.COMPLETED
                display_message = follow_up_answer.answer
                spoken_message = follow_up_answer.answer
            elif response.status is JarvisCommandStatus.COMPLETED:
                outcome = ConversationOutcome.COMPLETED
                if explanation is not None:
                    display_message = explanation.executive_briefing
                    spoken_message = explanation.executive_briefing
                elif presentation_failed:
                    display_message = (
                        "The evidence and debate are complete, but my CEO "
                        "briefing service has gone for an unscheduled chai. "
                        "The validated raw result is still available."
                    )
                    spoken_message = (
                        "The analysis is complete, but I couldn't prepare "
                        "the briefing. The validated result is still available."
                    )
                else:
                    display_message = (
                        "Jarvis completed the swing-trade analysis."
                    )
                    spoken_message = (
                        "I've completed the swing-trade analysis."
                    )
            else:
                outcome = ConversationOutcome.FAILED
                assert response.failure is not None
                display_message = response.failure.display_message
                spoken_message = response.failure.spoken_message
            self._transition(ConversationState.DORMANT, utterance.channel)
            turn = self._turn(
                utterance,
                state_before=state_before,
                outcome=outcome,
                display_message=display_message,
                spoken_message=spoken_message,
                research_response=response,
                research_explanation=explanation,
                judge_follow_up=follow_up_answer,
                session_id=active_session_id,
            )
            self._clear_session()
            return turn

    def _handle_resolution_failure(
        self,
        command: str,
        to_date: datetime | None,
        utterance: JarvisUtterance,
        state_before: ConversationState,
        error: ApplicationError,
    ) -> JarvisConversationTurn:
        if self._ticker_resolution_executor is None or isinstance(
            error,
            IntentRecognitionError,
        ):
            return self._clarification_turn(utterance, state_before, error)

        try:
            result = self._ticker_resolution_executor.attempt(command)
        except ApplicationError:
            return self._clarification_turn(utterance, state_before, error)

        if result.outcome == "resolved_needs_confirmation":
            return self._resolver_confirmation_turn(
                utterance,
                state_before,
                kind="ticker_guess",
                original_command=command,
                to_date=to_date,
                chosen_symbol=result.chosen_symbol,
                exchange=result.exchange,
            )

        with self._lock:
            self._consecutive_resolution_failures += 1
            failures = self._consecutive_resolution_failures
        threshold = (
            self._config.consecutive_resolution_failures_before_refresh_prompt
        )
        if failures >= threshold:
            return self._resolver_confirmation_turn(
                utterance,
                state_before,
                kind="catalog_refresh",
                original_command=command,
                to_date=to_date,
            )
        return self._clarification_turn(utterance, state_before, error)

    def _resolver_confirmation_turn(
        self,
        utterance: JarvisUtterance,
        state_before: ConversationState,
        *,
        kind: Literal["ticker_guess", "catalog_refresh"],
        original_command: str,
        to_date: datetime | None,
        chosen_symbol: str | None = None,
        exchange: str | None = None,
    ) -> JarvisConversationTurn:
        pending = PendingConfirmation(
            kind=kind,
            original_command=original_command,
            to_date=to_date,
            chosen_symbol=chosen_symbol,
            exchange=exchange,
        )
        if kind == "ticker_guess":
            message = f'Did you mean "{chosen_symbol}"? Reply yes or no.'
        else:
            message = (
                "I'm having trouble matching that company against my "
                "current list. Should I refresh it and try again? Reply "
                "yes or no."
            )
        with self._lock:
            self._pending_confirmation = pending
            self._transition(
                ConversationState.AWAITING_CONFIRMATION,
                utterance.channel,
            )
            return self._turn(
                utterance,
                state_before=state_before,
                outcome=ConversationOutcome.CONFIRMATION_REQUESTED,
                display_message=message,
                spoken_message=message,
            )

    def _activate(self, channel: InputChannel) -> None:
        session_id = self._session_id_factory()
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("conversation session ID must not be blank")
        self._session_id = session_id.strip()
        self._emitter = ConversationEventEmitter(
            self._session_id,
            self._event_sink,
        )
        self._transition(ConversationState.GREETING, channel)
        self._transition(ConversationState.LISTENING, channel)

    def _transition(
        self,
        next_state: ConversationState,
        channel: InputChannel,
    ) -> None:
        transition = (self._state, next_state)
        if transition not in _ALLOWED_TRANSITIONS:
            raise RuntimeError(
                f"unsupported conversation transition: "
                f"{self._state.value} -> {next_state.value}"
            )
        previous = self._state
        self._state = next_state
        if self._emitter is None:
            raise RuntimeError("active conversation has no event emitter")
        self._emitter.emit(previous, next_state, channel)

    def _clarification_turn(
        self,
        utterance: JarvisUtterance,
        state_before: ConversationState,
        error: ApplicationError,
    ) -> JarvisConversationTurn:
        if isinstance(error, AmbiguousInstrumentError):
            message = (
                "I found more than one matching instrument. Please include "
                "the exact company or NSE symbol."
            )
        elif isinstance(error, InstrumentNotFoundError):
            message = (
                "I couldn't identify that instrument. Please provide the "
                "company name or NSE symbol."
            )
        else:
            message = (
                "Please ask for a swing-trade analysis and include the "
                "company name or NSE symbol."
            )
        with self._lock:
            self._transition(ConversationState.LISTENING, utterance.channel)
            return self._turn(
                utterance,
                state_before=state_before,
                outcome=ConversationOutcome.CLARIFICATION_REQUIRED,
                display_message=message,
                spoken_message=message,
            )

    def _application_failure_turn(
        self,
        utterance: JarvisUtterance,
        state_before: ConversationState,
    ) -> JarvisConversationTurn:
        message = (
            "Jarvis hit a snag while preparing the analysis. No market "
            "verdict was produced; please try again shortly."
        )
        with self._lock:
            active_session_id = self._session_id
            self._transition(ConversationState.FAILED, utterance.channel)
            self._transition(ConversationState.DORMANT, utterance.channel)
            turn = self._turn(
                utterance,
                state_before=state_before,
                outcome=ConversationOutcome.FAILED,
                display_message=message,
                spoken_message=message,
                session_id=active_session_id,
            )
            self._clear_session()
            return turn

    def _turn(
        self,
        utterance: JarvisUtterance,
        *,
        state_before: ConversationState,
        outcome: ConversationOutcome,
        display_message: str | None = None,
        spoken_message: str | None = None,
        research_response: JarvisSwingAnalysisResponse | None = None,
        research_explanation: (
            JarvisResearchExplanation
            | JarvisMultiTimeframeResearchExplanation
            | None
        ) = None,
        judge_follow_up: JudgeFollowUpAnswer | None = None,
        session_id: str | None = None,
    ) -> JarvisConversationTurn:
        turn = JarvisConversationTurn(
            session_id=session_id or self._session_id,
            outcome=outcome,
            state_before=state_before,
            state_after=self._state,
            input_channel=utterance.channel,
            display_message=display_message,
            spoken_message=spoken_message,
            research_response=research_response,
            research_explanation=research_explanation,
            judge_follow_up=judge_follow_up,
        )
        if outcome is not ConversationOutcome.IGNORED:
            self._prompt_audit.record(
                PromptAuditEventType.CONVERSATION_OUTPUT,
                PromptAuditActor.JARVIS,
                {
                    "outcome": turn.outcome.value,
                    "state_before": turn.state_before.value,
                    "state_after": turn.state_after.value,
                    "input_channel": turn.input_channel.value,
                    "display_message": turn.display_message,
                    "spoken_message": turn.spoken_message,
                    "research_explanation": (
                        turn.research_explanation.model_dump(mode="json")
                        if turn.research_explanation is not None
                        else None
                    ),
                    "judge_follow_up": (
                        turn.judge_follow_up.model_dump(mode="json")
                        if turn.judge_follow_up is not None
                        else None
                    ),
                },
                session_id=turn.session_id,
                operation_id=(
                    turn.research_response.operation_id
                    if turn.research_response is not None
                    else None
                ),
            )
        return turn

    def _record_input(
        self,
        utterance: JarvisUtterance,
        state_before: ConversationState,
    ) -> None:
        self._prompt_audit.record(
            PromptAuditEventType.CONVERSATION_INPUT,
            PromptAuditActor.USER,
            {
                "content": utterance.text,
                "input_channel": utterance.channel.value,
                "state_before": state_before.value,
            },
            session_id=self._session_id,
        )

    def _clear_session(self) -> None:
        self._session_id = None
        self._emitter = None
        self._pending_confirmation = None

    def _follow_up_context(
        self,
        command: str,
    ) -> tuple[MultiTimeframeEvidenceReview, AgenticDebateResult] | None:
        if (
            self._judge_follow_up_executor is None
            or self._last_multi_timeframe_review is None
            or self._last_multi_timeframe_debate is None
            or not looks_like_analysis_follow_up(command)
        ):
            return None
        return (
            self._last_multi_timeframe_review,
            self._last_multi_timeframe_debate,
        )

    def _remember_multi_timeframe_context(
        self,
        response: JarvisSwingAnalysisResponse,
    ) -> None:
        review = response.multi_timeframe_review
        debate = response.multi_timeframe_debate
        if review is None or debate is None:
            self._clear_multi_timeframe_context()
            return
        self._last_multi_timeframe_review = review
        self._last_multi_timeframe_debate = debate

    def _clear_multi_timeframe_context(self) -> None:
        self._last_multi_timeframe_review = None
        self._last_multi_timeframe_debate = None
