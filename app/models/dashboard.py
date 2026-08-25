from datetime import datetime
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.multi_timeframe_evidence import SwingAnalysisTimeframe
from app.models.presentation import (
    JarvisMultiTimeframeResearchExplanation,
    JarvisResearchExplanation,
)
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    SignalValue,
    SwingTradingStance,
)
from app.models.technical_setup import (
    SwingSetupSide,
    SwingSetupStepId,
    SwingSetupStepState,
)
from app.models.technical import TechnicalModel
from app.models.timeframe_interpretation import (
    RewardRiskFeasibility,
    StructuralRisk,
    TacticalReadiness,
    TimeframeAlignment,
)
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
)
from app.models.workflow import (
    WorkflowEventState,
    WorkflowParticipantKind,
    WorkflowStage,
)


class DashboardCandle(TechnicalModel):
    """Chart-ready OHLCV point copied from a validated market series."""

    model_config = ConfigDict(frozen=True, strict=True)

    timestamp: datetime
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: int = Field(ge=0)


class DashboardQuote(TechnicalModel):
    """A broker quote kept separate from completed analysis candles."""

    model_config = ConfigDict(frozen=True, strict=True)

    price: float = Field(ge=0)
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    previous_close: float = Field(ge=0)
    observed_at: datetime
    source: str = Field(min_length=1)


class DashboardRefreshGap(TechnicalModel):
    """One definite missing intraday interval reported by market refresh."""

    model_config = ConfigDict(frozen=True, strict=True)

    interval: str = Field(min_length=1)
    gap_after: datetime
    resumes_at: datetime
    cadence_minutes: int = Field(ge=1)
    missing_candle_count: int = Field(ge=1)


