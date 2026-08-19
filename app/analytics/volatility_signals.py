from datetime import datetime
from math import isclose, isfinite

import numpy as np

from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
)
from app.models.technical import (
    IndicatorBundle,
    IndicatorComponent,
    IndicatorSeries,
    PriceField,
)


def generate_bollinger_band_signal(
    bollinger_bundle: IndicatorBundle,
    market_series: HistoricalCandleSeries,
    *,
    squeeze_lookback: int = 20,
    squeeze_percentile: float = 20.0,
    minimum_squeeze_points: int = 5,
    bandwidth_change_tolerance_percentage: float = 5.0,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate price-position and volatility-regime evidence."""
    period, deviations, components = _validate_bollinger_bundle(
        bollinger_bundle
    )
    _validate_market_identity(bollinger_bundle, market_series)
    _validate_candle_order(market_series)

    lookback = _validate_positive_integer(
        "Bollinger squeeze lookback",
        squeeze_lookback,
        minimum=2,
    )
    minimum_points = _validate_positive_integer(
        "Bollinger minimum squeeze points",
        minimum_squeeze_points,
        minimum=2,
    )
    if minimum_points > lookback:
        raise ValueError(
            "Bollinger minimum squeeze points cannot exceed squeeze "
            "lookback"
        )

    percentile = _validate_percentage(
        "Bollinger squeeze percentile",
        squeeze_percentile,
        allow_zero=False,
    )
    change_tolerance = _validate_percentage(
        "Bollinger bandwidth-change tolerance",
        bandwidth_change_tolerance_percentage,
        allow_zero=True,
    )
    _validate_as_of(as_of)

    middle_component = components["middle_band"]
    index = _latest_available_index(middle_component, as_of)
    timestamp = middle_component.points[index].timestamp
    candle = _candle_at(market_series, timestamp)
    price_field = bollinger_bundle.price_field
    price = float(getattr(candle, price_field.value))

    upper = components["upper_band"].points[index].value
    middle = middle_component.points[index].value
    lower = components["lower_band"].points[index].value
    _validate_band_values(upper, middle, lower)

    bandwidth = upper - lower
    bandwidth_percentage = _bandwidth_percentage(
        bandwidth,
        middle,
    )
    previous_bandwidth_percentage = None
    bandwidth_change_percentage = None
    volatility_regime = "unavailable"
    if index > 0:
        previous_bandwidth_percentage = _bandwidth_at(
            components,
            index - 1,
        )
        bandwidth_change_percentage = _percentage_change(
            previous_bandwidth_percentage,
            bandwidth_percentage,
        )
        volatility_regime = _classify_volatility_regime(
            bandwidth_change_percentage,
            change_tolerance,
        )

    available_bandwidths = [
        _bandwidth_at(components, point_index)
        for point_index in range(index + 1)
    ]
    window = available_bandwidths[-lookback:]
    squeeze_evaluated = len(window) >= minimum_points
    squeeze_threshold = None
    is_squeeze = False
    if squeeze_evaluated:
        squeeze_threshold = float(
            np.percentile(window, percentile, method="linear")
        )
        is_squeeze = bandwidth_percentage < squeeze_threshold

    band_position = _classify_band_position(
        price,
        upper,
        middle,
        lower,
    )
    direction, strength, condition = _classify_bollinger_condition(
        band_position,
        volatility_regime,
        is_squeeze,
    )
    upper_deviation, lower_deviation = deviations

    observed_values = {
        "price": price,
        "upper_band": upper,
        "middle_band": middle,
        "lower_band": lower,
        "bandwidth": bandwidth,
        "bandwidth_percentage": bandwidth_percentage,
        "band_position": band_position,
        "volatility_regime": volatility_regime,
        "squeeze_evaluated": squeeze_evaluated,
        "is_squeeze": is_squeeze,
        "squeeze_observation_count": len(window),
        "condition": condition,
    }
    if previous_bandwidth_percentage is not None:
        observed_values["previous_bandwidth_percentage"] = (
            previous_bandwidth_percentage
        )
        observed_values["bandwidth_change_percentage"] = (
            bandwidth_change_percentage
        )
    if squeeze_threshold is not None:
        observed_values["squeeze_threshold_percentage"] = (
            squeeze_threshold
        )

    return TechnicalSignalEvidence(
        evidence_id=(
            f"bollinger_position.bbands{period}.{price_field.value}"
        ),
        name=f"Bollinger Bands({period}) {condition}",
        category=SignalCategory.VOLATILITY,
        direction=direction,
        strength=strength,
        source="volatility_signals.bollinger_price_and_bandwidth",
        explanation=_build_explanation(
            period,
            price,
            price_field.value,
            upper,
            middle,
            lower,
            band_position,
            volatility_regime,
            squeeze_evaluated,
            is_squeeze,
        ),
        observed_at=timestamp,
        available_at=timestamp,
        observed_values=observed_values,
        parameters={
            "period": period,
            "price_field": price_field.value,
            "upper_deviation": upper_deviation,
            "lower_deviation": lower_deviation,
            "moving_average_type": "SMA",
            "squeeze_lookback": lookback,
            "squeeze_percentile": percentile,
            "minimum_squeeze_points": minimum_points,
            "bandwidth_change_tolerance_percentage": change_tolerance,
        },
    )


def generate_atr_volatility_signal(
    atr_series: IndicatorSeries,
    market_series: HistoricalCandleSeries,
    *,
    regime_lookback: int = 20,
    low_volatility_percentile: float = 25.0,
    high_volatility_percentile: float = 75.0,
    minimum_regime_points: int = 5,
    change_tolerance_percentage: float = 5.0,
    risk_atr_multiplier: float = 2.0,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate normalized ATR regime and risk-distance evidence."""
    period = _validate_atr_series(atr_series)
    _validate_atr_market_identity(atr_series, market_series)
    _validate_atr_candle_order(market_series)

    lookback = _validate_positive_integer(
        "ATR regime lookback",
        regime_lookback,
        minimum=2,
    )
    minimum_points = _validate_positive_integer(
        "ATR minimum regime points",
        minimum_regime_points,
        minimum=2,
    )
    if minimum_points > lookback:
        raise ValueError(
            "ATR minimum regime points cannot exceed regime lookback"
        )

    low_percentile = _validate_percentage(
        "ATR low-volatility percentile",
        low_volatility_percentile,
        allow_zero=True,
    )
    high_percentile = _validate_percentage(
        "ATR high-volatility percentile",
        high_volatility_percentile,
        allow_zero=True,
    )
    if low_percentile >= high_percentile:
        raise ValueError(
            "ATR low-volatility percentile must be below "
            "high-volatility percentile"
        )

    change_tolerance = _validate_percentage(
        "ATR change tolerance",
        change_tolerance_percentage,
        allow_zero=True,
    )
    risk_multiplier = _validate_positive_number(
        "ATR risk multiplier",
        risk_atr_multiplier,
    )
    _validate_atr_as_of(as_of)

    index = _latest_atr_index(atr_series, as_of)
    candle_lookup = {
        candle.timestamp: candle
        for candle in market_series.candles
    }
    window_start = max(0, index - lookback + 1)
    available_points = atr_series.points[window_start:index + 1]
    normalized_values = [
        _normalized_atr(point.value, candle_lookup, point.timestamp)
        for point in available_points
    ]
    atr_value = available_points[-1].value
    atr_percentage = normalized_values[-1]
    timestamp = available_points[-1].timestamp
    close = _close_at(candle_lookup, timestamp)

    previous_atr_percentage = None
    atr_change_percentage = None
    volatility_trend = "unavailable"
    if index > 0:
        previous_point = atr_series.points[index - 1]
        previous_atr_percentage = _normalized_atr(
            previous_point.value,
            candle_lookup,
            previous_point.timestamp,
        )
        atr_change_percentage = _percentage_change(
            previous_atr_percentage,
            atr_percentage,
        )
        volatility_trend = _classify_volatility_regime(
            atr_change_percentage,
            change_tolerance,
        )

    regime_evaluated = len(normalized_values) >= minimum_points
    low_threshold = None
    high_threshold = None
    volatility_regime = "unavailable"
    if regime_evaluated:
        low_threshold = float(
            np.percentile(
                normalized_values,
                low_percentile,
                method="linear",
            )
        )
        high_threshold = float(
            np.percentile(
                normalized_values,
                high_percentile,
                method="linear",
            )
        )
        volatility_regime = _classify_atr_regime(
            atr_percentage,
            low_threshold,
            high_threshold,
        )

    strength, condition = _classify_atr_condition(
        volatility_regime,
        volatility_trend,
    )
    reference_risk_distance = atr_value * risk_multiplier
    reference_risk_percentage = atr_percentage * risk_multiplier

    observed_values = {
        "atr": atr_value,
        "close": close,
        "atr_percentage": atr_percentage,
        "volatility_regime": volatility_regime,
        "volatility_trend": volatility_trend,
        "regime_evaluated": regime_evaluated,
        "regime_observation_count": len(normalized_values),
        "reference_risk_distance": reference_risk_distance,
        "reference_risk_percentage": reference_risk_percentage,
        "condition": condition,
    }
    if previous_atr_percentage is not None:
        observed_values["previous_atr_percentage"] = (
            previous_atr_percentage
        )
        observed_values["atr_change_percentage"] = (
            atr_change_percentage
        )
    if low_threshold is not None and high_threshold is not None:
        observed_values["low_regime_threshold_percentage"] = (
            low_threshold
        )
        observed_values["high_regime_threshold_percentage"] = (
            high_threshold
        )

    return TechnicalSignalEvidence(
        evidence_id=f"atr_volatility.atr{period}.hlc",
        name=f"ATR({period}) {condition}",
        category=SignalCategory.VOLATILITY,
        direction=SignalDirection.NEUTRAL,
        strength=strength,
        source="volatility_signals.atr_regime_and_risk_distance",
        explanation=_build_atr_explanation(
            period,
            atr_value,
            atr_percentage,
            volatility_regime,
            volatility_trend,
            reference_risk_distance,
            risk_multiplier,
        ),
        observed_at=timestamp,
        available_at=timestamp,
        observed_values=observed_values,
        parameters={
            "period": period,
            "input_fields": "high,low,close",
            "normalization_price_field": "close",
            "regime_lookback": lookback,
            "low_volatility_percentile": low_percentile,
            "high_volatility_percentile": high_percentile,
            "minimum_regime_points": minimum_points,
            "change_tolerance_percentage": change_tolerance,
            "risk_atr_multiplier": risk_multiplier,
            "risk_distance_is_reference_only": True,
        },
    )


def _validate_bollinger_bundle(
    bundle: IndicatorBundle,
) -> tuple[
    int,
    tuple[float, float],
    dict[str, IndicatorComponent],
]:
    if bundle.indicator.upper() != "BBANDS":
        raise ValueError(
            "Bollinger signal requires a BBANDS indicator bundle"
        )
    if (
        bundle.price_field is None
        or bundle.price_field is PriceField.VOLUME
        or bundle.input_fields
    ):
        raise ValueError(
            "Bollinger signal requires one OHLC price field"
        )

    period = bundle.parameters.get("period")
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError("Bollinger signal period must be an integer")
    if period < 2:
        raise ValueError("Bollinger signal period must be at least 2")

    upper_deviation = _validate_positive_number(
        "Bollinger upper deviation",
        bundle.parameters.get("upper_deviation"),
    )
    lower_deviation = _validate_positive_number(
        "Bollinger lower deviation",
        bundle.parameters.get("lower_deviation"),
    )
    moving_average_type = bundle.parameters.get("moving_average_type")
    if (
        not isinstance(moving_average_type, str)
        or moving_average_type.upper() != "SMA"
    ):
        raise ValueError(
            "Bollinger signal requires SMA-based band metadata"
        )

    components = {
        component.name.casefold(): component
        for component in bundle.components
    }
    required_names = {"upper_band", "middle_band", "lower_band"}
    if (
        len(components) != len(bundle.components)
        or set(components) != required_names
    ):
        raise ValueError(
            "Bollinger signal requires upper_band, middle_band, and "
            "lower_band components"
        )

    expected_timestamps = [
        point.timestamp
        for point in components["middle_band"].points
    ]
    for component in components.values():
        if [
            point.timestamp
            for point in component.points
        ] != expected_timestamps:
            raise ValueError(
                "Bollinger signal components must use identical "
                "timestamps"
            )

    return (
        period,
        (upper_deviation, lower_deviation),
        components,
    )


def _validate_market_identity(
    bundle: IndicatorBundle,
    series: HistoricalCandleSeries,
) -> None:
    bundle_identity = (
        bundle.exchange,
        bundle.symbol_token,
        bundle.symbol,
        bundle.interval,
    )
    series_identity = (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.interval,
    )
    if bundle_identity != series_identity:
        raise ValueError(
            "Bollinger bundle and market series must describe the same "
            "instrument and timeframe"
        )


def _validate_candle_order(series: HistoricalCandleSeries) -> None:
    for previous, current in zip(series.candles, series.candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "Bollinger market candles must have unique timestamps "
                "in ascending order"
            )


def _validate_positive_integer(
    name: str,
    value: int,
    *,
    minimum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _validate_percentage(
    name: str,
    value: float,
    *,
    allow_zero: bool,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if normalized < 0 or normalized > 100:
        raise ValueError(f"{name} must be between 0 and 100")
    if not allow_zero and normalized == 0:
        raise ValueError(f"{name} must be greater than zero")
    return normalized


def _validate_positive_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")

    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if normalized <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return normalized


def _validate_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError(
            "Bollinger signal evaluation time must be a datetime"
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "Bollinger signal evaluation time must include timezone "
            "information"
        )


def _latest_available_index(
    middle_band: IndicatorComponent,
    as_of: datetime | None,
) -> int:
    for index in range(len(middle_band.points) - 1, -1, -1):
        point = middle_band.points[index]
        if as_of is None or point.timestamp <= as_of:
            return index
    raise InsufficientDataError(
        "Bollinger signal requires an available band point"
    )


def _candle_at(series: HistoricalCandleSeries, timestamp: datetime):
    candles = {
        candle.timestamp: candle
        for candle in series.candles
    }
    try:
        return candles[timestamp]
    except KeyError as error:
        raise InsufficientDataError(
            "Bollinger signal requires a candle matching the band point"
        ) from error


def _validate_band_values(
    upper: float,
    middle: float,
    lower: float,
) -> None:
    if not all(isfinite(value) for value in (upper, middle, lower)):
        raise ValueError("Bollinger band values must be finite")
    if lower < 0:
        raise ValueError("Bollinger band values cannot be negative")
    if upper < middle or middle < lower:
        raise ValueError(
            "Bollinger bands must satisfy upper >= middle >= lower"
        )


def _bandwidth_percentage(width: float, middle: float) -> float:
    if middle <= 0:
        if width == 0:
            return 0.0
        raise ValueError(
            "Bollinger middle band must be positive for normalized "
            "bandwidth"
        )
    return width / middle * 100


def _bandwidth_at(
    components: dict[str, IndicatorComponent],
    index: int,
) -> float:
    upper = components["upper_band"].points[index].value
    middle = components["middle_band"].points[index].value
    lower = components["lower_band"].points[index].value
    _validate_band_values(upper, middle, lower)
    return _bandwidth_percentage(upper - lower, middle)


def _percentage_change(previous: float, current: float) -> float:
    if previous == 0:
        return 0.0 if current == 0 else 100.0
    return (current - previous) / previous * 100


def _classify_volatility_regime(
    change_percentage: float,
    tolerance: float,
) -> str:
    if change_percentage > tolerance:
        return "expanding"
    if change_percentage < -tolerance:
        return "contracting"
    return "stable"


def _classify_band_position(
    price: float,
    upper: float,
    middle: float,
    lower: float,
) -> str:
    if (
        isclose(upper, lower, rel_tol=1e-9, abs_tol=1e-12)
        and isclose(price, middle, rel_tol=1e-9, abs_tol=1e-12)
    ):
        return "at_middle_band"
    if isclose(price, upper, rel_tol=1e-9, abs_tol=1e-12):
        return "at_upper_band"
    if isclose(price, lower, rel_tol=1e-9, abs_tol=1e-12):
        return "at_lower_band"
    if price > upper:
        return "above_upper_band"
    if price < lower:
        return "below_lower_band"
    if price > middle:
        return "inside_upper_half"
    if price < middle:
        return "inside_lower_half"
    return "at_middle_band"


def _classify_bollinger_condition(
    band_position: str,
    volatility_regime: str,
    is_squeeze: bool,
) -> tuple[SignalDirection, SignalStrength, str]:
    if band_position == "above_upper_band":
        if volatility_regime == "expanding":
            return (
                SignalDirection.BULLISH,
                SignalStrength.STRONG,
                "upper-band breakout with volatility expansion",
            )
        return (
            SignalDirection.BULLISH,
            SignalStrength.MODERATE,
            "upper-band breakout",
        )
    if band_position == "below_lower_band":
        if volatility_regime == "expanding":
            return (
                SignalDirection.BEARISH,
                SignalStrength.STRONG,
                "lower-band breakdown with volatility expansion",
            )
        return (
            SignalDirection.BEARISH,
            SignalStrength.MODERATE,
            "lower-band breakdown",
        )
    if band_position == "at_upper_band":
        return (
            SignalDirection.BEARISH,
            SignalStrength.WEAK,
            "upper-band test without breakout",
        )
    if band_position == "at_lower_band":
        return (
            SignalDirection.BULLISH,
            SignalStrength.WEAK,
            "lower-band test without breakdown",
        )
    if is_squeeze:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.MODERATE,
            "volatility squeeze without breakout",
        )
    return (
        SignalDirection.NEUTRAL,
        SignalStrength.WEAK,
        "price contained within bands",
    )


def _build_explanation(
    period: int,
    price: float,
    price_field: str,
    upper: float,
    middle: float,
    lower: float,
    band_position: str,
    volatility_regime: str,
    squeeze_evaluated: bool,
    is_squeeze: bool,
) -> str:
    squeeze_text = "Squeeze status was not evaluated"
    if squeeze_evaluated:
        squeeze_text = (
            "A bandwidth squeeze is present"
            if is_squeeze
            else "No bandwidth squeeze is present"
        )
    return (
        f"Bollinger Bands({period}) place {price_field} price "
        f"{price:.6f} at {band_position}; upper, middle, and lower "
        f"bands are {upper:.6f}, {middle:.6f}, and {lower:.6f}. "
        f"Bandwidth is {volatility_regime}. {squeeze_text}."
    )


def _validate_atr_series(series: IndicatorSeries) -> int:
    if series.indicator.upper() != "ATR":
        raise ValueError("ATR signal requires an ATR indicator series")
    expected_inputs = (
        PriceField.HIGH,
        PriceField.LOW,
        PriceField.CLOSE,
    )
    if (
        series.price_field is not None
        or series.input_fields != expected_inputs
    ):
        raise ValueError("ATR signal requires high, low, and close inputs")

    period = series.parameters.get("period")
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError("ATR signal period must be an integer")
    if period < 2:
        raise ValueError("ATR signal period must be at least 2")

    for previous, current in zip(series.points, series.points[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "ATR indicator points must have unique timestamps in "
                "ascending order"
            )
    return period


def _validate_atr_market_identity(
    atr_series: IndicatorSeries,
    market_series: HistoricalCandleSeries,
) -> None:
    indicator_identity = (
        atr_series.exchange,
        atr_series.symbol_token,
        atr_series.symbol,
        atr_series.interval,
    )
    market_identity = (
        market_series.exchange,
        market_series.symbol_token,
        market_series.symbol,
        market_series.interval,
    )
    if indicator_identity != market_identity:
        raise ValueError(
            "ATR series and market series must describe the same "
            "instrument and timeframe"
        )


def _validate_atr_candle_order(
    series: HistoricalCandleSeries,
) -> None:
    for previous, current in zip(series.candles, series.candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "ATR market candles must have unique timestamps in "
                "ascending order"
            )


def _validate_atr_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError("ATR signal evaluation time must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "ATR signal evaluation time must include timezone "
            "information"
        )


def _latest_atr_index(
    atr_series: IndicatorSeries,
    as_of: datetime | None,
) -> int:
    for index in range(len(atr_series.points) - 1, -1, -1):
        point = atr_series.points[index]
        if as_of is None or point.timestamp <= as_of:
            return index
    raise InsufficientDataError(
        "ATR signal requires an available indicator point"
    )


def _close_at(
    candle_lookup: dict[datetime, Candle],
    timestamp: datetime,
) -> float:
    try:
        close = float(candle_lookup[timestamp].close)
    except KeyError as error:
        raise InsufficientDataError(
            "ATR signal requires a candle matching each indicator point"
        ) from error
    if close <= 0:
        raise ValueError(
            "ATR normalization requires a positive matching close"
        )
    return close


def _normalized_atr(
    atr_value: float,
    candle_lookup: dict[datetime, Candle],
    timestamp: datetime,
) -> float:
    if not isfinite(atr_value):
        raise ValueError("ATR signal values must be finite")
    if atr_value < 0:
        raise ValueError("ATR signal values cannot be negative")
    close = _close_at(candle_lookup, timestamp)
    return atr_value / close * 100


def _classify_atr_regime(
    atr_percentage: float,
    low_threshold: float,
    high_threshold: float,
) -> str:
    if isclose(
        low_threshold,
        high_threshold,
        rel_tol=1e-9,
        abs_tol=1e-12,
    ):
        return "normal"
    if atr_percentage <= low_threshold:
        return "low"
    if atr_percentage >= high_threshold:
        return "high"
    return "normal"


def _classify_atr_condition(
    volatility_regime: str,
    volatility_trend: str,
) -> tuple[SignalStrength, str]:
    if (
        volatility_regime == "high"
        and volatility_trend == "expanding"
    ):
        return SignalStrength.STRONG, "high and expanding volatility"
    if volatility_regime == "high":
        return SignalStrength.MODERATE, "high volatility"
    if volatility_regime == "low":
        return SignalStrength.MODERATE, "low volatility"
    if volatility_trend == "expanding":
        return SignalStrength.MODERATE, "volatility expanding"
    if volatility_trend == "contracting":
        return SignalStrength.MODERATE, "volatility contracting"
    if volatility_regime == "normal":
        return SignalStrength.WEAK, "normal volatility"
    return SignalStrength.WEAK, "volatility regime unavailable"


def _build_atr_explanation(
    period: int,
    atr_value: float,
    atr_percentage: float,
    volatility_regime: str,
    volatility_trend: str,
    reference_risk_distance: float,
    risk_multiplier: float,
) -> str:
    return (
        f"ATR({period}) is {atr_value:.6f}, or "
        f"{atr_percentage:.6f}% of the matching close. The historical "
        f"regime is {volatility_regime} and ATR is "
        f"{volatility_trend}. A {risk_multiplier:.6f}x ATR reference "
        f"distance is {reference_risk_distance:.6f}; it is a risk "
        "input, not a directional signal or stop recommendation."
    )
