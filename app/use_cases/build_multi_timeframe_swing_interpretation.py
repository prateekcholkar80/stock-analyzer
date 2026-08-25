from collections.abc import Iterable

from app.models.accumulation import LiquidityPoolSide
from app.models.agentic import TradePlanningReason
from app.models.multi_timeframe_evidence import (
    MultiTimeframeEvidenceReview,
    QualifiedTechnicalEvidence,
    SwingAnalysisTimeframe,
    TimeframeTechnicalEvidenceContext,
    qualified_zone_id,
)
from app.models.multi_timeframe_trade import MultiTimeframeLongTradePlanResult
from app.models.signals import (
    SignalDirection,
    SignalStrength,
    SwingTradingSignalProfile,
)
from app.models.technical_setup import (
    SwingSetupSide,
    SwingSetupStep,
    SwingSetupStepId,
    SwingSetupStepState,
    TimeframeSwingSetup,
)
from app.models.timeframe_interpretation import (
    MultiTimeframeSwingInterpretation,
    RewardRiskFeasibility,
    RewardRiskTargetInterpretation,
    StructuralRisk,
    SwingRiskRewardInterpretation,
    TacticalReadiness,
    TimeframeMarketInterpretation,
    derive_timeframe_alignment,
)
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
    combined_profile_market_condition,
    market_condition_from_profile,
)
from app.models.trade_setup import TradeTargetFeasibility


class BuildMultiTimeframeSwingInterpretation:
    """Build an evidence-only daily/weekly setup and decision interpretation."""

    use_case_id = "jarvis.build_multi_timeframe_swing_interpretation.v1"

    def execute(
        self,
        technical_review: MultiTimeframeEvidenceReview,
        trade_plan: MultiTimeframeLongTradePlanResult,
    ) -> MultiTimeframeSwingInterpretation:
        if not isinstance(technical_review, MultiTimeframeEvidenceReview):
            raise ValueError("interpretation requires a technical review")
        if not isinstance(trade_plan, MultiTimeframeLongTradePlanResult):
            raise ValueError("interpretation requires a trade-plan result")
        package = technical_review.released_evidence
        if package is None:
            raise ValueError("interpretation requires released evidence")
        if (
            trade_plan.technical_package_fingerprint
            != package.package_fingerprint
            or trade_plan.technical_decision_id
            != technical_review.decision.decision_id
        ):
            raise ValueError(
                "interpretation inputs must belong to the same review chain"
            )
        decision = trade_plan.trade_decision
        if decision is None:
            raise ValueError(
                "interpretation requires the version-2 trade decision"
            )

        analysis = package.technical_analysis
        daily = self._timeframe(
            package.daily,
            analysis.daily_submission.profile,
        )
        weekly = self._timeframe(
            package.weekly,
            analysis.weekly_submission.profile,
        )
        expected_condition = combined_profile_market_condition(
            analysis.daily_submission.profile,
            analysis.weekly_submission.profile,
        )
        if decision.market_condition is not expected_condition:
            raise ValueError(
                "interpretation decision does not match deterministic "
                "timeframe profiles"
            )

        tactical = _tactical_readiness(decision)
        structural = _structural_risk(weekly, decision)
        risk_reward = _risk_reward(package.daily, trade_plan)
        decisive_ids = tuple(
            dict.fromkeys(
                daily.decisive_evidence_ids
                + weekly.decisive_evidence_ids
            )
        )
        return MultiTimeframeSwingInterpretation(
            daily=daily,
            weekly=weekly,
            alignment=derive_timeframe_alignment(
                daily.market_condition,
                weekly.market_condition,
            ),
            tactical_readiness=tactical,
            structural_risk=structural,
            risk_reward=risk_reward,
            trade_decision=decision,
            decisive_evidence_ids=decisive_ids,
            decision_change_conditions=_decision_change_conditions(
                decision,
            ),
            rationale=(
                "Deterministic daily/weekly synthesis: daily is "
                f"{daily.market_condition.value}, weekly is "
                f"{weekly.market_condition.value}, and the resulting "
                f"decision is {decision.decision.value}. "
                f"{decision.rationale}"
            ),
            interpreted_at=max(daily.evaluated_at, weekly.evaluated_at),
        )

    def _timeframe(
        self,
        context: TimeframeTechnicalEvidenceContext,
        profile: SwingTradingSignalProfile,
    ) -> TimeframeMarketInterpretation:
        if profile.snapshot.evaluated_at != context.evaluated_at:
            raise ValueError(
                "interpretation profile and context evaluation times differ"
            )
        condition = market_condition_from_profile(profile)
        return TimeframeMarketInterpretation(
            timeframe=context.timeframe,
            interval=context.interval,
            evaluated_at=context.evaluated_at,
            market_condition=condition,
            bullish_setup=_build_setup(
                context,
                SwingSetupSide.BULLISH,
            ),
            bearish_setup=_build_setup(
                context,
                SwingSetupSide.BEARISH,
            ),
            decisive_evidence_ids=_decisive_evidence_ids(
                context,
                condition,
            ),
            rationale=(
                f"The deterministic {context.timeframe.value} swing profile "
                f"is {condition.value}. {profile.rationale}"
            ),
        )