class DashboardRefreshProvenance(TechnicalModel):
    """UI-safe provenance for the historical dataset used by one operation."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.dashboard_refresh.v1"] = (
        "jarvis.dashboard_refresh.v1"
    )
    mode: Literal["initial", "incremental", "unchanged"]
    dataset_id: str = Field(min_length=1)
    adapter_name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    requested_from: datetime | None = None
    requested_to: datetime | None = None
    resumed_from: datetime | None = None
    checked_at: datetime | None = None
    stored_from: datetime
    stored_to: datetime
    existing_candle_count: int = Field(ge=0)
    final_candle_count: int = Field(ge=1)
    new_candle_count: int = Field(ge=0)
    corrected_candle_count: int = Field(ge=0)
    deduplicated_fetched_candle_count: int = Field(ge=0)
    chunk_request_count: int = Field(ge=0)
    reused_existing_dataset: bool
    intraday_gaps: tuple[DashboardRefreshGap, ...] = ()

    @field_validator(
        "source_retrieved_at",
        "requested_from",
        "requested_to",
        "resumed_from",
        "checked_at",
        "stored_from",
        "stored_to",
    )
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("dashboard refresh timestamps require timezone")
        return value

    @model_validator(mode="after")
    def validate_refresh_provenance(self) -> Self:
        if self.stored_from > self.stored_to:
            raise ValueError("stored market range is reversed")
        if (
            self.requested_from is not None
            and self.requested_to is not None
            and self.requested_from > self.requested_to
        ):
            raise ValueError("requested market range is reversed")
        if (
            self.existing_candle_count + self.new_candle_count
            != self.final_candle_count
        ):
            raise ValueError(
                "refresh candle counts do not reconcile with final dataset"
            )
        if self.mode == "initial":
            if self.resumed_from is not None or self.reused_existing_dataset:
                raise ValueError(
                    "initial refresh cannot resume or reuse an existing dataset"
                )
        elif self.mode == "incremental":
            if self.resumed_from is None or self.reused_existing_dataset:
                raise ValueError(
                    "incremental refresh must resume without reusing unchanged data"
                )
        elif (
            self.resumed_from is None
            or not self.reused_existing_dataset
            or self.new_candle_count != 0
            or self.corrected_candle_count != 0
        ):
            raise ValueError(
                "unchanged refresh must reuse a resumed dataset without changes"
            )
        return self


class DashboardIndicatorPoint(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    timestamp: datetime
    value: float


class DashboardIndicatorSeries(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    indicator_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    pane: Literal["price", "rsi"]
    points: tuple[DashboardIndicatorPoint, ...]


class DashboardCandlestickPattern(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    pattern: str = Field(min_length=1)
    label: str = Field(min_length=1)
    direction: Literal["bullish", "bearish", "neutral"]
    timestamp: datetime
    price: float = Field(ge=0)
    raw_value: float


class DashboardPatternDefinition(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    pattern: str = Field(min_length=1)
    label: str = Field(min_length=1)


class DashboardCentralPivotRange(TechnicalModel):
    """One future-valid CPR band derived from a completed prior period."""

    model_config = ConfigDict(frozen=True, strict=True)

    basis: Literal["weekly", "monthly"]
    source_period_started_at: datetime
    source_period_ended_at: datetime
    valid_from: datetime
    valid_to: datetime
    pivot: float = Field(ge=0)
    bottom_central: float = Field(ge=0)
    top_central: float = Field(ge=0)
    width_percentage: float = Field(ge=0)


class DashboardFairValueGap(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    direction: Literal["bullish", "bearish"]
    detected_at: datetime
    ends_at: datetime
    lower_price: float = Field(ge=0)
    upper_price: float = Field(ge=0)
    status: str = Field(min_length=1)
    fill_percentage: float = Field(ge=0, le=100)


class DashboardStructurePoint(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    classification: str = Field(min_length=1)
    pivot_type: Literal["high", "low"]
    price: float = Field(ge=0)
    pivot_at: datetime
    confirmed_at: datetime


class DashboardChartPivot(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    pivot_type: Literal["high", "low"]
    price: float = Field(ge=0)
    pivot_at: datetime
    confirmed_at: datetime


class DashboardStructureBreak(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    break_type: str = Field(min_length=1)
    direction: Literal["bullish", "bearish"]
    occurred_at: datetime
    close_price: float = Field(ge=0)
    broken_pivot_price: float = Field(ge=0)


class DashboardChartZone(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    zone_id: str = Field(min_length=1)
    original_type: Literal["support", "resistance"]
    effective_type: Literal["support", "resistance"] | None = None
    lower_price: float = Field(ge=0)
    upper_price: float = Field(ge=0)
    touch_count: int = Field(ge=2)
    lifecycle_status: str = Field(min_length=1)
    confirmed_at: datetime
    broken_at: datetime | None = None
    retested_at: datetime | None = None
    reversal_confirmed_at: datetime | None = None
    failed_at: datetime | None = None
    immediate: bool = False


class DashboardAccumulationZone(TechnicalModel):
    """Chart-safe geometry for one released accumulation lifecycle."""

    model_config = ConfigDict(frozen=True, strict=True)

    zone_id: str = Field(min_length=1)
    lower_price: float = Field(gt=0)
    upper_price: float = Field(gt=0)
    base_started_at: datetime
    base_last_observed_at: datetime
    ends_at: datetime
    lifecycle_state: str = Field(min_length=1)
    active: bool
    confidence_percentage: float = Field(ge=0, le=100)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_accumulation_geometry(self) -> Self:
        if self.upper_price <= self.lower_price:
            raise ValueError(
                "dashboard accumulation upper price must exceed lower price"
            )
        if not (
            self.base_started_at
            <= self.base_last_observed_at
            <= self.ends_at
        ):
            raise ValueError(
                "dashboard accumulation timestamps must be chronological"
            )
        return self


class DashboardLiquiditySweep(TechnicalModel):
    """Chart marker for a released close-confirmed liquidity sweep."""

    model_config = ConfigDict(frozen=True, strict=True)

    sweep_id: str = Field(min_length=1)
    zone_id: str = Field(min_length=1)
    liquidity_side: Literal["sell_side", "buy_side"]
    implication: Literal["bullish", "bearish"]
    reference_price: float = Field(gt=0)
    extreme_price: float = Field(gt=0)
    reclaim_close_price: float = Field(gt=0)
    swept_at: datetime
    reclaimed_at: datetime
    available_at: datetime
    volume_multiple: float | None = Field(default=None, ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sweep_timeline(self) -> Self:
        if not self.swept_at <= self.reclaimed_at <= self.available_at:
            raise ValueError(
                "dashboard liquidity-sweep timestamps must be chronological"
            )
        return self


class DashboardTechnicalChart(TechnicalModel):
    """Deterministic chart overlays calculated from the assigned series."""

    model_config = ConfigDict(frozen=True, strict=True)

    indicators: tuple[DashboardIndicatorSeries, ...]
    pattern_catalog: tuple[DashboardPatternDefinition, ...] = ()
    candlestick_patterns: tuple[DashboardCandlestickPattern, ...] = ()
    central_pivot_ranges: tuple[DashboardCentralPivotRange, ...] = ()
    fair_value_gaps: tuple[DashboardFairValueGap, ...] = ()
    zones: tuple[DashboardChartZone, ...] = ()
    pivots: tuple[DashboardChartPivot, ...] = ()
    structure_points: tuple[DashboardStructurePoint, ...] = ()
    structure_breaks: tuple[DashboardStructureBreak, ...] = ()
    accumulation_zones: tuple[DashboardAccumulationZone, ...] = ()
    liquidity_sweeps: tuple[DashboardLiquiditySweep, ...] = ()


class DashboardEvidence(TechnicalModel):
    """One immutable technical finding prepared for a UI evidence drawer."""

    model_config = ConfigDict(frozen=True, strict=True)

    evidence_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: SignalCategory
    direction: SignalDirection
    strength: SignalStrength
    explanation: str = Field(min_length=1)
    observed_at: datetime
    available_at: datetime
    observed_values: dict[str, SignalValue]
    parameters: dict[str, SignalValue]
    decisive: bool = False


class DashboardPivot(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    pivot_id: str = Field(min_length=1)
    pivot_type: Literal["high", "low"]
    price: float = Field(ge=0)
    pivot_at: datetime
    confirmed_at: datetime


class DashboardPriceZone(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    zone_id: str = Field(min_length=1)
    zone_type: Literal["support", "resistance"]
    lower_price: float = Field(ge=0)
    upper_price: float = Field(ge=0)
    boundary_price: float = Field(ge=0)
    distance_percentage: float = Field(ge=0)
    lifecycle_status: str = Field(min_length=1)
    confirmed_at: datetime


class DashboardTimeframePanel(TechnicalModel):
    """Daily or weekly analyst card plus its chart overlays."""

    model_config = ConfigDict(frozen=True, strict=True)

    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    evaluated_at: datetime
    current_close: float = Field(gt=0)
    stance: SwingTradingStance
    score: float = Field(ge=-100, le=100)
    confidence_percentage: float = Field(ge=0, le=100)
    coverage_percentage: float = Field(ge=0, le=100)
    rationale: str = Field(min_length=1)
    source_candle_count: int = Field(ge=1)
    candles: tuple[DashboardCandle, ...] = Field(min_length=1)
    chart: DashboardTechnicalChart
    evidence: tuple[DashboardEvidence, ...] = Field(min_length=1)
    pivots: tuple[DashboardPivot, ...] = ()
    nearest_support: DashboardPriceZone | None = None
    nearest_resistance: DashboardPriceZone | None = None


class DashboardDebateArgument(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    argument_id: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    evidence_citations: tuple[str, ...] = Field(min_length=1)
    rebuts_argument_id: str | None = None
    model_id: str = Field(min_length=1)
    generated_at: datetime
    timeframe_relationship: str | None = None


class DashboardDebateRound(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    round_number: int = Field(ge=1)
    bull: DashboardDebateArgument
    bear: DashboardDebateArgument


class DashboardDebatePanel(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    rounds: tuple[DashboardDebateRound, ...] = Field(min_length=1)
    termination_reason: str = Field(min_length=1)
    verdict_id: str = Field(min_length=1)
    winner: SignalDirection
    confidence_percentage: float = Field(ge=0, le=100)
    decisive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    bull_case_summary: str = Field(min_length=1)
    bear_case_summary: str = Field(min_length=1)
    judge_rationale: str = Field(min_length=1)
    judge_model_id: str = Field(min_length=1)
    generated_at: datetime


class DashboardTradePlan(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    disposition: Literal["actionable", "no_trade"]
    reason: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    minimum_reward_to_risk: Literal[2.0] = 2.0
    direction: Literal["long"] | None = None
    status: str | None = None
    entry_price: float | None = Field(default=None, gt=0)
    stop_loss_price: float | None = Field(default=None, gt=0)
    risk_per_unit: float | None = Field(default=None, gt=0)
    target_2r_price: float | None = Field(default=None, gt=0)
    target_2r_feasibility: str | None = None
    target_3r_price: float | None = Field(default=None, gt=0)
    target_3r_feasibility: str | None = None
    maximum_structural_reward_to_risk: float | None = Field(
        default=None,
        ge=0,
    )


class DashboardSetupStep(TechnicalModel):
    """UI-safe projection of one deterministic setup checkpoint."""

    model_config = ConfigDict(frozen=True, strict=True)

    step_id: SwingSetupStepId
    label: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    state: SwingSetupStepState
    evidence_ids: tuple[str, ...] = ()
    observed_at: datetime | None = None
    available_at: datetime | None = None
    confirmed_at: datetime | None = None
    invalidated_at: datetime | None = None
    explanation: str = Field(min_length=1)
    observed_values: dict[str, SignalValue] = Field(default_factory=dict)
    thresholds: dict[str, SignalValue] = Field(default_factory=dict)


class DashboardSwingSetup(TechnicalModel):
    """Complete ordered bullish or bearish checklist for one timeframe."""

    model_config = ConfigDict(frozen=True, strict=True)

    setup_id: str = Field(min_length=1)
    side: SwingSetupSide
    steps: tuple[DashboardSetupStep, ...] = Field(min_length=8, max_length=9)


class DashboardTimeframeInterpretation(TechnicalModel):
    """Strategic or tactical interpretation linked to released evidence."""

    model_config = ConfigDict(frozen=True, strict=True)

    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    evaluated_at: datetime
    market_condition: MarketCondition
    bullish_setup: DashboardSwingSetup
    bearish_setup: DashboardSwingSetup
    decisive_evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)


class DashboardRewardRiskTarget(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    reward_to_risk: Literal[2.0, 3.0]
    feasibility: RewardRiskFeasibility
    target_price: float | None = Field(default=None, gt=0)
    blocking_evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)


class DashboardRiskRewardInterpretation(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    reference_entry: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    risk_per_unit: float | None = Field(default=None, gt=0)
    target_2r: DashboardRewardRiskTarget
    target_3r: DashboardRewardRiskTarget


class DashboardTradeDecision(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    market_condition: MarketCondition
    decision: TradeDecision
    no_trade_reasons: tuple[NoTradeReason, ...] = ()
    rationale: str = Field(min_length=1)


class DashboardMultiTimeframeInterpretation(TechnicalModel):
    """Additive v1 dashboard contract for deterministic swing conclusions."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.dashboard_interpretation.v1"] = (
        "jarvis.dashboard_interpretation.v1"
    )
    daily: DashboardTimeframeInterpretation
    weekly: DashboardTimeframeInterpretation
    alignment: TimeframeAlignment
    tactical_readiness: TacticalReadiness
    structural_risk: StructuralRisk
    risk_reward: DashboardRiskRewardInterpretation
    trade_decision: DashboardTradeDecision
    decisive_evidence_ids: tuple[str, ...] = ()
    decision_change_conditions: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    interpreted_at: datetime


class DashboardActivity(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    stage: WorkflowStage
    state: WorkflowEventState
    occurred_at: datetime
    message: str = Field(min_length=1)
    participant_id: str = Field(min_length=1)
    participant_kind: WorkflowParticipantKind
    participant_label: str = Field(min_length=1)
    timeframe: str | None = None
    round_number: int | None = Field(default=None, ge=1)


class JarvisDashboardView(TechnicalModel):
    """Stable, evidence-preserving read model for the Jarvis browser UI."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.dashboard.v1"] = "jarvis.dashboard.v1"
    operation_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    completed_at: datetime
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    source_interval: Literal["ONE_HOUR"] = "ONE_HOUR"
    source_retrieved_at: datetime
    latest_quote: DashboardQuote | None = None
    refresh: DashboardRefreshProvenance
    package_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    daily: DashboardTimeframePanel
    weekly: DashboardTimeframePanel
    debate: DashboardDebatePanel
    trade_plan: DashboardTradePlan
    interpretation: DashboardMultiTimeframeInterpretation | None = None
    activities: tuple[DashboardActivity, ...] = ()
    presentation: (
        JarvisResearchExplanation
        | JarvisMultiTimeframeResearchExplanation
        | None
    ) = None
