from app.analytics.fair_value_gaps import (
    detect_fair_value_gaps,
    track_fair_value_gap_lifecycle,
)
from app.analytics.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_candlestick_patterns,
    calculate_ema,
    calculate_rsi,
)
from app.analytics.market_structure import analyze_market_structure
from app.analytics.structure_breaks import detect_structure_breaks
from app.analytics.support_resistance import (
    detect_support_resistance_zones,
)
from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.analytics.swing_evaluator import UnifiedSwingEvaluatorConfig
from app.analytics.swing_pivots import detect_swing_pivots
from app.models.dashboard import (
    DashboardCandlestickPattern,
    DashboardAccumulationZone,
    DashboardCentralPivotRange,
    DashboardChartPivot,
    DashboardChartZone,
    DashboardFairValueGap,
    DashboardIndicatorPoint,
    DashboardIndicatorSeries,
    DashboardLiquiditySweep,
    DashboardPatternDefinition,
    DashboardStructureBreak,
    DashboardStructurePoint,
    DashboardTechnicalChart,
)
from app.models.market import HistoricalCandleSeries
from app.models.multi_timeframe_evidence import (
    SwingAnalysisTimeframe,
    TimeframeTechnicalEvidenceContext,
    effective_zone_type,
    qualified_zone_id,
)
from app.models.price_action import SwingPivotType
from app.models.technical import IndicatorComponent, IndicatorSeries


# The dashboard deliberately highlights the named, decision-useful subset
# requested for Jarvis rather than covering the chart with every TA-Lib hit.
_DISPLAY_PATTERNS: dict[str, str] = {
    "doji": "Doji",
    "doji_star": "Doji Star",
    "dragonfly_doji": "Dragonfly Doji",
    "gravestone_doji": "Gravestone Doji",
    "long_legged_doji": "Long-legged Doji",
    "engulfing": "Engulfing",
    "hammer": "Hammer",
    "hanging_man": "Hanging Man",
    "harami": "Harami",
    "harami_cross": "Harami Cross",
    "morning_star": "Morning Star",
    "morning_doji_star": "Morning Doji Star",
    "evening_star": "Evening Star",
    "evening_doji_star": "Evening Doji Star",
    "piercing": "Piercing",
    "dark_cloud_cover": "Dark Cloud Cover",
    "shooting_star": "Shooting Star",
    "three_black_crows": "Three Black Crows",
    "three_white_soldiers": "Three White Soldiers",
    "marubozu": "Marubozu",
    "closing_marubozu": "Closing Marubozu",
    "hikkake": "Hikkake",
    "hikkake_modified": "Modified Hikkake",
}
_NEUTRAL_PATTERNS = {"doji", "long_legged_doji"}
_BULLISH_PATTERNS = {
    "dragonfly_doji",
    "hammer",
    "morning_star",
    "morning_doji_star",
    "piercing",
    "three_white_soldiers",
}
_BEARISH_PATTERNS = {
    "gravestone_doji",
    "hanging_man",
    "evening_star",
    "evening_doji_star",
    "dark_cloud_cover",
    "shooting_star",
    "three_black_crows",
}


