from datetime import datetime
from typing import Protocol, runtime_checkable

from app.analytics.walk_forward import WalkForwardBacktestEngine
from app.models.backtest import WalkForwardBacktestResult
from app.models.market import HistoricalCandleSeries
from app.models.storage import (
    StoredBacktestRun,
    WalkForwardBacktestArchiveReceipt,
    payload_fingerprint,
)


@runtime_checkable
class WalkForwardBacktestRunner(Protocol):
    """Port for producing one validated walk-forward result."""

    def run(
        self,
        market_series: HistoricalCandleSeries,
    ) -> WalkForwardBacktestResult:
        ...


@runtime_checkable
class BacktestArchiveWriter(Protocol):
    """Port for archiving a completed walk-forward result."""

    @property
    def adapter_name(self) -> str:
        ...

    def archive_backtest_run(
        self,
        result: WalkForwardBacktestResult,
        *,
        run_id: str | None = None,
        stored_at: datetime | None = None,
        archive_market_data: bool = True,
    ) -> StoredBacktestRun:
        ...


class RunAndArchiveWalkForwardBacktest:
    """Run historical evaluation and persist its validated aggregate."""

    use_case_id = "jarvis.run_and_archive_walk_forward_backtest.v1"

    def __init__(
        self,
        archive: BacktestArchiveWriter,
        runner: WalkForwardBacktestRunner | None = None,
    ) -> None:
        effective_runner = runner or WalkForwardBacktestEngine()
        if not isinstance(effective_runner, WalkForwardBacktestRunner):
            raise ValueError(
                "run-and-archive use case requires a backtest runner"
            )
        if not isinstance(archive, BacktestArchiveWriter):
            raise ValueError(
                "run-and-archive use case requires an archive writer"
            )
        self._runner = effective_runner
        self._archive = archive

    def execute(
        self,
        market_series: HistoricalCandleSeries,
        *,
        run_id: str | None = None,
        stored_at: datetime | None = None,
    ) -> WalkForwardBacktestArchiveReceipt:
        if not isinstance(market_series, HistoricalCandleSeries):
            raise ValueError(
                "run-and-archive use case requires historical candles"
            )
        assigned_series = HistoricalCandleSeries.model_validate(
            market_series.model_dump()
        )
        result = self._runner.run(assigned_series)
        if not isinstance(result, WalkForwardBacktestResult):
            raise ValueError(
                "backtest runner must return a validated walk-forward result"
            )
        validated_result = WalkForwardBacktestResult.model_validate(
            result.model_dump()
        )
        if payload_fingerprint(validated_result.market_series) != (
            payload_fingerprint(assigned_series)
        ):
            raise ValueError(
                "backtest result must reference the exact assigned market "
                "series"
            )
        stored = self._archive.archive_backtest_run(
            validated_result,
            run_id=run_id,
            stored_at=stored_at,
            archive_market_data=True,
        )
        if not isinstance(stored, StoredBacktestRun):
            raise ValueError(
                "archive writer must return a validated stored backtest run"
            )
        validated_stored = StoredBacktestRun.model_validate(
            stored.model_dump()
        )
        if run_id is not None and validated_stored.run_id != run_id:
            raise ValueError(
                "archive writer did not preserve the requested run "
                "identifier"
            )
        if validated_stored.result_fingerprint != payload_fingerprint(
            validated_result
        ):
            raise ValueError(
                "archive writer returned a different backtest result"
            )
        return WalkForwardBacktestArchiveReceipt(
            use_case_id=self.use_case_id,
            adapter_name=self._archive.adapter_name,
            market_dataset_id=(
                f"market:{validated_stored.market_fingerprint}"
            ),
            backtest_run=validated_stored,
        )


def run_and_archive_walk_forward_backtest(
    market_series: HistoricalCandleSeries,
    *,
    archive: BacktestArchiveWriter,
    runner: WalkForwardBacktestRunner | None = None,
    run_id: str | None = None,
    stored_at: datetime | None = None,
) -> WalkForwardBacktestArchiveReceipt:
    return RunAndArchiveWalkForwardBacktest(
        archive,
        runner,
    ).execute(
        market_series,
        run_id=run_id,
        stored_at=stored_at,
    )
