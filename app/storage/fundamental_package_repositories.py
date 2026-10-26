"""Database-neutral atomic persistence port for fundamental packages."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.fundamental_package import FundamentalEvidencePackage
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
    ProviderConnectionScope,
)


class FundamentalPackageRepositoryScope(FundamentalModel):
    """Tenant, provider connection, and issuer boundary for package reads."""

    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity

    @classmethod
    def from_package(
        cls,
        package: FundamentalEvidencePackage,
    ) -> "FundamentalPackageRepositoryScope":
        return cls(
            connection=package.manifest.connection,
            issuer=package.manifest.issuer,
        )


@runtime_checkable
class FundamentalPackageRepository(Protocol):
    """Tenant-isolated store with atomic active-package publication.

    Implementations must persist the complete immutable aggregate before
    changing its active pointer. ``expected_active_package_id`` is a strict
    compare-and-swap precondition: ``None`` requires that no active package
    exists, while a package ID requires that exact package to be active.
    Failed writes or precondition conflicts must leave the prior package and
    active pointer unchanged. Reads must never return expired packages or data
    outside the supplied connection-and-issuer scope.
    """

    @property
    def adapter_name(self) -> str:
        """Return a non-secret persistence adapter name for diagnostics."""
        ...

    def commit_fundamental_package(
        self,
        package: FundamentalEvidencePackage,
        *,
        expected_active_package_id: str | None,
    ) -> FundamentalEvidencePackage:
        """Persist and atomically publish a complete package using CAS."""
        ...

    def get_active_fundamental_package(
        self,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        """Return the scoped active package when it remains unexpired."""
        ...

    def get_fundamental_package(
        self,
        package_id: str,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        """Resolve one immutable package by ID inside an explicit scope."""
        ...

    def purge_expired_fundamental_packages(
        self,
        *,
        as_of: datetime,
    ) -> int:
        """Delete expired packages and stale active pointers atomically."""
        ...
