from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable
from uuid import uuid4

from app.audit.prompt_audit import (
    PromptAuditActor,
    PromptAuditEventType,
    PromptAuditRecorder,
    PromptAuditSink,
    prompt_audit_session_context,
)
from app.conversation.config import JarvisConversationConfig
from app.conversation.events import (
    ConversationEventEmitter,
    ConversationEventSink,
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


_ALLOWED_TRANSITIONS = {
    (ConversationState.DORMANT, ConversationState.GREETING),
    (ConversationState.GREETING, ConversationState.LISTENING),
    (ConversationState.LISTENING, ConversationState.PROCESSING),
    (ConversationState.PROCESSING, ConversationState.RESPONDING),
    (ConversationState.PROCESSING, ConversationState.LISTENING),
    (ConversationState.PROCESSING, ConversationState.FAILED),
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

        with self._lock:
            state_before = self._state
            if self._state in {
                ConversationState.PROCESSING,
                ConversationState.RESPONDING,
            }:
                self._record_input(utterance, state_before)
                return self._turn(
                    utterance,
                    state_before=state_before,
                    outcome=ConversationOutcome.BUSY,
                    display_message="Jarvis is already working on a request.",
                    spoken_message="One moment. I'm still working on that.",
                )

            command = utterance.text
            detected_command = self._wake_detector.command_after_wake_phrase(
                utterance.text
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
            return self._clarification_turn(
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

    def _follow_up_context(
        self,
        command: str,
    ) -> tuple[MultiTimeframeEvidenceReview, AgenticDebateResult] | None:
        if (
            self._judge_follow_up_executor is None
            or self._last_multi_timeframe_review is None
            or self._last_multi_timeframe_debate is None
            or not _looks_like_analysis_follow_up(command)
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


_FOLLOW_UP_TERMS = frozenset(
    {
        "support",
        "resistance",
        "pivot",
        "daily",
        "weekly",
        "evidence",
        "judge",
        "verdict",
        "conclusion",
        "explain",
        "why",
        "tell me more",
    }
)
_NEW_ANALYSIS_TERMS = (
    "analyze ",
    "analyse ",
    "analysis of ",
    "research ",
    "swing trade",
    "how is ",
    "how's ",
    "how does ",
    "look at ",
)


def _looks_like_analysis_follow_up(command: str) -> bool:
    normalized = " ".join(command.casefold().split())
    if any(term in normalized for term in _NEW_ANALYSIS_TERMS):
        return False
    return any(term in normalized for term in _FOLLOW_UP_TERMS)
