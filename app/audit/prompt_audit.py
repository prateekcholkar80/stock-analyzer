import json
import os
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Protocol, runtime_checkable
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.logging_config import (
    get_logger,
    get_operation_id,
    redact_for_logging,
)


IST = ZoneInfo("Asia/Kolkata")
IST_OFFSET = timedelta(hours=5, minutes=30)
AuditClock = Callable[[], datetime]
logger = get_logger(__name__)

_AUDIT_SESSION_ID: ContextVar[str | None] = ContextVar(
    "jarvis_prompt_audit_session_id",
    default=None,
)


class PromptAuditActor(StrEnum):
    USER = "user"
    JARVIS = "jarvis"
    BULL = "bull"
    BEAR = "bear"
    JUDGE = "judge"


class PromptAuditEventType(StrEnum):
    CONVERSATION_INPUT = "conversation_input"
    CONVERSATION_OUTPUT = "conversation_output"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    LLM_FAILURE = "llm_failure"


class PromptAuditConfig(BaseModel):
    """Explicit opt-in controls for sensitive prompt/conversation auditing."""

    model_config = ConfigDict(frozen=True, strict=True)

    enabled: bool = False
    path: Path = Path("logs/jarvis-prompt-audit.jsonl")

    @field_validator("path")
    @classmethod
    def require_jsonl_file(cls, value: Path) -> Path:
        if value.name in {"", ".", ".."} or value.suffix.lower() != ".jsonl":
            raise ValueError("prompt audit path must name a .jsonl file")
        return value

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "PromptAuditConfig":
        source = os.environ if environment is None else environment
        enabled = _parse_enabled(
            source.get("JARVIS_PROMPT_AUDIT_ENABLED", "false")
        )
        raw_path = source.get(
            "JARVIS_PROMPT_AUDIT_PATH",
            "logs/jarvis-prompt-audit.jsonl",
        )
        return cls(enabled=enabled, path=Path(raw_path))


@runtime_checkable
class PromptAuditSink(Protocol):
    def publish(self, record: Mapping[str, Any]) -> None:
        ...


class NullPromptAuditSink:
    def publish(self, record: Mapping[str, Any]) -> None:
        return None


class InMemoryPromptAuditSink:
    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._lock = RLock()

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(record) for record in self._records)

    def publish(self, record: Mapping[str, Any]) -> None:
        if not isinstance(record, Mapping):
            raise ValueError("prompt audit sink requires a mapping")
        with self._lock:
            self._records.append(dict(record))


class JsonlPromptAuditSink:
    """Append prompt audit records to a private, thread-safe JSONL file."""

    def __init__(self, path: Path | str) -> None:
        resolved = Path(path)
        if (
            resolved.name in {"", ".", ".."}
            or resolved.suffix.lower() != ".jsonl"
        ):
            raise ValueError("prompt audit path must name a .jsonl file")
        self.path = resolved
        self._lock = RLock()

    def publish(self, record: Mapping[str, Any]) -> None:
        if not isinstance(record, Mapping):
            raise ValueError("prompt audit sink requires a mapping")
        serialized = json.dumps(
            dict(record),
            default=str,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        payload = f"{serialized}\n".encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                _write_all(descriptor, payload)
            finally:
                os.close(descriptor)


class PromptAuditRecorder:
    """Build, redact, and safely deliver review-only audit records."""

    def __init__(
        self,
        sink: PromptAuditSink | None = None,
        *,
        clock: AuditClock | None = None,
    ) -> None:
        resolved_sink = sink if sink is not None else NullPromptAuditSink()
        if not isinstance(resolved_sink, PromptAuditSink):
            raise ValueError("prompt audit recorder requires an audit sink")
        resolved_clock = clock or (lambda: datetime.now(IST))
        if not callable(resolved_clock):
            raise ValueError("prompt audit recorder clock must be callable")
        self._sink = resolved_sink
        self._clock = resolved_clock

    def record(
        self,
        event_type: PromptAuditEventType,
        actor: PromptAuditActor,
        payload: Mapping[str, Any],
        *,
        session_id: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        if not isinstance(event_type, PromptAuditEventType):
            raise ValueError("prompt audit event type must be validated")
        if not isinstance(actor, PromptAuditActor):
            raise ValueError("prompt audit actor must be validated")
        if not isinstance(payload, Mapping):
            raise ValueError("prompt audit payload must be a mapping")
        occurred_at = self._clock()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() != IST_OFFSET:
            raise ValueError("prompt audit timestamp must be in IST")
        active_session_id = session_id or get_prompt_audit_session_id()
        active_operation_id = operation_id or get_operation_id()
        record = {
            "schema_version": "jarvis.prompt_audit.v1",
            "record_id": uuid4().hex,
            "occurred_at": occurred_at.isoformat(),
            "event_type": event_type.value,
            "actor": actor.value,
            "session_id": active_session_id,
            "operation_id": active_operation_id,
            "payload": redact_for_logging(dict(payload)),
        }
        try:
            self._sink.publish(record)
        except Exception as exc:
            logger.error(
                "Jarvis prompt audit delivery failed",
                extra={
                    "event": "jarvis.prompt_audit.delivery_failed",
                    "audit_event_type": event_type.value,
                    "audit_actor": actor.value,
                    "error_type": type(exc).__name__,
                },
            )


def prompt_audit_sink_from_config(
    config: PromptAuditConfig | None = None,
) -> PromptAuditSink:
    resolved = config or PromptAuditConfig.from_environment()
    if not isinstance(resolved, PromptAuditConfig):
        raise ValueError("prompt audit requires validated configuration")
    if not resolved.enabled:
        return NullPromptAuditSink()
    return JsonlPromptAuditSink(resolved.path)


def get_prompt_audit_session_id() -> str | None:
    return _AUDIT_SESSION_ID.get()


@contextmanager
def prompt_audit_session_context(session_id: str) -> Iterator[str]:
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("prompt audit session ID must not be blank")
    normalized = session_id.strip()
    token = _AUDIT_SESSION_ID.set(normalized)
    try:
        yield normalized
    finally:
        _AUDIT_SESSION_ID.reset(token)


def _parse_enabled(value: str) -> bool:
    if not isinstance(value, str):
        raise ValueError("prompt audit enabled setting must be text")
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError("prompt audit enabled setting must be true or false")


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("prompt audit write did not make progress")
        remaining = remaining[written:]