def _build_setup(
    context: TimeframeTechnicalEvidenceContext,
    side: SwingSetupSide,
) -> TimeframeSwingSetup:
    evidence = _EvidenceIndex(context.evidence)
    if side is SwingSetupSide.BULLISH:
        steps = _bullish_steps(context, evidence)
    else:
        steps = _bearish_steps(context, evidence)
    return TimeframeSwingSetup(
        setup_id=f"{context.timeframe.value}:{side.value}_setup",
        side=side,
        timeframe=context.timeframe,
        interval=context.interval,
        evaluated_at=context.evaluated_at,
        steps=steps,
    )


class _EvidenceIndex:
    def __init__(
        self,
        evidence: tuple[QualifiedTechnicalEvidence, ...],
    ) -> None:
        self._evidence = evidence

    def one(self, prefix: str) -> QualifiedTechnicalEvidence | None:
        return next(
            (
                item
                for item in self._evidence
                if item.evidence.evidence_id.startswith(prefix)
            ),
            None,
        )

    def directional(
        self,
        direction: SignalDirection,
    ) -> QualifiedTechnicalEvidence | None:
        return next(
            (
                item
                for item in self._evidence
                if item.evidence.direction is direction
                and item.evidence.strength is not SignalStrength.WEAK
            ),
            None,
        )


def _bullish_steps(
    context: TimeframeTechnicalEvidenceContext,
    evidence: _EvidenceIndex,
) -> tuple[SwingSetupStep, ...]:
    structure = evidence.one("market_structure.")
    volume = evidence.one("obv_confirmation.")
    fvg = evidence.one("fvg_context.")
    zone = evidence.one("support_resistance.")
    ema = evidence.one("ma_alignment.")
    rsi = evidence.one("rsi_condition.")
    return (
        _structure_state_step(
            context,
            structure,
            SwingSetupStepId.BULLISH_PRIOR_DOWNTREND,
            side=SwingSetupSide.BULLISH,
            confirmed_bias="bearish",
            contradicted_bias="bullish",
            confirmed_pair=("lower_high", "lower_low"),
            contradicted_pair=("higher_high", "higher_low"),
            confirmed_text="A prior downtrend is confirmed.",
        ),
        _structure_break_step(
            context,
            structure,
            SwingSetupStepId.BULLISH_CHANGE_OF_CHARACTER,
            side=SwingSetupSide.BULLISH,
            break_type="change_of_character",
            direction="bullish",
            label="bullish CHOCH",
        ),
        _structure_break_step(
            context,
            structure,
            SwingSetupStepId.BULLISH_BREAK_ABOVE_RESISTANCE,
            side=SwingSetupSide.BULLISH,
            break_type="break_of_structure",
            direction="bullish",
            label="bullish BOS above resistance",
        ),
        _volume_step(
            context,
            volume,
            SwingSetupStepId.BULLISH_VOLUME_EXPANSION,
            side=SwingSetupSide.BULLISH,
            direction=SignalDirection.BULLISH,
        ),
        _value_pullback_step(
            context,
            fvg,
            zone,
            SwingSetupStepId.BULLISH_PULLBACK_INTO_VALUE,
            side=SwingSetupSide.BULLISH,
            direction="bullish",
        ),
        _ema_step(
            context,
            ema,
            SwingSetupStepId.BULLISH_EMA_ALIGNMENT,
            side=SwingSetupSide.BULLISH,
            relationship="above",
        ),
        _rsi_regime_step(context, rsi),
        _structure_classification_step(
            context,
            structure,
            SwingSetupStepId.BULLISH_HIGHER_LOW,
            side=SwingSetupSide.BULLISH,
            value_key="latest_low_classification",
            confirmed_value="higher_low",
            contradicted_value="lower_low",
            label="higher low",
        ),
        _structure_classification_step(
            context,
            structure,
            SwingSetupStepId.BULLISH_HIGHER_HIGH,
            side=SwingSetupSide.BULLISH,
            value_key="latest_high_classification",
            confirmed_value="higher_high",
            contradicted_value="lower_high",
            label="higher high",
        ),
    )


