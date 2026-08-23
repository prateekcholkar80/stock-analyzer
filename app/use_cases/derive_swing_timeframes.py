from app.analytics.candle_aggregation import aggregate_candles
from app.exceptions import InsufficientDataError
from app.models.market import HistoricalCandleSeries
from app.models.timeframes import (
    SwingTimeframeLineage,
    SwingTimeframeSeries,
    market_series_fingerprint,
)


class DeriveSwingTimeframes:
    """Derive completed daily and weekly bars from one hourly source."""

    use_case_id = "jarvis.derive_swing_timeframes.v1"

    def execute(
        self,
        hourly_series: HistoricalCandleSeries,
    ) -> SwingTimeframeSeries:
        if not isinstance(hourly_series, HistoricalCandleSeries):
            raise ValueError(
                "swing timeframe derivation requires a validated series"
            )
        if hourly_series.interval != "ONE_HOUR":
            raise ValueError(
                "swing timeframe derivation requires ONE_HOUR candles"
            )

        try:
            daily = aggregate_candles(
                hourly_series,
                target_interval="ONE_DAY",
            )
            weekly = aggregate_candles(
                daily,
                target_interval="ONE_WEEK",
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
