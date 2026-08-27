from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from app.conversation.config import JarvisConversationConfig
from app.conversation.follow_up import looks_like_analysis_follow_up
from app.conversation.pending_confirmation import (
    PendingConfirmation,
    classify_yes_no,
)
from app.conversation.session import TickerResolutionExecutor
from app.conversation.wake_word import (
    NormalizedWakePhraseDetector,
    WakePhraseDetector,
)
from app.exceptions import (
    ApplicationError,
    BrowserOperationConflictError,
    BrowserSessionNotFoundError,
)
from app.models.browser_conversation import (
    BrowserConversationSnapshot,
    BrowserConversationTurn,
    ConversationEventBatch,
    ConversationEventReplayCursor,
)
from app.models.browser_operations import (
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
)
from app.models.conversation import (
    ConversationOutcome,
    ConversationState,
    InputChannel,
    JarvisConversationEvent,
    JarvisUtterance,
)


_TRANSITION_MESSAGES = {
    ConversationState.GREETING: "Jarvis has been activated.",
    ConversationState.LISTENING: "Jarvis is listening for a request.",
    ConversationState.PROCESSING: "Jarvis dispatched the research workflow.",
    ConversationState.RESPONDING: "Jarvis has prepared a response.",
    ConversationState.FAILED: "Jarvis could not complete the request.",
    ConversationState.DORMANT: "Jarvis returned to standby.",
    ConversationState.AWAITING_CONFIRMATION: (
        "Jarvis is waiting for a yes/no confirmation."
    ),
}
_INSTRUMENT_RESOLUTION_FAILURE_CODES = frozenset(
    {"instrument.not_found", "instrument.ambiguous"}
)
_SLEEP_COMMANDS = frozenset(
    {
        "go to sleep",
        "sleep",
        "stand by",
        "standby",
        "thank you jarvis",
        "that's all",
    }
)
_ALLOWED_TRANSITIONS = {
    (ConversationState.DORMANT, ConversationState.GREETING),
    (ConversationState.GREETING, ConversationState.LISTENING),
    (ConversationState.LISTENING, ConversationState.PROCESSING),
    (ConversationState.LISTENING, ConversationState.DORMANT),
    (ConversationState.PROCESSING, ConversationState.RESPONDING),
    (ConversationState.PROCESSING, ConversationState.FAILED),
    (ConversationState.PROCESSING, ConversationState.AWAITING_CONFIRMATION),
    (ConversationState.AWAITING_CONFIRMATION, ConversationState.PROCESSING),
    (ConversationState.AWAITING_CONFIRMATION, ConversationState.LISTENING),
    (ConversationState.RESPONDING, ConversationState.DORMANT),
    (ConversationState.FAILED, ConversationState.DORMANT),
    (ConversationState.FAILED, ConversationState.PROCESSING),
}


@runtime_checkable
class BrowserConversationOperations(Protocol):
    def submit(
        self,
        request: BrowserOperationRequest,
    ) -> BrowserOperationSnapshot:
        ...

    def get_operation(
        self,
        operation_id: str,
    ) -> BrowserOperationSnapshot | None:
        ...

    def get_result(self, operation_id: str) -> BrowserOperationOutput | None:
        ...


@dataclass(slots=True)
class _ConversationRecord:
    snapshot: BrowserConversationSnapshot
    events: list[JarvisConversationEvent] = field(default_factory=list)
    last_idempotency_key: str | None = None
    last_text: str | None = None
    last_channel: InputChannel | None = None
    last_operation_id: str | None = None
    last_command: str | None = None
    pending_confirmation: PendingConfirmation | None = None
    consecutive_resolution_failures: int = 0