def project_technical_chart(
    series: HistoricalCandleSeries,
    context: TimeframeTechnicalEvidenceContext,
    timeframe: SwingAnalysisTimeframe,
    *,
    candle_limit: int,
    cpr_source_series: HistoricalCandleSeries | None = None,
    config: UnifiedSwingEvaluatorConfig | None = None,
) -> DashboardTechnicalChart:
    """Recalculate chart geometry from the exact assigned candle series."""
    resolved = config or UnifiedSwingEvaluatorConfig()
    visible_candles = series.candles[-candle_limit:]
    first_visible = visible_candles[0].timestamp
    last_visible = visible_candles[-1].timestamp

    ema = calculate_ema(series, resolved.fast_ema_period)
    slow_ema = calculate_ema(series, resolved.slow_ema_period)
    rsi = calculate_rsi(series, resolved.rsi_period)
    bollinger = calculate_bollinger_bands(
        series,
        resolved.bollinger_period,
        resolved.bollinger_deviation,
        resolved.bollinger_deviation,
    )
    atr = calculate_atr(series, resolved.atr_period)
    patterns = calculate_candlestick_patterns(
        series,
        penetration=resolved.candlestick_pattern_penetration,
    )

    pivots = detect_swing_pivots(
        series,
        left_strength=resolved.pivot_left_strength,
        right_strength=resolved.pivot_right_strength,
    )
    structure = analyze_market_structure(
        pivots,
        equality_tolerance_percentage=(
            resolved.structure_equality_tolerance_percentage
        ),
        as_of=context.evaluated_at,
    )
    breaks = detect_structure_breaks(
        series,
        pivots,
        equality_tolerance_percentage=(
            resolved.structure_equality_tolerance_percentage
        ),
        as_of=context.evaluated_at,
    )
    zones = detect_support_resistance_zones(
        pivots,
        tolerance_percentage=(
            resolved.support_resistance_tolerance_percentage
        ),
        minimum_touches=resolved.support_resistance_minimum_touches,
        as_of=context.evaluated_at,
    )
    lifecycles = track_support_resistance_lifecycle(
        series,
        zones,
        as_of=context.evaluated_at,
    )
    gaps = track_fair_value_gap_lifecycle(
        detect_fair_value_gaps(
            series,
            minimum_gap_percentage=resolved.fvg_minimum_gap_percentage,
            atr_series=atr,
            minimum_atr_multiple=resolved.fvg_minimum_atr_multiple,
        ),
        series,
        max_age_candles=resolved.fvg_max_age_candles,
    )

    components = {item.name: item for item in bollinger.components}
    indicators = (
        _indicator_series(
            f"ema_{resolved.fast_ema_period}",
            f"EMA {resolved.fast_ema_period}",
            "price",
            ema,
            first_visible,
        ),
        _indicator_series(
            f"ema_{resolved.slow_ema_period}",
            f"EMA {resolved.slow_ema_period}",
            "price",
            slow_ema,
            first_visible,
        ),
        _component_series(
            "bollinger_upper",
            f"BB upper ({resolved.bollinger_period}, {resolved.bollinger_deviation:g})",
            "price",
            components["upper_band"],
            first_visible,
        ),
        _component_series(
            "bollinger_middle",
            f"BB middle ({resolved.bollinger_period})",
            "price",
            components["middle_band"],
            first_visible,
        ),
        _component_series(
            "bollinger_lower",
            f"BB lower ({resolved.bollinger_period}, {resolved.bollinger_deviation:g})",
            "price",
            components["lower_band"],
            first_visible,
        ),
        _indicator_series(
            f"rsi_{resolved.rsi_period}",
            f"RSI {resolved.rsi_period}",
            "rsi",
            rsi,
            first_visible,
        ),
    )

    candles_by_time = {item.timestamp: item for item in visible_candles}
    immediate_ids = {
        item.qualified_zone_id
        for item in (context.nearest_support, context.nearest_resistance)
        if item is not None
    }
    chart_zones = tuple(
        DashboardChartZone(
            zone_id=qualified_zone_id(timeframe, lifecycle),
            original_type=lifecycle.zone.zone_type.value,
            effective_type=(
                value.value
                if (value := effective_zone_type(lifecycle)) is not None
                else None
            ),
            lower_price=lifecycle.zone.lower_price,
            upper_price=lifecycle.zone.upper_price,
            touch_count=lifecycle.zone.touch_count,
            lifecycle_status=lifecycle.status.value,
            confirmed_at=lifecycle.zone.confirmed_at,
            broken_at=lifecycle.broken_at,
            retested_at=lifecycle.retested_at,
            reversal_confirmed_at=lifecycle.reversal_confirmed_at,
            failed_at=lifecycle.failed_at,
            immediate=qualified_zone_id(timeframe, lifecycle) in immediate_ids,
        )
        for lifecycle in lifecycles.lifecycles[-20:]
    )
    accumulation_zones = tuple(
        DashboardAccumulationZone(
            zone_id=zone.zone_id,
            lower_price=zone.lower_price,
            upper_price=zone.upper_price,
            base_started_at=zone.base_started_at,
            base_last_observed_at=zone.base_last_observed_at,
            ends_at=(
                last_visible if zone.is_active else zone.lifecycle[-1].available_at
            ),
            lifecycle_state=zone.current_state.value,
            active=zone.is_active,
            confidence_percentage=zone.metrics.confidence_score,
            evidence_ids=zone.evidence_ids,
        )
        for zone in context.accumulation.zones
        if (
            zone.base_last_observed_at >= first_visible
            or zone.is_active
            or zone.lifecycle[-1].available_at >= first_visible
        )
    )
    liquidity_sweeps = tuple(
        DashboardLiquiditySweep(
            sweep_id=sweep.sweep_id,
            zone_id=zone.zone_id,
            liquidity_side=sweep.liquidity_side.value,
            implication=sweep.implication.value,
            reference_price=sweep.reference_price,
            extreme_price=sweep.extreme_price,
            reclaim_close_price=sweep.reclaim_close_price,
            swept_at=sweep.swept_at,
            reclaimed_at=sweep.reclaimed_at,
            available_at=sweep.available_at,
            volume_multiple=sweep.volume_multiple,
            evidence_ids=sweep.evidence_ids,
        )
        for zone in context.accumulation.zones
        for sweep in zone.liquidity_sweeps
        if sweep.available_at >= first_visible
    )

    visible_gaps = [
        gap
        for gap in gaps.gaps
        if gap.detected_at >= first_visible
        or gap.resolved_at is None
        or gap.resolved_at >= first_visible
    ][-40:]
    return DashboardTechnicalChart(
        indicators=indicators,
        pattern_catalog=tuple(
            DashboardPatternDefinition(pattern=name, label=label)
            for name, label in _DISPLAY_PATTERNS.items()
        ),
        candlestick_patterns=tuple(
            _pattern_markers(patterns.components, candles_by_time)
        ),
        central_pivot_ranges=tuple(
            _central_pivot_ranges(
                series,
                cpr_source_series or series,
                timeframe,
                first_visible,
            )
        ),
        fair_value_gaps=tuple(
            DashboardFairValueGap(
                direction=gap.direction.value,
                detected_at=gap.detected_at,
                ends_at=gap.resolved_at or last_visible,
                lower_price=gap.lower_price,
                upper_price=gap.upper_price,
                status=gap.status.value,
                fill_percentage=gap.fill_percentage,
            )
            for gap in visible_gaps
        ),
        zones=chart_zones,
        pivots=tuple(
            DashboardChartPivot(
                pivot_type=pivot.pivot_type.value,
                price=pivot.price,
                pivot_at=pivot.pivot_at,
                confirmed_at=pivot.confirmed_at,
            )
            for pivot in pivots.pivots
            if pivot.pivot_at >= first_visible
            and pivot.confirmed_at <= context.evaluated_at
        ),
        structure_points=tuple(
            DashboardStructurePoint(
                classification=point.classification.value,
                pivot_type=point.pivot.pivot_type.value,
                price=point.pivot.price,
                pivot_at=point.pivot.pivot_at,
                confirmed_at=point.pivot.confirmed_at,
            )
            for point in structure.points
            if point.pivot.pivot_at >= first_visible
        ),
        structure_breaks=tuple(
            DashboardStructureBreak(
                break_type=event.break_type.value,
                direction=event.direction.value,
                occurred_at=event.occurred_at,
                close_price=event.close_price,
                broken_pivot_price=event.broken_pivot.price,
            )
            for event in breaks.events
            if event.occurred_at >= first_visible
        ),
        accumulation_zones=accumulation_zones,
        liquidity_sweeps=liquidity_sweeps,
    )


