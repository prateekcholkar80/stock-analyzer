from datetime import datetime

from app.analytics.support_resistance import (
    detect_support_resistance_zones,
)
from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.analytics.swing_pivots import detect_swing_pivots
from app.models.agentic import TechnicalSwingAgentSubmission
from app.models.market import HistoricalCandleSeries
from app.models.multi_timeframe_evidence import (
    ConfirmedPivotSummary,
    MultiTimeframeEvidencePackage,
    NearestPriceZoneSummary,
    QualifiedTechnicalEvidence,
    SwingAnalysisTimeframe,
    TimeframeTechnicalEvidenceContext,
    effective_zone_type,
    multi_timeframe_evidence_fingerprint,
    qualified_pivot_id,
    qualified_zone_id,
)
from app.models.price_action import (
    PriceZoneType,
    SupportResistanceLifecycle,
    SwingPivot,
    SwingPivotType,
)
from app.models.signals import TechnicalSignalEvidence
from app.models.timeframes import MultiTimeframeTechnicalAnalysis


_SUPPORT_RESISTANCE_SOURCE = (
    "price_action_signals.support_resistance_lifecycle"
)


class BuildMultiTimeframeEvidence:
    """Package validated daily/weekly evidence without changing conclusions."""

    def __init__(self, *, recent_pivot_limit: int = 6) -> None:
        if (
            isinstance(recent_pivot_limit, bool)
            or not isinstance(recent_pivot_limit, int)
            or recent_pivot_limit < 1
        ):
            raise ValueError("recent pivot limit must be a positive integer")
        self.recent_pivot_limit = recent_pivot_limit

    def execute(
        self,
        analysis: MultiTimeframeTechnicalAnalysis,
    ) -> MultiTimeframeEvidencePackage:
        if not isinstance(analysis, MultiTimeframeTechnicalAnalysis):
            raise ValueError(
                "evidence packaging requires validated multi-timeframe analysis"
            )

        daily = self._build_context(
            SwingAnalysisTimeframe.DAILY,
            analysis.timeframes.daily,
            analysis.daily_submission,
        )
        weekly = self._build_context(
            SwingAnalysisTimeframe.WEEKLY,
            analysis.timeframes.weekly,
            analysis.weekly_submission,
        )
        return MultiTimeframeEvidencePackage(
            technical_analysis=analysis,
            daily=daily,
            weekly=weekly,
            package_fingerprint=multi_timeframe_evidence_fingerprint(
                analysis,
                daily,
                weekly,
            ),
        )

    def _build_context(
        self,
        timeframe: SwingAnalysisTimeframe,
        series: HistoricalCandleSeries,
        submission: TechnicalSwingAgentSubmission,
    ) -> TimeframeTechnicalEvidenceContext:
        evaluated_at = submission.evaluated_at
        available_candles = [
            candle
            for candle in series.candles
            if candle.timestamp <= evaluated_at
        ]
        if not available_candles:
            raise ValueError("timeframe context requires an available candle")
        current_close = available_candles[-1].close
        if current_close <= 0:
            raise ValueError("timeframe context requires a positive close")

        source_evidence = submission.profile.snapshot.evidence
        lifecycle_evidence = self._lifecycle_evidence(source_evidence)
        left_strength = self._integer_parameter(
            lifecycle_evidence,
            "pivot_left_strength",
        )
        right_strength = self._integer_parameter(
            lifecycle_evidence,
            "pivot_right_strength",
        )
        minimum_touches = self._integer_parameter(
            lifecycle_evidence,
            "minimum_touches",
        )
        tolerance = self._number_parameter(
            lifecycle_evidence,
            "zone_tolerance_percentage",
        )

        pivots = detect_swing_pivots(
            series,
            left_strength=left_strength,
            right_strength=right_strength,
        )
        confirmed = [
            pivot
            for pivot in pivots.pivots
            if pivot.confirmed_at <= evaluated_at
        ]
        zones = detect_support_resistance_zones(
            pivots,
            tolerance_percentage=tolerance,
            minimum_touches=minimum_touches,
            as_of=evaluated_at,
        )
        lifecycles = track_support_resistance_lifecycle(
            series,
            zones,
            as_of=evaluated_at,
        )

        return TimeframeTechnicalEvidenceContext(
            timeframe=timeframe,
            interval=series.interval,
            evaluated_at=evaluated_at,
            current_close=current_close,
            evidence=tuple(
                QualifiedTechnicalEvidence(
                    timeframe=timeframe,
                    interval=series.interval,
                    qualified_evidence_id=(
                        f"{timeframe.value}:{evidence.evidence_id}"
                    ),
                    evidence=evidence,
                )
                for evidence in source_evidence
            ),
            recent_confirmed_pivots=tuple(
                self._pivot_summary(timeframe, series.interval, pivot)
                for pivot in confirmed[-self.recent_pivot_limit :]
            ),
            latest_confirmed_high=self._latest_pivot(
                timeframe,
                series.interval,
                confirmed,
                SwingPivotType.HIGH,
            ),
            latest_confirmed_low=self._latest_pivot(
                timeframe,
                series.interval,
                confirmed,
                SwingPivotType.LOW,
            ),
            nearest_support=self._nearest_zone(
                timeframe,
                series.interval,
                lifecycles.lifecycles,
                current_close,
                PriceZoneType.SUPPORT,
            ),
            nearest_resistance=self._nearest_zone(
                timeframe,
                series.interval,
                lifecycles.lifecycles,
                current_close,
                PriceZoneType.RESISTANCE,
            ),
        )

    @staticmethod
    def _lifecycle_evidence(
        evidence: list[TechnicalSignalEvidence],
    ) -> TechnicalSignalEvidence:
        matches = [
            item
            for item in evidence
            if item.source == _SUPPORT_RESISTANCE_SOURCE
        ]
        if len(matches) != 1:
            raise ValueError(
                "validated submission must contain one lifecycle evidence item"
            )
        return matches[0]

    @staticmethod
    def _integer_parameter(
        evidence: TechnicalSignalEvidence,
        name: str,
    ) -> int:
        value = evidence.parameters.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"lifecycle parameter {name} must be an integer")
        return value

    @staticmethod
    def _number_parameter(
        evidence: TechnicalSignalEvidence,
        name: str,
    ) -> float:
        value = evidence.parameters.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"lifecycle parameter {name} must be numeric")
        return float(value)

    @staticmethod
    def _pivot_summary(
        timeframe: SwingAnalysisTimeframe,
        interval: str,
        pivot: SwingPivot,
    ) -> ConfirmedPivotSummary:
        return ConfirmedPivotSummary(
            timeframe=timeframe,
            interval=interval,
            qualified_pivot_id=qualified_pivot_id(timeframe, pivot),
            pivot=pivot,
        )

    def _latest_pivot(
        self,
        timeframe: SwingAnalysisTimeframe,
        interval: str,
        pivots: list[SwingPivot],
        pivot_type: SwingPivotType,
    ) -> ConfirmedPivotSummary | None:
        matches = [
            pivot for pivot in pivots if pivot.pivot_type is pivot_type
        ]
        if not matches:
            return None
        return self._pivot_summary(timeframe, interval, matches[-1])

    @staticmethod
    def _nearest_zone(
        timeframe: SwingAnalysisTimeframe,
        interval: str,
        lifecycles: list[SupportResistanceLifecycle],
        current_close: float,
        requested_type: PriceZoneType,
    ) -> NearestPriceZoneSummary | None:
        candidates = []
        for lifecycle in lifecycles:
            effective_type = effective_zone_type(lifecycle)
            if effective_type is not requested_type:
                continue
            zone = lifecycle.zone
            if (
                requested_type is PriceZoneType.SUPPORT
                and zone.lower_price > current_close
            ) or (
                requested_type is PriceZoneType.RESISTANCE
                and zone.upper_price < current_close
            ):
                continue
            distance = _zone_distance_percentage(lifecycle, current_close)
            candidates.append((distance, lifecycle))

        if not candidates:
            return None
        distance, selected = min(
            candidates,
            key=lambda item: (
                item[0],
                -_lifecycle_event_time(item[1]).timestamp(),
                -item[1].zone.touch_count,
                item[1].zone.lower_price,
            ),
        )
        boundary = (
            selected.zone.upper_price
            if requested_type is PriceZoneType.SUPPORT
            else selected.zone.lower_price
        )
        return NearestPriceZoneSummary(
            timeframe=timeframe,
            interval=interval,
            qualified_zone_id=qualified_zone_id(timeframe, selected),
            effective_zone_type=requested_type,
            current_close=current_close,
            boundary_price=boundary,
            distance_percentage=distance,
            lifecycle=selected,
        )


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


def _lifecycle_event_time(
    lifecycle: SupportResistanceLifecycle,
) -> datetime:
    return (
        lifecycle.failed_at
        or lifecycle.reversal_confirmed_at
        or lifecycle.retested_at
        or lifecycle.broken_at
        or lifecycle.zone.confirmed_at
    )
