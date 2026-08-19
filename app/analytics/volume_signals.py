from datetime import datetime
from math import isclose, isfinite

from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
)
from app.models.technical import (
    IndicatorPoint,
    IndicatorSeries,
    PriceField,
)


def generate_obv_confirmation_signal(
    obv_series: IndicatorSeries,
    market_series: HistoricalCandleSeries,
    *,
    trend_lookback: int = 5,
    price_change_tolerance_percentage: float = 0.5,
    obv_flow_tolerance_percentage: float = 1.0,
    volume_lookback: int = 20,
    minimum_volume_points: int = 5,
    high_volume_multiplier: float = 1.5,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate price/OBV confirmation and divergence evidence."""
    price_field = _validate_obv_series(obv_series)
    _validate_identity(obv_series, market_series)
    _validate_candle_order(market_series)

    normalized_trend_lookback = _validate_integer(
        "OBV trend lookback",
        trend_lookback,
        minimum=1,
    )
    price_tolerance = _validate_percentage(
        "OBV price-change tolerance",
        price_change_tolerance_percentage,
    )
    flow_tolerance = _validate_percentage(
        "OBV flow tolerance",
        obv_flow_tolerance_percentage,
    )
    normalized_volume_lookback = _validate_integer(
        "OBV volume lookback",
        volume_lookback,
        minimum=1,
    )
    minimum_volume = _validate_integer(
        "OBV minimum volume points",
        minimum_volume_points,
        minimum=1,
    )
    if minimum_volume > normalized_volume_lookback:
        raise ValueError(
            "OBV minimum volume points cannot exceed volume lookback"
        )
    volume_multiplier = _validate_high_volume_multiplier(
        high_volume_multiplier
    )
    _validate_as_of(as_of)

    current_index = _latest_available_index(obv_series, as_of)
    reference_index = max(
        0,
        current_index - normalized_trend_lookback,
    )
    points = obv_series.points[reference_index:current_index + 1]
    candles, current_market_index = _matching_candles(
        points,
        market_series,
    )
    _validate_obv_transitions(points, candles, price_field)

    current_point = points[-1]
    current_candle = candles[-1]
    current_price = float(getattr(current_candle, price_field.value))
    current_obv = current_point.value
    trend_evaluated = len(points) >= 2

    reference_price = current_price
    reference_obv = current_obv
    price_change_percentage = 0.0
    obv_change = 0.0
    obv_flow_percentage = 0.0
    price_direction = "neutral"
    obv_direction = "neutral"
    if trend_evaluated:
        reference_candle = candles[0]
        reference_price = float(
            getattr(reference_candle, price_field.value)
        )
        if reference_price <= 0:
            raise ValueError(
                "OBV price-change normalization requires a positive "
                "reference price"
            )
        reference_obv = points[0].value
        price_change_percentage = (
            (current_price - reference_price)
            / reference_price
            * 100
        )
        obv_change = current_obv - reference_obv
        traded_volume = sum(candle.volume for candle in candles[1:])
        obv_flow_percentage = _obv_flow_percentage(
            obv_change,
            traded_volume,
        )
        price_direction = _classify_change(
            price_change_percentage,
            price_tolerance,
        )
        obv_direction = _classify_change(
            obv_flow_percentage,
            flow_tolerance,
        )

    volume_evidence = _evaluate_current_volume(
        market_series,
        current_market_index,
        normalized_volume_lookback,
        minimum_volume,
        volume_multiplier,
    )
    direction, strength, condition = _classify_obv_condition(
        price_direction,
        obv_direction,
        volume_evidence["is_high_volume"],
        trend_evaluated,
    )

    observed_values = {
        "price_field": price_field.value,
        "reference_price": reference_price,
        "current_price": current_price,
        "price_change_percentage": price_change_percentage,
        "price_direction": price_direction,
        "reference_obv": reference_obv,
        "current_obv": current_obv,
        "obv_change": obv_change,
        "obv_flow_percentage": obv_flow_percentage,
        "obv_direction": obv_direction,
        "trend_evaluated": trend_evaluated,
        "trend_observation_count": len(points),
        "current_volume": current_candle.volume,
        "volume_evaluated": volume_evidence["evaluated"],
        "volume_observation_count": volume_evidence["count"],
        "is_high_volume": volume_evidence["is_high_volume"],
        "condition": condition,
    }
    if volume_evidence["evaluated"]:
        observed_values["average_prior_volume"] = volume_evidence[
            "average"
        ]
        observed_values["volume_baseline_zero"] = volume_evidence[
            "baseline_zero"
        ]
        if volume_evidence["ratio"] is not None:
            observed_values["current_volume_ratio"] = volume_evidence[
                "ratio"
            ]

    return TechnicalSignalEvidence(
        evidence_id=(
            f"obv_confirmation.obv.{price_field.value}."
            f"lookback{normalized_trend_lookback}"
        ),
        name=f"OBV({normalized_trend_lookback}) {condition}",
        category=SignalCategory.VOLUME,
        direction=direction,
        strength=strength,
        source="volume_signals.obv_price_confirmation",
        explanation=_build_explanation(
            price_field.value,
            price_change_percentage,
            obv_flow_percentage,
            condition,
            volume_evidence["evaluated"],
            volume_evidence["is_high_volume"],
        ),
        observed_at=current_point.timestamp,
        available_at=current_point.timestamp,
        observed_values=observed_values,
        parameters={
            "trend_lookback": normalized_trend_lookback,
            "price_change_tolerance_percentage": price_tolerance,
            "obv_flow_tolerance_percentage": flow_tolerance,
            "volume_lookback": normalized_volume_lookback,
            "minimum_volume_points": minimum_volume,
            "high_volume_multiplier": volume_multiplier,
        },
    )


def _validate_obv_series(series: IndicatorSeries) -> PriceField:
    if series.indicator.upper() != "OBV":
        raise ValueError("OBV signal requires an OBV indicator series")
    if series.price_field is not None or len(series.input_fields) != 2:
        raise ValueError(
            "OBV signal requires one OHLC field followed by volume"
        )

    price_field, volume_field = series.input_fields
    if (
        price_field is PriceField.VOLUME
        or volume_field is not PriceField.VOLUME
    ):
        raise ValueError(
            "OBV signal requires one OHLC field followed by volume"
        )

    for previous, current in zip(series.points, series.points[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "OBV indicator points must have unique timestamps in "
                "ascending order"
            )
    return price_field


def _validate_identity(
    obv_series: IndicatorSeries,
    market_series: HistoricalCandleSeries,
) -> None:
    indicator_identity = (
        obv_series.exchange,
        obv_series.symbol_token,
        obv_series.symbol,
        obv_series.interval,
    )
    market_identity = (
        market_series.exchange,
        market_series.symbol_token,
        market_series.symbol,
        market_series.interval,
    )
    if indicator_identity != market_identity:
        raise ValueError(
            "OBV series and market series must describe the same "
            "instrument and timeframe"
        )


def _validate_candle_order(series: HistoricalCandleSeries) -> None:
    for previous, current in zip(series.candles, series.candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "OBV market candles must have unique timestamps in "
                "ascending order"
            )


def _validate_integer(name: str, value: int, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _validate_percentage(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if not 0 <= normalized <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return normalized


def _validate_high_volume_multiplier(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("OBV high-volume multiplier must be a number")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError("OBV high-volume multiplier must be finite")
    if normalized < 1:
        raise ValueError(
            "OBV high-volume multiplier must be at least 1"
        )
    return normalized


def _validate_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError("OBV signal evaluation time must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "OBV signal evaluation time must include timezone "
            "information"
        )


def _latest_available_index(
    series: IndicatorSeries,
    as_of: datetime | None,
) -> int:
    for index in range(len(series.points) - 1, -1, -1):
        point = series.points[index]
        if as_of is None or point.timestamp <= as_of:
            return index
    raise InsufficientDataError(
        "OBV signal requires an available indicator point"
    )


def _matching_candles(
    points: list[IndicatorPoint],
    market_series: HistoricalCandleSeries,
) -> tuple[list[Candle], int]:
    candle_indexes = {
        candle.timestamp: index
        for index, candle in enumerate(market_series.candles)
    }
    try:
        indexes = [candle_indexes[point.timestamp] for point in points]
    except KeyError as error:
        raise InsufficientDataError(
            "OBV signal requires candles matching every selected point"
        ) from error

    expected_indexes = list(range(indexes[0], indexes[-1] + 1))
    if indexes != expected_indexes:
        raise InsufficientDataError(
            "OBV signal requires consecutive candles for its trend "
            "window"
        )
    return (
        [market_series.candles[index] for index in indexes],
        indexes[-1],
    )


def _validate_obv_transitions(
    points: list[IndicatorPoint],
    candles: list[Candle],
    price_field: PriceField,
) -> None:
    for point in points:
        if not isfinite(point.value):
            raise ValueError("OBV signal values must be finite")

    for index in range(1, len(points)):
        previous_price = float(
            getattr(candles[index - 1], price_field.value)
        )
        current_price = float(
            getattr(candles[index], price_field.value)
        )
        actual_change = points[index].value - points[index - 1].value
        if current_price > previous_price:
            expected_change = float(candles[index].volume)
        elif current_price < previous_price:
            expected_change = -float(candles[index].volume)
        else:
            expected_change = 0.0

        if not isclose(
            actual_change,
            expected_change,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "OBV changes must match price direction and candle "
                "volume"
            )


def _obv_flow_percentage(obv_change: float, traded_volume: int) -> float:
    if traded_volume == 0:
        if obv_change != 0:
            raise ValueError(
                "OBV cannot change when selected traded volume is zero"
            )
        return 0.0
    return obv_change / traded_volume * 100


def _classify_change(value: float, tolerance: float) -> str:
    if abs(value) <= tolerance:
        return "neutral"
    if value > 0:
        return "bullish"
    return "bearish"


def _evaluate_current_volume(
    market_series: HistoricalCandleSeries,
    current_index: int,
    lookback: int,
    minimum_points: int,
    multiplier: float,
) -> dict:
    start = max(0, current_index - lookback)
    prior_candles = market_series.candles[start:current_index]
    evaluated = len(prior_candles) >= minimum_points
    result = {
        "evaluated": evaluated,
        "count": len(prior_candles),
        "average": 0.0,
        "ratio": None,
        "baseline_zero": False,
        "is_high_volume": False,
    }
    if not evaluated:
        return result

    average = sum(candle.volume for candle in prior_candles) / len(
        prior_candles
    )
    current_volume = market_series.candles[current_index].volume
    result["average"] = average
    if average == 0:
        result["baseline_zero"] = True
        result["is_high_volume"] = current_volume > 0
        return result

    ratio = current_volume / average
    result["ratio"] = ratio
    result["is_high_volume"] = ratio >= multiplier
    return result


def _classify_obv_condition(
    price_direction: str,
    obv_direction: str,
    is_high_volume: bool,
    trend_evaluated: bool,
) -> tuple[SignalDirection, SignalStrength, str]:
    if not trend_evaluated:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            "price-volume trend unavailable",
        )

    strength = (
        SignalStrength.STRONG
        if is_high_volume
        else SignalStrength.MODERATE
    )
    if price_direction == "bullish" and obv_direction == "bullish":
        return (
            SignalDirection.BULLISH,
            strength,
            "bullish price-volume confirmation",
        )
    if price_direction == "bearish" and obv_direction == "bearish":
        return (
            SignalDirection.BEARISH,
            strength,
            "bearish price-volume confirmation",
        )
    if price_direction == "bearish" and obv_direction == "bullish":
        return (
            SignalDirection.BULLISH,
            strength,
            "bullish OBV divergence",
        )
    if price_direction == "bullish" and obv_direction == "bearish":
        return (
            SignalDirection.BEARISH,
            strength,
            "bearish OBV divergence",
        )
    if price_direction == "neutral" and obv_direction == "bullish":
        return (
            SignalDirection.BULLISH,
            SignalStrength.MODERATE
            if is_high_volume
            else SignalStrength.WEAK,
            "OBV accumulation with flat price",
        )
    if price_direction == "neutral" and obv_direction == "bearish":
        return (
            SignalDirection.BEARISH,
            SignalStrength.MODERATE
            if is_high_volume
            else SignalStrength.WEAK,
            "OBV distribution with flat price",
        )
    if price_direction != "neutral":
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            f"{price_direction} price move unconfirmed by OBV",
        )
    return (
        SignalDirection.NEUTRAL,
        SignalStrength.WEAK,
        "neutral price-volume flow",
    )


def _build_explanation(
    price_field: str,
    price_change_percentage: float,
    obv_flow_percentage: float,
    condition: str,
    volume_evaluated: bool,
    is_high_volume: bool,
) -> str:
    if not volume_evaluated:
        volume_text = "Current-volume confirmation was not evaluated."
    elif is_high_volume:
        volume_text = "Current volume is high relative to prior volume."
    else:
        volume_text = "Current volume is not abnormally high."
    return (
        f"The {price_field} price changed {price_change_percentage:.6f}% "
        f"while normalized OBV flow was {obv_flow_percentage:.6f}%, "
        f"indicating {condition}. {volume_text}"
    )