def _indicator_series(
    indicator_id: str,
    label: str,
    pane: str,
    series: IndicatorSeries,
    first_visible,
) -> DashboardIndicatorSeries:
    return DashboardIndicatorSeries(
        indicator_id=indicator_id,
        label=label,
        pane=pane,
        points=tuple(
            DashboardIndicatorPoint(timestamp=point.timestamp, value=point.value)
            for point in series.points
            if point.timestamp >= first_visible
        ),
    )


def _component_series(
    indicator_id: str,
    label: str,
    pane: str,
    component: IndicatorComponent,
    first_visible,
) -> DashboardIndicatorSeries:
    return DashboardIndicatorSeries(
        indicator_id=indicator_id,
        label=label,
        pane=pane,
        points=tuple(
            DashboardIndicatorPoint(timestamp=point.timestamp, value=point.value)
            for point in component.points
            if point.timestamp >= first_visible
        ),
    )


def _pattern_markers(components, candles_by_time):
    for component in components:
        label = _DISPLAY_PATTERNS.get(component.name)
        if label is None:
            continue
        for point in component.points:
            candle = candles_by_time.get(point.timestamp)
            if candle is None or point.value == 0:
                continue
            direction = _pattern_direction(component.name, point.value)
            price = candle.low if direction == "bullish" else candle.high
            yield DashboardCandlestickPattern(
                pattern=component.name,
                label=label,
                direction=direction,
                timestamp=point.timestamp,
                price=price,
                raw_value=point.value,
            )


