from datetime import datetime
from math import isfinite

from app.exceptions import InsufficientDataError
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
)
from app.models.technical import (
    IndicatorBundle,
    IndicatorComponent,
    IndicatorPoint,
    IndicatorSeries,
    PriceField,
)


MOVING_AVERAGE_INDICATORS = frozenset({"SMA", "EMA"})


def generate_moving_average_alignment_signal(
    fast_series: IndicatorSeries,
    slow_series: IndicatorSeries,
    *,
    equality_tolerance_percentage: float = 0.05,
    moderate_threshold_percentage: float = 0.5,
    strong_threshold_percentage: float = 1.5,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate deterministic fast/slow moving-average evidence."""
    fast_period = _validate_moving_average_series(
        fast_series,
        label="fast",
    )
    slow_period = _validate_moving_average_series(
        slow_series,
        label="slow",
    )
    _validate_series_compatibility(fast_series, slow_series)

    if fast_period >= slow_period:
        raise ValueError(
            "fast moving-average period must be below slow period"
        )

    equality_tolerance = _validate_non_negative_percentage(
        "moving-average equality tolerance",
        equality_tolerance_percentage,
    )
    moderate_threshold = _validate_non_negative_percentage(
        "moving-average moderate threshold",
        moderate_threshold_percentage,
    )
    strong_threshold = _validate_non_negative_percentage(
        "moving-average strong threshold",
        strong_threshold_percentage,
    )
    if moderate_threshold <= equality_tolerance:
        raise ValueError(
            "moving-average moderate threshold must exceed equality "
            "tolerance"
        )
    if strong_threshold <= moderate_threshold:
        raise ValueError(
            "moving-average strong threshold must exceed moderate "
            "threshold"
        )

    _validate_as_of(as_of)
    fast_point, slow_point = _latest_common_points(
        fast_series,
        slow_series,
        as_of,
    )
    if fast_point.value < 0 or slow_point.value < 0:
        raise ValueError("moving-average values cannot be negative")

    separation = _percentage_separation(
        fast_point.value,
        slow_point.value,
    )
    direction, relationship = _classify_direction(
        fast_point.value,
        slow_point.value,
        separation,
        equality_tolerance,
    )
    strength = _classify_strength(
        direction,
        separation,
        moderate_threshold,
        strong_threshold,
    )

    fast_indicator = fast_series.indicator.upper()
    slow_indicator = slow_series.indicator.upper()
    price_field = fast_series.price_field.value
    timestamp = fast_point.timestamp

    return TechnicalSignalEvidence(
        evidence_id=(
            f"ma_alignment.{fast_indicator.lower()}{fast_period}."
            f"{slow_indicator.lower()}{slow_period}.{price_field}"
        ),
        name=(
            f"{fast_indicator}({fast_period}) {relationship} "
            f"{slow_indicator}({slow_period})"
        ),
        category=SignalCategory.TREND,
        direction=direction,
        strength=strength,
        source="trend_signals.moving_average_alignment",
        explanation=(
            f"{fast_indicator}({fast_period}) is {relationship} "
            f"{slow_indicator}({slow_period}) by "
            f"{separation:.6f}%."
        ),
        observed_at=timestamp,
        available_at=timestamp,
        observed_values={
            "fast_indicator": fast_indicator,
            "fast_period": fast_period,
            "fast_value": fast_point.value,
            "slow_indicator": slow_indicator,
            "slow_period": slow_period,
            "slow_value": slow_point.value,
            "relationship": relationship,
            "separation_percentage": separation,
        },
        parameters={
            "price_field": price_field,
            "equality_tolerance_percentage": equality_tolerance,
            "moderate_threshold_percentage": moderate_threshold,
            "strong_threshold_percentage": strong_threshold,
        },
    )


def generate_adx_trend_signal(
    adx_bundle: IndicatorBundle,
    *,
    directional_tolerance: float = 1.0,
    trend_threshold: float = 20.0,
    strong_trend_threshold: float = 25.0,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate trend direction from DI and strength from ADX."""
    period, components = _validate_adx_bundle(adx_bundle)
    normalized_directional_tolerance = _validate_bounded_number(
        "ADX directional tolerance",
        directional_tolerance,
    )
    normalized_trend_threshold = _validate_bounded_number(
        "ADX trend threshold",
        trend_threshold,
    )
    normalized_strong_threshold = _validate_bounded_number(
        "ADX strong trend threshold",
        strong_trend_threshold,
    )
    if normalized_trend_threshold >= normalized_strong_threshold:
        raise ValueError(
            "ADX trend threshold must be below strong trend threshold"
        )

    _validate_adx_as_of(as_of)
    index = _latest_adx_index(components["adx"], as_of)
    adx_value = components["adx"].points[index].value
    plus_di = components["plus_di"].points[index].value
    minus_di = components["minus_di"].points[index].value
    _validate_adx_values(adx_value, plus_di, minus_di)

    direction, directional_condition = _classify_di_direction(
        plus_di,
        minus_di,
        normalized_directional_tolerance,
    )
    strength, strength_condition = _classify_adx_strength(
        adx_value,
        normalized_trend_threshold,
        normalized_strong_threshold,
    )
    condition = f"{strength_condition} {directional_condition}"
    timestamp = components["adx"].points[index].timestamp

    return TechnicalSignalEvidence(
        evidence_id=f"adx_trend.adx{period}.hlc",
        name=f"ADX({period}) {condition}",
        category=SignalCategory.TREND,
        direction=direction,
        strength=strength,
        source="trend_signals.adx_directional_strength",
        explanation=(
            f"ADX({period}) is {adx_value:.6f}, measuring "
            f"{strength_condition}; +DI is {plus_di:.6f} and -DI is "
            f"{minus_di:.6f}, indicating {directional_condition}."
        ),
        observed_at=timestamp,
        available_at=timestamp,
        observed_values={
            "adx": adx_value,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "di_spread": plus_di - minus_di,
            "directional_condition": directional_condition,
            "strength_condition": strength_condition,
        },
        parameters={
            "period": period,
            "input_fields": "high,low,close",
            "directional_tolerance": normalized_directional_tolerance,
            "trend_threshold": normalized_trend_threshold,
            "strong_trend_threshold": normalized_strong_threshold,
        },
    )


def _validate_moving_average_series(
    series: IndicatorSeries,
    *,
    label: str,
) -> int:
    if series.indicator.upper() not in MOVING_AVERAGE_INDICATORS:
        raise ValueError(
            f"{label} series must be an SMA or EMA indicator"
        )
    if series.price_field is None:
        raise ValueError(
            f"{label} moving-average series requires one price field"
        )

    period = series.parameters.get("period")
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError(
            f"{label} moving-average period must be an integer"
        )
    if period < 2:
        raise ValueError(
            f"{label} moving-average period must be at least 2"
        )

    return period


def _validate_series_compatibility(
    fast_series: IndicatorSeries,
    slow_series: IndicatorSeries,
) -> None:
    fast_identity = (
        fast_series.exchange,
        fast_series.symbol_token,
        fast_series.symbol,
        fast_series.interval,
        fast_series.price_field,
    )
    slow_identity = (
        slow_series.exchange,
        slow_series.symbol_token,
        slow_series.symbol,
        slow_series.interval,
        slow_series.price_field,
    )
    if fast_identity != slow_identity:
        raise ValueError(
            "moving-average series must describe the same instrument, "
            "timeframe, and price field"
        )


def _validate_non_negative_percentage(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if normalized < 0:
        raise ValueError(f"{name} cannot be negative")

    return normalized


def _validate_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError("moving-average evaluation time must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "moving-average evaluation time must include timezone "
            "information"
        )


def _latest_common_points(
    fast_series: IndicatorSeries,
    slow_series: IndicatorSeries,
    as_of: datetime | None,
) -> tuple[IndicatorPoint, IndicatorPoint]:
    fast_points = {
        point.timestamp: point
        for point in fast_series.points
        if as_of is None or point.timestamp <= as_of
    }
    slow_points = {
        point.timestamp: point
        for point in slow_series.points
        if as_of is None or point.timestamp <= as_of
    }
    common_timestamps = fast_points.keys() & slow_points.keys()
    if not common_timestamps:
        raise InsufficientDataError(
            "moving-average signal requires a common available point"
        )

    timestamp = max(common_timestamps)
    return fast_points[timestamp], slow_points[timestamp]


def _percentage_separation(
    fast_value: float,
    slow_value: float,
) -> float:
    if slow_value == 0:
        return 0.0 if fast_value == 0 else 100.0

    return abs(fast_value - slow_value) / slow_value * 100


def _classify_direction(
    fast_value: float,
    slow_value: float,
    separation: float,
    equality_tolerance: float,
) -> tuple[SignalDirection, str]:
    if separation <= equality_tolerance:
        return SignalDirection.NEUTRAL, "aligned with"
    if fast_value > slow_value:
        return SignalDirection.BULLISH, "above"
    return SignalDirection.BEARISH, "below"


def _classify_strength(
    direction: SignalDirection,
    separation: float,
    moderate_threshold: float,
    strong_threshold: float,
) -> SignalStrength:
    if direction is SignalDirection.NEUTRAL:
        return SignalStrength.WEAK
    if separation >= strong_threshold:
        return SignalStrength.STRONG
    if separation >= moderate_threshold:
        return SignalStrength.MODERATE
    return SignalStrength.WEAK


def _validate_adx_bundle(
    bundle: IndicatorBundle,
) -> tuple[int, dict[str, IndicatorComponent]]:
    if bundle.indicator.upper() != "ADX":
        raise ValueError("ADX signal requires an ADX indicator bundle")
    expected_inputs = (
        PriceField.HIGH,
        PriceField.LOW,
        PriceField.CLOSE,
    )
    if (
        bundle.price_field is not None
        or bundle.input_fields != expected_inputs
    ):
        raise ValueError("ADX signal requires high, low, and close inputs")

    period = bundle.parameters.get("period")
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError("ADX signal period must be an integer")
    if period < 2:
        raise ValueError("ADX signal period must be at least 2")

    components = {
        component.name.casefold(): component
        for component in bundle.components
    }
    required_names = {"adx", "plus_di", "minus_di"}
    if (
        len(components) != len(bundle.components)
        or set(components) != required_names
    ):
        raise ValueError(
            "ADX signal requires adx, plus_di, and minus_di components"
        )

    expected_timestamps = [
        point.timestamp
        for point in components["adx"].points
    ]
    for component in components.values():
        if [
            point.timestamp
            for point in component.points
        ] != expected_timestamps:
            raise ValueError(
                "ADX signal components must use identical timestamps"
            )

    return period, components


def _validate_bounded_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if not 0 <= normalized <= 100:
        raise ValueError(f"{name} must be between 0 and 100")

    return normalized


def _validate_adx_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError("ADX signal evaluation time must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "ADX signal evaluation time must include timezone information"
        )


def _latest_adx_index(
    adx_component: IndicatorComponent,
    as_of: datetime | None,
) -> int:
    for index in range(len(adx_component.points) - 1, -1, -1):
        point = adx_component.points[index]
        if as_of is None or point.timestamp <= as_of:
            return index

    raise InsufficientDataError(
        "ADX signal requires an available indicator point"
    )


def _validate_adx_values(
    adx_value: float,
    plus_di: float,
    minus_di: float,
) -> None:
    if any(
        value < 0 or value > 100
        for value in (adx_value, plus_di, minus_di)
    ):
        raise ValueError(
            "ADX, plus DI, and minus DI values must be between 0 and 100"
        )


def _classify_di_direction(
    plus_di: float,
    minus_di: float,
    tolerance: float,
) -> tuple[SignalDirection, str]:
    spread = plus_di - minus_di
    if abs(spread) <= tolerance:
        return SignalDirection.NEUTRAL, "balanced direction"
    if spread > 0:
        return SignalDirection.BULLISH, "bullish direction"
    return SignalDirection.BEARISH, "bearish direction"


def _classify_adx_strength(
    adx_value: float,
    trend_threshold: float,
    strong_trend_threshold: float,
) -> tuple[SignalStrength, str]:
    if adx_value >= strong_trend_threshold:
        return SignalStrength.STRONG, "strong trend strength"
    if adx_value >= trend_threshold:
        return SignalStrength.MODERATE, "emerging trend strength"
    return SignalStrength.WEAK, "weak trend strength"
