"""Immutable provider-neutral contracts for normalized financial evidence."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_package import (
    FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
    FundamentalPackageItemKey,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
    FundamentalNormalizationMethod,
    FundamentalValueKind,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_METRIC_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_SEMVER_PATTERN = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"
_REQUIRED_SOURCE_KEYS = frozenset(
    item.canonical_key for item in FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS
)


def _source_aggregate_fingerprint(
    *,
    manifest_fingerprint: str,
    sources: tuple["FundamentalMetricSourceBinding", ...],
) -> str:
    payload = {
        "schema_version": "jarvis.fundamental_evidence_package.v1",
        "manifest_fingerprint": manifest_fingerprint,
        "artifacts": sorted(
            (
                {
                    "key": source.package_item_key.canonical_key,
                    "cache_entry_id": source.source_reference_id,
                    "content_fingerprint": source.content_fingerprint,
                }
                for source in sources
            ),
            key=lambda item: item["key"],
        ),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class FundamentalMetricPeriodKind(StrEnum):
    """Time semantics for one normalized financial observation."""

    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    LTM = "ltm"
    YTD = "ytd"
    POINT_IN_TIME = "point_in_time"
    HORIZON = "horizon"
    UNKNOWN = "unknown"


class FundamentalMetricAvailability(StrEnum):
    """Whether the source publication time is known exactly."""

    KNOWN = "known"
    UNKNOWN = "unknown"


class FundamentalMetricValueStatus(StrEnum):
    """Value state; zero is deliberately distinct from missing data."""

    PRESENT = "present"
    ZERO = "zero"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    PARSE_ERROR = "parse_error"
    CONFLICT = "conflict"


class FundamentalMetricQualityFlag(StrEnum):
    """Machine-readable limitations preserved through later scoring."""

    SOURCE_AVAILABILITY_UNKNOWN = "source_availability_unknown"
    SOURCE_VALUE_MISSING = "source_value_missing"
    PARSE_FAILED = "parse_failed"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    REPORTING_BASIS_DIVERGENCE = "reporting_basis_divergence"
    FORECAST = "forecast"
    RESTATED = "restated"
    PROVIDER_STANDARDIZED = "provider_standardized"


class FundamentalMetricPeriod(FundamentalModel):
    """Provider period label with explicit date and availability precision."""

    period_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    source_label: str = Field(min_length=1, max_length=80)
    period_kind: FundamentalMetricPeriodKind
    start_date: date | None = None
    end_date: date | None = None
    availability: FundamentalMetricAvailability
    available_at: datetime | None = None
    is_forecast: bool = False

    @field_validator("available_at")
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("metric availability time requires timezone")
        return value

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.start_date is not None and self.end_date is None:
            raise ValueError("metric period start requires an end")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("metric period start cannot follow end")
        if self.period_kind is FundamentalMetricPeriodKind.POINT_IN_TIME:
            if self.end_date is None:
                raise ValueError("point-in-time metric requires an end date")
            if self.start_date is not None:
                raise ValueError("point-in-time metric cannot have a start")
        known = self.availability is FundamentalMetricAvailability.KNOWN
        if known != (self.available_at is not None):
            raise ValueError(
                "metric availability status contradicts its timestamp"
            )
        return self


class FundamentalMetricUnit(FundamentalModel):
    """Normalized unit with an explicit scale and optional currency."""

    value_kind: FundamentalValueKind
    normalized_unit: str = Field(min_length=1, max_length=80)
    scale: Decimal = Field(gt=Decimal("0"))
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )

    @field_validator("scale")
    @classmethod
    def require_finite_scale(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("metric unit scale must be finite")
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @model_validator(mode="after")
    def validate_currency_semantics(self) -> Self:
        currency_bearing = self.value_kind in {
            FundamentalValueKind.MONETARY,
            FundamentalValueKind.PER_SHARE,
        }
        if currency_bearing != (self.currency is not None):
            raise ValueError(
                "monetary and per-share metrics require currency exclusively"
            )
        return self


class FundamentalMetricSourceBinding(FundamentalModel):
    """One package artifact eligible to support normalized metrics."""

    package_item_key: FundamentalPackageItemKey
    source_reference_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    content_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    retrieved_at: datetime
    expires_at: datetime

    @field_validator("retrieved_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("metric source timestamps require timezone")
        return value

    @model_validator(mode="after")
    def validate_freshness(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError("metric source expiry must follow retrieval")
        return self


class FundamentalMetricCitation(FundamentalModel):
    """Exact JSON location supporting a normalized value or missing state."""

    package_item_key: FundamentalPackageItemKey
    source_reference_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    content_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    source_path: tuple[str, ...] = Field(min_length=1, max_length=32)
    source_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )
    period_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )

    @field_validator("source_path")
    @classmethod
    def require_bounded_source_path(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 240 for value in values):
            raise ValueError("metric citation path must be bounded")
        return values

    @computed_field
    @property
    def citation_key(self) -> str:
        path = "/".join(self.source_path)
        period = self.period_key or "undated"
        return (
            f"{self.source_reference_id}:{self.package_item_key.canonical_key}:"
            f"{path}:{period}"
        )


class FundamentalMetricKey(FundamentalModel):
    """Canonical identity of one company metric at one period and basis."""

    metric_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_METRIC_ID_PATTERN,
    )
    reporting_basis: FinancialReportingBasis
    period_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )

    @computed_field
    @property
    def canonical_key(self) -> str:
        return (
            f"{self.metric_id}:{self.reporting_basis.value}:"
            f"{self.period_key}"
        )


class FundamentalMetricCalculation(FundamentalModel):
    """Versioned formula lineage for a deterministically derived metric."""

    formula_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_METRIC_ID_PATTERN,
    )
    formula_version: str = Field(pattern=_SEMVER_PATTERN)
    input_metric_keys: tuple[str, ...] = Field(
        min_length=1,
        max_length=64,
    )

    @field_validator("input_metric_keys")
    @classmethod
    def require_unique_inputs(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 320 for value in values):
            raise ValueError("calculation input keys must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("calculation input keys must be unique")
        return values


class NormalizedFundamentalMetric(FundamentalModel):
    """One normalized fact or explicit unavailable/conflicting observation."""

    key: FundamentalMetricKey
    period: FundamentalMetricPeriod
    value_status: FundamentalMetricValueStatus
    value: Decimal | None = None
    unit: FundamentalMetricUnit
    source_values: tuple[str, ...] = Field(default=(), max_length=32)
    normalization_method: FundamentalNormalizationMethod
    normalization_rule_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_METRIC_ID_PATTERN,
    )
    normalization_rule_version: str = Field(pattern=_SEMVER_PATTERN)
    calculation: FundamentalMetricCalculation | None = None
    citations: tuple[FundamentalMetricCitation, ...] = Field(
        min_length=1,
        max_length=64,
    )
    quality_flags: tuple[FundamentalMetricQualityFlag, ...] = Field(
        default=(),
        max_length=len(FundamentalMetricQualityFlag),
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("normalized metric value must be finite")
        return value

    @field_validator("source_values", "limitations")
    @classmethod
    def require_bounded_unique_text(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("normalized metric text must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("normalized metric text must be unique")
        return values

    @model_validator(mode="after")
    def validate_metric(self) -> Self:
        if self.key.period_key != self.period.period_key:
            raise ValueError("metric key period must match period metadata")

        if self.value_status is FundamentalMetricValueStatus.PRESENT:
            if self.value is None or self.value == 0:
                raise ValueError("present metric requires a non-zero value")
        elif self.value_status is FundamentalMetricValueStatus.ZERO:
            if self.value != 0:
                raise ValueError("zero metric requires an explicit zero value")
        elif self.value is not None:
            raise ValueError("unavailable metric state cannot release a value")

        calculated = (
            self.normalization_method
            is FundamentalNormalizationMethod.CALCULATED
        )
        if calculated != (self.calculation is not None):
            raise ValueError(
                "calculated metrics require formula lineage exclusively"
            )
        if (
            not calculated
            and self.value_status
            in {
                FundamentalMetricValueStatus.PRESENT,
                FundamentalMetricValueStatus.ZERO,
                FundamentalMetricValueStatus.PARSE_ERROR,
            }
            and not self.source_values
        ):
            raise ValueError("source-backed metric requires its display value")

        required_flags = {
            FundamentalMetricValueStatus.MISSING: (
                FundamentalMetricQualityFlag.SOURCE_VALUE_MISSING
            ),
            FundamentalMetricValueStatus.PARSE_ERROR: (
                FundamentalMetricQualityFlag.PARSE_FAILED
            ),
            FundamentalMetricValueStatus.CONFLICT: (
                FundamentalMetricQualityFlag.DUPLICATE_CONFLICT
            ),
        }
        required_flag = required_flags.get(self.value_status)
        if required_flag is not None and required_flag not in self.quality_flags:
            raise ValueError("metric state requires its matching quality flag")
        if (
            self.period.availability is FundamentalMetricAvailability.UNKNOWN
            and FundamentalMetricQualityFlag.SOURCE_AVAILABILITY_UNKNOWN
            not in self.quality_flags
        ):
            raise ValueError(
                "unknown source availability requires its quality flag"
            )
        if (
            self.period.is_forecast
            and FundamentalMetricQualityFlag.FORECAST
            not in self.quality_flags
        ):
            raise ValueError("forecast metric requires its quality flag")
        if self.value_status is FundamentalMetricValueStatus.CONFLICT and (
            len(self.source_values) < 2 or len(self.citations) < 2
        ):
            raise ValueError(
                "conflicting metric requires multiple values and citations"
            )

        citation_keys = tuple(item.citation_key for item in self.citations)
        if len(citation_keys) != len(set(citation_keys)):
            raise ValueError("metric citations must be unique")
        if len(self.quality_flags) != len(set(self.quality_flags)):
            raise ValueError("metric quality flags must be unique")
        return self


class NormalizedFinancialEvidence(FundamentalModel):
    """Complete normalized metric surface bound to one committed package."""

    schema_version: Literal["jarvis.normalized_financial_evidence.v1"] = (
        "jarvis.normalized_financial_evidence.v1"
    )
    package_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    manifest_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    package_aggregate_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    issuer: FundamentalIssuerIdentity
    package_committed_at: datetime
    package_expires_at: datetime
    normalized_at: datetime
    normalization_policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_METRIC_ID_PATTERN,
    )
    normalization_policy_version: str = Field(pattern=_SEMVER_PATTERN)
    sources: tuple[FundamentalMetricSourceBinding, ...] = Field(
        min_length=len(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS),
        max_length=len(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS),
    )
    metrics: tuple[NormalizedFundamentalMetric, ...] = Field(
        min_length=1,
        max_length=20_000,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator(
        "package_committed_at",
        "package_expires_at",
        "normalized_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("normalized evidence timestamps require timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("evidence limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("evidence limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.package_id != (
            f"fundamental_package:{self.manifest_fingerprint}"
        ):
            raise ValueError(
                "normalized evidence package ID must match its manifest"
            )
        if self.package_expires_at <= self.package_committed_at:
            raise ValueError("package expiry must follow its commit")
        if not (
            self.package_committed_at
            <= self.normalized_at
            < self.package_expires_at
        ):
            raise ValueError(
                "normalization must use an active committed package"
            )

        source_by_key = {
            source.package_item_key.canonical_key: source
            for source in self.sources
        }
        if len(source_by_key) != len(self.sources):
            raise ValueError("normalized evidence source scenarios must be unique")
        if set(source_by_key) != _REQUIRED_SOURCE_KEYS:
            raise ValueError(
                "normalized evidence requires all sixteen package sources"
            )
        reference_ids = tuple(
            source.source_reference_id for source in self.sources
        )
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("normalized evidence source references must be unique")
        if any(
            source.expires_at <= self.normalized_at for source in self.sources
        ):
            raise ValueError("normalized evidence cannot use an expired source")
        if any(
            source.retrieved_at > self.package_committed_at
            for source in self.sources
        ):
            raise ValueError("package commit cannot predate source retrieval")
        if self.package_expires_at != min(
            source.expires_at for source in self.sources
        ):
            raise ValueError(
                "normalized evidence expiry must match its package sources"
            )
        if self.package_aggregate_fingerprint != _source_aggregate_fingerprint(
            manifest_fingerprint=self.manifest_fingerprint,
            sources=self.sources,
        ):
            raise ValueError(
                "normalized evidence sources do not match package fingerprint"
            )

        metric_keys = tuple(metric.key.canonical_key for metric in self.metrics)
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError("normalized metric keys must be unique")
        if metric_keys != tuple(sorted(metric_keys)):
            raise ValueError("normalized metrics must use canonical order")

        sources_by_reference = {
            source.source_reference_id: source for source in self.sources
        }
        for metric in self.metrics:
            for citation in metric.citations:
                source = sources_by_reference.get(citation.source_reference_id)
                if source is None:
                    raise ValueError(
                        "metric citation must reference a package source"
                    )
                if (
                    citation.package_item_key != source.package_item_key
                    or citation.content_fingerprint
                    != source.content_fingerprint
                ):
                    raise ValueError(
                        "metric citation must match its package source"
                    )
        return self

    @computed_field
    @property
    def evidence_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"evidence_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
