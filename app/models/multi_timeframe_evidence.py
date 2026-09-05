from datetime import datetime
from hashlib import sha256
from math import isclose, isfinite
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.agentic import (
    JarvisJudgeDecision,
    TechnicalSwingAgentSubmission,
)
from app.models.accumulation import TimeframeAccumulationAnalysis
from app.models.analysis_timeframe import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)
from app.models.cpr import CPRAnalysisRecord
from app.models.market import HistoricalCandleSeries
from app.models.price_action import (
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceLifecycle,
    SwingPivot,
    SwingPivotType,
)
from app.models.signals import TechnicalSignalEvidence
from app.models.technical import TechnicalModel
from app.models.timeframes import MultiTimeframeTechnicalAnalysis


_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
MULTI_TIMEFRAME_RELEASE_CHECKS = frozenset(
    {
        "multi_timeframe_package_schema_valid",
        "exact_multi_timeframe_assignment",
        "parallel_timeframe_execution_complete",
        "daily_technical_submission_approved",
        "weekly_technical_submission_approved",
        "daily_and_weekly_accumulation_verified",
        "daily_and_weekly_cpr_verified",
        "complete_daily_and_weekly_evidence",
        "timeframe_evidence_ids_disjoint",
        "timeframe_lineage_verified",
        "package_fingerprint_verified",
        "point_in_time_structure_verified",
        "evidence_preserved_without_reinterpretation",
    }
)


