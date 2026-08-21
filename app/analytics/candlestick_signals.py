from datetime import datetime

from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
)
from app.models.technical import IndicatorBundle, IndicatorComponent


# Interpretation metadata only: (direction, reliability, candle span).
# `direction=None` means the pattern covers both a bullish and a bearish
# shape and TA-Lib's own signed output tells them apart; `NEUTRAL` means
# the shape carries no inherent direction (pure indecision context).
_PATTERN_METADATA: dict[
    str,
    tuple[SignalDirection | None, SignalStrength, int],
] = {
    "two_crows": (SignalDirection.BEARISH, SignalStrength.STRONG, 3),
    "three_black_crows": (
        SignalDirection.BEARISH,
        SignalStrength.STRONG,
        3,
    ),
    "three_inside": (None, SignalStrength.MODERATE, 3),
    "three_line_strike": (None, SignalStrength.STRONG, 4),
    "three_outside": (None, SignalStrength.MODERATE, 3),
    "three_stars_in_south": (
        SignalDirection.BULLISH,
        SignalStrength.STRONG,
        3,
    ),
    "three_white_soldiers": (
        SignalDirection.BULLISH,
        SignalStrength.STRONG,
        3,
    ),
    "abandoned_baby": (None, SignalStrength.STRONG, 3),
    "advance_block": (
        SignalDirection.BEARISH,
        SignalStrength.MODERATE,
        3,
    ),
    "belt_hold": (None, SignalStrength.WEAK, 1),
    "breakaway": (None, SignalStrength.STRONG, 5),
    "closing_marubozu": (None, SignalStrength.WEAK, 1),
    "concealing_baby_swallow": (
        SignalDirection.BULLISH,
        SignalStrength.STRONG,
        4,
    ),
    "counterattack": (None, SignalStrength.MODERATE, 2),
    "dark_cloud_cover": (
        SignalDirection.BEARISH,
        SignalStrength.MODERATE,
        2,
    ),
    "doji": (SignalDirection.NEUTRAL, SignalStrength.WEAK, 1),
    "doji_star": (None, SignalStrength.WEAK, 2),
    "dragonfly_doji": (
        SignalDirection.BULLISH,
        SignalStrength.WEAK,
        1,
    ),
    "engulfing": (None, SignalStrength.MODERATE, 2),
    "evening_doji_star": (
        SignalDirection.BEARISH,
        SignalStrength.STRONG,
        3,
    ),
    "evening_star": (
        SignalDirection.BEARISH,
        SignalStrength.STRONG,
        3,
    ),
    "gap_side_by_side_white": (None, SignalStrength.MODERATE, 3),
    "gravestone_doji": (
        SignalDirection.BEARISH,
        SignalStrength.WEAK,
        1,
    ),
    "hammer": (SignalDirection.BULLISH, SignalStrength.WEAK, 1),
    "hanging_man": (SignalDirection.BEARISH, SignalStrength.WEAK, 1),
    "harami": (None, SignalStrength.MODERATE, 2),
    "harami_cross": (None, SignalStrength.MODERATE, 2),
    "high_wave": (SignalDirection.NEUTRAL, SignalStrength.WEAK, 1),
    "hikkake": (None, SignalStrength.WEAK, 3),
    "hikkake_modified": (None, SignalStrength.WEAK, 3),
    "homing_pigeon": (
        SignalDirection.BULLISH,
        SignalStrength.MODERATE,
        2,
    ),
    "identical_three_crows": (
        SignalDirection.BEARISH,
        SignalStrength.STRONG,
        3,
    ),
    "in_neck": (SignalDirection.BEARISH, SignalStrength.MODERATE, 2),
    "inverted_hammer": (
        SignalDirection.BULLISH,
        SignalStrength.WEAK,
        1,
    ),
    "kicking": (None, SignalStrength.MODERATE, 2),
    "kicking_by_length": (None, SignalStrength.MODERATE, 2),
    "ladder_bottom": (
        SignalDirection.BULLISH,
        SignalStrength.MODERATE,
        5,
    ),
    "long_legged_doji": (
        SignalDirection.NEUTRAL,
        SignalStrength.WEAK,
        1,
    ),
    "long_line": (None, SignalStrength.WEAK, 1),
    "marubozu": (None, SignalStrength.WEAK, 1),
    "matching_low": (
        SignalDirection.BULLISH,
        SignalStrength.MODERATE,
        2,
    ),
    "mat_hold": (SignalDirection.BULLISH, SignalStrength.MODERATE, 5),
    "morning_doji_star": (
        SignalDirection.BULLISH,
        SignalStrength.STRONG,
        3,
    ),
    "morning_star": (
        SignalDirection.BULLISH,
        SignalStrength.STRONG,
        3,
    ),
    "on_neck": (SignalDirection.BEARISH, SignalStrength.MODERATE, 2),
    "piercing": (SignalDirection.BULLISH, SignalStrength.MODERATE, 2),
    "rickshaw_man": (SignalDirection.NEUTRAL, SignalStrength.WEAK, 1),
    "rise_fall_three_methods": (None, SignalStrength.MODERATE, 5),
    "separating_lines": (None, SignalStrength.MODERATE, 2),
    "shooting_star": (
        SignalDirection.BEARISH,
        SignalStrength.WEAK,
        1,
    ),
    "short_line": (SignalDirection.NEUTRAL, SignalStrength.WEAK, 1),
    "spinning_top": (SignalDirection.NEUTRAL, SignalStrength.WEAK, 1),
    "stalled_pattern": (
        SignalDirection.BEARISH,
        SignalStrength.MODERATE,
        3,
    ),
    "stick_sandwich": (
        SignalDirection.BULLISH,
        SignalStrength.MODERATE,
        3,
    ),
    "takuri": (SignalDirection.BULLISH, SignalStrength.WEAK, 1),
    "tasuki_gap": (None, SignalStrength.MODERATE, 3),
    "thrusting": (
        SignalDirection.BEARISH,
        SignalStrength.MODERATE,
        2,
    ),
    "tristar": (None, SignalStrength.MODERATE, 3),
    "unique_three_river": (
        SignalDirection.BULLISH,
        SignalStrength.STRONG,
        3,
    ),
    "upside_gap_two_crows": (
        SignalDirection.BEARISH,
        SignalStrength.MODERATE,
        3,
    ),
    "gap_three_methods": (None, SignalStrength.MODERATE, 3),
}