def _bearish_steps(
    context: TimeframeTechnicalEvidenceContext,
    evidence: _EvidenceIndex,
) -> tuple[SwingSetupStep, ...]:
    structure = evidence.one("market_structure.")
    volume = evidence.one("obv_confirmation.")
    fvg = evidence.one("fvg_context.")
    ema = evidence.one("ma_alignment.")
    bearish_pressure = evidence.directional(SignalDirection.BEARISH)
    return (
        _uptrend_exhaustion_step(
            context,
            structure,
            bearish_pressure,
        ),
        _liquidity_sweep_step(context),
        _structure_break_step(
            context,
            structure,
            SwingSetupStepId.BEARISH_CHANGE_OF_CHARACTER,
            side=SwingSetupSide.BEARISH,
            break_type="change_of_character",
            direction="bearish",
            label="bearish CHOCH",
        ),
        _structure_break_step(
            context,
            structure,
            SwingSetupStepId.BEARISH_BREAK_BELOW_SUPPORT,
            side=SwingSetupSide.BEARISH,
            break_type="break_of_structure",
            direction="bearish",
            label="bearish BOS below support",
        ),
        _volume_step(
            context,
            volume,
            SwingSetupStepId.BEARISH_VOLUME_EXPANSION,
            side=SwingSetupSide.BEARISH,
            direction=SignalDirection.BEARISH,
        ),
        _value_pullback_step(
            context,
            fvg,
            None,
            SwingSetupStepId.BEARISH_FVG_RETEST,
            side=SwingSetupSide.BEARISH,
            direction="bearish",
        ),
        _ema_step(
            context,
            ema,
            SwingSetupStepId.BEARISH_EMA_ALIGNMENT,
            side=SwingSetupSide.BEARISH,
            relationship="below",
        ),
        _unavailable_step(
            context,
            SwingSetupStepId.BEARISH_RSI_DIVERGENCE,
            SwingSetupSide.BEARISH,
            "RSI divergence is not calculated by the released RSI signal and "
            "is therefore not inferred from RSI level alone.",
        ),
    )


def _structure_state_step(
    context,
    evidence,
    step_id,
    *,
    side,
    confirmed_bias,
    contradicted_bias,
    confirmed_pair,
    contradicted_pair,
    confirmed_text,
):
    if evidence is None:
        return _unavailable_step(
            context,
            step_id,
            side,
            "Market-structure evidence is unavailable.",
        )
    values = evidence.evidence.observed_values
    bias = values.get("effective_bias")
    pair = (
        values.get("latest_high_classification"),
        values.get("latest_low_classification"),
    )
    if bias == confirmed_bias or pair == confirmed_pair:
        state = SwingSetupStepState.CONFIRMED
        explanation = confirmed_text
    elif bias == contradicted_bias or pair == contradicted_pair:
        state = SwingSetupStepState.CONTRADICTED
        explanation = "The current confirmed structure contradicts this step."
    else:
        state = SwingSetupStepState.PENDING
        explanation = "Confirmed structure does not yet establish this step."
    return _evidenced_step(context, step_id, side, state, (evidence,), explanation)


