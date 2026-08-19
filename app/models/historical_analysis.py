from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import (
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalDirection,
    SwingTradingSignalProfile,
    SwingTradingStance,
)
from app.models.technical import TechnicalModel


class HistoricalAnalysisFailureKind(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    INDICATOR_CALCULATION = "indicator_calculation"
    TECHNICAL_ANALYSIS = "technical_analysis"


def _validate_index(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


class HistoricalSwingProfilePoint(TechnicalModel):
    candle_index: int
    available_candle_count: int
    candle: Candle
    profile: SwingTradingSignalProfile

    @field_validator("candle_index", mode="before")
    @classmethod
    def require_valid_index(cls, value: int) -> int:
        return _validate_index(value, name="historical point index")

    @field_validator("available_candle_count", mode="before")
    @classmethod
    def require_positive_count(cls, value: int) -> int:
        normalized = _validate_index(
            value,
            name="historical point candle count",
        )
        if normalized < 1:
            raise ValueError(
                "historical point candle count must be at least one"
            )
        return normalized

    @model_validator(mode="after")
    def validate_point(self) -> Self:
        if self.available_candle_count != self.candle_index + 1:
            raise ValueError(
                "historical point candle count must match its index"
            )
        if self.profile.snapshot.evaluated_at != self.candle.timestamp:
            raise ValueError(
                "historical profile must be evaluated at its candle"
            )
        return self

    @computed_field
    @property
    def evaluated_at(self) -> datetime:
        return self.candle.timestamp

    @computed_field
    @property
    def close(self) -> float:
        return self.candle.close

    @computed_field
    @property
    def stance(self) -> SwingTradingStance:
        return self.profile.stance

    @computed_field
    @property
    def direction(self) -> SignalDirection:
        return self.profile.direction

    @computed_field
    @property
    def score(self) -> float:
        return self.profile.score

    @computed_field
    @property
    def confidence_percentage(self) -> float:
        return self.profile.confidence_percentage

    @computed_field
    @property
    def coverage_percentage(self) -> float:
        return self.profile.coverage_percentage

    @computed_field
    @property
    def agreement_percentage(self) -> float:
        return self.profile.agreement_percentage

    @computed_field
    @property
    def category_scores(self) -> dict[str, float]:
        return dict(self.profile.category_scores)

    @computed_field
    @property
    def evidence_ids(self) -> list[str]:
        return [
            evidence.evidence_id
            for evidence in self.profile.snapshot.evidence
        ]


class HistoricalAnalysisFailure(TechnicalModel):
    candle_index: int
    available_candle_count: int
    candle: Candle
    kind: HistoricalAnalysisFailureKind
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)

    @field_validator("candle_index", mode="before")
    @classmethod
    def require_valid_index(cls, value: int) -> int:
        return _validate_index(value, name="historical failure index")

    @field_validator("available_candle_count", mode="before")
    @classmethod
    def require_positive_count(cls, value: int) -> int:
        normalized = _validate_index(
            value,
            name="historical failure candle count",
        )
        if normalized < 1:
            raise ValueError(
                "historical failure candle count must be at least one"
            )
        return normalized

    @field_validator("error_type", "message")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("historical failure text cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_failure(self) -> Self:
        if self.available_candle_count != self.candle_index + 1:
            raise ValueError(
                "historical failure candle count must match its index"
            )
        return self

    @computed_field
    @property
    def evaluated_at(self) -> datetime:
        return self.candle.timestamp

    @computed_field
    @property
    def close(self) -> float:
        return self.candle.close


class HistoricalSwingProfileSeries(TechnicalModel):
    analysis_version: str = Field(min_length=1)
    evaluator_name: str = Field(min_length=1)
    market_series: HistoricalCandleSeries
    warmup_candles: int = Field(ge=0)
    evaluation_stride: int = Field(ge=1)
    start_at: datetime | None = None
    end_at: datetime | None = None
    points: list[HistoricalSwingProfilePoint] = Field(
        default_factory=list
    )
    failures: list[HistoricalAnalysisFailure] = Field(
        default_factory=list
    )

    @field_validator("analysis_version", "evaluator_name")
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "historical analysis metadata cannot be blank"
            )
        return value

    @field_validator("warmup_candles", mode="before")
    @classmethod
    def require_valid_warmup(cls, value: int) -> int:
        return _validate_index(value, name="historical warmup")

    @field_validator("evaluation_stride", mode="before")
    @classmethod
    def require_valid_stride(cls, value: int) -> int:
        normalized = _validate_index(
            value,
            name="historical evaluation stride",
        )
        if normalized < 1:
            raise ValueError(
                "historical evaluation stride must be at least one"
            )
        return normalized

    @field_validator("start_at", "end_at")
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "historical analysis bounds must include timezone "
                "information"
            )
        return value

    @model_validator(mode="after")
    def validate_series(self) -> Self:
        self._validate_time_bounds()
        self._validate_market_candles()
        self._validate_attempt_records()
        self._validate_profile_configuration()
        return self

    def _validate_time_bounds(self) -> None:
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.start_at > self.end_at
        ):
            raise ValueError(
                "historical analysis start cannot follow its end"
            )
        if (
            self.start_at is not None
            and self.start_at > self.market_series.retrieved_at
        ) or (
            self.end_at is not None
            and self.end_at > self.market_series.retrieved_at
        ):
            raise ValueError(
                "historical analysis bounds cannot follow source "
                "retrieval"
            )

    def _validate_market_candles(self) -> None:
        previous_timestamp: datetime | None = None
        for candle in self.market_series.candles:
            if (
                previous_timestamp is not None
                and candle.timestamp <= previous_timestamp
            ):
                raise ValueError(
                    "historical analysis candles must have unique "
                    "timestamps in ascending order"
                )
            if candle.timestamp > self.market_series.retrieved_at:
                raise ValueError(
                    "historical analysis cannot use candles after source "
                    "retrieval"
                )
            previous_timestamp = candle.timestamp

    def _validate_attempt_records(self) -> None:
        expected_indices = self._expected_indices()
        records = [
            (point.candle_index, point, "point")
            for point in self.points
        ] + [
            (failure.candle_index, failure, "failure")
            for failure in self.failures
        ]
        actual_indices = sorted(index for index, _, _ in records)
        if actual_indices != expected_indices:
            raise ValueError(
                "historical analysis requires one point or failure for "
                "each scheduled evaluation"
            )

        if [point.candle_index for point in self.points] != sorted(
            point.candle_index for point in self.points
        ):
            raise ValueError(
                "historical profile points must be in ascending order"
            )
        if [failure.candle_index for failure in self.failures] != sorted(
            failure.candle_index for failure in self.failures
        ):
            raise ValueError(
                "historical failures must be in ascending order"
            )

        snapshot_identity = (
            self.market_series.exchange,
            self.market_series.symbol_token,
            self.market_series.symbol,
            self.market_series.interval,
            self.market_series.source,
            self.market_series.retrieved_at,
        )
        for candle_index, record, record_type in records:
            expected_candle = self.market_series.candles[candle_index]
            if record.candle != expected_candle:
                raise ValueError(
                    f"historical {record_type} candle must match market "
                    "history"
                )
            if record_type == "point":
                profile_snapshot = record.profile.snapshot
                profile_identity = (
                    profile_snapshot.exchange,
                    profile_snapshot.symbol_token,
                    profile_snapshot.symbol,
                    profile_snapshot.interval,
                    profile_snapshot.source,
                    profile_snapshot.source_retrieved_at,
                )
                if profile_identity != snapshot_identity:
                    raise ValueError(
                        "historical profile and market series must "
                        "describe the same source"
                    )

    def _validate_profile_configuration(self) -> None:
        configurations = {
            (
                point.profile.profile_id,
                point.profile.minimum_coverage_percentage,
                point.profile.directional_threshold,
                point.profile.strong_threshold,
                point.profile.synchronized_evidence_required,
                tuple(sorted(point.profile.category_weights.items())),
            )
            for point in self.points
        }
        if len(configurations) > 1:
            raise ValueError(
                "historical profiles must use one fixed configuration"
            )

    def _expected_indices(self) -> list[int]:
        eligible = [
            index
            for index, candle in enumerate(self.market_series.candles)
            if index >= self.warmup_candles
            and (self.start_at is None or candle.timestamp >= self.start_at)
            and (self.end_at is None or candle.timestamp <= self.end_at)
        ]
        return eligible[::self.evaluation_stride]

    @computed_field
    @property
    def attempted_evaluation_count(self) -> int:
        return len(self.points) + len(self.failures)

    @computed_field
    @property
    def successful_evaluation_count(self) -> int:
        return len(self.points)

    @computed_field
    @property
    def failed_evaluation_count(self) -> int:
        return len(self.failures)

    @computed_field
    @property
    def success_rate_percentage(self) -> float:
        if self.attempted_evaluation_count == 0:
            return 0.0
        return (
            self.successful_evaluation_count
            / self.attempted_evaluation_count
            * 100
        )

    @computed_field
    @property
    def stance_counts(self) -> dict[str, int]:
        counts = {stance.value: 0 for stance in SwingTradingStance}
        for point in self.points:
            counts[point.stance.value] += 1
        return counts
