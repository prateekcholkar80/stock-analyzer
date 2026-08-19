from hashlib import sha256
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from app.agents.technical_swing_agent import market_series_fingerprint
from app.analytics.risk_reward import build_swing_trade_plan
from app.analytics.support_resistance import (
    detect_support_resistance_zones,
)
from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.analytics.swing_pivots import detect_swing_pivots
from app.exceptions import (
    AgentSubmissionRejectedError,
    InsufficientDataError,
)
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    TradePlanningAgentSubmission,
    TradePlanningDisposition,
    TradePlanningReason,
)
from app.models.market import HistoricalCandleSeries
from app.models.signals import SignalDirection
from app.models.technical import TechnicalModel
from app.models.trade_setup import SwingTradePlan, TradeSetupStatus


SUPPORT_RESISTANCE_SIGNAL_SOURCE = (
    "price_action_signals.support_resistance_lifecycle"
)


class TradePlanningAgentConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    planner_id: str = Field(
        default="jarvis.swing_trade_planner.v1",
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    structural_buffer_atr_multiplier: float = Field(
        default=0.25,
        ge=0,
    )
    minimum_buffer_percentage: float = Field(
        default=0.1,
        gt=0,
        le=100,
    )
    fallback_stop_atr_multiplier: float = Field(default=2.0, gt=0)
    minimum_reward_to_risk: float = Field(default=2.0, gt=0)
    preferred_reward_to_risk: float = Field(default=3.0, gt=0)

    @model_validator(mode="after")
    def validate_reward_multiples(self) -> Self:
        if self.preferred_reward_to_risk <= self.minimum_reward_to_risk:
            raise ValueError(
                "trade-planning preferred reward-to-risk must exceed "
                "the minimum"
            )
        return self


class TradePlanningAgent:
    """Turn a judge-approved technical profile into a trade intent."""

    agent_id = "jarvis.trade_planning_agent.v1"

    def __init__(
        self,
        config: TradePlanningAgentConfig | None = None,
    ) -> None:
        if config is not None and not isinstance(
            config,
            TradePlanningAgentConfig,
        ):
            raise ValueError(
                "trade-planning config must be a validated configuration"
            )
        self.config = config or TradePlanningAgentConfig()

    @property
    def planner_id(self) -> str:
        return self.config.planner_id

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.config.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def execute(
        self,
        technical_result: AgenticSwingAnalysisResult,
        market_series: HistoricalCandleSeries,
    ) -> TradePlanningAgentSubmission:
        technical_result = _validate_technical_assignment(
            technical_result,
            market_series,
        )
        profile = technical_result.submission.profile

        if profile.direction is SignalDirection.NEUTRAL:
            return self._submission(
                technical_result,
                market_series,
                disposition=TradePlanningDisposition.NO_TRADE,
                reason=TradePlanningReason.NON_DIRECTIONAL_PROFILE,
                plan=None,
                rationale=(
                    "No trade was proposed because the approved technical "
                    "profile is non-directional."
                ),
            )

        zone_lifecycles = _rebuild_zone_lifecycles(
            profile,
            market_series,
        )
        try:
            plan = build_swing_trade_plan(
                profile,
                market_series,
                zone_lifecycles,
                structural_buffer_atr_multiplier=(
                    self.config.structural_buffer_atr_multiplier
                ),
                minimum_buffer_percentage=(
                    self.config.minimum_buffer_percentage
                ),
                fallback_stop_atr_multiplier=(
                    self.config.fallback_stop_atr_multiplier
                ),
                minimum_reward_to_risk=(
                    self.config.minimum_reward_to_risk
                ),
                preferred_reward_to_risk=(
                    self.config.preferred_reward_to_risk
                ),
            )
        except InsufficientDataError as error:
            return self._submission(
                technical_result,
                market_series,
                disposition=TradePlanningDisposition.NO_TRADE,
                reason=TradePlanningReason.INSUFFICIENT_STOP_EVIDENCE,
                plan=None,
                rationale=(
                    "No trade was proposed because an evidence-grounded "
                    f"stop could not be selected: {error}"
                ),
            )

        if plan.evaluation.status is TradeSetupStatus.REJECTED:
            return self._submission(
                technical_result,
                market_series,
                disposition=TradePlanningDisposition.NO_TRADE,
                reason=TradePlanningReason.MINIMUM_TARGET_BLOCKED,
                plan=plan,
                rationale=(
                    "No trade was proposed because confirmed structure "
                    "blocks the minimum reward target."
                ),
            )

        return self._submission(
            technical_result,
            market_series,
            disposition=TradePlanningDisposition.ACTIONABLE,
            reason=TradePlanningReason.DIRECTIONAL_SETUP,
            plan=plan,
            rationale=(
                "A directional trade intent was proposed with an "
                "evidence-grounded stop and feasible minimum reward "
                "target."
            ),
        )

    def _submission(
        self,
        technical_result: AgenticSwingAnalysisResult,
        market_series: HistoricalCandleSeries,
        *,
        disposition: TradePlanningDisposition,
        reason: TradePlanningReason,
        plan: SwingTradePlan | None,
        rationale: str,
    ) -> TradePlanningAgentSubmission:
        technical_submission = technical_result.submission
        submission_id = (
            f"{self.agent_id}:{technical_submission.submission_id}:"
            f"{self.configuration_fingerprint}"
        )
        return TradePlanningAgentSubmission(
            submission_id=submission_id,
            agent_id=self.agent_id,
            planner_id=self.planner_id,
            configuration_fingerprint=self.configuration_fingerprint,
            market_fingerprint=market_series_fingerprint(market_series),
            technical_submission_id=(
                technical_submission.submission_id
            ),
            technical_decision_id=technical_result.decision.decision_id,
            evaluated_at=technical_submission.evaluated_at,
            disposition=disposition,
            reason=reason,
            profile=technical_submission.profile,
            plan=plan,
            rationale=rationale,
        )


def _validate_technical_assignment(
    technical_result: AgenticSwingAnalysisResult,
    market_series: HistoricalCandleSeries,
) -> AgenticSwingAnalysisResult:
    if not isinstance(technical_result, AgenticSwingAnalysisResult):
        raise ValueError(
            "trade-planning agent requires an agentic swing result"
        )
    technical_result = AgenticSwingAnalysisResult.model_validate(
        technical_result.model_dump(exclude_computed_fields=True)
    )
    if not technical_result.decision.accepted:
        raise AgentSubmissionRejectedError(
            "trade-planning agent requires a Jarvis-approved technical "
            "submission"
        )
    if (
        technical_result.submission.input_fingerprint
        != market_series_fingerprint(market_series)
    ):
        raise AgentSubmissionRejectedError(
            "trade-planning market prefix does not match the approved "
            "technical submission"
        )
    return technical_result


def _rebuild_zone_lifecycles(profile, market_series):
    matches = [
        evidence
        for evidence in profile.snapshot.evidence
        if evidence.source == SUPPORT_RESISTANCE_SIGNAL_SOURCE
    ]
    if len(matches) != 1:
        raise ValueError(
            "trade planning requires one support-resistance evidence item"
        )
    parameters = matches[0].parameters
    left_strength = _integer_parameter(
        parameters,
        "pivot_left_strength",
        minimum=1,
    )
    right_strength = _integer_parameter(
        parameters,
        "pivot_right_strength",
        minimum=1,
    )
    minimum_touches = _integer_parameter(
        parameters,
        "minimum_touches",
        minimum=2,
    )
    tolerance = _number_parameter(
        parameters,
        "zone_tolerance_percentage",
        minimum=0.0,
    )
    evaluated_at = profile.snapshot.evaluated_at
    pivots = detect_swing_pivots(
        market_series,
        left_strength=left_strength,
        right_strength=right_strength,
    )
    zones = detect_support_resistance_zones(
        pivots,
        tolerance_percentage=tolerance,
        minimum_touches=minimum_touches,
        as_of=evaluated_at,
    )
    return track_support_resistance_lifecycle(
        market_series,
        zones,
        as_of=evaluated_at,
    )


def _integer_parameter(
    parameters: dict,
    name: str,
    *,
    minimum: int,
) -> int:
    value = parameters.get(name)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise ValueError(
            f"support-resistance {name} must be an integer of at least "
            f"{minimum}"
        )
    return value


def _number_parameter(
    parameters: dict,
    name: str,
    *,
    minimum: float,
) -> float:
    value = parameters.get(name)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or float(value) < minimum
    ):
        raise ValueError(
            f"support-resistance {name} must be at least {minimum:g}"
        )
    return float(value)