def _structure_break_step(
    context,
    evidence,
    step_id,
    *,
    side,
    break_type,
    direction,
    label,
):
    if evidence is None:
        return _unavailable_step(
            context,
            step_id,
            side,
            "Market-structure break evidence is unavailable.",
        )
    values = evidence.evidence.observed_values
    actual_type = values.get("latest_break_type")
    actual_direction = values.get("latest_break_direction")
    if actual_type == break_type and actual_direction == direction:
        state = SwingSetupStepState.CONFIRMED
        explanation = f"The latest confirmed structure event is {label}."
    elif actual_type == break_type and actual_direction in {"bullish", "bearish"}:
        state = SwingSetupStepState.CONTRADICTED
        explanation = f"The latest confirmed {break_type} is {actual_direction}."
    else:
        state = SwingSetupStepState.PENDING
        explanation = f"No latest confirmed {label} is available."
    return _evidenced_step(context, step_id, side, state, (evidence,), explanation)


def _volume_step(context, evidence, step_id, *, side, direction):
    if evidence is None:
        return _unavailable_step(
            context,
            step_id,
            side,
            "Price-volume evidence is unavailable.",
        )
    values = evidence.evidence.observed_values
    high_volume = values.get("is_high_volume") is True
    actual_direction = evidence.evidence.direction
    if high_volume and actual_direction is direction:
        state = SwingSetupStepState.CONFIRMED
        explanation = f"High-volume {direction.value} expansion is confirmed."
    elif high_volume and actual_direction is not SignalDirection.NEUTRAL:
        state = SwingSetupStepState.CONTRADICTED
        explanation = "High-volume expansion is confirmed in the opposite direction."
    elif actual_direction is direction:
        state = SwingSetupStepState.DEVELOPING
        explanation = (
            f"{direction.value.title()} price-volume confirmation exists, "
            "but current volume is not expanded."
        )
    else:
        state = SwingSetupStepState.PENDING
        explanation = f"{direction.value.title()} volume expansion is not confirmed."
    thresholds = {
        "high_volume_multiplier": evidence.evidence.parameters.get(
            "high_volume_multiplier",
            1.5,
        )
    }
    return _evidenced_step(
        context,
        step_id,
        side,
        state,
        (evidence,),
        explanation,
        thresholds=thresholds,
    )


def _value_pullback_step(
    context,
    fvg,
    zone,
    step_id,
    *,
    side,
    direction,
):
    candidates = tuple(item for item in (fvg, zone) if item is not None)
    if not candidates:
        return _unavailable_step(
            context,
            step_id,
            side,
            "FVG and support/resistance evidence are unavailable.",
        )
    fvg_confirmed = False
    zone_confirmed = False
    if fvg is not None:
        values = fvg.evidence.observed_values
        threshold = float(
            fvg.evidence.parameters.get("proximity_threshold_percentage", 1.0)
        )
        fvg_confirmed = (
            values.get("selected_gap_direction") == direction
            and values.get("selected_gap_status") in {"open", "partially_filled"}
            and (
                values.get("current_price_location") == "inside_gap"
                or float(values.get("selected_gap_distance_percentage", 100.0))
                <= threshold
            )
        )
    if zone is not None and direction == "bullish":
        values = zone.evidence.observed_values
        threshold = float(
            zone.evidence.parameters.get("proximity_threshold_percentage", 1.0)
        )
        zone_confirmed = (
            values.get("selected_zone_type") == "support"
            and values.get("selected_zone_status")
            in {"active", "failed_break", "role_reversed"}
            and float(values.get("selected_zone_distance_percentage", 100.0))
            <= threshold
        )
    if fvg_confirmed or zone_confirmed:
        state = SwingSetupStepState.CONFIRMED
        explanation = (
            "Price is inside or within the configured proximity of the "
            f"relevant {direction} value area."
        )
    else:
        state = SwingSetupStepState.PENDING
        explanation = f"No active {direction} FVG/value retest is confirmed."
    return _evidenced_step(
        context,
        step_id,
        side,
        state,
        candidates,
        explanation,
    )


