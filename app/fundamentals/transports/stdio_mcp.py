"""Hardened local stdio transport for the Jarvis-owned Tijori MCP fork.

The transport never invokes a shell, inherits no application environment, and
does not know a user's credentials.  It accepts only a pre-existing owner-only
session file created by a future, separately approved authentication flow.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
from threading import BoundedSemaphore
from time import monotonic
from typing import Any, Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.fundamentals.tijori_mcp_contracts import (
    TIJORI_APPROVED_TOOLS,
    TijoriMcpToolResult,
    TijoriMcpTransport,
    TijoriMcpTransportError,
    TijoriMcpTransportInspection,
    TijoriToolName,
    TijoriToolStatus,
    TijoriTransportFailureKind,
)
from app.models.fundamentals import FundamentalModel, ProviderConnectionScope


_JSONRPC_VERSION = "2.0"
_CLIENT_NAME = "jarvis-tijori-transport"
_CLIENT_VERSION = "1.0.0"
_SESSION_ENVIRONMENT_KEY = "JARVIS_TIJORI_SESSION_FILE"
_CONTRACT_ENVIRONMENT_KEY = "JARVIS_TIJORI_PROVIDER_CONTRACT_VERSION"
# MCP tool schemas can legitimately nest unions, array items, and object
# properties more deeply than provider evidence payloads. Byte and node caps
# remain the primary allocation bounds; this ceiling still rejects recursive
# or adversarial structures well before Python recursion becomes a concern.
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 250_000
_ERROR_KIND_BY_CODE = {
    -32001: TijoriTransportFailureKind.AUTHENTICATION,
    -32003: TijoriTransportFailureKind.ENTITLEMENT,
    -32029: TijoriTransportFailureKind.RATE_LIMIT,
    -32600: TijoriTransportFailureKind.PROTOCOL,
    -32601: TijoriTransportFailureKind.PROTOCOL,
    -32602: TijoriTransportFailureKind.PROTOCOL,
    -32603: TijoriTransportFailureKind.PROTOCOL,
    -32700: TijoriTransportFailureKind.PROTOCOL,
}


class TijoriStdioMcpSettings(FundamentalModel):
    """Pinned non-secret process and protocol configuration."""

    transport_version: Literal["jarvis.tijori_stdio_mcp.v1"] = (
        "jarvis.tijori_stdio_mcp.v1"
    )
    runtime_executable: Path
    runtime_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_arguments: tuple[str, ...] = Field(default=(), max_length=20)
    server_entrypoint: Path
    server_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    server_arguments: tuple[str, ...] = Field(default=(), max_length=20)
    session_root: Path
    protocol_version: str = Field(
        default="2025-06-18",
        min_length=1,
        max_length=40,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    )
    provider_contract_version: str = Field(
        default="tijori.synthetic_contract.v1",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    timeout_seconds: float = Field(default=30.0, ge=0.1, le=120.0)
    terminate_grace_seconds: float = Field(default=1.0, ge=0.0, le=10.0)
    max_message_bytes: int = Field(
        default=8_500_000,
        ge=1_024,
        le=10_000_000,
    )
    max_concurrency: int = Field(default=3, ge=1, le=8)
    max_session_bytes: int = Field(
        default=5_000_000,
        ge=2,
        le=20_000_000,
    )

    @field_validator(
        "runtime_executable",
        "server_entrypoint",
        "session_root",
    )
    @classmethod
    def require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Tijori transport paths must be absolute")
        return value

    @field_validator("runtime_arguments", "server_arguments")
    @classmethod
    def validate_arguments(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value
            or len(value) > 200
            or "\x00" in value
            or "\n" in value
            or "\r" in value
            for value in values
        ):
            raise ValueError("Tijori process arguments must be bounded")
        return values

    @model_validator(mode="after")
    def require_distinct_files(self) -> Self:
        if self.runtime_executable == self.server_entrypoint:
            raise ValueError("runtime and server entrypoint must be distinct")
        return self

    @computed_field
    @property
    def configuration_fingerprint(self) -> str:
        payload = self.model_dump_json(
            exclude={"configuration_fingerprint"}
        )
        return sha256(payload.encode("utf-8")).hexdigest()


class _McpProcess:
    """One bounded MCP lifecycle; never shared between provider calls."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        deadline: float,
        max_message_bytes: int,
        terminate_grace_seconds: float,
    ) -> None:
        self._process = process
        self._deadline = deadline
        self._max_message_bytes = max_message_bytes
        self._terminate_grace_seconds = terminate_grace_seconds
        self._buffer = bytearray()
        self._selector = selectors.DefaultSelector()
        if process.stdout is None or process.stdin is None:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )
        self._stdout = process.stdout
        self._stdin = process.stdin
        self._selector.register(self._stdout, selectors.EVENT_READ)

    def request(
        self,
        *,
        request_id: int,
        method: str,
        params: dict[str, object],
    ) -> dict[str, Any]:
        self._write(
            {
                "jsonrpc": _JSONRPC_VERSION,
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        return self._read_response(request_id)

    def notify(self, *, method: str) -> None:
        self._write({"jsonrpc": _JSONRPC_VERSION, "method": method})

    def close(self) -> None:
        try:
            self._selector.close()
        finally:
            try:
                self._stdin.close()
            except OSError:
                pass
            if self._process.poll() is None:
                self._signal_process_group(signal.SIGTERM)
                try:
                    self._process.wait(timeout=self._terminate_grace_seconds)
                except subprocess.TimeoutExpired:
                    self._signal_process_group(signal.SIGKILL)
                    self._process.wait(timeout=1.0)
            try:
                self._stdout.close()
            except OSError:
                pass

    def _signal_process_group(self, process_signal: int) -> None:
        try:
            if hasattr(os, "killpg"):
                os.killpg(self._process.pid, process_signal)
            elif process_signal == signal.SIGTERM:
                self._process.terminate()
            else:
                self._process.kill()
        except ProcessLookupError:
            return

    def _write(self, message: dict[str, object]) -> None:
        try:
            encoded = json.dumps(
                message,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.PROTOCOL
            ) from exc
        if len(encoded) > self._max_message_bytes:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.PROTOCOL
            )
        try:
            self._stdin.write(encoded)
            self._stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.UNAVAILABLE
            ) from exc

    def _read_response(self, request_id: int) -> dict[str, Any]:
        while True:
            line = self._next_line()
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                ) from exc
            _validate_json_shape(message)
            if not isinstance(message, dict):
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                )
            if "id" not in message:
                continue
            if message.get("id") != request_id:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                )
            if message.get("jsonrpc") != _JSONRPC_VERSION:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                )
            if "error" in message:
                self._raise_rpc_error(message["error"])
            result = message.get("result")
            if not isinstance(result, dict):
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                )
            return result

    def _next_line(self) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                if not line:
                    continue
                return line
            remaining = self._deadline - monotonic()
            if remaining <= 0:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.UNAVAILABLE
                )
            events = self._selector.select(remaining)
            if not events:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.UNAVAILABLE
                )
            try:
                chunk = os.read(self._stdout.fileno(), 65_536)
            except OSError as exc:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.UNAVAILABLE
                ) from exc
            if not chunk:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.UNAVAILABLE
                )
            self._buffer.extend(chunk)
            if len(self._buffer) > self._max_message_bytes:
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                )

    @staticmethod
    def _raise_rpc_error(error: object) -> None:
        if not isinstance(error, dict):
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.PROTOCOL
            )
        code = error.get("code")
        if isinstance(code, bool) or not isinstance(code, int):
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.PROTOCOL
            )
        kind = _ERROR_KIND_BY_CODE.get(
            code,
            TijoriTransportFailureKind.UNAVAILABLE,
        )
        raise TijoriMcpTransportError(kind)


