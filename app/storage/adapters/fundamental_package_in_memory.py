"""Thread-safe in-memory repository for complete fundamental packages."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import RLock

from app.exceptions import StorageConflictError, StorageError
from app.models.fundamental_package import FundamentalEvidencePackage
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepositoryScope,
)


FundamentalPackageClock = Callable[[], datetime]


def _copy(package: FundamentalEvidencePackage) -> FundamentalEvidencePackage:
    return package.model_copy(deep=True)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _scope_key(scope: FundamentalPackageRepositoryScope) -> str:
    return scope.model_dump_json()


class InMemoryFundamentalPackageRepository:
    """Reference adapter with compare-and-swap active publication."""

    adapter_name = "in_memory_fundamental_packages"

    def __init__(
        self,
        *,
        clock: FundamentalPackageClock | None = None,
        initial_packages: Iterable[FundamentalEvidencePackage] = (),
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._packages: dict[str, FundamentalEvidencePackage] = {}
        self._active_package_ids: dict[str, str] = {}
        self._lock = RLock()

        for package in initial_packages:
            self.commit_fundamental_package(
                package,
                expected_active_package_id=None,
            )

    def _now(self) -> datetime:
        return _require_aware(
            self._clock(),
            "fundamental package repository clock",
        )

    def commit_fundamental_package(
        self,
        package: FundamentalEvidencePackage,
        *,
        expected_active_package_id: str | None,
    ) -> FundamentalEvidencePackage:
        value = FundamentalEvidencePackage.model_validate(package)
        now = self._now()
        scope = FundamentalPackageRepositoryScope.from_package(value)
        scope_key = _scope_key(scope)

        with self._lock:
            self._purge_expired_locked(as_of=now)
            self._validate_writable(value, as_of=now)
            active_package_id = self._active_package_ids.get(scope_key)
            if active_package_id != expected_active_package_id:
                raise StorageConflictError(
                    "active fundamental package changed before commit"
                )

            existing = self._packages.get(value.package_id)
            if (
                existing is not None
                and existing.aggregate_fingerprint
                != value.aggregate_fingerprint
            ):
                raise StorageConflictError(
                    "fundamental package ID contains different data"
                )

            # Both mutations occur under the same lock and only after every
            # precondition passes; no partial aggregate becomes active.
            if existing is None:
                self._packages[value.package_id] = _copy(value)
            self._active_package_ids[scope_key] = value.package_id
            return _copy(self._packages[value.package_id])

    def get_active_fundamental_package(
        self,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        caller_scope = FundamentalPackageRepositoryScope.model_validate(scope)
        read_at = _require_aware(as_of, "fundamental package read time")
        scope_key = _scope_key(caller_scope)
        with self._lock:
            self._purge_expired_locked(as_of=read_at)
            package_id = self._active_package_ids.get(scope_key)
            if package_id is None:
                return None
            return self._read_locked(
                package_id,
                scope=caller_scope,
                as_of=read_at,
            )

    def get_fundamental_package(
        self,
        package_id: str,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        if not isinstance(package_id, str) or not package_id.startswith(
            "fundamental_package:"
        ):
            raise ValueError("fundamental package ID is invalid")
        caller_scope = FundamentalPackageRepositoryScope.model_validate(scope)
        read_at = _require_aware(as_of, "fundamental package read time")
        with self._lock:
            self._purge_expired_locked(as_of=read_at)
            return self._read_locked(
                package_id,
                scope=caller_scope,
                as_of=read_at,
            )

    def purge_expired_fundamental_packages(
        self,
        *,
        as_of: datetime,
    ) -> int:
        purge_at = _require_aware(as_of, "fundamental package purge time")
        with self._lock:
            return self._purge_expired_locked(as_of=purge_at)

    def _read_locked(
        self,
        package_id: str,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        package = self._packages.get(package_id)
        if package is None:
            return None
        if package.manifest.committed_at > as_of:
            return None
        if FundamentalPackageRepositoryScope.from_package(package) != scope:
            return None
        return _copy(package)

    def _purge_expired_locked(self, *, as_of: datetime) -> int:
        expired_package_ids = {
            package_id
            for package_id, package in self._packages.items()
            if package.is_expired(as_of=as_of)
        }
        for package_id in expired_package_ids:
            del self._packages[package_id]
        stale_scope_keys = (
            scope_key
            for scope_key, package_id in self._active_package_ids.items()
            if package_id in expired_package_ids
            or package_id not in self._packages
        )
        for scope_key in tuple(stale_scope_keys):
            del self._active_package_ids[scope_key]
        return len(expired_package_ids)

    @staticmethod
    def _validate_writable(
        package: FundamentalEvidencePackage,
        *,
        as_of: datetime,
    ) -> None:
        if package.manifest.committed_at > as_of:
            raise StorageError(
                "fundamental package commit time is in the future"
            )
        if package.is_expired(as_of=as_of):
            raise StorageError("expired fundamental package cannot be committed")