def _ema_step(context, evidence, step_id, *, side, relationship):
    if evidence is None:
        return _unavailable_step(
            context,
            step_id,
            side,
            "EMA alignment evidence is unavailable.",
        )
    actual = evidence.evidence.observed_values.get("relationship")
    opposite = "below" if relationship == "above" else "above"
    if actual == relationship:
        state = SwingSetupStepState.CONFIRMED
        explanation = f"EMA20 is confirmed {relationship} EMA50."
    elif actual == opposite:
        state = SwingSetupStepState.CONTRADICTED
        explanation = f"EMA20 is {opposite} EMA50."
    else:
        state = SwingSetupStepState.PENDING
        explanation = "EMA20 and EMA50 do not have the required separation."
    return _evidenced_step(context, step_id, side, state, (evidence,), explanation)


def _rsi_regime_step(context, evidence):
    step_id = SwingSetupStepId.BULLISH_RSI_REGIME
    side = SwingSetupSide.BULLISH
    if evidence is None:
        return _unavailable_step(
            context,
            step_id,
            side,
            "RSI evidence is unavailable.",
        )
    value = float(evidence.evidence.observed_values["rsi"])
    if value > 50.0:
        state = SwingSetupStepState.CONFIRMED
        explanation = f"RSI is {value:.2f}, above the bullish 50 regime level."
    elif value < 50.0:
        state = SwingSetupStepState.CONTRADICTED
        explanation = f"RSI is {value:.2f}, below the bullish 50 regime level."
    else:
        state = SwingSetupStepState.PENDING
        explanation = "RSI is exactly at the neutral 50 regime level."
    return _evidenced_step(
        context,
        step_id,
        side,
        state,
        (evidence,),
        explanation,
        thresholds={"bullish_regime_above": 50.0},
    )


def _structure_classification_step(
    context,
    evidence,
    step_id,
    *,
    side,
    value_key,
    confirmed_value,
    contradicted_value,
    label,
):
    if evidence is None:
        return _unavailable_step(
            context,
            step_id,
            side,
            "Market-structure classification is unavailable.",
        )
    actual = evidence.evidence.observed_values.get(value_key)
    if actual == confirmed_value:
        state = SwingSetupStepState.CONFIRMED
        explanation = f"The latest confirmed structure point is a {label}."
    elif actual == contradicted_value:
        state = SwingSetupStepState.CONTRADICTED
        explanation = f"The latest confirmed structure point is {actual}."
    else:
        state = SwingSetupStepState.PENDING
        explanation = f"The latest confirmed structure does not establish a {label}."
    return _evidenced_step(context, step_id, side, state, (evidence,), explanation)


def _uptrend_exhaustion_step(context, structure, bearish_pressure):
    step_id = SwingSetupStepId.BEARISH_UPTREND_EXHAUSTION
    side = SwingSetupSide.BEARISH
    if structure is None:
        return _unavailable_step(
            context,
            step_id,
            side,
            "Uptrend exhaustion cannot be evaluated without structure.",
        )
    values = structure.evidence.observed_values
    prior_uptrend = (
        values.get("effective_bias") == "bullish"
        or (
            values.get("latest_high_classification") == "higher_high"
            and values.get("latest_low_classification") == "higher_low"
        )
    )
    if prior_uptrend and bearish_pressure is not None:
        state = SwingSetupStepState.DEVELOPING
        items = (structure, bearish_pressure)
        explanation = (
            "A prior uptrend and bearish pressure are present, but no dedicated "
            "exhaustion confirmation is available."
        )
    elif prior_uptrend:
        state = SwingSetupStepState.PENDING
        items = (structure,)
        explanation = "An uptrend exists without confirmed exhaustion pressure."
    else:
        state = SwingSetupStepState.CONTRADICTED
        items = (structure,)
        explanation = "Confirmed structure does not show the required prior uptrend."
    return _evidenced_step(context, step_id, side, state, items, explanation)