def _pattern_direction(name: str, value: float) -> str:
    if name in _NEUTRAL_PATTERNS:
        return "neutral"
    if name in _BULLISH_PATTERNS:
        return "bullish"
    if name in _BEARISH_PATTERNS:
        return "bearish"
    return "bullish" if value > 0 else "bearish"


def _central_pivot_ranges(
    chart_series: HistoricalCandleSeries,
    source_series: HistoricalCandleSeries,
    timeframe: SwingAnalysisTimeframe,
    first_visible,
):
    basis = "weekly" if timeframe is SwingAnalysisTimeframe.DAILY else "monthly"
    key = _week_key if basis == "weekly" else _month_key
    source_groups = _group_candles(source_series, key)
    chart_groups = _group_candles(chart_series, key)
    ordered_source_keys = sorted(source_groups)

    # The first observed source group can begin mid-week or mid-month at the
    # archive boundary. Never use it as a completed CPR source period.
    for index in range(2, len(ordered_source_keys)):
        target_key = ordered_source_keys[index]
        target_candles = chart_groups.get(target_key)
        if not target_candles:
            continue
        source_candles = source_groups[ordered_source_keys[index - 1]]
        high = max(candle.high for candle in source_candles)
        low = min(candle.low for candle in source_candles)
        close = source_candles[-1].close
        pivot = (high + low + close) / 3
        raw_bottom = (high + low) / 2
        raw_top = 2 * pivot - raw_bottom
        bottom = min(raw_bottom, raw_top)
        top = max(raw_bottom, raw_top)
        if target_candles[-1].timestamp < first_visible:
            continue
        yield DashboardCentralPivotRange(
            basis=basis,
            source_period_started_at=source_candles[0].timestamp,
            source_period_ended_at=source_candles[-1].timestamp,
            valid_from=target_candles[0].timestamp,
            valid_to=target_candles[-1].timestamp,
            pivot=pivot,
            bottom_central=bottom,
            top_central=top,
            width_percentage=(
                (top - bottom) / pivot * 100 if pivot > 0 else 0.0
            ),
        )


def _group_candles(series, key_fn):
    groups = {}
    for candle in series.candles:
        groups.setdefault(key_fn(candle.timestamp), []).append(candle)
    return groups


def _week_key(timestamp):
    iso = timestamp.isocalendar()
    return iso.year, iso.week


def _month_key(timestamp):
    return timestamp.year, timestamp.month