class BrowserConversationCoordinator:
    """Wake-aware asynchronous conversation state for browser sessions."""

    def __init__(
        self,
        operations: BrowserConversationOperations,
        config: JarvisConversationConfig,
        *,
        wake_detector: WakePhraseDetector | None = None,
        ticker_resolution_executor: TickerResolutionExecutor | None = None,
    ) -> None:
        if not isinstance(operations, BrowserConversationOperations):
            raise ValueError("browser conversation requires operation services")
        if not isinstance(config, JarvisConversationConfig):
            raise ValueError("browser conversation requires validated settings")
        detector = wake_detector or NormalizedWakePhraseDetector(
            config.wake_phrase
        )
        if not isinstance(detector, WakePhraseDetector):
            raise ValueError("browser conversation requires a wake detector")
        if ticker_resolution_executor is not None and not isinstance(
            ticker_resolution_executor,
            TickerResolutionExecutor,
        ):
            raise ValueError(
                "browser conversation requires a ticker resolution executor"
            )
        self._operations = operations
        self._config = config
        self._wake_detector = detector
        self._ticker_resolution_executor = ticker_resolution_executor
        self._records: dict[str, _ConversationRecord] = {}
        self._lock = RLock()

    def open_session(
        self,
        session_id: str,
        *,
        at: datetime,
    ) -> BrowserConversationSnapshot:
        snapshot = BrowserConversationSnapshot(
            session_id=session_id,
            updated_at=at,
        )
        with self._lock:
            existing = self._records.get(snapshot.session_id)
            if existing is not None:
                if existing.snapshot != snapshot:
                    raise BrowserOperationConflictError(
                        "browser conversation session already exists"
                    )
                return existing.snapshot
            self._records[snapshot.session_id] = _ConversationRecord(snapshot)
            return snapshot

    def close_session(
        self,
        session_id: str,
        *,
        at: datetime,
    ) -> None:
        with self._lock:
            record = self._require(session_id)
            self._reconcile_locked(record, at)
            if record.snapshot.state is ConversationState.PROCESSING:
                raise BrowserOperationConflictError(
                    "processing conversation cannot be closed"
                )
            if record.snapshot.state is not ConversationState.DORMANT:
                self._transition_locked(
                    record,
                    ConversationState.DORMANT,
                    record.last_channel or InputChannel.TEXT,
                    at,
                    display_message="Jarvis is now in standby.",
                    spoken_message="Standing by.",
                )
            del self._records[record.snapshot.session_id]

    def get_snapshot(
        self,
        session_id: str,
        *,
        at: datetime,
    ) -> BrowserConversationSnapshot:
        with self._lock:
            record = self._require(session_id)
            self._reconcile_locked(record, at)
            return record.snapshot

    def handle(
        self,
        session_id: str,
        utterance: JarvisUtterance,
        *,
        idempotency_key: str,
        operation_id_factory: Callable[[], str],
        at: datetime,
    ) -> BrowserConversationTurn:
        if not isinstance(utterance, JarvisUtterance):
            raise ValueError("browser conversation requires an utterance")
        key = _required("idempotency key", idempotency_key)
        if not callable(operation_id_factory):
            raise ValueError("browser operation ID factory must be callable")
        with self._lock:
            record = self._require(session_id)
            duplicate = (
                record.last_idempotency_key == key
                and record.last_text == utterance.text
                and record.last_channel is utterance.channel
                and record.last_operation_id is not None
            )
            self._reconcile_locked(record, at)
            if duplicate:
                operation = self._operations.get_operation(
                    record.last_operation_id
                )
                if operation is None:
                    raise RuntimeError("conversation operation disappeared")
                outcome = (
                    ConversationOutcome.DISPATCHED
                    if record.snapshot.state is ConversationState.PROCESSING
                    else ConversationOutcome.BUSY
                )
                return BrowserConversationTurn(
                    outcome=outcome,
                    conversation=record.snapshot,
                    input_channel=utterance.channel,
                    operation=operation,
                )
            if record.last_idempotency_key == key:
                raise BrowserOperationConflictError(
                    "conversation idempotency key was reused"
                )

            if record.snapshot.state is ConversationState.AWAITING_CONFIRMATION:
                return self._handle_confirmation_reply(
                    record,
                    utterance,
                    key,
                    operation_id_factory,
                    at,
                )

            if record.snapshot.state in {
                ConversationState.PROCESSING,
                ConversationState.RESPONDING,
            }:
                operation = (
                    self._operations.get_operation(
                        record.snapshot.active_operation_id
                    )
                    if record.snapshot.active_operation_id is not None
                    else None
                )
                return BrowserConversationTurn(
                    outcome=ConversationOutcome.BUSY,
                    conversation=record.snapshot,
                    input_channel=utterance.channel,
                    operation=operation,
                )

            command = utterance.text
            detected = self._wake_detector.command_after_wake_phrase(command)
            if record.snapshot.state is ConversationState.DORMANT:
                if detected is None:
                    return BrowserConversationTurn(
                        outcome=ConversationOutcome.IGNORED,
                        conversation=record.snapshot,
                        input_channel=utterance.channel,
                    )
                command = detected
                record.last_channel = utterance.channel
                self._transition_locked(
                    record,
                    ConversationState.GREETING,
                    utterance.channel,
                    at,
                )
                self._transition_locked(
                    record,
                    ConversationState.LISTENING,
                    utterance.channel,
                    at,
                    display_message=(
                        f"Hello {self._config.user_name}. "
                        "How can I help you today?"
                    ),
                    spoken_message=(
                        f"Hello {self._config.user_name}. "
                        "How can I help you today?"
                    ),
                )
            elif detected is not None:
                command = detected
            record.last_channel = utterance.channel

            if not command:
                return BrowserConversationTurn(
                    outcome=ConversationOutcome.ACTIVATED,
                    conversation=record.snapshot,
                    input_channel=utterance.channel,
                )
            if _normalized(command) in _SLEEP_COMMANDS:
                self._transition_locked(
                    record,
                    ConversationState.DORMANT,
                    utterance.channel,
                    at,
                    display_message="Jarvis is now in standby.",
                    spoken_message="Standing by.",
                )
                return BrowserConversationTurn(
                    outcome=ConversationOutcome.COMPLETED,
                    conversation=record.snapshot,
                    input_channel=utterance.channel,
                )

            return self._dispatch(
                record,
                utterance,
                key,
                operation_id_factory,
                at,
                command,
            )

    def _dispatch(
        self,
        record: "_ConversationRecord",
        utterance: JarvisUtterance,
        key: str,
        operation_id_factory: Callable[[], str],
        at: datetime,
        command: str,
    ) -> BrowserConversationTurn:
        """Submit a resolved command as a new operation. Called while
        holding self._lock, either from handle() for a fresh command or
        from _handle_confirmation_reply() after a "yes" resumes a
        pending ticker guess or catalog refresh.
        """
        kind = (
            BrowserOperationKind.JUDGE_FOLLOW_UP
            if record.snapshot.has_follow_up_context
            and looks_like_analysis_follow_up(command)
            else BrowserOperationKind.SWING_ANALYSIS
        )
        operation = self._operations.submit(
            BrowserOperationRequest(
                operation_id=operation_id_factory(),
                session_id=record.snapshot.session_id,
                idempotency_key=key,
                kind=kind,
                input_channel=utterance.channel,
                message=command,
                requested_at=at,
            )
        )
        record.last_idempotency_key = key
        record.last_text = utterance.text
        record.last_channel = utterance.channel
        record.last_operation_id = operation.request.operation_id
        record.last_command = command
        self._transition_locked(
            record,
            ConversationState.PROCESSING,
            utterance.channel,
            at,
            active_operation_id=operation.request.operation_id,
            has_follow_up_context=(
                record.snapshot.has_follow_up_context
                if kind is BrowserOperationKind.JUDGE_FOLLOW_UP
                else False
            ),
            display_message=(
                "Certainly. I’ll coordinate the analysts and return with "
                "the evidence once the Judge has finished."
            ),
            spoken_message=(
                "Certainly. I’ll coordinate the analysts and report back."
            ),
        )
        return BrowserConversationTurn(
            outcome=ConversationOutcome.DISPATCHED,
            conversation=record.snapshot,
            input_channel=utterance.channel,
            operation=operation,
        )

    def _handle_confirmation_reply(
        self,
        record: "_ConversationRecord",
        utterance: JarvisUtterance,
        key: str,
        operation_id_factory: Callable[[], str],
        at: datetime,
    ) -> BrowserConversationTurn:
        """Handle a "yes"/"no" reply while AWAITING_CONFIRMATION. Called
        while holding self._lock.
        """
        pending = record.pending_confirmation
        assert pending is not None
        if classify_yes_no(utterance.text) != "yes":
            record.pending_confirmation = None
            record.consecutive_resolution_failures += 1
            message = (
                "No problem. Let me know the company or NSE symbol you'd "
                "like to analyze."
            )
            self._transition_locked(
                record,
                ConversationState.LISTENING,
                utterance.channel,
                at,
                display_message=message,
                spoken_message=message,
            )
            return BrowserConversationTurn(
                outcome=ConversationOutcome.CLARIFICATION_REQUIRED,
                conversation=record.snapshot,
                input_channel=utterance.channel,
            )

        record.pending_confirmation = None
        if pending.kind == "catalog_refresh":
            if self._ticker_resolution_executor is not None:
                try:
                    self._ticker_resolution_executor.refresh_catalog()
                except ApplicationError:
                    # Resolution below will simply fail again and route
                    # through the normal failure handling; a refresh
                    # failure must not crash the turn.
                    pass
            command = pending.original_command
        else:
            command = f"Analyze {pending.chosen_symbol} for a swing trade"
        return self._dispatch(
            record,
            utterance,
            key,
            operation_id_factory,
            at,
            command,
        )

    def sleep(
        self,
        session_id: str,
        *,
        at: datetime,
    ) -> BrowserConversationSnapshot:
        with self._lock:
            record = self._require(session_id)
            self._reconcile_locked(record, at)
            if record.snapshot.state is ConversationState.PROCESSING:
                raise BrowserOperationConflictError(
                    "processing conversation cannot sleep"
                )
            if record.snapshot.state is not ConversationState.DORMANT:
                self._transition_locked(
                    record,
                    ConversationState.DORMANT,
                    record.last_channel or InputChannel.TEXT,
                    at,
                    display_message="Jarvis is now in standby.",
                    spoken_message="Standing by.",
                )
            return record.snapshot

    def replay(
        self,
        cursor: ConversationEventReplayCursor,
    ) -> ConversationEventBatch:
        if not isinstance(cursor, ConversationEventReplayCursor):
            raise ValueError("conversation replay requires a validated cursor")
        with self._lock:
            record = self._require(cursor.session_id)
            if cursor.after_sequence > record.snapshot.last_event_sequence:
                raise BrowserOperationConflictError(
                    "conversation replay cursor is beyond recorded progress"
                )
            available = [
                event
                for event in record.events
                if event.sequence > cursor.after_sequence
            ]
            page = tuple(available[: cursor.limit])
            next_sequence = page[-1].sequence if page else cursor.after_sequence
            return ConversationEventBatch(
                session_id=cursor.session_id,
                after_sequence=cursor.after_sequence,
                events=page,
                next_sequence=next_sequence,
                has_more=len(available) > len(page),
            )

    def _reconcile_locked(
        self,
        record: _ConversationRecord,
        at: datetime,
    ) -> None:
        operation_id = record.snapshot.active_operation_id
        if (
            record.snapshot.state is not ConversationState.PROCESSING
            or operation_id is None
        ):
            return
        operation = self._operations.get_operation(operation_id)
        if operation is None:
            raise RuntimeError("active conversation operation disappeared")
        if not operation.status.terminal:
            return
        if operation.status is BrowserOperationStatus.COMPLETED:
            record.consecutive_resolution_failures = 0
            output = self._operations.get_result(operation_id)
            if output is None:
                raise RuntimeError("completed conversation result disappeared")
            display, spoken = _completed_messages(output)
            has_context = record.snapshot.has_follow_up_context
            if operation.request.kind is BrowserOperationKind.SWING_ANALYSIS:
                response = output.research_response
                has_context = bool(
                    response is not None
                    and response.multi_timeframe_review is not None
                    and response.multi_timeframe_debate is not None
                )
            self._transition_locked(
                record,
                ConversationState.RESPONDING,
                operation.request.input_channel,
                at,
                active_operation_id=None,
                has_follow_up_context=has_context,
                display_message=display,
                spoken_message=spoken,
            )
            return
        if operation.status is BrowserOperationStatus.CANCELLED:
            self._transition_locked(
                record,
                ConversationState.RESPONDING,
                operation.request.input_channel,
                at,
                active_operation_id=None,
                display_message="The operation was cancelled. Jarvis is standing by.",
                spoken_message="Operation cancelled. Standing by.",
            )
            return
        failure = operation.failure
        if (
            operation.request.kind is BrowserOperationKind.SWING_ANALYSIS
            and self._ticker_resolution_executor is not None
            and failure is not None
            and failure.code in _INSTRUMENT_RESOLUTION_FAILURE_CODES
            and self._attempt_ticker_resolution_locked(record, operation, at)
        ):
            return
        self._transition_locked(
            record,
            ConversationState.FAILED,
            operation.request.input_channel,
            at,
            active_operation_id=None,
            display_message=(
                failure.message
                if failure is not None
                else "Jarvis could not complete the operation."
            ),
            spoken_message="I couldn't complete that safely. Please try again.",
        )

    def _attempt_ticker_resolution_locked(
        self,
        record: _ConversationRecord,
        operation: BrowserOperationSnapshot,
        at: datetime,
    ) -> bool:
        """Called from _reconcile_locked (holding self._lock) when a
        SWING_ANALYSIS operation failed on instrument resolution. Tries
        the conversational ticker-resolution fallback and, if it has
        something to propose, pivots the conversation into
        AWAITING_CONFIRMATION instead of a flat failure. Returns True if
        it took over the transition, False to let the caller fall back
        to the normal FAILED transition using the operation's original
        failure message.

        Runs the resolution attempt (a real LLM call) while holding the
        coordinator's shared lock -- acceptable for a single-user local
        deployment; a multi-session production deployment would want
        this call moved outside the lock.
        """
        assert self._ticker_resolution_executor is not None
        command = record.last_command
        if command is None:
            return False
        try:
            result = self._ticker_resolution_executor.attempt(command)
        except ApplicationError:
            return False

        channel = operation.request.input_channel
        if result.outcome == "resolved_needs_confirmation":
            record.pending_confirmation = PendingConfirmation(
                kind="ticker_guess",
                original_command=command,
                chosen_symbol=result.chosen_symbol,
                exchange=result.exchange,
            )
            message = f'Did you mean "{result.chosen_symbol}"? Reply yes or no.'
            self._transition_locked(
                record,
                ConversationState.AWAITING_CONFIRMATION,
                channel,
                at,
                active_operation_id=None,
                display_message=message,
                spoken_message=message,
            )
            return True

        record.consecutive_resolution_failures += 1
        threshold = (
            self._config.consecutive_resolution_failures_before_refresh_prompt
        )
        if record.consecutive_resolution_failures < threshold:
            return False

        record.pending_confirmation = PendingConfirmation(
            kind="catalog_refresh",
            original_command=command,
        )
        message = (
            "I'm having trouble matching that company against my "
            "current list. Should I refresh it and try again? Reply "
            "yes or no."
        )
        self._transition_locked(
            record,
            ConversationState.AWAITING_CONFIRMATION,
            channel,
            at,
            active_operation_id=None,
            display_message=message,
            spoken_message=message,
        )
        return True

    def _transition_locked(
        self,
        record: _ConversationRecord,
        target: ConversationState,
        channel,
        at: datetime,
        *,
        active_operation_id=...,
        has_follow_up_context: bool | None = None,
        display_message: str | None = None,
        spoken_message: str | None = None,
    ) -> None:
        previous = record.snapshot
        if at < previous.updated_at:
            raise BrowserOperationConflictError(
                "browser conversation time cannot move backwards"
            )
        if (previous.state, target) not in _ALLOWED_TRANSITIONS:
            raise RuntimeError(
                "unsupported browser conversation transition: "
                f"{previous.state.value} -> {target.value}"
            )
        sequence = previous.last_event_sequence + 1
        event = JarvisConversationEvent(
            event_id=f"{previous.session_id}:{sequence}",
            session_id=previous.session_id,
            sequence=sequence,
            from_state=previous.state,
            to_state=target,
            input_channel=channel,
            occurred_at=at,
            message=_TRANSITION_MESSAGES[target],
        )
        resolved_operation_id = (
            previous.active_operation_id
            if active_operation_id is ...
            else active_operation_id
        )
        record.snapshot = BrowserConversationSnapshot(
            session_id=previous.session_id,
            state=target,
            updated_at=at,
            last_event_sequence=sequence,
            active_operation_id=resolved_operation_id,
            has_follow_up_context=(
                previous.has_follow_up_context
                if has_follow_up_context is None
                else has_follow_up_context
            ),
            display_message=display_message,
            spoken_message=spoken_message,
        )
        record.events.append(event)

    def _require(self, session_id: str) -> _ConversationRecord:
        normalized = _required("session ID", session_id)
        try:
            return self._records[normalized]
        except KeyError as exc:
            raise BrowserSessionNotFoundError(
                "browser conversation session was not found"
            ) from exc


def _completed_messages(output: BrowserOperationOutput) -> tuple[str, str]:
    if output.judge_follow_up is not None:
        return output.judge_follow_up.answer, output.judge_follow_up.answer
    if output.research_explanation is not None:
        message = output.research_explanation.executive_briefing
        return message, message
    if output.presentation_failure is not None:
        return output.presentation_failure.message, output.presentation_failure.message
    return (
        "Jarvis completed the evidence-grounded research operation.",
        "I've completed the evidence-grounded research operation.",
    )


def _required(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"browser {label} must not be blank")
    return value.strip()


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split()).strip(".!?,;:")