def _liquidity_sweep_step(
    context: TimeframeTechnicalEvidenceContext,
) -> SwingSetupStep:
    step_id = SwingSetupStepId.BEARISH_LIQUIDITY_SWEEP
    side = SwingSetupSide.BEARISH
    sweeps = [
        sweep
        for zone in context.accumulation.zones
        for sweep in zone.liquidity_sweeps
    ]
    if not sweeps:
        return SwingSetupStep(
            step_id=step_id,
            side=side,
            timeframe=context.timeframe,
            interval=context.interval,
            sequence=_step_sequence(step_id, side),
            state=SwingSetupStepState.PENDING,
            evaluated_at=context.evaluated_at,
            explanation=(
                "The deterministic accumulation analysis found no "
                "close-confirmed liquidity sweep at this evaluation."
            ),
        )

    latest = max(sweeps, key=lambda item: item.available_at)
    is_bearish = latest.liquidity_side is LiquidityPoolSide.BUY_SIDE
    state = (
        SwingSetupStepState.CONFIRMED
        if is_bearish
        else SwingSetupStepState.CONTRADICTED
    )
    explanation = (
        "A buy-side liquidity sweep breached the known upper boundary and "
        "closed back below it, confirming bearish rejection."
        if is_bearish
        else "The latest confirmed event is a sell-side sweep and bullish "
        "reclaim, which contradicts the bearish liquidity-sweep step."
    )
    observed_values = {
        "liquidity_side": latest.liquidity_side.value,
        "implication": latest.implication.value,
        "reference_price": latest.reference_price,
        "extreme_price": latest.extreme_price,
        "reclaim_close_price": latest.reclaim_close_price,
        "sweep_percentage": latest.sweep_percentage,
    }
    if latest.volume_multiple is not None:
        observed_values["volume_multiple"] = latest.volume_multiple
    evidence_ids = tuple(
        dict.fromkeys((latest.sweep_id,) + latest.evidence_ids)
    )
    return SwingSetupStep(
        step_id=step_id,
        side=side,
        timeframe=context.timeframe,
        interval=context.interval,
        sequence=_step_sequence(step_id, side),
        state=state,
        evidence_ids=evidence_ids,
        observed_at=latest.swept_at,
        available_at=latest.available_at,
        confirmed_at=latest.available_at if is_bearish else None,
        evaluated_at=context.evaluated_at,
        explanation=explanation,
        observed_values=observed_values,
    )


def _evidenced_step(
    context,
    step_id,
    side,
    state,
    evidence: Iterable[QualifiedTechnicalEvidence],
    explanation,
    *,
    thresholds=None,
):
    items_by_id = {
        item.qualified_evidence_id: item
        for item in evidence
    }
    items = tuple(items_by_id.values())
    observed_values = {}
    for item in items:
        source = item.evidence.evidence_id.split(".", 1)[0]
        for key, value in item.evidence.observed_values.items():
            observed_values[f"{source}.{key}"] = value
    available_at = max(item.evidence.available_at for item in items)
    return SwingSetupStep(
        step_id=step_id,
        side=side,
        timeframe=context.timeframe,
        interval=context.interval,
        sequence=(
            _step_sequence(step_id, side)
        ),
        state=state,
        evidence_ids=tuple(item.qualified_evidence_id for item in items),
        observed_at=max(item.evidence.observed_at for item in items),
        available_at=available_at,
        confirmed_at=(
            available_at
            if state is SwingSetupStepState.CONFIRMED
            else None
        ),
        evaluated_at=context.evaluated_at,
        explanation=explanation,
        observed_values=observed_values,
        thresholds=thresholds or {},
    )


def _unavailable_step(context, step_id, side, explanation):
    return SwingSetupStep(
        step_id=step_id,
        side=side,
        timeframe=context.timeframe,
        interval=context.interval,
        sequence=_step_sequence(step_id, side),
        state=SwingSetupStepState.UNAVAILABLE,
        evaluated_at=context.evaluated_at,
        explanation=explanation,
    )


