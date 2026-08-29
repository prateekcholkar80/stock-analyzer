"""Database-neutral persistence port for cached fundamental snapshots."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.fundamental_storage import (
    FundamentalRepositoryScope,
    FundamentalSnapshotCacheKey,
    FundamentalSnapshotQuery,
    FundamentalSnapshotSummary,
    StoredFundamentalSnapshot,
)


@runtime_checkable
class FundamentalSnapshotRepository(Protocol):
    """Tenant-isolated cache for immutable, validated fundamental evidence.

    Implementations must purge expired entries opportunistically before reads
    and writes. Expired rows must never be returned, even when physical purge
    has not yet completed. Saving identical content is idempotent; saving
    different content under the same cache key must raise StorageConflictError.
    """

    @property
    def adapter_name(self) -> str:
        """Return a non-secret adapter identity for diagnostics."""
        ...

    def save_fundamental_snapshot(
        self,
        stored: StoredFundamentalSnapshot,
    ) -> StoredFundamentalSnapshot:
        """Save an unexpired snapshot or return its identical existing row."""
        ...

    def get_fundamental_snapshot(
        self,
        key: FundamentalSnapshotCacheKey,
        *,
        scope: FundamentalRepositoryScope,
        as_of: datetime,
    ) -> StoredFundamentalSnapshot | None:
        """Return a scoped unexpired entry, otherwise None and purge it."""
        ...

    def list_fundamental_snapshots(
        self,
        query: FundamentalSnapshotQuery,
        *,
        as_of: datetime,
    ) -> tuple[FundamentalSnapshotSummary, ...]:
        """List newest-first unexpired metadata within mandatory scope."""
        ...

    def delete_fundamental_snapshot(
        self,
        key: FundamentalSnapshotCacheKey,
        *,
        scope: FundamentalRepositoryScope,
    ) -> bool:
        """Delete one exactly scoped cache entry and report whether it existed."""
        ...

    def purge_expired_fundamental_snapshots(
        self,
        *,
        as_of: datetime,
    ) -> int:
        """Delete every entry expired at ``as_of`` for startup/maintenance."""
        ...