class QualifiedTechnicalEvidence(TechnicalModel):
    """One unchanged signal with an unambiguous timeframe identity."""

    model_config = ConfigDict(frozen=True, strict=True)

    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    qualified_evidence_id: str = Field(
        min_length=1,
        pattern=r"^(daily|weekly):[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    evidence: TechnicalSignalEvidence

    @model_validator(mode="after")
    def validate_qualification(self) -> Self:
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("qualified evidence interval is incorrect")
        expected_id = f"{self.timeframe.value}:{self.evidence.evidence_id}"
        if self.qualified_evidence_id != expected_id:
            raise ValueError(
                "qualified evidence id must preserve timeframe and source id"
            )
        return self


class ConfirmedPivotSummary(TechnicalModel):
    """A look-ahead-safe pivot exposed with its confirmation timestamp."""

    model_config = ConfigDict(frozen=True, strict=True)

    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    qualified_pivot_id: str = Field(
        min_length=1,
        pattern=r"^(daily|weekly):pivot:[a-f0-9]{64}$",
    )
    pivot: SwingPivot

    @model_validator(mode="after")
    def validate_qualification(self) -> Self:
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("confirmed pivot interval is incorrect")
        expected_id = qualified_pivot_id(self.timeframe, self.pivot)
        if self.qualified_pivot_id != expected_id:
            raise ValueError("confirmed pivot id does not match its pivot")
        return self


class NearestPriceZoneSummary(TechnicalModel):
    """Nearest confirmed support or resistance usable at evaluation time."""

    model_config = ConfigDict(frozen=True, strict=True)

    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    qualified_zone_id: str = Field(
        min_length=1,
        pattern=r"^(daily|weekly):zone:[a-f0-9]{64}$",
    )
    effective_zone_type: PriceZoneType
    current_close: float = Field(gt=0)
    boundary_price: float = Field(ge=0)
    distance_percentage: float = Field(ge=0)
    lifecycle: SupportResistanceLifecycle

    @field_validator(
        "current_close",
        "boundary_price",
        "distance_percentage",
    )
    @classmethod
    def require_finite_number(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("nearest-zone values must be finite")
        return value

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("nearest-zone interval is incorrect")
        expected_id = qualified_zone_id(self.timeframe, self.lifecycle)
        if self.qualified_zone_id != expected_id:
            raise ValueError("nearest-zone id does not match its lifecycle")

        expected_type = effective_zone_type(self.lifecycle)
        if expected_type is None:
            raise ValueError(
                "nearest zones must be active, failed, or role-reversed"
            )
        if self.effective_zone_type is not expected_type:
            raise ValueError("nearest-zone effective type is incorrect")

        zone = self.lifecycle.zone
        expected_boundary = (
            zone.upper_price
            if expected_type is PriceZoneType.SUPPORT
            else zone.lower_price
        )
        if not isclose(self.boundary_price, expected_boundary):
            raise ValueError("nearest-zone boundary is incorrect")
        if (
            expected_type is PriceZoneType.SUPPORT
            and zone.lower_price > self.current_close
        ):
            raise ValueError("support zone cannot be wholly above close")
        if (
            expected_type is PriceZoneType.RESISTANCE
            and zone.upper_price < self.current_close
        ):
            raise ValueError("resistance zone cannot be wholly below close")

        expected_distance = _zone_distance_percentage(
            self.lifecycle,
            self.current_close,
        )
        if not isclose(self.distance_percentage, expected_distance):
            raise ValueError("nearest-zone distance is incorrect")
        return self


class TimeframeTechnicalEvidenceContext(TechnicalModel):
    """Debate-ready technical evidence and structural context."""

    model_config = ConfigDict(frozen=True, strict=True)

    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    evaluated_at: datetime
    current_close: float = Field(gt=0)
    accumulation: TimeframeAccumulationAnalysis
    cpr: CPRAnalysisRecord | None = None
    evidence: tuple[QualifiedTechnicalEvidence, ...] = Field(min_length=1)
    recent_confirmed_pivots: tuple[ConfirmedPivotSummary, ...] = ()
    latest_confirmed_high: ConfirmedPivotSummary | None = None
    latest_confirmed_low: ConfirmedPivotSummary | None = None
    nearest_support: NearestPriceZoneSummary | None = None
    nearest_resistance: NearestPriceZoneSummary | None = None

    @field_validator("evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timeframe context must include timezone")
        return value

    @field_validator("current_close")
    @classmethod
    def require_finite_close(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("timeframe current close must be finite")
        return value

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("timeframe context interval is incorrect")
        if (
            self.accumulation.timeframe is not self.timeframe
            or self.accumulation.interval != self.interval
            or self.accumulation.evaluated_at != self.evaluated_at
        ):
            raise ValueError(
                "accumulation analysis must match its timeframe context"
            )
        if self.cpr is not None:
            if (
                self.cpr.timeframe is not self.timeframe
                or self.cpr.interval != self.interval
                or self.cpr.evaluated_at != self.evaluated_at
                or not isclose(self.cpr.current_price, self.current_close)
            ):
                raise ValueError(
                    "CPR analysis must match its timeframe context"
                )
        evidence_ids = [item.qualified_evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("timeframe evidence ids must be unique")

        for item in self.evidence:
            self._require_assignment(item.timeframe, item.interval)
            if item.evidence.available_at > self.evaluated_at:
                raise ValueError("timeframe context cannot use future evidence")
        for item in self.recent_confirmed_pivots:
            self._require_assignment(item.timeframe, item.interval)
            if item.pivot.confirmed_at > self.evaluated_at:
                raise ValueError("timeframe context cannot use future pivots")
        pivot_ids = {
            item.qualified_pivot_id
            for item in self.recent_confirmed_pivots
        }
        if len(pivot_ids) != len(self.recent_confirmed_pivots):
            raise ValueError("recent confirmed pivots must be unique")

        self._validate_latest(self.latest_confirmed_high, SwingPivotType.HIGH)
        self._validate_latest(self.latest_confirmed_low, SwingPivotType.LOW)
        self._validate_zone(self.nearest_support, PriceZoneType.SUPPORT)
        self._validate_zone(
            self.nearest_resistance,
            PriceZoneType.RESISTANCE,
        )
        return self

    def _require_assignment(
        self,
        timeframe: SwingAnalysisTimeframe,
        interval: str,
    ) -> None:
        if timeframe is not self.timeframe or interval != self.interval:
            raise ValueError("context item belongs to another timeframe")

    def _validate_latest(
        self,
        summary: ConfirmedPivotSummary | None,
        expected_type: SwingPivotType,
    ) -> None:
        if summary is None:
            return
        self._require_assignment(summary.timeframe, summary.interval)
        if summary.pivot.pivot_type is not expected_type:
            raise ValueError("latest pivot has the incorrect pivot type")
        if summary.pivot.confirmed_at > self.evaluated_at:
            raise ValueError("latest pivot cannot be confirmed in the future")

    def _validate_zone(
        self,
        summary: NearestPriceZoneSummary | None,
        expected_type: PriceZoneType,
    ) -> None:
        if summary is None:
            return
        self._require_assignment(summary.timeframe, summary.interval)
        if summary.effective_zone_type is not expected_type:
            raise ValueError("nearest zone has the incorrect effective type")
        if not isclose(summary.current_close, self.current_close):
            raise ValueError("nearest zone must use context current close")


class MultiTimeframeEvidencePackage(TechnicalModel):
    """Validated daily/weekly package ready for the existing Judge."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.multi_timeframe_evidence.v1"] = (
        "jarvis.multi_timeframe_evidence.v1"
    )
    technical_analysis: MultiTimeframeTechnicalAnalysis
    daily: TimeframeTechnicalEvidenceContext
    weekly: TimeframeTechnicalEvidenceContext
    package_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def validate_package(self) -> Self:
        if self.daily.timeframe is not SwingAnalysisTimeframe.DAILY:
            raise ValueError("daily context must be assigned to daily")
        if self.weekly.timeframe is not SwingAnalysisTimeframe.WEEKLY:
            raise ValueError("weekly context must be assigned to weekly")
        self._validate_source_evidence(
            self.daily,
            self.technical_analysis.daily_submission.profile.snapshot.evidence,
        )
        self._validate_source_evidence(
            self.weekly,
            self.technical_analysis.weekly_submission.profile.snapshot.evidence,
        )
        if (
            self.daily.accumulation
            != self.technical_analysis.daily_accumulation
            or self.weekly.accumulation
            != self.technical_analysis.weekly_accumulation
        ):
            raise ValueError(
                "timeframe contexts must preserve the assigned accumulation "
                "evidence"
            )
        self._validate_context_identity(
            self.daily,
            self.technical_analysis.daily_submission,
            self.technical_analysis.timeframes.daily,
        )
        self._validate_context_identity(
            self.weekly,
            self.technical_analysis.weekly_submission,
            self.technical_analysis.timeframes.weekly,
        )
        all_ids = [
            item.qualified_evidence_id
            for context in (self.daily, self.weekly)
            for item in context.evidence
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("qualified evidence ids must be globally unique")
        expected = multi_timeframe_evidence_fingerprint(
            self.technical_analysis,
            self.daily,
            self.weekly,
        )
        if self.package_fingerprint != expected:
            raise ValueError("multi-timeframe evidence fingerprint is invalid")
        return self

    @staticmethod
    def _validate_source_evidence(
        context: TimeframeTechnicalEvidenceContext,
        source: list[TechnicalSignalEvidence],
    ) -> None:
        if [item.evidence for item in context.evidence] != source:
            raise ValueError(
                "qualified evidence must preserve the validated submission"
            )

    @staticmethod
    def _validate_context_identity(
        context: TimeframeTechnicalEvidenceContext,
        submission: TechnicalSwingAgentSubmission,
        series: HistoricalCandleSeries,
    ) -> None:
        if context.interval != submission.interval:
            raise ValueError("context interval must match its submission")
        if context.evaluated_at != submission.evaluated_at:
            raise ValueError(
                "context evaluation time must match its submission"
            )
        accumulation = context.accumulation
        accumulation_identity = (
            accumulation.exchange,
            accumulation.symbol_token,
            accumulation.symbol,
            accumulation.interval,
            accumulation.source,
            accumulation.source_retrieved_at,
            accumulation.evaluated_at,
        )
        expected_accumulation_identity = (
            series.exchange,
            series.symbol_token,
            series.symbol,
            series.interval,
            series.source,
            series.retrieved_at,
            submission.evaluated_at,
        )
        if accumulation_identity != expected_accumulation_identity:
            raise ValueError(
                "context accumulation must match its assigned market series"
            )
        if context.cpr is not None:
            cpr_identity = (
                context.cpr.exchange,
                context.cpr.symbol_token,
                context.cpr.symbol,
                context.cpr.interval,
                context.cpr.source,
                context.cpr.source_retrieved_at,
            )
            expected_cpr_identity = (
                series.exchange,
                series.symbol_token,
                series.symbol,
                series.interval,
                series.source,
                series.retrieved_at,
            )
            if cpr_identity != expected_cpr_identity:
                raise ValueError(
                    "context CPR must match its assigned market series"
                )
        available = [
            candle
            for candle in series.candles
            if candle.timestamp <= context.evaluated_at
        ]
        if not available or not isclose(
            context.current_close,
            available[-1].close,
        ):
            raise ValueError("context close must match its market series")


class MultiTimeframeEvidenceReview(TechnicalModel):
    """The existing Judge's chain-of-custody decision for debate release."""

    model_config = ConfigDict(frozen=True, strict=True)

    orchestrator_id: Literal["jarvis.agent_orchestrator.v1"] = (
        "jarvis.agent_orchestrator.v1"
    )
    evidence_package: MultiTimeframeEvidencePackage
    decision: JarvisJudgeDecision

    @model_validator(mode="after")
    def validate_review_chain(self) -> Self:
        if self.decision.judge_id != "jarvis.swing_judge.v1":
            raise ValueError(
                "multi-timeframe release must use the existing swing Judge"
            )
        if (
            self.decision.submission_id
            != self.evidence_package.package_fingerprint
        ):
            raise ValueError(
                "multi-timeframe decision must reference its exact package"
            )
        latest_evaluation = max(
            self.evidence_package.daily.evaluated_at,
            self.evidence_package.weekly.evaluated_at,
        )
        if self.decision.decided_at < latest_evaluation:
            raise ValueError(
                "multi-timeframe decision cannot precede agent completion"
            )
        if self.decision.accepted and not (
            MULTI_TIMEFRAME_RELEASE_CHECKS
            <= set(self.decision.passed_checks)
        ):
            raise ValueError(
                "accepted multi-timeframe release is missing gate checks"
            )
        return self

    @property
    def released_evidence(self) -> MultiTimeframeEvidencePackage | None:
        """Expose the unchanged package only after Judge acceptance."""
        if not self.decision.accepted:
            return None
        return self.evidence_package


def effective_zone_type(
    lifecycle: SupportResistanceLifecycle,
) -> PriceZoneType | None:
    if lifecycle.status in (
        PriceZoneLifecycleStatus.ACTIVE,
        PriceZoneLifecycleStatus.FAILED_BREAK,
    ):
        return lifecycle.zone.zone_type
    if lifecycle.status is PriceZoneLifecycleStatus.ROLE_REVERSED:
        return lifecycle.reversed_zone_type
    return None


def qualified_pivot_id(
    timeframe: SwingAnalysisTimeframe,
    pivot: SwingPivot,
) -> str:
    digest = sha256(pivot.model_dump_json().encode("utf-8")).hexdigest()
    return f"{timeframe.value}:pivot:{digest}"


def qualified_zone_id(
    timeframe: SwingAnalysisTimeframe,
    lifecycle: SupportResistanceLifecycle,
) -> str:
    digest = sha256(lifecycle.model_dump_json().encode("utf-8")).hexdigest()
    return f"{timeframe.value}:zone:{digest}"


def multi_timeframe_evidence_fingerprint(
    analysis: MultiTimeframeTechnicalAnalysis,
    daily: TimeframeTechnicalEvidenceContext,
    weekly: TimeframeTechnicalEvidenceContext,
) -> str:
    payload = ":".join(
        (
            analysis.combined_fingerprint,
            daily.model_dump_json(),
            weekly.model_dump_json(),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _zone_distance_percentage(
    lifecycle: SupportResistanceLifecycle,
    current_close: float,
) -> float:
    zone = lifecycle.zone
    if current_close < zone.lower_price:
        distance = zone.lower_price - current_close
    elif current_close > zone.upper_price:
        distance = current_close - zone.upper_price
    else:
        distance = 0.0
    return distance / current_close * 100
