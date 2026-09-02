"""Persistence port for cached Peer Comparison JSON documents."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.models.peer_comparison_storage import (
    PeerComparisonCacheKey,
    StoredPeerComparisonDocument,
)


@runtime_checkable
class PeerComparisonRepository(Protocol):
    """Tenant-isolated cache for immutable Peer Comparison JSON documents.

    Implementations must never return expired documents and must purge expired
    entries opportunistically around reads and writes. Saving identical content
    is idempotent. Different content under an active semantic key must be
    rejected unless the explicitly scoped atomic replacement operation is used.
    A failed replacement must preserve the previously active JSON document.
    """

    @property
    def adapter_name(self) -> str:
        """Return a non-secret persistence adapter name for diagnostics."""
        ...

    def save_peer_comparison(
        self,
        stored: StoredPeerComparisonDocument,
    ) -> StoredPeerComparisonDocument:
        """Insert a fresh document or return its identical active entry."""
        ...

    def replace_peer_comparison(
        self,
        stored: StoredPeerComparisonDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredPeerComparisonDocument:
        """Atomically replace one scoped entry with validated JSON content."""
        ...

    def get_peer_comparison(
        self,
        key: PeerComparisonCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredPeerComparisonDocument | None:
        """Return one scoped, unexpired entry or ``None``."""
        ...

    def get_peer_comparison_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredPeerComparisonDocument | None:
        """Resolve one browser-safe JSON reference inside an explicit scope."""
        ...

    def delete_peer_comparison(
        self,
        key: PeerComparisonCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        """Delete one exactly scoped cache entry and report whether it existed."""
        ...

    def purge_expired_peer_comparisons(
        self,
        *,
        as_of: datetime,
    ) -> int:
        """Delete entries expired at ``as_of`` and return the row count."""
        ...
