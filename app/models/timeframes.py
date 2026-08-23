from hashlib import sha256
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.models.market import HistoricalCandleSeries
from app.models.agentic import TechnicalSwingAgentSubmission
from app.models.technical import TechnicalModel


_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


def market_series_fingerprint(series: HistoricalCandleSeries) -> str:
    """Return a stable digest for an exact validated OHLCV series."""
    if not isinstance(series, HistoricalCandleSeries):
        raise ValueError("market fingerprint requires a validated series")
    return sha256(series.model_dump_json().encode("utf-8")).hexdigest()


class SwingTimeframeLineage(TechnicalModel):
    """Immutable derivation trail from hourly source to swing timeframes."""

    model_config = ConfigDict(frozen=True, strict=True)

    source_interval: Literal["ONE_HOUR"] = "ONE_HOUR"
    daily_interval: Literal["ONE_DAY"] = "ONE_DAY"
    weekly_interval: Literal["ONE_WEEK"] = "ONE_WEEK"
    aggregation_path: tuple[Literal["ONE_HOUR", "ONE_DAY", "ONE_WEEK"], ...] = (
        "ONE_HOUR",
        "ONE_DAY",
        "ONE_WEEK",
    )
    completed_buckets_only: Literal[True] = True
    hourly_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    daily_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    weekly_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def require_exact_cascade(self) -> Self:
        if self.aggregation_path != (
            "ONE_HOUR",
            "ONE_DAY",
            "ONE_WEEK",
        ):
            raise ValueError(
                "swing timeframe lineage must follow hourly, daily, weekly"
            )
        return self


class SwingTimeframeSeries(TechnicalModel):
    """One hourly source and its completed daily and weekly derivatives."""

    model_config = ConfigDict(frozen=True, strict=True)

    hourly: HistoricalCandleSeries
    daily: HistoricalCandleSeries
    weekly: HistoricalCandleSeries
    lineage: SwingTimeframeLineage

    @model_validator(mode="after")
    def validate_identity_and_lineage(self) -> Self:
        if self.hourly.interval != "ONE_HOUR":
            raise ValueError("swing timeframe source must be ONE_HOUR")
        if self.daily.interval != "ONE_DAY":
            raise ValueError("daily derivative must be ONE_DAY")
        if self.weekly.interval != "ONE_WEEK":
            raise ValueError("weekly derivative must be ONE_WEEK")

        expected_identity = _series_identity(self.hourly)
        if _series_identity(self.daily) != expected_identity:
            raise ValueError("daily derivative must match hourly identity")
        if _series_identity(self.weekly) != expected_identity:
            raise ValueError("weekly derivative must match hourly identity")

        expected_fingerprints = (
            market_series_fingerprint(self.hourly),
            market_series_fingerprint(self.daily),
            market_series_fingerprint(self.weekly),
        )
        actual_fingerprints = (
            self.lineage.hourly_fingerprint,
            self.lineage.daily_fingerprint,
            self.lineage.weekly_fingerprint,
        )
        if actual_fingerprints != expected_fingerprints:
            raise ValueError(
                "swing timeframe fingerprints must match their series"
            )
        return self


class TechnicalSubmissionValidationReceipt(TechnicalModel):
    """Non-opinionated proof that one technical submission passed checks."""

    model_config = ConfigDict(frozen=True, strict=True)

    validation_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    validator_id: Literal["jarvis.technical_submission_guard.v1"] = (
        "jarvis.technical_submission_guard.v1"
    )
    submission_id: str = Field(min_length=1)
    accepted: Literal[True] = True
    passed_checks: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_checks(self) -> Self:
        if any(not item.strip() for item in self.passed_checks):
            raise ValueError("technical validation checks cannot be blank")
        if len(self.passed_checks) != len(set(self.passed_checks)):
            raise ValueError("technical validation checks must be unique")
        return self


class MultiTimeframeTechnicalAnalysis(TechnicalModel):
    """Concurrent daily/weekly submissions awaiting the debate workflow."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.multi_timeframe_technical.v1"] = (
        "jarvis.multi_timeframe_technical.v1"
    )
    orchestrator_id: Literal[
        "jarvis.parallel_timeframe_technical_orchestrator.v1"
    ] = "jarvis.parallel_timeframe_technical_orchestrator.v1"
    execution_mode: Literal["parallel"] = "parallel"
    timeframes: SwingTimeframeSeries
    daily_submission: TechnicalSwingAgentSubmission
    weekly_submission: TechnicalSwingAgentSubmission
    daily_validation: TechnicalSubmissionValidationReceipt
    weekly_validation: TechnicalSubmissionValidationReceipt
    combined_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def validate_parallel_assignments(self) -> Self:
        if self.daily_submission.interval != "ONE_DAY":
            raise ValueError("daily technical submission must be ONE_DAY")
        if self.weekly_submission.interval != "ONE_WEEK":
            raise ValueError("weekly technical submission must be ONE_WEEK")
        if self.daily_submission.agent_id == self.weekly_submission.agent_id:
            raise ValueError("daily and weekly agents must have distinct ids")
        if (
            self.daily_validation.submission_id
            != self.daily_submission.submission_id
            or self.weekly_validation.submission_id
            != self.weekly_submission.submission_id
        ):
            raise ValueError(
                "technical validation must reference its assigned submission"
            )
        if self.daily_submission.input_fingerprint != (
            self.timeframes.lineage.daily_fingerprint
        ):
            raise ValueError("daily submission must match daily lineage")
        if self.weekly_submission.input_fingerprint != (
            self.timeframes.lineage.weekly_fingerprint
        ):
            raise ValueError("weekly submission must match weekly lineage")
        expected = multi_timeframe_technical_fingerprint(
            self.timeframes,
            self.daily_submission,
            self.weekly_submission,
        )
        if self.combined_fingerprint != expected:
            raise ValueError(
                "combined technical fingerprint must match both submissions"
            )
        return self


def multi_timeframe_technical_fingerprint(
    timeframes: SwingTimeframeSeries,
    daily: TechnicalSwingAgentSubmission,
    weekly: TechnicalSwingAgentSubmission,
) -> str:
    payload = ":".join(
        (
            timeframes.lineage.hourly_fingerprint,
            timeframes.lineage.daily_fingerprint,
            timeframes.lineage.weekly_fingerprint,
            daily.model_dump_json(),
            weekly.model_dump_json(),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _series_identity(series: HistoricalCandleSeries) -> tuple[object, ...]:
    return (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.source,
        series.retrieved_at,
    )