_STRENGTH_ORDER = (
    SignalStrength.WEAK,
    SignalStrength.MODERATE,
    SignalStrength.STRONG,
)


def generate_candlestick_pattern_signal(
    candlestick_bundle: IndicatorBundle,
    *,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Aggregate TA-Lib candlestick pattern hits into one evidence item."""
    components = _validate_candlestick_bundle(candlestick_bundle)
    _validate_as_of(as_of)
    index = _latest_index(components, as_of)
    timestamp = components[0].points[index].timestamp

    bullish: list[tuple[str, SignalStrength, int]] = []
    bearish: list[tuple[str, SignalStrength, int]] = []
    neutral: list[str] = []

    for component in components:
        value = component.points[index].value
        if value == 0:
            continue

        direction, reliability, span = _PATTERN_METADATA[component.name]
        if direction is SignalDirection.NEUTRAL:
            neutral.append(component.name)
        elif direction is SignalDirection.BULLISH:
            bullish.append((component.name, reliability, span))
        elif direction is SignalDirection.BEARISH:
            bearish.append((component.name, reliability, span))
        elif value > 0:
            bullish.append((component.name, reliability, span))
        else:
            bearish.append((component.name, reliability, span))

    direction, strength, condition = _classify_confluence(
        bullish,
        bearish,
    )

    return TechnicalSignalEvidence(
        evidence_id="candlestick_pattern.talib_v1",
        name=f"Candlestick pattern {condition}",
        category=SignalCategory.CANDLESTICK,
        direction=direction,
        strength=strength,
        source="candlestick_signals.candlestick_pattern",
        explanation=_build_explanation(
            condition,
            direction,
            bullish,
            bearish,
            neutral,
        ),
        observed_at=timestamp,
        available_at=timestamp,
        observed_values={
            "bullish_patterns": _name_list(bullish),
            "bearish_patterns": _name_list(bearish),
            "neutral_patterns": ",".join(neutral) if neutral else "none",
            "condition": condition,
        },
        parameters={
            "penetration": candlestick_bundle.parameters["penetration"],
            "pattern_count": len(components),
        },
    )


def _validate_candlestick_bundle(
    bundle: IndicatorBundle,
) -> list[IndicatorComponent]:
    if bundle.indicator.upper() != "CDL_PATTERNS":
        raise ValueError(
            "candlestick signal requires a CDL_PATTERNS indicator bundle"
        )

    component_names = {component.name for component in bundle.components}
    expected_names = set(_PATTERN_METADATA)
    if component_names != expected_names:
        raise ValueError(
            "candlestick signal requires the complete set of "
            "supported TA-Lib pattern components"
        )

    return bundle.components


def _validate_as_of(as_of: datetime | None) -> None:
    if as_of is None:
        return
    if not isinstance(as_of, datetime):
        raise ValueError(
            "candlestick signal evaluation time must be a datetime"
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "candlestick signal evaluation time must include timezone "
            "information"
        )


def _latest_index(
    components: list[IndicatorComponent],
    as_of: datetime | None,
) -> int:
    reference = components[0].points
    for index in range(len(reference) - 1, -1, -1):
        if as_of is None or reference[index].timestamp <= as_of:
            return index

    raise ValueError(
        "candlestick signal requires an available indicator point"
    )


def _classify_confluence(
    bullish: list[tuple[str, SignalStrength, int]],
    bearish: list[tuple[str, SignalStrength, int]],
) -> tuple[SignalDirection, SignalStrength, str]:
    if bullish and bearish:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            "shows conflicting bullish and bearish patterns",
        )
    if not bullish and not bearish:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            "shows no directional pattern",
        )

    fired = bullish or bearish
    direction = (
        SignalDirection.BULLISH if bullish else SignalDirection.BEARISH
    )
    best_reliability = max(
        _STRENGTH_ORDER.index(reliability)
        for _, reliability, _ in fired
    )
    if len(fired) >= 2:
        best_reliability = min(best_reliability + 1, len(_STRENGTH_ORDER) - 1)
    strength = _STRENGTH_ORDER[best_reliability]

    condition = (
        f"confirms {direction.value} confluence"
        if len(fired) >= 2
        else f"confirms a {direction.value} pattern"
    )
    return direction, strength, condition


def _name_list(patterns: list[tuple[str, SignalStrength, int]]) -> str:
    if not patterns:
        return "none"
    return ",".join(name for name, _, _ in patterns)


def _build_explanation(
    condition: str,
    direction: SignalDirection,
    bullish: list[tuple[str, SignalStrength, int]],
    bearish: list[tuple[str, SignalStrength, int]],
    neutral: list[str],
) -> str:
    prefix = f"The completed candle {condition}."
    details = []
    if bullish:
        details.append(f"Bullish: {_name_list(bullish)}.")
    if bearish:
        details.append(f"Bearish: {_name_list(bearish)}.")
    if neutral:
        details.append(f"Indecision context: {','.join(neutral)}.")
    if not details:
        return (
            f"{prefix} No TA-Lib candlestick pattern fired on this bar; "
            "this is neutral evidence, not confirmation of a reversal."
        )
    return (
        f"{prefix} {' '.join(details)} This is {direction.value} "
        "candlestick evidence, not confirmation of a reversal."
    )