class TijoriStdioMcpTransport(TijoriMcpTransport):
    """MCP client for a pinned Jarvis-owned local server entrypoint."""

    def __init__(
        self,
        *,
        settings: TijoriStdioMcpSettings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._semaphore = BoundedSemaphore(settings.max_concurrency)
        self._validate_static_paths()

    @property
    def configuration_fingerprint(self) -> str:
        return self._settings.configuration_fingerprint

    def inspect(
        self,
        *,
        connection: ProviderConnectionScope,
    ) -> TijoriMcpTransportInspection:
        result, metadata = self._exchange(
            connection=connection,
            method="tools/list",
            params={},
        )
        tools = result.get("tools")
        if not isinstance(tools, list) or result.get("nextCursor") is not None:
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                )
            names.append(tool["name"])
        try:
            return TijoriMcpTransportInspection(
                available_tools=tuple(names),
                authenticated=metadata["authenticated"],
                checked_at=self._aware_now(),
                provider_contract_version=metadata[
                    "provider_contract_version"
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.PROTOCOL
            ) from exc

    def call_tool(
        self,
        *,
        connection: ProviderConnectionScope,
        tool_name: TijoriToolName,
        arguments: dict[str, object],
    ) -> TijoriMcpToolResult:
        if tool_name not in TIJORI_APPROVED_TOOLS:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )
        _validate_json_shape(arguments)
        result, _ = self._exchange(
            connection=connection,
            method="tools/call",
            params={"name": tool_name, "arguments": arguments},
        )
        content = result.get("content")
        if (
            not isinstance(content, list)
            or len(content) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "text"
            or not isinstance(content[0].get("text"), str)
        ):
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        text = content[0]["text"]
        if len(text.encode("utf-8")) > self._settings.max_message_bytes:
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        try:
            envelope = TijoriMcpToolResult.model_validate_json(text)
        except (TypeError, ValueError) as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.PROTOCOL
            ) from exc
        is_error = result.get("isError", False)
        if not isinstance(is_error, bool):
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        if is_error and envelope.status is TijoriToolStatus.SUCCESS:
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        if envelope.tool_name != tool_name:
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        return envelope

    def session_path_for(self, connection: ProviderConnectionScope) -> Path:
        """Return a non-identifying deterministic session path."""

        self._validate_connection(connection)
        material = ":".join(
            (
                connection.tenant_id,
                connection.provider_connection_id,
                connection.account_reference_hash or "unbound",
            )
        )
        digest = sha256(material.encode("utf-8")).hexdigest()
        return self._settings.session_root / f"{digest}.json"

    def _exchange(
        self,
        *,
        connection: ProviderConnectionScope,
        method: str,
        params: dict[str, object],
    ) -> tuple[dict[str, Any], dict[str, object]]:
        self._validate_connection(connection)
        self._validate_static_paths()
        session_path = self.session_path_for(connection)
        self._validate_owner_only_session(session_path)
        acquired = self._semaphore.acquire(timeout=self._settings.timeout_seconds)
        if not acquired:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.UNAVAILABLE
            )
        process_session: _McpProcess | None = None
        try:
            process_session = self._start_process(session_path)
            initialized = process_session.request(
                request_id=1,
                method="initialize",
                params={
                    "protocolVersion": self._settings.protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": _CLIENT_NAME,
                        "version": _CLIENT_VERSION,
                    },
                },
            )
            metadata = self._validate_initialization(initialized)
            process_session.notify(method="notifications/initialized")
            result = process_session.request(
                request_id=2,
                method=method,
                params=params,
            )
            return result, metadata
        finally:
            if process_session is not None:
                process_session.close()
            self._semaphore.release()

    def _start_process(self, session_path: Path) -> _McpProcess:
        command = [
            str(self._settings.runtime_executable),
            *self._settings.runtime_arguments,
            str(self._settings.server_entrypoint),
            *self._settings.server_arguments,
        ]
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            _SESSION_ENVIRONMENT_KEY: str(session_path),
            _CONTRACT_ENVIRONMENT_KEY: (
                self._settings.provider_contract_version
            ),
        }
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self._settings.server_entrypoint.parent,
                env=environment,
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            ) from exc
        return _McpProcess(
            process=process,
            deadline=monotonic() + self._settings.timeout_seconds,
            max_message_bytes=self._settings.max_message_bytes,
            terminate_grace_seconds=self._settings.terminate_grace_seconds,
        )

    def _validate_initialization(
        self,
        result: dict[str, Any],
    ) -> dict[str, object]:
        if result.get("protocolVersion") != self._settings.protocol_version:
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict) or not isinstance(
            capabilities.get("tools"), dict
        ):
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        experimental = capabilities.get("experimental")
        if not isinstance(experimental, dict):
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        metadata = experimental.get("jarvisTijori")
        if not isinstance(metadata, dict):
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        authenticated = metadata.get("authenticated")
        contract = metadata.get("providerContractVersion")
        if not isinstance(authenticated, bool) or contract != (
            self._settings.provider_contract_version
        ):
            raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)
        return {
            "authenticated": authenticated,
            "provider_contract_version": contract,
        }

    def _validate_static_paths(self) -> None:
        self._validate_runtime_file(
            self._settings.runtime_executable,
            expected_hash=self._settings.runtime_sha256,
            require_executable=True,
            require_current_owner=False,
        )
        self._validate_runtime_file(
            self._settings.server_entrypoint,
            expected_hash=self._settings.server_sha256,
            require_executable=False,
            require_current_owner=True,
        )
        self._validate_owner_only_directory(self._settings.session_root)

    @staticmethod
    def _validate_runtime_file(
        path: Path,
        *,
        expected_hash: str,
        require_executable: bool,
        require_current_owner: bool,
    ) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            ) from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
            or (require_executable and not os.access(path, os.X_OK))
            or (require_current_owner and info.st_uid != os.getuid())
        ):
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )
        if _sha256_file(path) != expected_hash:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )

    @staticmethod
    def _validate_owner_only_directory(path: Path) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            ) from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )

    def _validate_owner_only_session(self, path: Path) -> None:
        if path.parent != self._settings.session_root:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )
        try:
            info = path.lstat()
        except OSError as exc:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.AUTHENTICATION
            ) from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size < 2
            or info.st_size > self._settings.max_session_bytes
        ):
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.AUTHENTICATION
            )

    @staticmethod
    def _validate_connection(connection: ProviderConnectionScope) -> None:
        if connection.provider.casefold() != "tijori":
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.CONFIGURATION
            )
        return value


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(128 * 1024):
                digest.update(block)
    except OSError as exc:
        raise TijoriMcpTransportError(
            TijoriTransportFailureKind.CONFIGURATION
        ) from exc
    return digest.hexdigest()


def _validate_json_shape(value: object) -> None:
    nodes = [0]

    def visit(item: object, depth: int) -> None:
        nodes[0] += 1
        if nodes[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise TijoriMcpTransportError(
                TijoriTransportFailureKind.PROTOCOL
            )
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not (-float("inf") < item < float("inf")):
                raise TijoriMcpTransportError(
                    TijoriTransportFailureKind.PROTOCOL
                )
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or not key or len(key) > 160:
                    raise TijoriMcpTransportError(
                        TijoriTransportFailureKind.PROTOCOL
                    )
                visit(child, depth + 1)
            return
        raise TijoriMcpTransportError(TijoriTransportFailureKind.PROTOCOL)

    visit(value, 0)
