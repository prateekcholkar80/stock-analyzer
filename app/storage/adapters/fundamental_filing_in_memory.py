"""Thread-safe in-memory repository for immutable company-filing packages."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from re import fullmatch
from threading import RLock

from app.exceptions import StorageConflictError, StorageError
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingPackage,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingDeleteAuthorization,
    FundamentalFilingDeleteReceipt,
    FundamentalFilingDeleteStatus,
    FundamentalFilingDeleteTarget,
    FundamentalFilingRepositoryScope,
)


FundamentalFilingClock = Callable[[], datetime]
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_ARTIFACT_ID_PATTERN = r"^filing_artifact:[a-f0-9]{64}$"
_PACKAGE_ID_PATTERN = r"^filing_package:[a-f0-9]{64}$"
_REFERENCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$"
_ACTIVE_REFERENCE_DETAIL = (
    "The filing artifact is referenced by a retained filing package."
)


def _copy_artifact(
    artifact: FundamentalFilingArtifactManifest,
) -> FundamentalFilingArtifactManifest:
    return artifact.model_copy(deep=True)


def _copy_package(package: FundamentalFilingPackage) -> FundamentalFilingPackage:
    return package.model_copy(deep=True)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _scope_key(scope: FundamentalFilingRepositoryScope) -> str:
    return scope.model_dump_json()


class InMemoryFundamentalFilingRepository:
    """Reference adapter for retained PDF metadata and atomic package CAS.

    PDF bytes remain outside this repository. Recheck timestamps represent
    provider freshness and never cause implicit removal.
    """

    adapter_name = "in_memory_fundamental_filings"

    def __init__(
        self,
        *,
        clock: FundamentalFilingClock | None = None,
        initial_artifacts: Iterable[FundamentalFilingArtifactManifest] = (),
        initial_packages: Iterable[FundamentalFilingPackage] = (),
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._artifacts: dict[str, FundamentalFilingArtifactManifest] = {}
        self._packages: dict[str, FundamentalFilingPackage] = {}
        self._active_package_ids: dict[str, str] = {}
        self._lock = RLock()

        for artifact in initial_artifacts:
            self.save_filing_artifact(artifact)
        for package in initial_packages:
            value = FundamentalFilingPackage.model_validate(package)
            scope_key = _scope_key(
                FundamentalFilingRepositoryScope.from_package(value)
            )
            self.commit_filing_package(
                value,
                expected_active_package_id=self._active_package_ids.get(
                    scope_key
                ),
            )

    def _now(self) -> datetime:
        return _require_aware(
            self._clock(),
            "fundamental filing repository clock",
        )

    def save_filing_artifact(
        self,
        artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest:
        value = FundamentalFilingArtifactManifest.model_validate(artifact)
        now = self._now()
        with self._lock:
            self._validate_artifact_writable(value, as_of=now)
            existing = self._artifacts.get(value.artifact_id)
            if existing is not None:
                if existing != value:
                    raise StorageConflictError(
                        "filing artifact ID contains different metadata"
                    )
                return _copy_artifact(existing)
            self._validate_artifact_links_locked(
                value,
                available_artifacts=self._artifacts,
            )
            self._artifacts[value.artifact_id] = _copy_artifact(value)
            return _copy_artifact(value)

    def find_filing_artifacts_by_content_hash(
        self,
        content_sha256: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> tuple[FundamentalFilingArtifactManifest, ...]:
        if (
            not isinstance(content_sha256, str)
            or fullmatch(_FINGERPRINT_PATTERN, content_sha256) is None
        ):
            raise ValueError("filing content SHA-256 is invalid")
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        with self._lock:
            matches = (
                artifact
                for artifact in self._artifacts.values()
                if artifact.content_sha256 == content_sha256
                and FundamentalFilingRepositoryScope.from_artifact(artifact)
                == caller_scope
            )
            return tuple(
                _copy_artifact(value)
                for value in sorted(matches, key=self._artifact_sort_key)
            )

    def get_filing_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingArtifactManifest | None:
        self._validate_identifier(
            artifact_id,
            pattern=_ARTIFACT_ID_PATTERN,
            label="filing artifact ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        with self._lock:
            stored = self._artifacts.get(artifact_id)
            if stored is None or (
                FundamentalFilingRepositoryScope.from_artifact(stored)
                != caller_scope
            ):
                return None
            return _copy_artifact(stored)

    def list_filing_artifact_versions(
        self,
        filing_reference_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> tuple[FundamentalFilingArtifactManifest, ...]:
        self._validate_identifier(
            filing_reference_id,
            pattern=_REFERENCE_ID_PATTERN,
            label="filing reference ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        with self._lock:
            versions = (
                artifact
                for artifact in self._artifacts.values()
                if artifact.reference.filing_reference_id
                == filing_reference_id
                and FundamentalFilingRepositoryScope.from_artifact(artifact)
                == caller_scope
            )
            return tuple(
                _copy_artifact(value)
                for value in sorted(versions, key=self._artifact_sort_key)
            )

    def commit_filing_package(
        self,
        package: FundamentalFilingPackage,
        *,
        expected_active_package_id: str | None,
    ) -> FundamentalFilingPackage:
        value = FundamentalFilingPackage.model_validate(package)
        self._validate_optional_package_id(expected_active_package_id)
        now = self._now()
        scope = FundamentalFilingRepositoryScope.from_package(value)
        scope_key = _scope_key(scope)

        with self._lock:
            self._validate_package_writable(value, as_of=now)
            active_package_id = self._active_package_ids.get(scope_key)
            if active_package_id != expected_active_package_id:
                raise StorageConflictError(
                    "active filing package changed before commit"
                )

            existing_package = self._packages.get(value.package_id)
            if existing_package is not None and existing_package != value:
                raise StorageConflictError(
                    "filing package ID contains different data"
                )

            pending_artifacts: dict[
                str,
                FundamentalFilingArtifactManifest,
            ] = {}
            for artifact in value.artifacts:
                existing_artifact = self._artifacts.get(artifact.artifact_id)
                if existing_artifact is not None and existing_artifact != artifact:
                    raise StorageConflictError(
                        "filing package artifact conflicts with stored metadata"
                    )
                if existing_artifact is None:
                    pending_artifacts[artifact.artifact_id] = _copy_artifact(
                        artifact
                    )

            available_artifacts = {
                **self._artifacts,
                **pending_artifacts,
            }
            for artifact in value.artifacts:
                self._validate_artifact_links_locked(
                    artifact,
                    available_artifacts=available_artifacts,
                )

            # Every validation and CAS precondition passes before any state is
            # mutated. The aggregate and its artifacts become visible under
            # the same lock before the active pointer changes.
            self._artifacts.update(pending_artifacts)
            if existing_package is None:
                self._packages[value.package_id] = _copy_package(value)
            self._active_package_ids[scope_key] = value.package_id
            return _copy_package(self._packages[value.package_id])

    def get_active_filing_package(
        self,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        with self._lock:
            package_id = self._active_package_ids.get(_scope_key(caller_scope))
            if package_id is None:
                return None
            return self._read_package_locked(package_id, scope=caller_scope)

    def get_filing_package(
        self,
        package_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        self._validate_identifier(
            package_id,
            pattern=_PACKAGE_ID_PATTERN,
            label="filing package ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        with self._lock:
            return self._read_package_locked(package_id, scope=caller_scope)

    def delete_filing_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        authorization: FundamentalFilingDeleteAuthorization,
    ) -> FundamentalFilingDeleteReceipt:
        self._validate_identifier(
            artifact_id,
            pattern=_ARTIFACT_ID_PATTERN,
            label="filing artifact ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        approved = self._validate_delete_authorization(
            authorization,
            target_kind=FundamentalFilingDeleteTarget.ARTIFACT,
            target_id=artifact_id,
        )
        completed_at = self._now()
        self._require_authorization_not_future(approved, as_of=completed_at)

        with self._lock:
            stored = self._artifacts.get(artifact_id)
            if stored is None or (
                FundamentalFilingRepositoryScope.from_artifact(stored)
                != caller_scope
            ):
                return self._delete_receipt(
                    approved,
                    status=FundamentalFilingDeleteStatus.NOT_FOUND,
                    completed_at=completed_at,
                )
            if any(
                artifact_id in package.manifest.artifact_ids
                for package in self._packages.values()
            ):
                return self._delete_receipt(
                    approved,
                    status=(
                        FundamentalFilingDeleteStatus.REJECTED_ACTIVE_REFERENCE
                    ),
                    completed_at=completed_at,
                    sanitized_detail=_ACTIVE_REFERENCE_DETAIL,
                )

            receipt = self._delete_receipt(
                approved,
                status=FundamentalFilingDeleteStatus.DELETED,
                completed_at=completed_at,
            )
            del self._artifacts[artifact_id]
            return receipt

    def delete_filing_package(
        self,
        package_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        authorization: FundamentalFilingDeleteAuthorization,
    ) -> FundamentalFilingDeleteReceipt:
        self._validate_identifier(
            package_id,
            pattern=_PACKAGE_ID_PATTERN,
            label="filing package ID",
        )
        caller_scope = FundamentalFilingRepositoryScope.model_validate(scope)
        approved = self._validate_delete_authorization(
            authorization,
            target_kind=FundamentalFilingDeleteTarget.PACKAGE,
            target_id=package_id,
        )
        completed_at = self._now()
        self._require_authorization_not_future(approved, as_of=completed_at)
        scope_key = _scope_key(caller_scope)

        with self._lock:
            stored = self._packages.get(package_id)
            if stored is None or (
                FundamentalFilingRepositoryScope.from_package(stored)
                != caller_scope
            ):
                return self._delete_receipt(
                    approved,
                    status=FundamentalFilingDeleteStatus.NOT_FOUND,
                    completed_at=completed_at,
                )

            receipt = self._delete_receipt(
                approved,
                status=FundamentalFilingDeleteStatus.DELETED,
                completed_at=completed_at,
            )
            if self._active_package_ids.get(scope_key) == package_id:
                del self._active_package_ids[scope_key]
            del self._packages[package_id]
            return receipt

    def _read_package_locked(
        self,
        package_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        stored = self._packages.get(package_id)
        if stored is None or (
            FundamentalFilingRepositoryScope.from_package(stored) != scope
        ):
            return None
        return _copy_package(stored)

    @staticmethod
    def _validate_artifact_links_locked(
        artifact: FundamentalFilingArtifactManifest,
        *,
        available_artifacts: dict[
            str,
            FundamentalFilingArtifactManifest,
        ],
    ) -> None:
        artifact_scope = FundamentalFilingRepositoryScope.from_artifact(
            artifact
        )
        for relation, related_id in (
            ("duplicate", artifact.duplicate_of_artifact_id),
            ("supersedes", artifact.supersedes_artifact_id),
        ):
            if related_id is None:
                continue
            related = available_artifacts.get(related_id)
            if related is None or (
                FundamentalFilingRepositoryScope.from_artifact(related)
                != artifact_scope
            ):
                raise StorageConflictError(
                    f"filing artifact {relation} link is unavailable in scope"
                )
            if relation == "duplicate":
                if related.content_sha256 != artifact.content_sha256:
                    raise StorageConflictError(
                        "duplicate filing artifacts must share a content hash"
                    )
            elif (
                related.reference.filing_reference_id
                != artifact.reference.filing_reference_id
                or related.content_sha256 == artifact.content_sha256
            ):
                raise StorageConflictError(
                    "superseded filing artifact lineage is inconsistent"
                )

    @staticmethod
    def _artifact_sort_key(
        artifact: FundamentalFilingArtifactManifest,
    ) -> tuple[datetime, datetime, str]:
        return (
            artifact.downloaded_at,
            artifact.last_checked_at,
            artifact.artifact_id,
        )

    @staticmethod
    def _validate_artifact_writable(
        artifact: FundamentalFilingArtifactManifest,
        *,
        as_of: datetime,
    ) -> None:
        if artifact.last_checked_at > as_of:
            raise StorageError("filing artifact check time is in the future")

    @staticmethod
    def _validate_package_writable(
        package: FundamentalFilingPackage,
        *,
        as_of: datetime,
    ) -> None:
        if package.manifest.committed_at > as_of:
            raise StorageError("filing package commit time is in the future")

    @staticmethod
    def _validate_identifier(value: str, *, pattern: str, label: str) -> None:
        if not isinstance(value, str) or fullmatch(pattern, value) is None:
            raise ValueError(f"{label} is invalid")

    @classmethod
    def _validate_optional_package_id(cls, value: str | None) -> None:
        if value is not None:
            cls._validate_identifier(
                value,
                pattern=_PACKAGE_ID_PATTERN,
                label="expected active filing package ID",
            )

    @staticmethod
    def _validate_delete_authorization(
        authorization: FundamentalFilingDeleteAuthorization,
        *,
        target_kind: FundamentalFilingDeleteTarget,
        target_id: str,
    ) -> FundamentalFilingDeleteAuthorization:
        value = FundamentalFilingDeleteAuthorization.model_validate(
            authorization
        )
        if value.target_kind is not target_kind or value.target_id != target_id:
            raise StorageError(
                "filing deletion authorization does not match the target"
            )
        return value

    @staticmethod
    def _require_authorization_not_future(
        authorization: FundamentalFilingDeleteAuthorization,
        *,
        as_of: datetime,
    ) -> None:
        if authorization.authorized_at > as_of:
            raise StorageError("filing deletion authorization is in the future")

    @staticmethod
    def _delete_receipt(
        authorization: FundamentalFilingDeleteAuthorization,
        *,
        status: FundamentalFilingDeleteStatus,
        completed_at: datetime,
        sanitized_detail: str | None = None,
    ) -> FundamentalFilingDeleteReceipt:
        return FundamentalFilingDeleteReceipt(
            authorization=authorization,
            status=status,
            completed_at=completed_at,
            sanitized_detail=sanitized_detail,
        )
