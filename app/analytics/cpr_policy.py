from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from app.models.analysis_timeframe import SwingAnalysisTimeframe
from app.models.cpr import (
    CPRAnalysisRecord,
    CPRLifecycleState,
    CPRPricePosition,
    CPRWidthRegime,
)
from app.models.technical import TechnicalModel
from app.models.trade_decision import NoTradeReason


class CPRDirectionalBias(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    INSUFFICIENT = "insufficient"


class CPRTimeframeRelationship(StrEnum):
    ALIGNED_BULLISH = "aligned_bullish"
    ALIGNED_BEARISH = "aligned_bearish"
    MIXED = "mixed"
    CONFLICTED = "conflicted"
    INSUFFICIENT = "insufficient"


class CPRTradePolicyAssessment(TechnicalModel):
    """Deterministic CPR confirmation and long-side risk assessment."""

    model_config = ConfigDict(frozen=True, strict=True)

    daily_bias: CPRDirectionalBias
    weekly_bias: CPRDirectionalBias
    relationship: CPRTimeframeRelationship
    daily_acceptance_confirmed: bool
    weekly_acceptance_confirmed: bool
    daily_narrow_expansion: CPRDirectionalBias | None = None
    weekly_narrow_expansion: CPRDirectionalBias | None = None
    failed_break_risk: bool
    buy_eligible: bool
    blocking_reasons: tuple[NoTradeReason, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.buy_eligible == bool(self.blocking_reasons):
            raise ValueError(
                "CPR buy eligibility must be the inverse of its blockers"
            )
        if len(self.blocking_reasons) != len(set(self.blocking_reasons)):
            raise ValueError("CPR blocking reasons must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("CPR evidence IDs must be unique")
        if any(
            not value.startswith(("daily:cpr.", "weekly:cpr."))
            for value in self.evidence_ids
        ):
            raise ValueError("CPR policy evidence IDs must be qualified")
        for expansion in (
            self.daily_narrow_expansion,
            self.weekly_narrow_expansion,
        ):
            if expansion in (
                CPRDirectionalBias.NEUTRAL,
                CPRDirectionalBias.INSUFFICIENT,
            ):
                raise ValueError("narrow CPR expansion must be directional")
        return self


def evaluate_cpr_trade_policy(
    daily: CPRAnalysisRecord | None,
    weekly: CPRAnalysisRecord | None,
) -> CPRTradePolicyAssessment:
    """Evaluate CPR as confirmation/risk; never as a standalone BUY signal."""
    _validate_assignment(daily, SwingAnalysisTimeframe.DAILY)
    _validate_assignment(weekly, SwingAnalysisTimeframe.WEEKLY)

    daily_bias = _bias(daily)
    weekly_bias = _bias(weekly)
    relationship = _relationship(daily_bias, weekly_bias)
    daily_acceptance = _bullish_acceptance(daily)
    weekly_acceptance = _bullish_acceptance(weekly)
    daily_expansion = _narrow_expansion(daily)
    weekly_expansion = _narrow_expansion(weekly)
    failed_break_risk = any(
        record is not None
        and record.lifecycle_state
        is CPRLifecycleState.FAILED_BULLISH_BREAKOUT
        for record in (daily, weekly)
    )

    blockers: list[NoTradeReason] = []
    if daily is None or weekly is None:
        blockers.append(NoTradeReason.INSUFFICIENT_DATA)
    if relationship is CPRTimeframeRelationship.CONFLICTED:
        blockers.append(NoTradeReason.CPR_TIMEFRAME_CONFLICT)
    if CPRDirectionalBias.BEARISH in (daily_bias, weekly_bias):
        blockers.append(NoTradeReason.CPR_BEARISH_RISK)
    if failed_break_risk:
        blockers.append(NoTradeReason.CPR_FAILED_BREAK_RISK)
    if (
        daily_bias is not CPRDirectionalBias.BEARISH
        and not daily_acceptance
    ):
        blockers.append(NoTradeReason.CPR_ACCEPTANCE_INCOMPLETE)
    blocking_reasons = tuple(dict.fromkeys(blockers))
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for record in (weekly, daily)
            if record is not None
            for evidence_id in record.evidence_ids
        )
    )
    return CPRTradePolicyAssessment(
        daily_bias=daily_bias,
        weekly_bias=weekly_bias,
        relationship=relationship,
        daily_acceptance_confirmed=daily_acceptance,
        weekly_acceptance_confirmed=weekly_acceptance,
        daily_narrow_expansion=daily_expansion,
        weekly_narrow_expansion=weekly_expansion,
        failed_break_risk=failed_break_risk,
        buy_eligible=not blocking_reasons,
        blocking_reasons=blocking_reasons,
        evidence_ids=evidence_ids,
        rationale=_rationale(
            daily_bias=daily_bias,
            weekly_bias=weekly_bias,
            relationship=relationship,
            daily_acceptance=daily_acceptance,
            weekly_acceptance=weekly_acceptance,
            daily_expansion=daily_expansion,
            weekly_expansion=weekly_expansion,
            failed_break_risk=failed_break_risk,
            blockers=blocking_reasons,
        ),
    )


def _validate_assignment(
    record: CPRAnalysisRecord | None,
    expected: SwingAnalysisTimeframe,
) -> None:
    if record is not None and record.timeframe is not expected:
        raise ValueError(
            f"CPR policy requires {expected.value} evidence in its "
            f"{expected.value} slot"
        )


def _bias(record: CPRAnalysisRecord | None) -> CPRDirectionalBias:
    if record is None:
        return CPRDirectionalBias.INSUFFICIENT
    bullish = {
        CPRLifecycleState.BULLISH_BREAKOUT,
        CPRLifecycleState.BULLISH_RECLAIM,
        CPRLifecycleState.BULLISH_RETEST,
        CPRLifecycleState.ACCEPTED_ABOVE,
        CPRLifecycleState.LOWER_REJECTION,
        CPRLifecycleState.FAILED_BEARISH_BREAKDOWN,
    }
    bearish = {
        CPRLifecycleState.BEARISH_BREAKDOWN,
        CPRLifecycleState.BEARISH_RECLAIM,
        CPRLifecycleState.BEARISH_RETEST,
        CPRLifecycleState.ACCEPTED_BELOW,
        CPRLifecycleState.UPPER_REJECTION,
        CPRLifecycleState.FAILED_BULLISH_BREAKOUT,
    }
    if record.lifecycle_state in bullish:
        return CPRDirectionalBias.BULLISH
    if record.lifecycle_state in bearish:
        return CPRDirectionalBias.BEARISH
    return CPRDirectionalBias.NEUTRAL


def _bullish_acceptance(record: CPRAnalysisRecord | None) -> bool:
    return bool(
        record is not None
        and record.price_position is CPRPricePosition.ABOVE
        and record.consecutive_acceptance_candles >= 2
        and record.lifecycle_state
        in {
            CPRLifecycleState.ACCEPTED_ABOVE,
            CPRLifecycleState.BULLISH_RETEST,
        }
    )


def _bearish_acceptance(record: CPRAnalysisRecord | None) -> bool:
    return bool(
        record is not None
        and record.price_position is CPRPricePosition.BELOW
        and record.consecutive_acceptance_candles >= 2
        and record.lifecycle_state
        in {
            CPRLifecycleState.ACCEPTED_BELOW,
            CPRLifecycleState.BEARISH_RETEST,
        }
    )


def _narrow_expansion(
    record: CPRAnalysisRecord | None,
) -> CPRDirectionalBias | None:
    if record is None or record.width_regime is not CPRWidthRegime.NARROW:
        return None
    if _bullish_acceptance(record):
        return CPRDirectionalBias.BULLISH
    if _bearish_acceptance(record):
        return CPRDirectionalBias.BEARISH
    return None


def _relationship(
    daily: CPRDirectionalBias,
    weekly: CPRDirectionalBias,
) -> CPRTimeframeRelationship:
    if CPRDirectionalBias.INSUFFICIENT in (daily, weekly):
        return CPRTimeframeRelationship.INSUFFICIENT
    if daily is weekly is CPRDirectionalBias.BULLISH:
        return CPRTimeframeRelationship.ALIGNED_BULLISH
    if daily is weekly is CPRDirectionalBias.BEARISH:
        return CPRTimeframeRelationship.ALIGNED_BEARISH
    if {daily, weekly} == {
        CPRDirectionalBias.BULLISH,
        CPRDirectionalBias.BEARISH,
    }:
        return CPRTimeframeRelationship.CONFLICTED
    return CPRTimeframeRelationship.MIXED


def _rationale(**values) -> str:
    blockers = values["blockers"]
    blocker_text = (
        "none"
        if not blockers
        else ", ".join(reason.value for reason in blockers)
    )
    return (
        "CPR policy: daily bias="
        f"{values['daily_bias'].value}, weekly bias="
        f"{values['weekly_bias'].value}, relationship="
        f"{values['relationship'].value}, daily acceptance="
        f"{values['daily_acceptance']}, weekly acceptance="
        f"{values['weekly_acceptance']}, daily narrow expansion="
        f"{getattr(values['daily_expansion'], 'value', None)}, weekly narrow "
        f"expansion={getattr(values['weekly_expansion'], 'value', None)}, "
        f"failed bullish-break risk={values['failed_break_risk']}; "
        f"blockers={blocker_text}. CPR confirms or blocks a setup but never "
        "creates a BUY independently."
    )