def _step_sequence(step_id, side):
    from app.models.technical_setup import (
        BEARISH_SETUP_SEQUENCE,
        BULLISH_SETUP_SEQUENCE,
    )

    sequence = (
        BULLISH_SETUP_SEQUENCE
        if side is SwingSetupSide.BULLISH
        else BEARISH_SETUP_SEQUENCE
    )
    return sequence.index(step_id) + 1


def _decisive_evidence_ids(context, condition):
    direction = {
        MarketCondition.BULLISH: SignalDirection.BULLISH,
        MarketCondition.BEARISH: SignalDirection.BEARISH,
    }.get(condition)
    if condition is MarketCondition.INSUFFICIENT:
        return ()
    if direction is not None:
        selected = [
            item
            for item in context.evidence
            if item.evidence.direction is direction
            and item.evidence.strength is not SignalStrength.WEAK
        ]
    elif condition is MarketCondition.CONFLICTED:
        selected = [
            item
            for item in context.evidence
            if item.evidence.direction is not SignalDirection.NEUTRAL
            and item.evidence.strength is not SignalStrength.WEAK
        ]
    else:
        selected = [
            item
            for item in context.evidence
            if item.evidence.direction is SignalDirection.NEUTRAL
            and item.evidence.strength is not SignalStrength.WEAK
        ]
    if not selected:
        selected = list(context.evidence[:1])
    return tuple(item.qualified_evidence_id for item in selected)


def _tactical_readiness(decision):
    if decision.market_condition is MarketCondition.INSUFFICIENT:
        return TacticalReadiness.INSUFFICIENT
    if decision.market_condition is not MarketCondition.BULLISH:
        return TacticalReadiness.NOT_APPLICABLE
    if decision.decision is TradeDecision.BUY:
        return TacticalReadiness.READY
    reasons = set(decision.no_trade_reasons)
    if NoTradeReason.JUDGE_REJECTED_BULLISH_CASE in reasons:
        return TacticalReadiness.BLOCKED
    if reasons & {
        NoTradeReason.BUY_SETUP_INVALIDATED,
        NoTradeReason.ACTIVE_BEARISH_BREAK,
    }:
        return TacticalReadiness.BLOCKED
    if NoTradeReason.DAILY_TRIGGER_INCOMPLETE in reasons:
        return TacticalReadiness.DEVELOPING
    return TacticalReadiness.READY


def _structural_risk(weekly, decision):
    reasons = set(decision.no_trade_reasons)
    if NoTradeReason.STRUCTURAL_STOP_UNAVAILABLE in reasons:
        return StructuralRisk.HIGH
    if weekly.market_condition is MarketCondition.INSUFFICIENT:
        return StructuralRisk.UNKNOWN
    if weekly.market_condition is MarketCondition.BEARISH:
        return StructuralRisk.PROHIBITIVE
    if weekly.market_condition is MarketCondition.CONFLICTED:
        return StructuralRisk.HIGH
    if weekly.market_condition is MarketCondition.NEUTRAL:
        return StructuralRisk.MODERATE
    return StructuralRisk.LOW


def _risk_reward(context, trade_plan):
    planning = trade_plan.daily_planning_result
    if planning is None:
        return _unevaluated_risk_reward(RewardRiskFeasibility.NOT_EVALUATED)
    plan = planning.submission.plan
    if plan is None:
        status = (
            RewardRiskFeasibility.INSUFFICIENT_DATA
            if planning.submission.reason
            is TradePlanningReason.INSUFFICIENT_STOP_EVIDENCE
            else RewardRiskFeasibility.NOT_EVALUATED
        )
        return _unevaluated_risk_reward(status)
    evaluation = plan.evaluation
    barrier_id = _barrier_id(context, evaluation.nearest_structural_barrier)
    return SwingRiskRewardInterpretation(
        reference_entry=evaluation.entry_price,
        stop_loss=evaluation.stop_loss_price,
        risk_per_unit=evaluation.risk_per_unit,
        target_2r=_target_interpretation(
            evaluation.minimum_target,
            barrier_id,
            evaluation.rationale,
        ),
        target_3r=_target_interpretation(
            evaluation.preferred_target,
            barrier_id,
            evaluation.rationale,
        ),
    )


