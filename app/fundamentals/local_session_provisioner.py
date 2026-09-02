"""Owner-only local inspection and revocation for provider browser sessions."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from re import fullmatch
import stat
import subprocess
from threading import RLock

from app.fundamentals.session_provisioning import (
    ProviderSessionInspectionRequest,
    ProviderSessionLifecycle,
    ProviderSessionProvisioner,
    ProviderSessionProvisioningRequest,
    ProviderSessionRevocationRequest,
    ProviderSessionStatus,
    validate_provider_session_response_binding,
)
from app.models.fundamentals import ProviderConnectionScope


_SESSION_FILE_PATTERN = r"^[a-f0-9]{64}\.json$"
_MAX_ALLOWED_SESSION_BYTES = 20_000_000
_MIN_INTERACTIVE_TIMEOUT = timedelta(seconds=30)
_MAX_INTERACTIVE_TIMEOUT = timedelta(minutes=15)
_PROCESS_GRACE_SECONDS = 15
_MAX_PROCESS_OUTPUT_BYTES = 512
_SESSION_FILE_ENV = "JARVIS_TIJORI_SESSION_FILE"
_PROVISION_TIMEOUT_ENV = "JARVIS_TIJORI_PROVISION_TIMEOUT_MS"
_SAFE_CHILD_ENVIRONMENT = (
    "DISPLAY",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "WAYLAND_DISPLAY",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
)


class LocalSessionProvisioningError(Exception):
    """Sanitized local session lifecycle failure."""

    def __init__(self) -> None:
        super().__init__("Provider session could not be handled safely")


class InteractiveSessionProvisioningRequired(LocalSessionProvisioningError):
    """Provisioning needs the separately approved interactive browser flow."""

    def __init__(self) -> None:
        super().__init__()


SessionPathResolver = Callable[[ProviderConnectionScope], Path]


class LocalFileProviderSessionProvisioner(ProviderSessionProvisioner):
    """Provision, inspect, and revoke a scoped local browser session.

    Revocation overwrites the current regular file, flushes it, unlinks it, and
    flushes the owner-only directory. This is a best-effort local deletion; no
    filesystem API can guarantee physical erasure on SSDs or copy-on-write
    storage.
    """

    def __init__(
        self,
        *,
        session_root: Path,
        session_path_resolver: SessionPathResolver,
        session_max_age: timedelta,
        max_session_bytes: int = 5_000_000,
        clock: Callable[[], datetime] | None = None,
        interactive_command: tuple[Path, Path] | None = None,
        interactive_timeout: timedelta = timedelta(minutes=5),
        process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        if not isinstance(session_root, Path) or not session_root.is_absolute():
            raise TypeError("Local session root must be an absolute path")
        if not callable(session_path_resolver):
            raise TypeError("Local session path resolver must be callable")
        if (
            not isinstance(session_max_age, timedelta)
            or session_max_age <= timedelta(0)
            or session_max_age > timedelta(days=30)
        ):
            raise ValueError("Local session maximum age must be within 30 days")
        if (
            isinstance(max_session_bytes, bool)
            or not isinstance(max_session_bytes, int)
            or max_session_bytes < 2
            or max_session_bytes > _MAX_ALLOWED_SESSION_BYTES
        ):
            raise ValueError("Local session size ceiling is invalid")
        if clock is not None and not callable(clock):
            raise TypeError("Local session clock must be callable")
        if (
            not isinstance(interactive_timeout, timedelta)
            or interactive_timeout < _MIN_INTERACTIVE_TIMEOUT
            or interactive_timeout > _MAX_INTERACTIVE_TIMEOUT
        ):
            raise ValueError("Interactive session timeout is invalid")
        if interactive_command is not None and (
            not isinstance(interactive_command, tuple)
            or len(interactive_command) != 2
            or any(
                not isinstance(component, Path) or not component.is_absolute()
                for component in interactive_command
            )
        ):
            raise TypeError(
                "Interactive session command must contain two absolute paths"
            )
        if process_runner is not None and not callable(process_runner):
            raise TypeError("Interactive session process runner must be callable")
        if process_runner is not None and interactive_command is None:
            raise ValueError("Interactive session runner requires a configured command")

        self._session_root = session_root
        self._session_path_resolver = session_path_resolver
        self._session_max_age = session_max_age
        self._max_session_bytes = max_session_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._interactive_command = interactive_command
        self._interactive_timeout = interactive_timeout
        self._process_runner = process_runner or subprocess.run
        self._lock = RLock()
        self._validate_root()

        material = json.dumps(
            {
                "adapter": "jarvis.local_provider_session.v1",
                "max_age_seconds": session_max_age.total_seconds(),
                "max_session_bytes": max_session_bytes,
                "interactive_command_hashes": (
                    None
                    if interactive_command is None
                    else [
                        sha256(str(component).encode("utf-8")).hexdigest()
                        for component in interactive_command
                    ]
                ),
                "interactive_timeout_seconds": interactive_timeout.total_seconds(),
                "session_root_hash": sha256(
                    str(session_root).encode("utf-8")
                ).hexdigest(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        self._configuration_fingerprint = sha256(
            material.encode("utf-8")
        ).hexdigest()

    @property
    def configuration_fingerprint(self) -> str:
        return self._configuration_fingerprint

    def inspect(
        self,
        *,
        request: ProviderSessionInspectionRequest,
    ) -> ProviderSessionLifecycle:
        if not isinstance(request, ProviderSessionInspectionRequest):
            raise TypeError("Session inspection requires its request contract")
        with self._lock:
            self._validate_root()
            checked_at = self._now(request.requested_at)
            path = self._safe_path(request.connection)
            try:
                path.lstat()
            except FileNotFoundError:
                lifecycle = ProviderSessionLifecycle(
                    connection=request.connection,
                    status=ProviderSessionStatus.UNCONFIGURED,
                    checked_at=checked_at,
                )
            except OSError as exc:
                raise LocalSessionProvisioningError() from exc
            else:
                lifecycle = self._lifecycle_for_file(
                    path,
                    request.connection,
                    checked_at,
                )
            validate_provider_session_response_binding(request, lifecycle)
            return lifecycle

    def provision(
        self,
        *,
        request: ProviderSessionProvisioningRequest,
    ) -> ProviderSessionLifecycle:
        if not isinstance(request, ProviderSessionProvisioningRequest):
            raise TypeError("Session provisioning requires its request contract")
        if self._interactive_command is None:
            raise InteractiveSessionProvisioningRequired()
        with self._lock:
            self._validate_root()
            path = self._safe_path(request.connection)
            checked_at = self._now(request.requested_at)
            replaced_artifact: _SessionArtifact | None = None
            replacement_path: Path | None = None
            try:
                existing = self._read_artifact(path)
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                raise LocalSessionProvisioningError() from exc
            else:
                if (
                    not request.replace_existing
                    or checked_at < existing.expires_at
                ):
                    raise LocalSessionProvisioningError()
                replaced_artifact = existing
                replacement_path = self._quarantine_for_replacement(
                    path,
                    existing.info,
                )

            try:
                response = self._run_interactive_process(path)
                checked_at = self._now(request.requested_at)
                lifecycle = self._lifecycle_for_file(
                    path,
                    request.connection,
                    checked_at,
                )
                if (
                    response["session_reference_hash"]
                    != lifecycle.session_reference_hash
                ):
                    raise LocalSessionProvisioningError()
            except Exception as exc:
                if replacement_path is not None and replaced_artifact is not None:
                    try:
                        self._restore_replaced_artifact(
                            path,
                            replacement_path,
                            replaced_artifact.info,
                        )
                    except Exception as restore_exc:
                        raise LocalSessionProvisioningError() from restore_exc
                if isinstance(exc, LocalSessionProvisioningError):
                    raise
                raise LocalSessionProvisioningError() from exc

            if replacement_path is not None and replaced_artifact is not None:
                self._overwrite_and_unlink(
                    replacement_path,
                    replaced_artifact.info,
                )
            validate_provider_session_response_binding(request, lifecycle)
            return lifecycle

    def revoke(
        self,
        *,
        request: ProviderSessionRevocationRequest,
    ) -> ProviderSessionLifecycle:
        if not isinstance(request, ProviderSessionRevocationRequest):
            raise TypeError("Session revocation requires its request contract")
        with self._lock:
            self._validate_root()
            checked_at = self._now(request.requested_at)
            path = self._safe_path(request.connection)
            try:
                artifact = self._read_artifact(path)
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise LocalSessionProvisioningError() from exc
            self._overwrite_and_unlink(path, artifact.info)
            lifecycle = ProviderSessionLifecycle(
                connection=request.connection,
                status=ProviderSessionStatus.REVOKED,
                checked_at=checked_at,
                session_reference_hash=artifact.fingerprint,
                provisioned_at=artifact.provisioned_at,
                expires_at=artifact.expires_at,
                revoked_at=checked_at,
                revocation_reason=request.reason,
            )
            validate_provider_session_response_binding(request, lifecycle)
            return lifecycle

    def _lifecycle_for_file(
        self,
        path: Path,
        connection: ProviderConnectionScope,
        checked_at: datetime,
    ) -> ProviderSessionLifecycle:
        try:
            artifact = self._read_artifact(path)
        except (OSError, ValueError) as exc:
            raise LocalSessionProvisioningError() from exc
        if checked_at < artifact.provisioned_at:
            raise LocalSessionProvisioningError()
        status = (
            ProviderSessionStatus.EXPIRED
            if checked_at >= artifact.expires_at
            else ProviderSessionStatus.READY
        )
        return ProviderSessionLifecycle(
            connection=connection,
            status=status,
            checked_at=checked_at,
            session_reference_hash=artifact.fingerprint,
            provisioned_at=artifact.provisioned_at,
            expires_at=artifact.expires_at,
        )

    def _run_interactive_process(self, path: Path) -> dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in _SAFE_CHILD_ENVIRONMENT
            if key in os.environ
        }
        environment[_SESSION_FILE_ENV] = str(path)
        environment[_PROVISION_TIMEOUT_ENV] = str(
            int(self._interactive_timeout.total_seconds() * 1000)
        )
        try:
            completed = self._process_runner(
                tuple(str(component) for component in self._interactive_command or ()),
                check=False,
                encoding="utf-8",
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=(
                    self._interactive_timeout.total_seconds()
                    + _PROCESS_GRACE_SECONDS
                ),
            )
        except Exception as exc:
            raise LocalSessionProvisioningError() from exc
        output = getattr(completed, "stdout", None)
        try:
            output_size = (
                len(output.encode("utf-8")) if isinstance(output, str) else None
            )
        except UnicodeEncodeError as exc:
            raise LocalSessionProvisioningError() from exc
        if (
            getattr(completed, "returncode", None) != 0
            or not isinstance(output, str)
            or output_size is None
            or output_size > _MAX_PROCESS_OUTPUT_BYTES
            or not output.endswith("\n")
            or output.count("\n") != 1
        ):
            raise LocalSessionProvisioningError()
        try:
            response = json.loads(output)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise LocalSessionProvisioningError() from exc
        if (
            not isinstance(response, dict)
            or set(response) != {"session_reference_hash", "status"}
            or response.get("status") != "ready"
            or not isinstance(response.get("session_reference_hash"), str)
            or fullmatch(
                r"[a-f0-9]{64}", response["session_reference_hash"]
            ) is None
        ):
            raise LocalSessionProvisioningError()
        return response

    def _read_artifact(self, path: Path) -> "_SessionArtifact":
        info = self._validate_session_file(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != info.st_dev
                or opened.st_ino != info.st_ino
                or opened.st_nlink != 1
            ):
                raise LocalSessionProvisioningError()
            content = bytearray()
            while len(content) <= self._max_session_bytes:
                block = os.read(
                    descriptor,
                    min(128 * 1024, self._max_session_bytes + 1),
                )
                if not block:
                    break
                content.extend(block)
            if len(content) < 2 or len(content) > self._max_session_bytes:
                raise LocalSessionProvisioningError()
        finally:
            os.close(descriptor)
        try:
            parsed = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise LocalSessionProvisioningError() from exc
        if not isinstance(parsed, dict):
            raise LocalSessionProvisioningError()
        provisioned_at = datetime.fromtimestamp(info.st_mtime, UTC)
        return _SessionArtifact(
            info=info,
            fingerprint=sha256(content).hexdigest(),
            provisioned_at=provisioned_at,
            expires_at=provisioned_at + self._session_max_age,
        )

    def _validate_root(self) -> None:
        try:
            info = self._session_root.lstat()
            canonical = self._session_root.resolve(strict=True)
        except OSError as exc:
            raise LocalSessionProvisioningError() from exc
        if (
            canonical != self._session_root
            or stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise LocalSessionProvisioningError()

    def _safe_path(self, connection: ProviderConnectionScope) -> Path:
        try:
            path = self._session_path_resolver(connection)
        except Exception as exc:
            raise LocalSessionProvisioningError() from exc
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or path.parent != self._session_root
            or fullmatch(_SESSION_FILE_PATTERN, path.name) is None
        ):
            raise LocalSessionProvisioningError()
        return path

    def _validate_session_file(self, path: Path) -> os.stat_result:
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size < 2
            or info.st_size > self._max_session_bytes
        ):
            raise LocalSessionProvisioningError()
        return info

    def _quarantine_for_replacement(
        self,
        path: Path,
        expected: os.stat_result,
    ) -> Path:
        replacement = self._session_root / f".replacement-{path.stem}.tmp"
        try:
            replacement.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise LocalSessionProvisioningError() from exc
        else:
            raise LocalSessionProvisioningError()

        try:
            current = path.lstat()
            if not _same_file(current, expected):
                raise LocalSessionProvisioningError()
            os.replace(path, replacement)
            moved = replacement.lstat()
            if not _same_file(moved, expected):
                raise LocalSessionProvisioningError()
            self._sync_root()
        except OSError as exc:
            raise LocalSessionProvisioningError() from exc
        return replacement

    def _restore_replaced_artifact(
        self,
        path: Path,
        replacement: Path,
        expected: os.stat_result,
    ) -> None:
        try:
            current = self._validate_session_file(path)
        except FileNotFoundError:
            pass
        else:
            self._overwrite_and_unlink(path, current)

        try:
            quarantined = replacement.lstat()
            if not _same_file(quarantined, expected):
                raise LocalSessionProvisioningError()
            os.replace(replacement, path)
            restored = path.lstat()
            if not _same_file(restored, expected):
                raise LocalSessionProvisioningError()
            self._sync_root()
        except OSError as exc:
            raise LocalSessionProvisioningError() from exc

    def _overwrite_and_unlink(self, path: Path, expected: os.stat_result) -> None:
        flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            try:
                current = os.fstat(descriptor)
                if (
                    current.st_dev != expected.st_dev
                    or current.st_ino != expected.st_ino
                    or current.st_nlink != 1
                    or current.st_size != expected.st_size
                ):
                    raise LocalSessionProvisioningError()
                remaining = current.st_size
                os.lseek(descriptor, 0, os.SEEK_SET)
                zeros = b"\0" * min(64 * 1024, remaining)
                while remaining:
                    written = os.write(descriptor, zeros[:remaining])
                    if written <= 0:
                        raise LocalSessionProvisioningError()
                    remaining -= written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            path.unlink()
            self._sync_root()
        except OSError as exc:
            raise LocalSessionProvisioningError() from exc

    def _sync_root(self) -> None:
        descriptor = os.open(self._session_root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _now(self, requested_at: datetime) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None or value < requested_at:
            raise LocalSessionProvisioningError()
        return value


class _SessionArtifact:
    def __init__(
        self,
        *,
        info: os.stat_result,
        fingerprint: str,
        provisioned_at: datetime,
        expires_at: datetime,
    ) -> None:
        self.info = info
        self.fingerprint = fingerprint
        self.provisioned_at = provisioned_at
        self.expires_at = expires_at


def _same_file(current: os.stat_result, expected: os.stat_result) -> bool:
    return (
        current.st_dev == expected.st_dev
        and current.st_ino == expected.st_ino
        and current.st_nlink == 1
        and current.st_size == expected.st_size
    )
