from datetime import datetime

from app.analytics.candle_aggregation import aggregate_candles
from app.exceptions import InsufficientDataError
from app.models.market import HistoricalCandleSeries
from app.models.workflow import (
    WorkflowActivityDescriptor,
    WorkflowEventState,
    WorkflowParticipantKind,
    WorkflowStage,
)
from app.models.timeframes import (
    SwingTimeframeLineage,
    SwingTimeframeSeries,
    market_series_fingerprint,
)
from app.workflow.events import WorkflowEventEmitter


_DAILY_AGGREGATION = WorkflowActivityDescriptor(
    activity_id="market.aggregate.daily",
    participant_id="research.market_data_service",
    participant_kind=WorkflowParticipantKind.SERVICE,
    participant_label="Daily Data Aggregator",
    timeframe="ONE_DAY",
)
_WEEKLY_AGGREGATION = WorkflowActivityDescriptor(
    activity_id="market.aggregate.weekly",
    participant_id="research.market_data_service",
    participant_kind=WorkflowParticipantKind.SERVICE,
    participant_label="Weekly Data Aggregator",
    timeframe="ONE_WEEK",
)


class DeriveSwingTimeframes:
    """Derive completed daily and weekly bars from one hourly source."""

    use_case_id = "jarvis.derive_swing_timeframes.v1"

    def execute(
        self,
        hourly_series: HistoricalCandleSeries,
        *,
        as_of: datetime | None = None,
        event_emitter: WorkflowEventEmitter | None = None,
    ) -> SwingTimeframeSeries:
        if not isinstance(hourly_series, HistoricalCandleSeries):
            raise ValueError(
                "swing timeframe derivation requires a validated series"
            )
        if hourly_series.interval != "ONE_HOUR":
            raise ValueError(
                "swing timeframe derivation requires ONE_HOUR candles"
            )
        if event_emitter is not None and not isinstance(
            event_emitter,
            WorkflowEventEmitter,
        ):
            raise ValueError("timeframe derivation requires a workflow emitter")

        try:
            daily = _aggregate_with_events(
                hourly_series,
                "ONE_DAY",
                _DAILY_AGGREGATION,
                event_emitter,
                as_of,
            )
            weekly = _aggregate_with_events(
                daily,
                "ONE_WEEK",
                _WEEKLY_AGGREGATION,
                event_emitter,
                as_of,
            )
        except ValueError as exc:
            raise InsufficientDataError(
                "hourly history does not contain complete daily and weekly "
                "candles"
            ) from exc

        lineage = SwingTimeframeLineage(
            hourly_fingerprint=market_series_fingerprint(hourly_series),
            daily_fingerprint=market_series_fingerprint(daily),
            weekly_fingerprint=market_series_fingerprint(weekly),
        )
        return SwingTimeframeSeries(
            hourly=hourly_series,
            daily=daily,
            weekly=weekly,
            lineage=lineage,
        )


def _aggregate_with_events(
    series: HistoricalCandleSeries,
    target_interval: str,
    activity: WorkflowActivityDescriptor,
    emitter: WorkflowEventEmitter | None,
    as_of: datetime | None,
) -> HistoricalCandleSeries:
    common = {
        "exchange": series.exchange,
        "symbol": series.symbol,
        "activity": activity,
    }
    if emitter is not None:
        emitter.emit(
            WorkflowStage.DATA_PREPARATION,
            WorkflowEventState.STARTED,
            **common,
        )
    try:
        result = aggregate_candles(
            series,
            target_interval=target_interval,
            as_of=as_of,
        )
    except Exception:
        if emitter is not None:
            emitter.emit(
                WorkflowStage.DATA_PREPARATION,
                WorkflowEventState.FAILED,
                **common,
            )
        raise
    if emitter is not None:
        emitter.emit(
            WorkflowStage.DATA_PREPARATION,
            WorkflowEventState.COMPLETED,
            **common,
        )
    return result
