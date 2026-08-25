from datetime import datetime
from threading import RLock
from typing import Protocol, runtime_checkable

from app.exceptions import (
    BrowserOperationConflictError,
    BrowserOperationNotFoundError,
    BrowserSessionNotFoundError,
)
from app.models.browser_operations import (
    BrowserOperationFailure,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    BrowserSessionState,
    WorkflowEventBatch,
    WorkflowEventReplayCursor,
)
from app.models.workflow import JarvisWorkflowEvent


@runtime_checkable
class BrowserOperationRegistry(Protocol):
    """Transport-neutral session, operation, and event-replay boundary."""

    def open_session(self, session: BrowserSessionSnapshot) -> BrowserSessionSnapshot:
        ...

    def get_session(self, session_id: str) -> BrowserSessionSnapshot | None:
        ...

    def close_session(
        self,
        session_id: str,
        *,
        closed_at: datetime,
    ) -> BrowserSessionSnapshot:
        ...

    def submit(self, request: BrowserOperationRequest) -> BrowserOperationSnapshot:
        ...

    def get_operation(
        self,
        operation_id: str,
    ) -> BrowserOperationSnapshot | None:
        ...

    def mark_running(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        ...

    def request_cancellation(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        ...

    def mark_cancelled(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        ...

    def mark_completed(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        ...

    def mark_failed(
        self,
        operation_id: str,
        failure: BrowserOperationFailure,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        ...

    def publish(self, event: JarvisWorkflowEvent) -> None:
        ...

    def replay(self, cursor: WorkflowEventReplayCursor) -> WorkflowEventBatch:
        ...


class InMemoryBrowserOperationRegistry:
    """Thread-safe local registry and workflow-event sink for the first UI."""

    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSessionSnapshot] = {}
        self._operations: dict[str, BrowserOperationSnapshot] = {}
        self._events: dict[str, list[JarvisWorkflowEvent]] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = RLock()

    def open_session(
        self,
        session: BrowserSessionSnapshot,
    ) -> BrowserSessionSnapshot:
        if not isinstance(session, BrowserSessionSnapshot):
            raise ValueError("browser registry requires a session snapshot")
        if session.active_operation_id is not None:
            raise BrowserOperationConflictError(
                "new browser session cannot claim an unknown active operation"
            )
        with self._lock:
            existing = self._sessions.get(session.session_id)
            if existing is None:
                self._sessions[session.session_id] = session
                return session
            if existing != session:
                raise BrowserOperationConflictError(
                    "browser session identity already has different state"
                )
            return existing

    def get_session(self, session_id: str) -> BrowserSessionSnapshot | None:
        with self._lock:
            return self._sessions.get(_identifier("session", session_id))

    def close_session(
        self,
        session_id: str,
        *,
        closed_at: datetime,
    ) -> BrowserSessionSnapshot:
        with self._lock:
            session = self._require_session(session_id)
            if session.state is BrowserSessionState.CLOSED:
                return session
            if session.active_operation_id is not None:
                raise BrowserOperationConflictError(
                    "browser session cannot close while an operation is active"
                )
            closed = BrowserSessionSnapshot(
                **(
                    session.model_dump()
                    | {
                        "state": BrowserSessionState.CLOSED,
                        "updated_at": closed_at,
                    }
                )
            )
            self._sessions[session.session_id] = closed
            return closed

    def submit(
        self,
        request: BrowserOperationRequest,
    ) -> BrowserOperationSnapshot:
        if not isinstance(request, BrowserOperationRequest):
            raise ValueError("browser registry requires an operation request")
        with self._lock:
            session = self._require_session(request.session_id)
            if session.state is BrowserSessionState.CLOSED:
                raise BrowserOperationConflictError(
                    "closed browser session cannot accept operations"
                )
            idempotency_identity = (
                request.session_id,
                request.idempotency_key,
            )
            original_id = self._idempotency.get(idempotency_identity)
            if original_id is not None:
                original = self._operations[original_id]
                if original.request.idempotent_payload != request.idempotent_payload:
                    raise BrowserOperationConflictError(
                        "idempotency key was reused for a different request"
                    )
                return original
            if request.requested_at < session.updated_at:
                raise BrowserOperationConflictError(
                    "operation request cannot precede session state"
                )
            existing = self._operations.get(request.operation_id)
            if existing is not None:
                if existing.request == request:
                    return existing
                raise BrowserOperationConflictError(
                    "operation identity already has different content"
                )
            if session.active_operation_id is not None:
                raise BrowserOperationConflictError(
                    "browser session already has an active operation"
                )
            snapshot = BrowserOperationSnapshot(
                request=request,
                status=BrowserOperationStatus.QUEUED,
                updated_at=request.requested_at,
            )
            self._operations[request.operation_id] = snapshot
            self._events[request.operation_id] = []
            self._idempotency[idempotency_identity] = request.operation_id
            self._sessions[session.session_id] = BrowserSessionSnapshot(
                **(
                    session.model_dump()
                    | {
                        "active_operation_id": request.operation_id,
                        "updated_at": request.requested_at,
                    }
                )
            )
            return snapshot

    def get_operation(
        self,
        operation_id: str,
    ) -> BrowserOperationSnapshot | None:
        with self._lock:
            return self._operations.get(
                _identifier("operation", operation_id)
            )

    def mark_running(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        return self._transition(
            operation_id,
            BrowserOperationStatus.RUNNING,
            at=at,
            allowed_from={BrowserOperationStatus.QUEUED},
        )

    def request_cancellation(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        with self._lock:
            current = self._require_operation(operation_id)
            if current.status in {
                BrowserOperationStatus.CANCELLATION_REQUESTED,
                BrowserOperationStatus.CANCELLED,
            }:
                return current
            if current.status is BrowserOperationStatus.QUEUED:
                return self._transition_locked(
                    current,
                    BrowserOperationStatus.CANCELLED,
                    at=at,
                    cancellation_requested_at=at,
                )
            if current.status is BrowserOperationStatus.RUNNING:
                return self._transition_locked(
                    current,
                    BrowserOperationStatus.CANCELLATION_REQUESTED,
                    at=at,
                    cancellation_requested_at=at,
                )
            raise BrowserOperationConflictError(
                "terminal operation cannot be cancelled"
            )

    def mark_cancelled(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        return self._transition(
            operation_id,
            BrowserOperationStatus.CANCELLED,
            at=at,
            allowed_from={BrowserOperationStatus.CANCELLATION_REQUESTED},
        )

    def mark_completed(
        self,
        operation_id: str,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        return self._transition(
            operation_id,
            BrowserOperationStatus.COMPLETED,
            at=at,
            allowed_from={
                BrowserOperationStatus.RUNNING,
                BrowserOperationStatus.CANCELLATION_REQUESTED,
            },
            result_available=True,
        )

    def mark_failed(
        self,
        operation_id: str,
        failure: BrowserOperationFailure,
        *,
        at: datetime,
    ) -> BrowserOperationSnapshot:
        if not isinstance(failure, BrowserOperationFailure):
            raise ValueError("failed operation requires a safe failure")
        return self._transition(
            operation_id,
            BrowserOperationStatus.FAILED,
            at=at,
            allowed_from={
                BrowserOperationStatus.RUNNING,
                BrowserOperationStatus.CANCELLATION_REQUESTED,
            },
            failure=failure,
        )

    def publish(self, event: JarvisWorkflowEvent) -> None:
        if not isinstance(event, JarvisWorkflowEvent):
            raise ValueError("operation registry requires a workflow event")
        with self._lock:
            current = self._require_operation(event.operation_id)
            if current.status.terminal:
                raise BrowserOperationConflictError(
                    "terminal operation cannot accept new workflow events"
                )
            expected = current.last_event_sequence + 1
            if event.sequence != expected:
                raise BrowserOperationConflictError(
                    "workflow event sequence must be contiguous"
                )
            if event.occurred_at < current.updated_at:
                raise BrowserOperationConflictError(
                    "workflow event cannot precede operation state"
                )
            self._events[event.operation_id].append(event)
            self._operations[event.operation_id] = BrowserOperationSnapshot(
                **(
                    current.model_dump()
                    | {
                        "last_event_sequence": event.sequence,
                        "updated_at": event.occurred_at,
                    }
                )
            )
            self._touch_session(
                current.request.session_id,
                event.occurred_at,
            )

    def replay(self, cursor: WorkflowEventReplayCursor) -> WorkflowEventBatch:
        if not isinstance(cursor, WorkflowEventReplayCursor):
            raise ValueError("event replay requires a validated cursor")
        with self._lock:
            current = self._require_operation(cursor.operation_id)
            if cursor.after_sequence > current.last_event_sequence:
                raise BrowserOperationConflictError(
                    "event replay cursor is beyond the recorded sequence"
                )
            available = [
                event
                for event in self._events[cursor.operation_id]
                if event.sequence > cursor.after_sequence
            ]
            page = tuple(available[: cursor.limit])
            next_sequence = (
                page[-1].sequence if page else cursor.after_sequence
            )
            return WorkflowEventBatch(
                operation_id=cursor.operation_id,
                after_sequence=cursor.after_sequence,
                events=page,
                next_sequence=next_sequence,
                has_more=len(available) > len(page),
            )

    def _transition(
        self,
        operation_id: str,
        target: BrowserOperationStatus,
        *,
        at: datetime,
        allowed_from: set[BrowserOperationStatus],
        result_available: bool = False,
        failure: BrowserOperationFailure | None = None,
    ) -> BrowserOperationSnapshot:
        with self._lock:
            current = self._require_operation(operation_id)
            if current.status is target:
                if (
                    target is BrowserOperationStatus.FAILED
                    and current.failure != failure
                ):
                    raise BrowserOperationConflictError(
                        "failed operation cannot be rewritten"
                    )
                return current
            if current.status not in allowed_from:
                raise BrowserOperationConflictError(
                    f"operation cannot transition from {current.status.value} "
                    f"to {target.value}"
                )
            return self._transition_locked(
                current,
                target,
                at=at,
                result_available=result_available,
                failure=failure,
            )

    def _transition_locked(
        self,
        current: BrowserOperationSnapshot,
        target: BrowserOperationStatus,
        *,
        at: datetime,
        result_available: bool = False,
        cancellation_requested_at=None,
        failure: BrowserOperationFailure | None = None,
    ) -> BrowserOperationSnapshot:
        if at < current.updated_at:
            raise BrowserOperationConflictError(
                "operation transition time cannot move backwards"
            )
        cancellation_time = (
            cancellation_requested_at
            if cancellation_requested_at is not None
            else current.cancellation_requested_at
        )
        updated = BrowserOperationSnapshot(
            request=current.request,
            status=target,
            updated_at=at,
            last_event_sequence=current.last_event_sequence,
            result_available=result_available,
            cancellation_requested_at=cancellation_time,
            failure=failure,
        )
        self._operations[current.request.operation_id] = updated
        if target.terminal:
            self._release_session_operation(current.request.session_id, at)
        else:
            self._touch_session(current.request.session_id, at)
        return updated

    def _require_session(self, session_id: str) -> BrowserSessionSnapshot:
        normalized = _identifier("session", session_id)
        try:
            return self._sessions[normalized]
        except KeyError as exc:
            raise BrowserSessionNotFoundError(
                "browser session was not found"
            ) from exc

    def _require_operation(
        self,
        operation_id: str,
    ) -> BrowserOperationSnapshot:
        normalized = _identifier("operation", operation_id)
        try:
            return self._operations[normalized]
        except KeyError as exc:
            raise BrowserOperationNotFoundError(
                "browser operation was not found"
            ) from exc

    def _touch_session(self, session_id: str, at: datetime) -> None:
        session = self._sessions[session_id]
        if at < session.updated_at:
            raise BrowserOperationConflictError(
                "session update time cannot move backwards"
            )
        self._sessions[session_id] = BrowserSessionSnapshot(
            **(session.model_dump() | {"updated_at": at})
        )

    def _release_session_operation(
        self,
        session_id: str,
        at: datetime,
    ) -> None:
        session = self._sessions[session_id]
        self._sessions[session_id] = BrowserSessionSnapshot(
            **(
                session.model_dump()
                | {"active_operation_id": None, "updated_at": at}
            )
        )


def _identifier(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"browser {label} ID must not be blank")
    return value.strip()
