from app.models.market import HistoricalCandleSeries
from app.models.price_action import (
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


def detect_swing_pivots(
    series: HistoricalCandleSeries,
    *,
    left_strength: int = 2,
    right_strength: int = 2,
) -> SwingPivotDetectionResult:
    """Detect strictly confirmed swing highs and lows."""
    _validate_strength("left strength", left_strength)
    _validate_strength("right strength", right_strength)
    _validate_candle_order(series)

    candles = series.candles
    pivots: list[SwingPivot] = []
    stop_index = len(candles) - right_strength

    for index in range(left_strength, stop_index):
        candidate = candles[index]
        neighboring_candles = (
            candles[index - left_strength : index]
            + candles[index + 1 : index + right_strength + 1]
        )
        confirmed_at = candles[index + right_strength].timestamp

        if all(
            candidate.high > candle.high
            for candle in neighboring_candles
        ):
            pivots.append(
                SwingPivot(
                    pivot_type=SwingPivotType.HIGH,
                    pivot_at=candidate.timestamp,
                    confirmed_at=confirmed_at,
                    price=candidate.high,
                    left_strength=left_strength,
                    right_strength=right_strength,
                )
            )

        if all(
            candidate.low < candle.low
            for candle in neighboring_candles
        ):
            pivots.append(
                SwingPivot(
                    pivot_type=SwingPivotType.LOW,
                    pivot_at=candidate.timestamp,
                    confirmed_at=confirmed_at,
                    price=candidate.low,
                    left_strength=left_strength,
                    right_strength=right_strength,
                )
            )

    return SwingPivotDetectionResult(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        source=series.source,
        source_retrieved_at=series.retrieved_at,
        left_strength=left_strength,
        right_strength=right_strength,
        pivots=pivots,
    )


def _validate_strength(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"swing-pivot {name} must be an integer")

    if value < 1:
        raise ValueError(f"swing-pivot {name} must be at least 1")


def _validate_candle_order(series: HistoricalCandleSeries) -> None:
    for previous, current in zip(
        series.candles,
        series.candles[1:],
    ):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "historical candles must have unique timestamps in "
                "ascending order"
            )
