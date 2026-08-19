from datetime import datetime
from math import isclose, isfinite

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


def generate_rsi_mean_reversion_signal(
    rsi_series: IndicatorSeries,
    *,
    extreme_oversold_threshold: float = 20.0,
    oversold_threshold: float = 30.0,
    overbought_threshold: float = 70.0,
    extreme_overbought_threshold: float = 80.0,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate deterministic RSI mean-reversion evidence."""
    period = _validate_rsi_series(rsi_series)
    thresholds = _validate_rsi_thresholds(
        extreme_oversold_threshold,
        oversold_threshold,
        overbought_threshold,
        extreme_overbought_threshold,
    )
    _validate_as_of(as_of)
    point = _latest_available_point(rsi_series, as_of)
    if not 0 <= point.value <= 100:
        raise ValueError("RSI signal value must be between 0 and 100")

    (
        extreme_oversold,
        oversold,
        overbought,
        extreme_overbought,
    ) = thresholds
    direction, strength, condition = _classify_rsi(
        point.value,
        extreme_oversold,
        oversold,
        overbought,
        extreme_overbought,
    )
    price_field = rsi_series.price_field.value

    return TechnicalSignalEvidence(
        evidence_id=f"rsi_condition.rsi{period}.{price_field}",
        name=f"RSI({period}) {condition}",
        category=SignalCategory.MOMENTUM,
        direction=direction,
        strength=strength,
        source="momentum_signals.rsi_mean_reversion",
        explanation=_build_explanation(
            period,
            point.value,
            condition,
            direction,
        ),
        observed_at=point.timestamp,
        available_at=point.timestamp,
        observed_values={
            "rsi": point.value,
            "condition": condition,
        },
        parameters={
            "period": period,
            "price_field": price_field,
            "extreme_oversold_threshold": extreme_oversold,
            "oversold_threshold": oversold,
            "overbought_threshold": overbought,
            "extreme_overbought_threshold": extreme_overbought,
            "interpretation": "mean_reversion",
        },
    )


def generate_macd_momentum_signal(
    macd_bundle: IndicatorBundle,
    *,
    histogram_zero_tolerance: float = 1e-12,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate MACD alignment and histogram-momentum evidence."""
    periods, components = _validate_macd_bundle(macd_bundle)
    tolerance = _validate_histogram_tolerance(
        histogram_zero_tolerance
    )
    _validate_macd_as_of(as_of)
    index = _latest_macd_index(components["macd"], as_of)

    current_values = _macd_values_at(components, index)
    _validate_histogram_consistency(*current_values)
    previous_values = None
    if index > 0:
        previous_values = _macd_values_at(components, index - 1)
        _validate_histogram_consistency(*previous_values)

    macd_value, signal_value, histogram = current_values
    previous_histogram = (
        previous_values[2]
        if previous_values is not None
        else None
    )
    direction = _classify_histogram_direction(histogram, tolerance)
    strength, condition = _classify_macd_condition(
        direction,
        histogram,
        previous_histogram,
        tolerance,
    )
    fast_period, slow_period, signal_period = periods
    price_field = macd_bundle.price_field.value
    timestamp = components["macd"].points[index].timestamp
    observed_values = {
        "macd": macd_value,
        "signal": signal_value,
        "histogram": histogram,
        "condition": condition,
    }
    if previous_histogram is not None:
        observed_values.update(
            {
                "previous_histogram": previous_histogram,
                "histogram_change": histogram - previous_histogram,
            }
        )

    return TechnicalSignalEvidence(
        evidence_id=(
            f"macd_momentum.macd{fast_period}_{slow_period}_"
            f"{signal_period}.{price_field}"
        ),
        name=(
            f"MACD({fast_period},{slow_period},{signal_period}) "
            f"{condition}"
        ),
        category=SignalCategory.MOMENTUM,
        direction=direction,
        strength=strength,
        source="momentum_signals.macd_momentum",
        explanation=_build_macd_explanation(
            fast_period,
            slow_period,
            signal_period,
            macd_value,
            signal_value,
            histogram,
            condition,
        ),
        observed_at=timestamp,
        available_at=timestamp,
        observed_values=observed_values,
        parameters={
            "fast_period": fast_period,
            "slow_period": slow_period,
            "signal_period": signal_period,
            "price_field": price_field,
            "histogram_zero_tolerance": tolerance,
        },
    )


def generate_stochastic_momentum_signal(
    stochastic_bundle: IndicatorBundle,
    *,
    oversold_threshold: float = 20.0,
    overbought_threshold: float = 80.0,
    crossover_tolerance: float = 0.0,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate zone and confirmed %K/%D crossover evidence."""
    periods, components = _validate_stochastic_bundle(
        stochastic_bundle
    )
    oversold = _validate_stochastic_threshold(
        "oversold",
        oversold_threshold,
    )
    overbought = _validate_stochastic_threshold(
        "overbought",
        overbought_threshold,
    )
    if oversold >= overbought:
        raise ValueError(
            "Stochastic oversold threshold must be below overbought "
            "threshold"
        )

    tolerance = _validate_stochastic_tolerance(
        crossover_tolerance
    )
    _validate_stochastic_as_of(as_of)
    index = _latest_stochastic_index(components["percent_k"], as_of)

    percent_k = components["percent_k"].points[index].value
    percent_d = components["percent_d"].points[index].value
    _validate_stochastic_values(percent_k, percent_d)

    previous_values = None
    if index > 0:
        previous_values = (
            components["percent_k"].points[index - 1].value,
            components["percent_d"].points[index - 1].value,
        )
        _validate_stochastic_values(*previous_values)

    zone = _classify_stochastic_zone(
        percent_k,
        percent_d,
        oversold,
        overbought,
    )
    crossover = _classify_stochastic_crossover(
        percent_k,
        percent_d,
        previous_values,
        tolerance,
    )
    direction, strength, condition = _classify_stochastic_condition(
        zone,
        crossover,
        previous_values,
        oversold,
        overbought,
    )

    fast_k_period, slow_k_period, slow_d_period = periods
    timestamp = components["percent_k"].points[index].timestamp
    observed_values = {
        "percent_k": percent_k,
        "percent_d": percent_d,
        "k_minus_d": percent_k - percent_d,
        "zone": zone,
        "crossover": crossover,
        "crossover_confirmed": crossover != "none",
        "condition": condition,
    }
    if previous_values is not None:
        previous_k, previous_d = previous_values
        observed_values.update(
            {
                "previous_percent_k": previous_k,
                "previous_percent_d": previous_d,
                "previous_k_minus_d": previous_k - previous_d,
            }
        )

    return TechnicalSignalEvidence(
        evidence_id=(
            f"stochastic_momentum.stoch{fast_k_period}_"
            f"{slow_k_period}_{slow_d_period}.hlc"
        ),
        name=(
            "Stochastic"
            f"({fast_k_period},{slow_k_period},{slow_d_period}) "
            f"{condition}"
        ),
        category=SignalCategory.MOMENTUM,
        direction=direction,
        strength=strength,
        source="momentum_signals.stochastic_zone_crossover",
        explanation=_build_stochastic_explanation(
            fast_k_period,
            slow_k_period,
            slow_d_period,
            percent_k,
            percent_d,
            zone,
            crossover,
            direction,
        ),
        observed_at=timestamp,
        available_at=timestamp,
        observed_values=observed_values,
        parameters={
            "fast_k_period": fast_k_period,
            "slow_k_period": slow_k_period,
            "slow_d_period": slow_d_period,
            "input_fields": "high,low,close",
            "moving_average_type": "SMA",
            "oversold_threshold": oversold,
            "overbought_threshold": overbought,
            "crossover_tolerance": tolerance,
            "interpretation": "zone_and_confirmed_crossover",
        },
    )


def _validate_rsi_series(series: IndicatorSeries) -> int:
    if series.indicator.upper() != "RSI":
        raise ValueError("RSI signal requires an RSI indicator series")
    if series.price_field is None:
        raise ValueError("RSI signal requires one price field")

    period = series.parameters.get("period")
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError("RSI signal period must be an integer")
    if period < 2:
        raise ValueError("RSI signal period must be at least 2")

    return period


def _validate_rsi_thresholds(
    extreme_oversold_threshold: float,
    oversold_threshold: float,
    overbought_threshold: float,
    extreme_overbought_threshold: float,
) -> tuple[float, float, float, float]:
    thresholds = tuple(
        _validate_bounded_threshold(name, value)
        for name, value in (
            ("extreme oversold", extreme_oversold_threshold),
            ("oversold", oversold_threshold),
            ("overbought", overbought_threshold),
            ("extreme overbought", extreme_overbought_threshold),
        )
    )
    if not all(
        current < following
        for current, following in zip(thresholds, thresholds[1:])
    ):
        raise ValueError(
            "RSI thresholds must satisfy extreme oversold < oversold "
            "< overbought < extreme overbought"
        )

    return thresholds


def _validate_bounded_threshold(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"RSI {name} threshold must be a number")

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"RSI {name} threshold must be finite")
    if not 0 <= normalized <= 100:
        raise ValueError(
            f"RSI {name} threshold must be between 0 and 100"
        )

    return normalized


def _validate_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError("RSI signal evaluation time must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "RSI signal evaluation time must include timezone information"
        )


def _latest_available_point(
    series: IndicatorSeries,
    as_of: datetime | None,
) -> IndicatorPoint:
    for point in reversed(series.points):
        if as_of is None or point.timestamp <= as_of:
            return point

    raise InsufficientDataError(
        "RSI signal requires an available indicator point"
    )


def _classify_rsi(
    value: float,
    extreme_oversold: float,
    oversold: float,
    overbought: float,
    extreme_overbought: float,
) -> tuple[SignalDirection, SignalStrength, str]:
    if value <= extreme_oversold:
        return (
            SignalDirection.BULLISH,
            SignalStrength.STRONG,
            "extremely oversold",
        )
    if value <= oversold:
        return (
            SignalDirection.BULLISH,
            SignalStrength.MODERATE,
            "oversold",
        )
    if value >= extreme_overbought:
        return (
            SignalDirection.BEARISH,
            SignalStrength.STRONG,
            "extremely overbought",
        )
    if value >= overbought:
        return (
            SignalDirection.BEARISH,
            SignalStrength.MODERATE,
            "overbought",
        )
    return SignalDirection.NEUTRAL, SignalStrength.WEAK, "neutral"


def _build_explanation(
    period: int,
    value: float,
    condition: str,
    direction: SignalDirection,
) -> str:
    if direction is SignalDirection.NEUTRAL:
        return (
            f"RSI({period}) is {value:.6f}, inside the configured "
            "neutral range."
        )

    return (
        f"RSI({period}) is {value:.6f} and {condition}; this is "
        f"{direction.value} mean-reversion evidence, not confirmation "
        "of a reversal."
    )


def _validate_macd_bundle(
    bundle: IndicatorBundle,
) -> tuple[
    tuple[int, int, int],
    dict[str, IndicatorComponent],
]:
    if bundle.indicator.upper() != "MACD":
        raise ValueError("MACD signal requires a MACD indicator bundle")
    if bundle.price_field is None:
        raise ValueError("MACD signal requires one price field")

    period_names = ("fast_period", "slow_period", "signal_period")
    periods: list[int] = []
    for name in period_names:
        period = bundle.parameters.get(name)
        if isinstance(period, bool) or not isinstance(period, int):
            raise ValueError(
                f"MACD signal {name.replace('_', ' ')} must be an integer"
            )
        if period < 2:
            raise ValueError(
                f"MACD signal {name.replace('_', ' ')} must be at least 2"
            )
        periods.append(period)

    fast_period, slow_period, signal_period = periods
    if fast_period >= slow_period:
        raise ValueError(
            "MACD signal fast period must be below slow period"
        )

    components = {
        component.name.casefold(): component
        for component in bundle.components
    }
    required_names = {"macd", "signal", "histogram"}
    if (
        len(components) != len(bundle.components)
        or set(components) != required_names
    ):
        raise ValueError(
            "MACD signal requires macd, signal, and histogram components"
        )

    expected_timestamps = [
        point.timestamp
        for point in components["macd"].points
    ]
    for component in components.values():
        if [
            point.timestamp
            for point in component.points
        ] != expected_timestamps:
            raise ValueError(
                "MACD signal components must use identical timestamps"
            )

    return (
        (fast_period, slow_period, signal_period),
        components,
    )


def _validate_histogram_tolerance(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("MACD histogram tolerance must be a number")

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError("MACD histogram tolerance must be finite")
    if normalized < 0:
        raise ValueError("MACD histogram tolerance cannot be negative")

    return normalized


def _validate_macd_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError("MACD signal evaluation time must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "MACD signal evaluation time must include timezone information"
        )


def _latest_macd_index(
    macd_component: IndicatorComponent,
    as_of: datetime | None,
) -> int:
    for index in range(len(macd_component.points) - 1, -1, -1):
        point = macd_component.points[index]
        if as_of is None or point.timestamp <= as_of:
            return index

    raise InsufficientDataError(
        "MACD signal requires an available indicator point"
    )


def _macd_values_at(
    components: dict[str, IndicatorComponent],
    index: int,
) -> tuple[float, float, float]:
    return (
        components["macd"].points[index].value,
        components["signal"].points[index].value,
        components["histogram"].points[index].value,
    )


def _validate_histogram_consistency(
    macd_value: float,
    signal_value: float,
    histogram: float,
) -> None:
    if not isclose(
        histogram,
        macd_value - signal_value,
        rel_tol=1e-9,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "MACD histogram must equal the MACD line minus signal line"
        )


def _classify_histogram_direction(
    histogram: float,
    tolerance: float,
) -> SignalDirection:
    if abs(histogram) <= tolerance:
        return SignalDirection.NEUTRAL
    if histogram > 0:
        return SignalDirection.BULLISH
    return SignalDirection.BEARISH


def _classify_macd_condition(
    direction: SignalDirection,
    histogram: float,
    previous_histogram: float | None,
    tolerance: float,
) -> tuple[SignalStrength, str]:
    if direction is SignalDirection.NEUTRAL:
        return SignalStrength.WEAK, "neutral alignment"
    if previous_histogram is None:
        return SignalStrength.MODERATE, f"{direction.value} alignment"

    previous_direction = _classify_histogram_direction(
        previous_histogram,
        tolerance,
    )
    if previous_direction is not direction:
        return SignalStrength.STRONG, f"{direction.value} crossover"

    change = histogram - previous_histogram
    if direction is SignalDirection.BULLISH:
        if change > tolerance:
            return (
                SignalStrength.STRONG,
                "bullish momentum expanding",
            )
        if change < -tolerance:
            return (
                SignalStrength.WEAK,
                "bullish momentum contracting",
            )
        return SignalStrength.MODERATE, "bullish momentum stable"

    if change < -tolerance:
        return SignalStrength.STRONG, "bearish momentum expanding"
    if change > tolerance:
        return SignalStrength.WEAK, "bearish momentum contracting"
    return SignalStrength.MODERATE, "bearish momentum stable"


def _build_macd_explanation(
    fast_period: int,
    slow_period: int,
    signal_period: int,
    macd_value: float,
    signal_value: float,
    histogram: float,
    condition: str,
) -> str:
    return (
        f"MACD({fast_period},{slow_period},{signal_period}) is "
        f"{macd_value:.6f}, its signal line is {signal_value:.6f}, "
        f"and its histogram is {histogram:.6f}; this indicates "
        f"{condition}."
    )


def _validate_stochastic_bundle(
    bundle: IndicatorBundle,
) -> tuple[
    tuple[int, int, int],
    dict[str, IndicatorComponent],
]:
    if bundle.indicator.upper() != "STOCH":
        raise ValueError(
            "Stochastic signal requires a STOCH indicator bundle"
        )
    expected_inputs = (
        PriceField.HIGH,
        PriceField.LOW,
        PriceField.CLOSE,
    )
    if (
        bundle.price_field is not None
        or bundle.input_fields != expected_inputs
    ):
        raise ValueError(
            "Stochastic signal requires high, low, and close inputs"
        )

    period_names = (
        "fast_k_period",
        "slow_k_period",
        "slow_d_period",
    )
    periods: list[int] = []
    for name in period_names:
        period = bundle.parameters.get(name)
        if isinstance(period, bool) or not isinstance(period, int):
            raise ValueError(
                "Stochastic signal "
                f"{name.replace('_', ' ')} must be an integer"
            )
        if period < 2:
            raise ValueError(
                "Stochastic signal "
                f"{name.replace('_', ' ')} must be at least 2"
            )
        periods.append(period)

    moving_average_type = bundle.parameters.get("moving_average_type")
    if (
        not isinstance(moving_average_type, str)
        or moving_average_type.upper() != "SMA"
    ):
        raise ValueError(
            "Stochastic signal requires SMA-smoothed components"
        )

    components = {
        component.name.casefold(): component
        for component in bundle.components
    }
    required_names = {"percent_k", "percent_d"}
    if (
        len(components) != len(bundle.components)
        or set(components) != required_names
    ):
        raise ValueError(
            "Stochastic signal requires percent_k and percent_d "
            "components"
        )

    expected_timestamps = [
        point.timestamp
        for point in components["percent_k"].points
    ]
    for component in components.values():
        if [
            point.timestamp
            for point in component.points
        ] != expected_timestamps:
            raise ValueError(
                "Stochastic signal components must use identical "
                "timestamps"
            )

    return (
        (periods[0], periods[1], periods[2]),
        components,
    )


def _validate_stochastic_threshold(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"Stochastic {name} threshold must be a number"
        )

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(
            f"Stochastic {name} threshold must be finite"
        )
    if not 0 <= normalized <= 100:
        raise ValueError(
            f"Stochastic {name} threshold must be between 0 and 100"
        )

    return normalized


def _validate_stochastic_tolerance(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "Stochastic crossover tolerance must be a number"
        )

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(
            "Stochastic crossover tolerance must be finite"
        )
    if not 0 <= normalized <= 100:
        raise ValueError(
            "Stochastic crossover tolerance must be between 0 and 100"
        )

    return normalized


def _validate_stochastic_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError(
            "Stochastic signal evaluation time must be a datetime"
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "Stochastic signal evaluation time must include timezone "
            "information"
        )


def _latest_stochastic_index(
    percent_k: IndicatorComponent,
    as_of: datetime | None,
) -> int:
    for index in range(len(percent_k.points) - 1, -1, -1):
        point = percent_k.points[index]
        if as_of is None or point.timestamp <= as_of:
            return index

    raise InsufficientDataError(
        "Stochastic signal requires an available indicator point"
    )


def _validate_stochastic_values(
    percent_k: float,
    percent_d: float,
) -> None:
    if any(
        value < 0 or value > 100
        for value in (percent_k, percent_d)
    ):
        raise ValueError(
            "Stochastic percent K and percent D values must be "
            "between 0 and 100"
        )


def _classify_stochastic_zone(
    percent_k: float,
    percent_d: float,
    oversold: float,
    overbought: float,
) -> str:
    if max(percent_k, percent_d) <= oversold:
        return "oversold"
    if min(percent_k, percent_d) >= overbought:
        return "overbought"
    return "neutral"


def _classify_stochastic_crossover(
    percent_k: float,
    percent_d: float,
    previous_values: tuple[float, float] | None,
    tolerance: float,
) -> str:
    if previous_values is None:
        return "none"

    previous_k, previous_d = previous_values
    previous_spread = previous_k - previous_d
    current_spread = percent_k - percent_d
    if previous_spread <= tolerance and current_spread > tolerance:
        return "bullish"
    if previous_spread >= -tolerance and current_spread < -tolerance:
        return "bearish"
    return "none"


def _classify_stochastic_condition(
    zone: str,
    crossover: str,
    previous_values: tuple[float, float] | None,
    oversold: float,
    overbought: float,
) -> tuple[SignalDirection, SignalStrength, str]:
    if crossover == "bullish":
        originated_oversold = (
            previous_values is not None
            and max(previous_values) <= oversold
        )
        if originated_oversold:
            return (
                SignalDirection.BULLISH,
                SignalStrength.STRONG,
                "bullish crossover from oversold",
            )
        return (
            SignalDirection.BULLISH,
            SignalStrength.MODERATE,
            "bullish crossover",
        )

    if crossover == "bearish":
        originated_overbought = (
            previous_values is not None
            and min(previous_values) >= overbought
        )
        if originated_overbought:
            return (
                SignalDirection.BEARISH,
                SignalStrength.STRONG,
                "bearish crossover from overbought",
            )
        return (
            SignalDirection.BEARISH,
            SignalStrength.MODERATE,
            "bearish crossover",
        )

    if zone == "oversold":
        return (
            SignalDirection.BULLISH,
            SignalStrength.WEAK,
            "oversold without bullish crossover",
        )
    if zone == "overbought":
        return (
            SignalDirection.BEARISH,
            SignalStrength.WEAK,
            "overbought without bearish crossover",
        )
    return (
        SignalDirection.NEUTRAL,
        SignalStrength.WEAK,
        "neutral without crossover",
    )


def _build_stochastic_explanation(
    fast_k_period: int,
    slow_k_period: int,
    slow_d_period: int,
    percent_k: float,
    percent_d: float,
    zone: str,
    crossover: str,
    direction: SignalDirection,
) -> str:
    prefix = (
        "Stochastic"
        f"({fast_k_period},{slow_k_period},{slow_d_period}) has "
        f"%K at {percent_k:.6f} and %D at {percent_d:.6f} in the "
        f"{zone} zone."
    )
    if crossover != "none":
        return (
            f"{prefix} The current completed point confirms a "
            f"{crossover} %K/%D crossover."
        )
    if direction is SignalDirection.NEUTRAL:
        return f"{prefix} No directional crossover is confirmed."
    return (
        f"{prefix} This is {direction.value} mean-reversion context, "
        "but no directional crossover is confirmed."
    )
