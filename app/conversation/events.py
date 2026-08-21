from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from app.logging_config import get_logger
from app.models.conversation import (
    ConversationState,
    InputChannel,
    JarvisConversationEvent,
)


IST = ZoneInfo("Asia/Kolkata")
EventClock = Callable[[], datetime]
logger = get_logger(__name__)


_TRANSITION_MESSAGES = {
    ConversationState.GREETING: "Jarvis has been activated.",
    ConversationState.LISTENING: "Jarvis is listening for a request.",
    ConversationState.PROCESSING: "Jarvis is processing the request.",
    ConversationState.RESPONDING: "Jarvis has prepared a response.",
    ConversationState.FAILED: "Jarvis could not complete the request.",
    ConversationState.DORMANT: "Jarvis returned to standby.",
}


@runtime_checkable
class ConversationEventSink(Protocol):
    def publish(self, event: JarvisConversationEvent) -> None:
        ...


class NullConversationEventSink:
    def publish(self, event: JarvisConversationEvent) -> None:
        return None


class InMemoryConversationEventSink:
    def __init__(self) -> None:
        self._events: list[JarvisConversationEvent] = []
        self._lock = RLock()

    @property
    def events(self) -> tuple[JarvisConversationEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def publish(self, event: JarvisConversationEvent) -> None:
        if not isinstance(event, JarvisConversationEvent):
            raise ValueError("conversation sink requires a validated event")
        with self._lock:
            self._events.append(event)


class ConversationEventEmitter:
    def __init__(
        self,
        session_id: str,
        sink: ConversationEventSink | None = None,
        *,
        clock: EventClock | None = None,
    ) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("conversation session ID must not be blank")
        resolved_sink = sink if sink is not None else NullConversationEventSink()
        if not isinstance(resolved_sink, ConversationEventSink):
            raise ValueError("conversation emitter requires an event sink")
        self._session_id = session_id.strip()
        self._sink = resolved_sink
        self._clock = clock or (lambda: datetime.now(IST))
        self._sequence = 0
        self._lock = RLock()

    def emit(
        self,
        from_state: ConversationState,
        to_state: ConversationState,
        channel: InputChannel,
    ) -> JarvisConversationEvent:
        with self._lock:
            self._sequence += 1
            event = JarvisConversationEvent(
                event_id=f"{self._session_id}:{self._sequence}",
                session_id=self._session_id,
                sequence=self._sequence,
                from_state=from_state,
                to_state=to_state,
                input_channel=channel,
                occurred_at=self._clock(),
                message=_TRANSITION_MESSAGES[to_state],
            )
            try:
                self._sink.publish(event)
            except Exception as exc:
                logger.error(
                    "Jarvis conversation event delivery failed",
                    extra={
                        "event": "jarvis.conversation_event.delivery_failed",
                        "conversation_state": to_state.value,
                        "error_type": type(exc).__name__,
                    },
                )
            return event
