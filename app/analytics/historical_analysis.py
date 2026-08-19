from collections.abc import Callable
from datetime import datetime

from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
    TechnicalAnalysisError,
)
from app.models.historical_analysis import (
    HistoricalAnalysisFailure,
    HistoricalAnalysisFailureKind,
    HistoricalSwingProfilePoint,
    HistoricalSwingProfileSeries,
)
from app.models.market import HistoricalCandleSeries
from app.models.signals import SwingTradingSignalProfile


SwingProfileEvaluator = Callable[
    [HistoricalCandleSeries],
    SwingTradingSignalProfile,
]


def analyze_swing_profiles_over_history(
    market_series: HistoricalCandleSeries,
    evaluator: SwingProfileEvaluator,
    *,
    warmup_candles: int = 0,
    evaluation_stride: int = 1,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    analysis_version: str = "swing-history-v1",
    evaluator_name: str | None = None,
) -> HistoricalSwingProfileSeries:
    """Evaluate swing profiles using only each date's candle prefix."""
    if not callable(evaluator):
        raise ValueError("historical evaluator must be callable")
    name = evaluator_name or _evaluator_name(evaluator)
    scheduled_indices = _scheduled_indices(
        market_series,
        warmup_candles,
        evaluation_stride,
        start_at,
        end_at,
    )

    points = []
    failures = []
    for index in scheduled_indices:
        candle = market_series.candles[index]
        prefix = market_series.model_copy(
            update={"candles": market_series.candles[:index + 1]}
        )
        try:
            profile = evaluator(prefix)
        except TechnicalAnalysisError as exc:
            failures.append(
                HistoricalAnalysisFailure(
                    candle_index=index,
                    available_candle_count=index + 1,
                    candle=candle,
                    kind=_failure_kind(exc),
                    error_type=type(exc).__name__,
                    message=str(exc) or type(exc).__name__,
                )
            )
            continue

        profile = _validate_profile_result(
            profile,
            market_series,
            candle.timestamp,
        )
        points.append(
            HistoricalSwingProfilePoint(
                candle_index=index,
                available_candle_count=index + 1,
                candle=candle,
                profile=profile,
            )
        )

    return HistoricalSwingProfileSeries(
        analysis_version=analysis_version,
        evaluator_name=name,
        market_series=market_series,
        warmup_candles=warmup_candles,
        evaluation_stride=evaluation_stride,
        start_at=start_at,
        end_at=end_at,
        points=points,
        failures=failures,
    )


def _scheduled_indices(
    market_series: HistoricalCandleSeries,
    warmup_candles: int,
    evaluation_stride: int,
    start_at: datetime | None,
    end_at: datetime | None,
) -> list[int]:
    if (
        isinstance(warmup_candles, bool)
        or not isinstance(warmup_candles, int)
        or warmup_candles < 0
    ):
        raise ValueError(
            "historical warmup must be a non-negative integer"
        )
    if (
        isinstance(evaluation_stride, bool)
        or not isinstance(evaluation_stride, int)
        or evaluation_stride < 1
    ):
        raise ValueError(
            "historical evaluation stride must be a positive integer"
        )
    for name, value in (("start", start_at), ("end", end_at)):
        if value is None:
            continue
        if not isinstance(value, datetime):
            raise ValueError(
                f"historical analysis {name} must be a datetime"
            )
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"historical analysis {name} must include timezone "
                "information"
            )
        if value > market_series.retrieved_at:
            raise ValueError(
                f"historical analysis {name} cannot follow source "
                "retrieval"
            )
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError(
            "historical analysis start cannot follow its end"
        )

    previous_timestamp = None
    for candle in market_series.candles:
        if (
            previous_timestamp is not None
            and candle.timestamp <= previous_timestamp
        ):
            raise ValueError(
                "historical analysis candles must have unique timestamps "
                "in ascending order"
            )
        if candle.timestamp > market_series.retrieved_at:
            raise ValueError(
                "historical analysis cannot use candles after source "
                "retrieval"
            )
        previous_timestamp = candle.timestamp

    eligible = [
        index
        for index, candle in enumerate(market_series.candles)
        if index >= warmup_candles
        and (start_at is None or candle.timestamp >= start_at)
        and (end_at is None or candle.timestamp <= end_at)
    ]
    return eligible[::evaluation_stride]


def _evaluator_name(evaluator) -> str:
    name = getattr(evaluator, "__qualname__", None) or getattr(
        evaluator,
        "__class__",
        type(evaluator),
    ).__name__
    if not isinstance(name, str) or not name.strip():
        raise ValueError("historical evaluator name cannot be determined")
    return name


def _failure_kind(
    error: TechnicalAnalysisError,
) -> HistoricalAnalysisFailureKind:
    if isinstance(error, InsufficientDataError):
        return HistoricalAnalysisFailureKind.INSUFFICIENT_DATA
    if isinstance(error, IndicatorCalculationError):
        return HistoricalAnalysisFailureKind.INDICATOR_CALCULATION
    return HistoricalAnalysisFailureKind.TECHNICAL_ANALYSIS


def _validate_profile_result(
    profile: SwingTradingSignalProfile,
    market_series: HistoricalCandleSeries,
    evaluated_at: datetime,
) -> SwingTradingSignalProfile:
    if type(profile) is not SwingTradingSignalProfile:
        raise ValueError(
            "historical evaluator must return a swing trading profile"
        )
    profile = SwingTradingSignalProfile.model_validate(
        profile.model_dump(exclude_computed_fields=True)
    )
    snapshot = profile.snapshot
    expected_identity = (
        market_series.exchange,
        market_series.symbol_token,
        market_series.symbol,
        market_series.interval,
        market_series.source,
        market_series.retrieved_at,
    )
    actual_identity = (
        snapshot.exchange,
        snapshot.symbol_token,
        snapshot.symbol,
        snapshot.interval,
        snapshot.source,
        snapshot.source_retrieved_at,
    )
    if actual_identity != expected_identity:
        raise ValueError(
            "historical evaluator returned a profile for another source"
        )
    if snapshot.evaluated_at != evaluated_at:
        raise ValueError(
            "historical evaluator must evaluate the scheduled candle"
        )
    return profile
