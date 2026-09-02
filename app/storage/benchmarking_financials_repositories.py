"""Persistence port for cached Benchmarking Financials JSON documents."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.benchmarking_financials_storage import (
    BenchmarkingFinancialsCacheKey,
    StoredBenchmarkingFinancialsDocument,
)
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)


@runtime_checkable
class BenchmarkingFinancialsRepository(Protocol):
    """Tenant-isolated cache for immutable Financial benchmark matrices.

    Implementations must never return expired documents and must purge expired
    entries opportunistically around reads and writes. Identical saves are
    idempotent. Conflicting active content must be rejected unless the scoped
    atomic replacement operation is used. A failed replacement must preserve
    the previously active JSON document.
    """

    @property
    def adapter_name(self) -> str:
        """Return a non-secret persistence adapter name for diagnostics."""
        ...

    def save_benchmarking_financials(
        self,
        stored: StoredBenchmarkingFinancialsDocument,
    ) -> StoredBenchmarkingFinancialsDocument:
        """Insert a fresh document or return its identical active entry."""
        ...

    def replace_benchmarking_financials(
        self,
        stored: StoredBenchmarkingFinancialsDocument,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> StoredBenchmarkingFinancialsDocument:
        """Atomically replace one scoped entry with validated JSON content."""
        ...

    def get_benchmarking_financials(
        self,
        key: BenchmarkingFinancialsCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredBenchmarkingFinancialsDocument | None:
        """Return one scoped, unexpired entry or ``None``."""
        ...

    def get_benchmarking_financials_by_cache_entry_id(
        self,
        cache_entry_id: str,
        *,
        scope: StructuredDocumentRepositoryScope,
        as_of: datetime,
    ) -> StoredBenchmarkingFinancialsDocument | None:
        """Resolve one browser-safe JSON reference inside an explicit scope."""
        ...

    def delete_benchmarking_financials(
        self,
        key: BenchmarkingFinancialsCacheKey,
        *,
        scope: StructuredDocumentRepositoryScope,
    ) -> bool:
        """Delete one exactly scoped cache entry and report whether it existed."""
        ...

    def purge_expired_benchmarking_financials(
        self,
        *,
        as_of: datetime,
    ) -> int:
        """Delete entries expired at ``as_of`` and return the row count."""
        ...
