"""Database-neutral storage adapter contracts.

Identifiers are immutable: saving the same identifier and payload is
idempotent, while different content for an existing identifier raises
StorageConflictError. Concrete adapters translate native failures into the
application StorageError hierarchy.
"""

from typing import Protocol, runtime_checkable

from app.models.storage import (
    BacktestRunQuery,
    BacktestRunSummary,
    MarketSeriesQuery,
    MarketSeriesSummary,
    StoredBacktestRun,
    StoredMarketSeries,
)


@runtime_checkable
class MarketSeriesRepository(Protocol):
    """Persistence port for immutable historical candle datasets."""

    def save_market_series(
        self,
        stored: StoredMarketSeries,
    ) -> StoredMarketSeries:
        """Store a dataset or return an identical existing dataset."""
        ...

    def get_market_series(
        self,
        dataset_id: str,
    ) -> StoredMarketSeries | None:
        """Return a stored dataset, or None when it does not exist."""
        ...

    def list_market_series(
        self,
        query: MarketSeriesQuery | None = None,
    ) -> tuple[MarketSeriesSummary, ...]:
        """Return newest-first metadata without loading candle payloads."""
        ...

    def delete_market_series(self, dataset_id: str) -> bool:
        """Delete a dataset and report whether it existed."""
        ...


@runtime_checkable
class BacktestRunRepository(Protocol):
    """Persistence port for immutable walk-forward backtest aggregates."""

    def save_backtest_run(
        self,
        stored: StoredBacktestRun,
    ) -> StoredBacktestRun:
        """Store a run or return an identical existing run."""
        ...

    def get_backtest_run(
        self,
        run_id: str,
    ) -> StoredBacktestRun | None:
        """Return a stored run, or None when it does not exist."""
        ...

    def list_backtest_runs(
        self,
        query: BacktestRunQuery | None = None,
    ) -> tuple[BacktestRunSummary, ...]:
        """Return newest-first metadata without loading full results."""
        ...

    def delete_backtest_run(self, run_id: str) -> bool:
        """Delete a run and report whether it existed."""
        ...


@runtime_checkable
class JarvisStorageAdapter(
    MarketSeriesRepository,
    BacktestRunRepository,
    Protocol,
):
    """Complete storage port implemented by each database adapter."""

    @property
    def adapter_name(self) -> str:
        """Return a non-secret identifier for diagnostics."""
        ...