def _unevaluated_risk_reward(status):
    rationale = (
        "Reward-to-risk was not evaluated because no eligible long setup "
        "reached deterministic trade planning."
        if status is RewardRiskFeasibility.NOT_EVALUATED
        else "Reward-to-risk could not be evaluated from available stop evidence."
    )
    return SwingRiskRewardInterpretation(
        target_2r=RewardRiskTargetInterpretation(
            reward_to_risk=2.0,
            feasibility=status,
            rationale=rationale,
        ),
        target_3r=RewardRiskTargetInterpretation(
            reward_to_risk=3.0,
            feasibility=status,
            rationale=rationale,
        ),
    )


def _target_interpretation(target, barrier_id, rationale):
    feasible = target.feasibility is TradeTargetFeasibility.REACHABLE
    if not feasible and barrier_id is None:
        raise ValueError(
            "blocked reward target requires a qualified resistance zone"
        )
    return RewardRiskTargetInterpretation(
        reward_to_risk=float(target.reward_to_risk),
        feasibility=(
            RewardRiskFeasibility.FEASIBLE
            if feasible
            else RewardRiskFeasibility.BLOCKED_BY_STRUCTURE
        ),
        target_price=target.target_price,
        blocking_evidence_ids=(() if feasible else (barrier_id,)),
        rationale=rationale,
    )


def _barrier_id(context, barrier):
    if barrier is None:
        return None
    expected = qualified_zone_id(context.timeframe, barrier.lifecycle)
    summaries = (context.nearest_support, context.nearest_resistance)
    if not any(
        summary is not None and summary.qualified_zone_id == expected
        for summary in summaries
    ):
        raise ValueError(
            "trade-plan barrier is absent from released timeframe context"
        )
    return expected


def _decision_change_conditions(decision):
    if decision.decision is TradeDecision.BUY:
        return (
            "Reassess if the daily bullish trigger invalidates or the "
            "structural stop is breached.",
        )
    mapping = {
        NoTradeReason.BEARISH_CONDITION: (
            "Wait for bearish structure to resolve and daily/weekly evidence "
            "to align bullish."
        ),
        NoTradeReason.NEUTRAL_CONDITION: (
            "Wait for a directional bullish profile on both timeframes."
        ),
        NoTradeReason.TIMEFRAME_CONFLICT: (
            "Wait for daily and weekly direction to align bullish."
        ),
        NoTradeReason.INSUFFICIENT_DATA: (
            "Refresh the dataset until both timeframe evaluations are complete."
        ),
        NoTradeReason.DAILY_TRIGGER_INCOMPLETE: (
            "Wait for the deterministic daily bullish trigger to confirm."
        ),
        NoTradeReason.BUY_SETUP_INVALIDATED: (
            "Wait for a new valid daily bullish setup after invalidation."
        ),
        NoTradeReason.ACTIVE_BEARISH_BREAK: (
            "Wait for the active bearish structure break to fail or reverse."
        ),
        NoTradeReason.STRUCTURAL_STOP_UNAVAILABLE: (
            "Wait for a confirmed structural invalidation level below entry."
        ),
        NoTradeReason.MINIMUM_TARGET_BLOCKED: (
            "Wait until resistance leaves room for at least a 1:2 target."
        ),
        NoTradeReason.INSUFFICIENT_REWARD_TO_RISK: (
            "Wait until the structure supports a minimum 1:2 reward-to-risk."
        ),
        NoTradeReason.JUDGE_REJECTED_BULLISH_CASE: (
            "Wait for evidence that resolves the Judge's bearish or neutral "
            "objections."
        ),
        NoTradeReason.STALE_DATA: "Refresh market data before reassessment.",
    }
    return tuple(mapping[reason] for reason in decision.no_trade_reasons)
