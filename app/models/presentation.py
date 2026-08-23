from datetime import datetime
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    SwingTradingStance,
)
from app.models.multi_timeframe_trade import (
    MultiTimeframeTradeDisposition,
    MultiTimeframeTradeReason,
)
from app.models.multi_timeframe_evidence import SwingAnalysisTimeframe
from app.models.technical import TechnicalModel
from app.models.trade_setup import (
    StopLossMethod,
    TradeDirection,
    TradeEntryMethod,
    TradeSetupStatus,
    TradeTargetFeasibility,
)


class JarvisTechnicalExplanation(TechnicalModel):
    """Plain-language explanation of exactly one deterministic signal."""

    model_config = ConfigDict(frozen=True, strict=True)

    evidence_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: SignalCategory
    direction: SignalDirection
    strength: SignalStrength
    fact_explanation: str = Field(min_length=1)
    inference: str = Field(min_length=1)

    @field_validator("evidence_id", "name", "fact_explanation", "inference")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Jarvis technical explanation cannot be blank")
        return normalized


class JarvisCaseExplanation(TechnicalModel):
    """Faithful explanation of one side of the completed debate."""

    model_config = ConfigDict(frozen=True, strict=True)

    summary: str = Field(min_length=1)
    argument_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Jarvis case summary cannot be blank")
        return normalized

    @field_validator("argument_ids", "evidence_ids")
    @classmethod
    def require_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("Jarvis case references cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("Jarvis case references must be unique")
        return values


class JarvisResearchExplanation(TechnicalModel):
    """Evidence-locked CEO briefing generated after the debate Judge."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.research_explanation.v1"] = (
        "jarvis.research_explanation.v1"
    )
    persona_id: Literal[
        "jarvis.chief_investment_research_assistant.v1"
    ] = "jarvis.chief_investment_research_assistant.v1"
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    technical_stance: SwingTradingStance
    technical_score: float = Field(ge=-100, le=100)
    verdict_id: str = Field(min_length=1)
    judge_winner: SignalDirection
    judge_confidence_percentage: float = Field(ge=0, le=100)
    decisive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    executive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    executive_briefing: str = Field(min_length=1)
    judge_conclusion_explanation: str = Field(min_length=1)
    technical_findings: tuple[JarvisTechnicalExplanation, ...] = Field(
        min_length=1
    )
    bull_case: JarvisCaseExplanation
    bear_case: JarvisCaseExplanation
    limitations: tuple[str, ...] = Field(min_length=1)
    disclaimer: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    generated_at: datetime

    @field_validator(
        "symbol",
        "interval",
        "verdict_id",
        "executive_briefing",
        "judge_conclusion_explanation",
        "disclaimer",
        "provider",
        "model_id",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Jarvis presentation text cannot be blank")
        return normalized

    @field_validator("decisive_evidence_ids", "executive_evidence_ids")
    @classmethod
    def require_unique_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence references cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("evidence references must be unique")
        return values

    @field_validator("limitations")
    @classmethod
    def normalize_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("Jarvis limitations cannot be blank")
        return normalized

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("presentation timestamp must include timezone")
        return value

    @model_validator(mode="after")
    def require_unique_technical_findings(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.technical_findings]
        available = set(evidence_ids)
        if len(evidence_ids) != len(available):
            raise ValueError(
                "Jarvis technical findings require one item per evidence id"
            )
        referenced = (
            set(self.decisive_evidence_ids)
            | set(self.executive_evidence_ids)
            | set(self.bull_case.evidence_ids)
            | set(self.bear_case.evidence_ids)
        )
        if not referenced.issubset(available):
            raise ValueError(
                "Jarvis explanation can reference only included evidence"
            )
        return self


class JarvisTradePlanExplanation(TechnicalModel):
    """Exact deterministic trade fields attached to Jarvis's prose."""

    model_config = ConfigDict(frozen=True, strict=True)

    disposition: MultiTimeframeTradeDisposition
    reason: MultiTimeframeTradeReason
    rationale: str = Field(min_length=1)
    direction: TradeDirection | None = None
    entry_method: TradeEntryMethod | None = None
    reference_entry_price: float | None = Field(default=None, gt=0)
    stop_loss_method: StopLossMethod | None = None
    stop_loss_price: float | None = Field(default=None, gt=0)
    risk_per_unit: float | None = Field(default=None, gt=0)
    minimum_reward_to_risk: float = Field(default=2.0, gt=0)
    minimum_target_price: float | None = Field(default=None, gt=0)
    minimum_target_feasibility: TradeTargetFeasibility | None = None
    preferred_reward_to_risk: float | None = Field(default=None, gt=0)
    preferred_target_price: float | None = Field(default=None, gt=0)
    preferred_target_feasibility: TradeTargetFeasibility | None = None
    execution_note: str = Field(min_length=1)

    @field_validator("rationale", "execution_note")
    @classmethod
    def normalize_plan_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Jarvis trade-plan explanation cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_plan_fields(self) -> Self:
        plan_fields = (
            self.direction,
            self.entry_method,
            self.reference_entry_price,
            self.stop_loss_method,
            self.stop_loss_price,
            self.risk_per_unit,
            self.minimum_target_price,
            self.minimum_target_feasibility,
            self.preferred_reward_to_risk,
            self.preferred_target_price,
            self.preferred_target_feasibility,
        )
        if self.disposition is MultiTimeframeTradeDisposition.ACTIONABLE:
            if any(value is None for value in plan_fields):
                raise ValueError("actionable briefing requires complete plan fields")
            if self.direction is not TradeDirection.LONG:
                raise ValueError("Jarvis multi-timeframe plans must be long-only")
        elif any(value is not None for value in plan_fields):
            raise ValueError("no-trade briefing cannot expose trade levels")
        return self


class JarvisMultiTimeframeFinding(TechnicalModel):
    """Plain-language explanation of one qualified signal, pivot, or zone."""

    model_config = ConfigDict(frozen=True, strict=True)

    evidence_id: str = Field(min_length=1)
    timeframe: SwingAnalysisTimeframe
    finding_type: Literal["signal", "pivot", "zone"]
    name: str = Field(min_length=1)
    fact_explanation: str = Field(min_length=1)
    inference: str = Field(min_length=1)

    @field_validator(
        "evidence_id",
        "name",
        "fact_explanation",
        "inference",
    )
    @classmethod
    def normalize_finding_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("multi-timeframe finding cannot be blank")
        return normalized


class JarvisMultiTimeframeResearchExplanation(TechnicalModel):
    """Evidence-locked CEO briefing for daily/weekly research."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.multi_timeframe_explanation.v1"] = (
        "jarvis.multi_timeframe_explanation.v1"
    )
    persona_id: Literal[
        "jarvis.chief_investment_research_assistant.v1"
    ] = "jarvis.chief_investment_research_assistant.v1"
    symbol: str = Field(min_length=1)
    source_interval: Literal["ONE_HOUR"] = "ONE_HOUR"
    daily_technical_stance: SwingTradingStance
    daily_technical_score: float = Field(ge=-100, le=100)
    weekly_technical_stance: SwingTradingStance
    weekly_technical_score: float = Field(ge=-100, le=100)
    verdict_id: str = Field(min_length=1)
    judge_winner: SignalDirection
    judge_confidence_percentage: float = Field(ge=0, le=100)
    decisive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    executive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    executive_briefing: str = Field(min_length=1)
    weekly_analysis: str = Field(min_length=1)
    weekly_analysis_evidence_ids: tuple[str, ...] = Field(min_length=1)
    daily_analysis: str = Field(min_length=1)
    daily_analysis_evidence_ids: tuple[str, ...] = Field(min_length=1)
    judge_conclusion_explanation: str = Field(min_length=1)
    technical_findings: tuple[JarvisMultiTimeframeFinding, ...] = Field(
        min_length=1
    )
    bull_case: JarvisCaseExplanation
    bear_case: JarvisCaseExplanation
    trade_plan: JarvisTradePlanExplanation
    limitations: tuple[str, ...] = Field(min_length=1)
    disclaimer: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    generated_at: datetime

    @field_validator(
        "symbol",
        "verdict_id",
        "executive_briefing",
        "weekly_analysis",
        "daily_analysis",
        "judge_conclusion_explanation",
        "disclaimer",
        "provider",
        "model_id",
    )
    @classmethod
    def normalize_multi_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("multi-timeframe presentation text cannot be blank")
        return normalized

    @field_validator(
        "decisive_evidence_ids",
        "executive_evidence_ids",
        "weekly_analysis_evidence_ids",
        "daily_analysis_evidence_ids",
    )
    @classmethod
    def require_unique_multi_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("multi-timeframe evidence references cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("multi-timeframe evidence references must be unique")
        return values

    @field_validator("limitations")
    @classmethod
    def normalize_multi_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("Jarvis limitations cannot be blank")
        return normalized

    @field_validator("generated_at")
    @classmethod
    def require_multi_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("presentation timestamp must include timezone")
        return value

    @model_validator(mode="after")
    def require_grounded_references(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.technical_findings]
        available = set(evidence_ids)
        if len(evidence_ids) != len(available):
            raise ValueError("multi-timeframe findings must be unique")
        referenced = (
            set(self.decisive_evidence_ids)
            | set(self.executive_evidence_ids)
            | set(self.weekly_analysis_evidence_ids)
            | set(self.daily_analysis_evidence_ids)
            | set(self.bull_case.evidence_ids)
            | set(self.bear_case.evidence_ids)
        )
        if not referenced.issubset(available):
            raise ValueError("multi-timeframe explanation cited unknown evidence")
        return self
