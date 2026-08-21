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
    DebateRunQuery,
    DebateRunSummary,
    MarketSeriesQuery,
    MarketSeriesSummary,
    StoredBacktestRun,
    StoredDebateRun,
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
class DebateRunRepository(Protocol):
    """Persistence port for immutable Bull/Bear debate results."""

    def save_debate_run(
        self,
        stored: StoredDebateRun,
    ) -> StoredDebateRun:
        """Store a debate run or return an identical existing run."""
        ...

    def get_debate_run(
        self,
        run_id: str,
    ) -> StoredDebateRun | None:
        """Return a stored debate run, or None when it does not exist."""
        ...

    def list_debate_runs(
        self,
        query: DebateRunQuery | None = None,
    ) -> tuple[DebateRunSummary, ...]:
        """Return newest-first metadata without loading full transcripts."""
        ...

    def delete_debate_run(self, run_id: str) -> bool:
        """Delete a debate run and report whether it existed."""
        ...

    def find_similar_debate_runs(
        self,
        signature: tuple[str, ...],
        *,
        exclude_run_id: str | None = None,
        limit: int = 5,
    ) -> tuple[DebateRunSummary, ...]:
        """Return stored runs ranked by shared-signature-token overlap.

        Ties break newest-stored-first. Runs sharing zero tokens with
        ``signature`` are never returned, even if ``limit`` isn't reached.
        """
        ...


@runtime_checkable
class JarvisStorageAdapter(
    MarketSeriesRepository,
    BacktestRunRepository,
    DebateRunRepository,
    Protocol,
):
    """Complete storage port implemented by each database adapter."""

    @property
    def adapter_name(self) -> str:
        """Return a non-secret identifier for diagnostics."""
        ...
