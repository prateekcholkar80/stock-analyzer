from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable
from uuid import uuid4

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


@runtime_checkable
class SwingResearchExecutor(Protocol):
    def execute(
        self,
        text: str,
        *,
        to_date: datetime | None = None,
    ) -> JarvisSwingAnalysisResponse:
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
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(research_executor, SwingResearchExecutor):
            raise ValueError("conversation requires a swing research executor")
        if not isinstance(config, JarvisConversationConfig):
            raise ValueError("conversation requires validated settings")
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
        self._config = config
        self._wake_detector = detector
        self._event_sink = event_sink
        self._session_id_factory = id_factory
        self._state = ConversationState.DORMANT
        self._session_id: str | None = None
        self._emitter: ConversationEventEmitter | None = None
        self._lock = RLock()

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
            response = self._research_executor.execute(
                command,
                to_date=to_date,
            )
            if not isinstance(response, JarvisSwingAnalysisResponse):
                raise ValueError(
                    "research executor returned an invalid response"
                )
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
                self._transition(ConversationState.FAILED, utterance.channel)
                self._transition(ConversationState.DORMANT, utterance.channel)
                self._clear_session()
            raise

        with self._lock:
            self._transition(ConversationState.RESPONDING, utterance.channel)
            if response.status is JarvisCommandStatus.COMPLETED:
                outcome = ConversationOutcome.COMPLETED
                display_message = "Jarvis completed the swing-trade analysis."
                spoken_message = "I've completed the swing-trade analysis."
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
        session_id: str | None = None,
    ) -> JarvisConversationTurn:
        return JarvisConversationTurn(
            session_id=session_id or self._session_id,
            outcome=outcome,
            state_before=state_before,
            state_after=self._state,
            input_channel=utterance.channel,
            display_message=display_message,
            spoken_message=spoken_message,
            research_response=research_response,
        )

    def _clear_session(self) -> None:
        self._session_id = None
        self._emitter = None

