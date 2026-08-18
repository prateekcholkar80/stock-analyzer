import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


LOGGER_NAMESPACE = "jarvis"
REDACTED_VALUE = "[REDACTED]"
_HANDLER_MARKER = "_jarvis_structured_handler"

_OPERATION_ID: ContextVar[str | None] = ContextVar(
    "jarvis_operation_id",
    default=None,
)

_STANDARD_LOG_ATTRIBUTES = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "operation_id",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}

_SENSITIVE_FIELD_NAMES = {
    "apikey",
    "angelapikey",
    "clientcode",
    "angelclientcode",
    "pin",
    "angelpin",
    "password",
    "totp",
    "totpsecret",
    "angeltotpsecret",
    "authorization",
    "accesstoken",
    "refreshtoken",
    "feedtoken",
    "jwttoken",
    "clientsecret",
}

_BEARER_PATTERN = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+",
    flags=re.IGNORECASE,
)

_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"""
    (?P<prefix>
        ["']?
        \b
        (?:
            angel[_-]?api[_-]?key
            | api[_-]?key
            | angel[_-]?client[_-]?code
            | client[_-]?code
            | angel[_-]?pin
            | pin
            | password
            | angel[_-]?totp[_-]?secret
            | totp[_-]?secret
            | totp
            | access[_-]?token
            | refresh[_-]?token
            | feed[_-]?token
            | jwt[_-]?token
            | client[_-]?secret
        )
        \b
        ["']?
        \s*[:=]\s*
        ["']?
    )
    (?P<value>[^\s,"'}\]]+)
    (?P<suffix>["']?)
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


def _normalize_field_name(name: object) -> str:
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(name).lower(),
    )


def _is_sensitive_field(name: object) -> bool:
    return (
        _normalize_field_name(name)
        in _SENSITIVE_FIELD_NAMES
    )


def _redact_text(value: str) -> str:
    redacted = _BEARER_PATTERN.sub(
        f"Bearer {REDACTED_VALUE}",
        value,
    )

    return _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}"
            f"{REDACTED_VALUE}"
            f"{match.group('suffix')}"
        ),
        redacted,
    )


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED_VALUE
                if _is_sensitive_field(key)
                else _redact_value(item)
            )
            for key, item in value.items()
        }

    if isinstance(
        value,
        (list, tuple, set, frozenset),
    ):
        return [
            _redact_value(item)
            for item in value
        ]

    if isinstance(value, str):
        return _redact_text(value)

    return value


def create_operation_id() -> str:
    return uuid4().hex


def get_operation_id() -> str | None:
    return _OPERATION_ID.get()


@contextmanager
def operation_context(
    operation_id: str | None = None,
) -> Iterator[str]:
    active_operation_id = (
        operation_id or create_operation_id()
    )

    token = _OPERATION_ID.set(
        active_operation_id
    )

    try:
        yield active_operation_id
    finally:
        _OPERATION_ID.reset(token)


class OperationContextFilter(logging.Filter):
    def filter(
        self,
        record: logging.LogRecord,
    ) -> bool:
        if not hasattr(record, "operation_id"):
            operation_id = get_operation_id()

            if operation_id is not None:
                record.operation_id = operation_id

        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(
                record.getMessage()
            ),
        }

        operation_id = getattr(
            record,
            "operation_id",
            None,
        )

        if operation_id is not None:
            payload["operation_id"] = operation_id

        extra_fields = {
            key: (
                REDACTED_VALUE
                if _is_sensitive_field(key)
                else _redact_value(value)
            )
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_ATTRIBUTES
            and not key.startswith("_")
        }

        if extra_fields:
            payload["context"] = extra_fields

        if record.exc_info:
            exception_text = self.formatException(
                record.exc_info
            )
            payload["exception"] = _redact_text(
                exception_text
            )

        return json.dumps(
            payload,
            default=str,
            separators=(",", ":"),
        )


def _resolve_log_level(level: str | int) -> int:
    if isinstance(level, int):
        return level

    numeric_level = getattr(
        logging,
        level.upper(),
        None,
    )

    if not isinstance(numeric_level, int):
        raise ValueError(
            f"Invalid logging level: {level}"
        )

    return numeric_level


def configure_logging(
    level: str | int = logging.INFO,
) -> None:
    numeric_level = _resolve_log_level(level)
    application_logger = logging.getLogger(
        LOGGER_NAMESPACE
    )

    application_logger.setLevel(numeric_level)
    application_logger.propagate = False

    for handler in application_logger.handlers:
        if getattr(
            handler,
            _HANDLER_MARKER,
            False,
        ):
            handler.setLevel(numeric_level)

            if not any(
                isinstance(
                    log_filter,
                    OperationContextFilter,
                )
                for log_filter in handler.filters
            ):
                handler.addFilter(
                    OperationContextFilter()
                )

            return

    handler = logging.StreamHandler()
    handler.setLevel(numeric_level)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(OperationContextFilter())

    setattr(handler, _HANDLER_MARKER, True)
    application_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    normalized_name = name.removeprefix("app.")

    return logging.getLogger(
        f"{LOGGER_NAMESPACE}.{normalized_name}"
    )
