"""Database-neutral persistence port for company-filing artifacts."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingPackage,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
    ProviderConnectionScope,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


class FundamentalFilingRepositoryScope(FundamentalModel):
    """Tenant, provider-connection, and verified-issuer storage boundary."""

    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity

    @classmethod
    def from_artifact(
        cls,
        artifact: FundamentalFilingArtifactManifest,
    ) -> "FundamentalFilingRepositoryScope":
        return cls(
            connection=artifact.reference.connection,
            issuer=artifact.reference.issuer,
        )

    @classmethod
    def from_package(
        cls,
        package: FundamentalFilingPackage,
    ) -> "FundamentalFilingRepositoryScope":
        return cls(
            connection=package.manifest.connection,
            issuer=package.manifest.issuer,
        )


class FundamentalFilingDeleteTarget(StrEnum):
    ARTIFACT = "artifact"
    PACKAGE = "package"


class FundamentalFilingDeleteStatus(StrEnum):
    DELETED = "deleted"
    NOT_FOUND = "not_found"
    REJECTED_ACTIVE_REFERENCE = "rejected_active_reference"


class FundamentalFilingDeleteAuthorization(FundamentalModel):
    """Narrow human authorization for one auditable destructive operation."""

    schema_version: str = Field(
        default="jarvis.fundamental_filing_delete_authorization.v1",
        pattern=r"^jarvis\.fundamental_filing_delete_authorization\.v1$",
    )
    audit_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    operation_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    authorized_by: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    target_kind: FundamentalFilingDeleteTarget
    target_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    target_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    reason: str = Field(min_length=10, max_length=500)
    authorized_at: datetime

    @field_validator("authorized_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("filing deletion authorization requires timezone")
        return value

    @field_validator("reason")
    @classmethod
    def require_single_line_reason(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("filing deletion reason must be a single line")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        expected_prefix = {
            FundamentalFilingDeleteTarget.ARTIFACT: "filing_artifact:",
            FundamentalFilingDeleteTarget.PACKAGE: "filing_package:",
        }[self.target_kind]
        if not self.target_id.startswith(expected_prefix):
            raise ValueError("filing deletion target does not match its kind")
        if self.target_id != f"{expected_prefix}{self.target_fingerprint}":
            raise ValueError("filing deletion target must bind its exact fingerprint")
        return self


class FundamentalFilingDeleteReceipt(FundamentalModel):
    """Non-secret result retained for every requested filing deletion."""

    schema_version: str = Field(
        default="jarvis.fundamental_filing_delete_receipt.v1",
        pattern=r"^jarvis\.fundamental_filing_delete_receipt\.v1$",
    )
    authorization: FundamentalFilingDeleteAuthorization
    status: FundamentalFilingDeleteStatus
    completed_at: datetime
    sanitized_detail: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    @field_validator("completed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("filing deletion receipt requires timezone")
        return value

    @field_validator("sanitized_detail")
    @classmethod
    def require_single_line_detail(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("filing deletion detail must be a single line")
        return value

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.completed_at < self.authorization.authorized_at:
            raise ValueError("filing deletion cannot predate authorization")
        if (
            self.status is FundamentalFilingDeleteStatus.REJECTED_ACTIVE_REFERENCE
            and self.sanitized_detail is None
        ):
            raise ValueError("rejected filing deletion requires a safe explanation")
        return self

    @computed_field
    @property
    def deleted(self) -> bool:
        return self.status is FundamentalFilingDeleteStatus.DELETED


@runtime_checkable
class FundamentalFilingRepository(Protocol):
    """Tenant-isolated metadata store for retained PDF artifacts and packages.

    Implementations persist metadata only; PDF bytes remain in the configured
    owner-only artifact store. Artifact saves are immutable and idempotent for
    identical fingerprints. Content-hash and version reads must remain inside
    the explicit connection-and-issuer scope.

    Package publication is compare-and-swap: ``None`` requires no active
    package, while an ID requires that exact package to be active. The complete
    aggregate must be durable before its active pointer changes. Any write or
    precondition failure preserves the previous active package.

    Recheck dates describe provider freshness, not retention expiry. Nothing
    is automatically purged. Deletion requires an exact, audited authorization
    and must reject artifacts referenced by a retained or active package.
    """

    @property
    def adapter_name(self) -> str:
        """Return a non-secret persistence adapter name for diagnostics."""
        ...

    def save_filing_artifact(
        self,
        artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest:
        """Persist immutable metadata or return its identical stored record."""
        ...

    def find_filing_artifacts_by_content_hash(
        self,
        content_sha256: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> tuple[FundamentalFilingArtifactManifest, ...]:
        """Return scoped artifact records sharing one exact PDF hash."""
        ...

    def get_filing_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingArtifactManifest | None:
        """Resolve one immutable artifact metadata record inside its scope."""
        ...

    def list_filing_artifact_versions(
        self,
        filing_reference_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> tuple[FundamentalFilingArtifactManifest, ...]:
        """Return all retained versions for one scoped provider reference."""
        ...

    def commit_filing_package(
        self,
        package: FundamentalFilingPackage,
        *,
        expected_active_package_id: str | None,
    ) -> FundamentalFilingPackage:
        """Persist and atomically publish a complete filing package using CAS."""
        ...

    def get_active_filing_package(
        self,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        """Return the scoped active package, including when recheck is due."""
        ...

    def get_filing_package(
        self,
        package_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        """Resolve one immutable filing package inside an explicit scope."""
        ...

    def delete_filing_artifact(
        self,
        artifact_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        authorization: FundamentalFilingDeleteAuthorization,
    ) -> FundamentalFilingDeleteReceipt:
        """Delete one unreferenced artifact after exact human authorization."""
        ...

    def delete_filing_package(
        self,
        package_id: str,
        *,
        scope: FundamentalFilingRepositoryScope,
        authorization: FundamentalFilingDeleteAuthorization,
    ) -> FundamentalFilingDeleteReceipt:
        """Delete one package after exact authorization and pointer handling."""
        ...
