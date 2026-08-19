from threading import RLock
from typing import TypeVar

from app.exceptions import StorageConflictError
from app.models.storage import (
    BacktestRunQuery,
    BacktestRunSummary,
    MarketSeriesQuery,
    MarketSeriesSummary,
    StorageModel,
    StoredBacktestRun,
    StoredMarketSeries,
    backtest_run_summary,
    market_series_summary,
)


_StorageModelT = TypeVar("_StorageModelT", bound=StorageModel)


def _copy(model: _StorageModelT) -> _StorageModelT:
    return model.model_copy(deep=True)


def _matches_stored_range(stored_at, query) -> bool:
    return not (
        query.stored_from is not None
        and stored_at < query.stored_from
    ) and not (
        query.stored_to is not None
        and stored_at > query.stored_to
    )


class InMemoryJarvisStorage:
    """Thread-safe reference adapter for tests and ephemeral workflows."""

    adapter_name = "in_memory"

    def __init__(self) -> None:
        self._market_series: dict[str, StoredMarketSeries] = {}
        self._backtest_runs: dict[str, StoredBacktestRun] = {}
        self._lock = RLock()

    def save_market_series(
        self,
        stored: StoredMarketSeries,
    ) -> StoredMarketSeries:
        value = StoredMarketSeries.model_validate(stored)
        with self._lock:
            existing = self._market_series.get(value.dataset_id)
            if existing is not None:
                if existing.payload_fingerprint != value.payload_fingerprint:
                    raise StorageConflictError(
                        "market dataset identifier already contains "
                        "different data"
                    )
                return _copy(existing)
            self._market_series[value.dataset_id] = _copy(value)
            return _copy(value)

    def get_market_series(
        self,
        dataset_id: str,
    ) -> StoredMarketSeries | None:
        with self._lock:
            stored = self._market_series.get(dataset_id)
            return _copy(stored) if stored is not None else None

    def list_market_series(
        self,
        query: MarketSeriesQuery | None = None,
    ) -> tuple[MarketSeriesSummary, ...]:
        filters = query or MarketSeriesQuery()
        with self._lock:
            values = [
                value
                for value in self._market_series.values()
                if _matches_stored_range(value.stored_at, filters)
                and (
                    filters.exchange is None
                    or value.series.exchange == filters.exchange
                )
                and (
                    filters.symbol_token is None
                    or value.series.symbol_token == filters.symbol_token
                )
                and (
                    filters.interval is None
                    or value.series.interval == filters.interval
                )
                and (
                    filters.source is None
                    or value.series.source == filters.source
                )
            ]
        values.sort(
            key=lambda value: (value.stored_at, value.dataset_id),
            reverse=True,
        )
        selected = values[
            filters.offset:filters.offset + filters.limit
        ]
        return tuple(market_series_summary(value) for value in selected)

    def delete_market_series(self, dataset_id: str) -> bool:
        with self._lock:
            return self._market_series.pop(dataset_id, None) is not None

    def save_backtest_run(
        self,
        stored: StoredBacktestRun,
    ) -> StoredBacktestRun:
        value = StoredBacktestRun.model_validate(stored)
        with self._lock:
            existing = self._backtest_runs.get(value.run_id)
            if existing is not None:
                if existing.result_fingerprint != value.result_fingerprint:
                    raise StorageConflictError(
                        "backtest run identifier already contains "
                        "different data"
                    )
                return _copy(existing)
            self._backtest_runs[value.run_id] = _copy(value)
            return _copy(value)

    def get_backtest_run(
        self,
        run_id: str,
    ) -> StoredBacktestRun | None:
        with self._lock:
            stored = self._backtest_runs.get(run_id)
            return _copy(stored) if stored is not None else None

    def list_backtest_runs(
        self,
        query: BacktestRunQuery | None = None,
    ) -> tuple[BacktestRunSummary, ...]:
        filters = query or BacktestRunQuery()
        with self._lock:
            values = [
                value
                for value in self._backtest_runs.values()
                if _matches_stored_range(value.stored_at, filters)
                and (
                    filters.backtest_id is None
                    or value.result.backtest_id == filters.backtest_id
                )
                and (
                    filters.engine_id is None
                    or value.result.engine_id == filters.engine_id
                )
                and (
                    filters.exchange is None
                    or value.result.market_series.exchange
                    == filters.exchange
                )
                and (
                    filters.symbol_token is None
                    or value.result.market_series.symbol_token
                    == filters.symbol_token
                )
                and (
                    filters.interval is None
                    or value.result.market_series.interval
                    == filters.interval
                )
            ]
        values.sort(
            key=lambda value: (value.stored_at, value.run_id),
            reverse=True,
        )
        selected = values[
            filters.offset:filters.offset + filters.limit
        ]
        return tuple(backtest_run_summary(value) for value in selected)

    def delete_backtest_run(self, run_id: str) -> bool:
        with self._lock:
            return self._backtest_runs.pop(run_id, None) is not None
