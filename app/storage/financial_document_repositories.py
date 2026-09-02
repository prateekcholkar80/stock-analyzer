"""Persistence ports for cached structured financial documents."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.financial_document_storage import (
    StoredStructuredFinancialDocument,
    StructuredDocumentCacheKey,
    StructuredDocumentRepositoryScope,
)


@runtime_checkable
class StructuredFinancialDocumentRepository(Protocol):
    """Tenant-isolated cache for immutable structured financial documents.

    Implementations must never return expired documents and must purge expired
    entries opportunistically around reads and writes. Saving identical content
    is idempotent. Different content under an active semantic cache key must be
    rejected unless the explicitly scoped atomic replacement operation is used.
    A failed replacement must preserve the previously active document.
    """

    @property
    def adapter_name(self) -> str:
        """Return a non-secret persistence adapter name for diagnostics."""
        ...

    def save_structured_financial_document(
        self,
        stored: StoredStructuredFinancialDocument,
    ) -> StoredStructuredFinancialDocument:
        """Insert a fresh document or return its identical active entry."""
        ...

    def replace_structured_financial_document(
        self,
        stored: StoredStructuredFinancialDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredStructuredFinancialDocument:
        """Atomically replace one scoped entry with newer validated content."""
        ...

    def get_structured_financial_document(
        self,
        key: StructuredDocumentCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredStructuredFinancialDocument | None:
        """Return one scoped, unexpired entry or ``None``."""
        ...

    def get_structured_financial_document_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredStructuredFinancialDocument | None:
        """Resolve one browser-safe reference inside an explicit scope."""
        ...

    def delete_structured_financial_document(
        self,
        key: StructuredDocumentCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        """Delete one exactly scoped cache entry and report whether it existed."""
        ...

    def purge_expired_structured_financial_documents(
        self,
        *,
        as_of: datetime,
    ) -> int:
        """Delete documents expired at ``as_of`` and return the row count."""
        ...
