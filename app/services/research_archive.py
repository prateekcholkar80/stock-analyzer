from datetime import UTC, datetime

from app.models.backtest import WalkForwardBacktestResult
from app.models.debate import AgenticDebateResult
from app.models.market import HistoricalCandleSeries
from app.models.signals import SwingTradingSignalProfile
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
    debate_signal_signature,
    stored_backtest_run,
    stored_debate_run,
    stored_market_series,
)
from app.storage.repositories import JarvisStorageAdapter


class ResearchArchiveService:
    """Database-agnostic application service for research persistence."""

    def __init__(self, storage: JarvisStorageAdapter) -> None:
        if not isinstance(storage, JarvisStorageAdapter):
            raise ValueError(
                "research archive requires a complete storage adapter"
            )
        self._storage = storage

    @property
    def adapter_name(self) -> str:
        return self._storage.adapter_name

    def archive_market_series(
        self,
        series: HistoricalCandleSeries,
        *,
        dataset_id: str | None = None,
        stored_at: datetime | None = None,
    ) -> StoredMarketSeries:
        stored = stored_market_series(
            series,
            dataset_id=dataset_id,
            stored_at=stored_at or datetime.now(UTC),
        )
        return self._storage.save_market_series(stored)

    def get_market_series(
        self,
        dataset_id: str,
    ) -> StoredMarketSeries | None:
        return self._storage.get_market_series(dataset_id)

    def list_market_series(
        self,
        query: MarketSeriesQuery | None = None,
    ) -> tuple[MarketSeriesSummary, ...]:
        return self._storage.list_market_series(query)

    def delete_market_series(self, dataset_id: str) -> bool:
        return self._storage.delete_market_series(dataset_id)

    def archive_backtest_run(
        self,
        result: WalkForwardBacktestResult,
        *,
        run_id: str | None = None,
        stored_at: datetime | None = None,
        archive_market_data: bool = True,
    ) -> StoredBacktestRun:
        timestamp = stored_at or datetime.now(UTC)
        if archive_market_data:
            self.archive_market_series(
                result.market_series,
                stored_at=timestamp,
            )
        stored = stored_backtest_run(
            result,
            run_id=run_id,
            stored_at=timestamp,
        )
        return self._storage.save_backtest_run(stored)

    def get_backtest_run(
        self,
        run_id: str,
    ) -> StoredBacktestRun | None:
        return self._storage.get_backtest_run(run_id)

    def list_backtest_runs(
        self,
        query: BacktestRunQuery | None = None,
    ) -> tuple[BacktestRunSummary, ...]:
        return self._storage.list_backtest_runs(query)

    def delete_backtest_run(self, run_id: str) -> bool:
        return self._storage.delete_backtest_run(run_id)

    def archive_debate_run(
        self,
        result: AgenticDebateResult,
        profile: SwingTradingSignalProfile,
        *,
        run_id: str | None = None,
        stored_at: datetime | None = None,
    ) -> StoredDebateRun:
        stored = stored_debate_run(
            result,
            profile,
            run_id=run_id,
            stored_at=stored_at or datetime.now(UTC),
        )
        return self._storage.save_debate_run(stored)

    def get_debate_run(
        self,
        run_id: str,
    ) -> StoredDebateRun | None:
        return self._storage.get_debate_run(run_id)

    def list_debate_runs(
        self,
        query: DebateRunQuery | None = None,
    ) -> tuple[DebateRunSummary, ...]:
        return self._storage.list_debate_runs(query)

    def delete_debate_run(self, run_id: str) -> bool:
        return self._storage.delete_debate_run(run_id)

    def find_similar_debate_runs(
        self,
        profile: SwingTradingSignalProfile,
        *,
        exclude_run_id: str | None = None,
        limit: int = 5,
    ) -> tuple[DebateRunSummary, ...]:
        signature = debate_signal_signature(profile)
        return self._storage.find_similar_debate_runs(
            signature,
            exclude_run_id=exclude_run_id,
            limit=limit,
        )
