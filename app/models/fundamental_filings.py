"""Provider-neutral contracts for immutable company-filing artifacts.

These contracts bind every discovered filing and downloaded PDF to one
tenant, provider connection, and verified issuer. Provider adapters may
discover and stage files, but only validated, content-addressed artifacts can
enter an atomically published filing package.
"""

from datetime import date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import PurePosixPath
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
    FundamentalValidationStatus,
    ProviderConnectionScope,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_MAX_RECHECK_INTERVAL = timedelta(days=10)


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _require_https_url(value: str, field_name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{field_name} must be a credential-free HTTPS URL")
    return value


def _require_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")
    return values


class FundamentalFilingCategory(StrEnum):
    """Initial source-document categories supported by filing acquisition."""

    EARNINGS_RELEASE = "earnings_release"
    CONFERENCE_CALL = "conference_call"


class FundamentalFilingSourceClass(StrEnum):
    """Evidence class of a provider-listed filing."""

    COMPANY_AUTHORED_EARNINGS_RELEASE = "company_authored_earnings_release"
    EARNINGS_CALL_TRANSCRIPT = "earnings_call_transcript"


class FundamentalFilingPeriodType(StrEnum):
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    UNCLASSIFIED = "unclassified"


class FundamentalFilingPeriodState(StrEnum):
    RESOLVED = "resolved"
    UNCLASSIFIED = "unclassified"


class FundamentalFilingFailureStage(StrEnum):
    DISCOVERY = "discovery"
    DOWNLOAD = "download"
    VALIDATION = "validation"
    STORAGE = "storage"
    PACKAGE_PUBLICATION = "package_publication"


class FundamentalFilingFailureCategory(StrEnum):
    AUTHENTICATION_UNAVAILABLE = "authentication_unavailable"
    NOT_ENTITLED = "not_entitled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REFERENCE = "invalid_reference"
    ISSUER_MISMATCH = "issuer_mismatch"
    UNAPPROVED_SOURCE = "unapproved_source"
    TIMEOUT = "timeout"
    INVALID_CONTENT = "invalid_content"
    SIZE_LIMIT_EXCEEDED = "size_limit_exceeded"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    UNKNOWN = "unknown"


class FundamentalFilingPeriod(FundamentalModel):
    """Fiscal-period identity that never guesses unresolved dates."""

    state: FundamentalFilingPeriodState
    period_type: FundamentalFilingPeriodType
    source_label: str = Field(min_length=1, max_length=120)
    fiscal_year: int | None = Field(default=None, ge=1900, le=2200)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    period_start: date | None = None
    period_end: date | None = None

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        dates_supplied = self.period_start is not None or self.period_end is not None
        if dates_supplied and (
            self.period_start is None or self.period_end is None
        ):
            raise ValueError("filing period dates must be supplied together")
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_start > self.period_end
        ):
            raise ValueError("filing period start cannot follow its end")

        if self.state is FundamentalFilingPeriodState.UNCLASSIFIED:
            if (
                self.period_type is not FundamentalFilingPeriodType.UNCLASSIFIED
                or self.fiscal_year is not None
                or self.fiscal_quarter is not None
                or dates_supplied
            ):
                raise ValueError(
                    "unclassified filing period cannot release inferred period data"
                )
            return self

        if (
            self.period_type is FundamentalFilingPeriodType.UNCLASSIFIED
            or self.fiscal_year is None
        ):
            raise ValueError("resolved filing period requires a fiscal year and type")
        if self.period_type is FundamentalFilingPeriodType.QUARTERLY:
            if self.fiscal_quarter is None:
                raise ValueError("quarterly filing period requires a fiscal quarter")
        elif self.fiscal_quarter is not None:
            raise ValueError("annual filing period cannot contain a fiscal quarter")
        return self


_EXPECTED_SOURCE_CLASS = {
    FundamentalFilingCategory.EARNINGS_RELEASE: (
        FundamentalFilingSourceClass.COMPANY_AUTHORED_EARNINGS_RELEASE
    ),
    FundamentalFilingCategory.CONFERENCE_CALL: (
        FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
    ),
}


class FundamentalFilingReference(FundamentalModel):
    """One discovered, issuer-bound provider reference to an available PDF."""

    schema_version: Literal["jarvis.fundamental_filing_reference.v1"] = (
        "jarvis.fundamental_filing_reference.v1"
    )
    filing_reference_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    provider: str = Field(min_length=1, max_length=80, pattern=_ID_PATTERN)
    source_discovery_fingerprint: str = Field(
        pattern=_FINGERPRINT_PATTERN,
    )
    category: FundamentalFilingCategory
    source_class: FundamentalFilingSourceClass
    period: FundamentalFilingPeriod
    title: str = Field(min_length=1, max_length=500)
    provider_label: str = Field(min_length=1, max_length=300)
    original_filename: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )
    source_page_url: str = Field(min_length=1, max_length=2_000)
    artifact_url: str = Field(min_length=1, max_length=2_000)
    provider_published_at: datetime | None = None
    available_at: datetime | None = None
    discovered_at: datetime

    @field_validator("source_page_url")
    @classmethod
    def validate_source_page_url(cls, value: str) -> str:
        return _require_https_url(value, "filing source page URL")

    @field_validator("artifact_url")
    @classmethod
    def validate_artifact_url(cls, value: str) -> str:
        return _require_https_url(value, "filing artifact URL")

    @field_validator(
        "provider_published_at",
        "available_at",
        "discovered_at",
    )
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            return _require_timezone(value, "filing reference timestamp")
        return value

    @field_validator("original_filename")
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("provider filename cannot contain control characters")
        return value

    @model_validator(mode="after")
    def validate_binding_and_timeline(self) -> Self:
        if self.provider != self.connection.provider:
            raise ValueError("filing provider must match its connection")
        if (
            self.issuer.provider_company_id is None
            or self.issuer.provider_slug is None
        ):
            raise ValueError("filing reference requires a resolved provider issuer")
        if self.source_class is not _EXPECTED_SOURCE_CLASS[self.category]:
            raise ValueError("filing source class does not match its category")
        if (
            self.provider_published_at is not None
            and self.provider_published_at > self.discovered_at
        ):
            raise ValueError("filing publication cannot follow discovery")
        if self.available_at is not None and self.available_at > self.discovered_at:
            raise ValueError("filing availability cannot follow discovery")
        if (
            self.provider_published_at is not None
            and self.available_at is not None
            and self.available_at < self.provider_published_at
        ):
            raise ValueError("filing availability cannot predate publication")
        return self


class FundamentalFilingDiscoverySnapshot(FundamentalModel):
    """Complete bounded discovery result for one verified issuer."""

    schema_version: Literal["jarvis.fundamental_filing_discovery.v1"] = (
        "jarvis.fundamental_filing_discovery.v1"
    )
    discovery_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    categories: tuple[FundamentalFilingCategory, ...] = Field(
        min_length=1,
        max_length=2,
    )
    from_fiscal_year: int | None = Field(default=None, ge=1900, le=2200)
    through_date: date
    max_documents: int = Field(ge=1, le=500)
    discovered_at: datetime
    references: tuple[FundamentalFilingReference, ...] = Field(
        default=(),
        max_length=500,
    )
    unavailable_period_labels: tuple[str, ...] = Field(
        default=(),
        max_length=500,
    )
    validation_status: FundamentalValidationStatus
    limitations: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("discovered_at")
    @classmethod
    def validate_discovered_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing discovery timestamp")

    @field_validator("categories")
    @classmethod
    def validate_categories(
        cls,
        values: tuple[FundamentalFilingCategory, ...],
    ) -> tuple[FundamentalFilingCategory, ...]:
        _require_unique(tuple(value.value for value in values), "filing categories")
        return values

    @field_validator("unavailable_period_labels", "limitations")
    @classmethod
    def validate_bounded_text(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("filing discovery notes must be non-blank and bounded")
        return _require_unique(values, "filing discovery notes")

    @model_validator(mode="after")
    def validate_discovery(self) -> Self:
        if self.through_date > self.discovered_at.date():
            raise ValueError("filing discovery cannot include a future through-date")
        if len(self.references) > self.max_documents:
            raise ValueError("filing discovery exceeds its document limit")
        reference_ids = tuple(
            reference.filing_reference_id for reference in self.references
        )
        _require_unique(reference_ids, "filing reference IDs")
        _require_unique(
            tuple(reference.artifact_url for reference in self.references),
            "filing artifact URLs",
        )
        for reference in self.references:
            if (
                reference.connection != self.connection
                or reference.issuer != self.issuer
            ):
                raise ValueError("filing discovery references must share its scope")
            if reference.category not in self.categories:
                raise ValueError("filing discovery contains an unrequested category")
            if reference.discovered_at > self.discovered_at:
                raise ValueError("filing reference cannot follow discovery completion")
            if (
                self.from_fiscal_year is not None
                and reference.period.fiscal_year is not None
                and reference.period.fiscal_year < self.from_fiscal_year
            ):
                raise ValueError("filing reference predates requested fiscal range")
        return self

    @computed_field
    @property
    def discovery_fingerprint(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "connection": self.connection.model_dump(mode="json"),
            "issuer": self.issuer.model_dump(mode="json"),
            "categories": sorted(category.value for category in self.categories),
            "from_fiscal_year": self.from_fiscal_year,
            "through_date": self.through_date.isoformat(),
            "max_documents": self.max_documents,
            "discovered_at": self.discovered_at.isoformat(),
            "references": sorted(
                (
                    reference.model_dump(mode="json")
                    for reference in self.references
                ),
                key=lambda item: item["filing_reference_id"],
            ),
            "unavailable_period_labels": sorted(self.unavailable_period_labels),
            "validation_status": self.validation_status.value,
            "limitations": sorted(self.limitations),
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class FundamentalFilingArtifactManifest(FundamentalModel):
    """Validated durable metadata for one content-addressed PDF."""

    schema_version: Literal["jarvis.fundamental_filing_artifact.v1"] = (
        "jarvis.fundamental_filing_artifact.v1"
    )
    reference: FundamentalFilingReference
    content_sha256: str = Field(pattern=_FINGERPRINT_PATTERN)
    safe_relative_path: str = Field(min_length=1, max_length=1_000)
    content_type: Literal["application/pdf"] = "application/pdf"
    byte_length: int = Field(ge=5, le=100_000_000)
    page_count: int = Field(ge=1, le=5_000)
    pdf_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        pattern=r"^[0-9]+(?:\.[0-9]+)?$",
    )
    downloaded_at: datetime
    last_checked_at: datetime
    recheck_after: datetime
    duplicate_of_artifact_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    supersedes_artifact_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )

    @field_validator("safe_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or value != path.as_posix()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.suffix.casefold() != ".pdf"
        ):
            raise ValueError("filing artifact path must be a safe relative PDF path")
        return value

    @field_validator("downloaded_at", "last_checked_at", "recheck_after")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing artifact timestamp")

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if self.downloaded_at < self.reference.discovered_at:
            raise ValueError("filing download cannot predate discovery")
        if self.last_checked_at < self.downloaded_at:
            raise ValueError("filing check cannot predate download")
        if self.recheck_after <= self.last_checked_at:
            raise ValueError("filing recheck must follow the last check")
        if self.recheck_after - self.last_checked_at > _MAX_RECHECK_INTERVAL:
            raise ValueError("filing recheck interval cannot exceed ten days")
        if (
            self.duplicate_of_artifact_id is not None
            and self.duplicate_of_artifact_id == self.supersedes_artifact_id
        ):
            raise ValueError("duplicate and superseded artifacts must differ")
        expected_suffix = f"/{self.content_sha256}.pdf"
        if not f"/{self.safe_relative_path}".endswith(expected_suffix):
            raise ValueError("filing artifact path must end in its content hash")
        return self

    @computed_field
    @property
    def artifact_fingerprint(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "reference": self.reference.model_dump(mode="json"),
            "content_sha256": self.content_sha256,
            "safe_relative_path": self.safe_relative_path,
            "content_type": self.content_type,
            "byte_length": self.byte_length,
            "page_count": self.page_count,
            "pdf_version": self.pdf_version,
            "downloaded_at": self.downloaded_at.isoformat(),
            "last_checked_at": self.last_checked_at.isoformat(),
            "recheck_after": self.recheck_after.isoformat(),
            "duplicate_of_artifact_id": self.duplicate_of_artifact_id,
            "supersedes_artifact_id": self.supersedes_artifact_id,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @computed_field
    @property
    def artifact_id(self) -> str:
        return f"filing_artifact:{self.artifact_fingerprint}"

    def is_recheck_due(self, *, as_of: datetime) -> bool:
        return _require_timezone(as_of, "filing freshness time") >= self.recheck_after


class FundamentalFilingFailure(FundamentalModel):
    """Sanitized, attributable failure for one filing-pipeline stage."""

    schema_version: Literal["jarvis.fundamental_filing_failure.v1"] = (
        "jarvis.fundamental_filing_failure.v1"
    )
    operation_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    filing_reference_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    stage: FundamentalFilingFailureStage
    category: FundamentalFilingFailureCategory
    retryable: bool
    occurred_at: datetime
    sanitized_message: str = Field(min_length=1, max_length=500)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing failure timestamp")

    @field_validator("sanitized_message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("filing failure message must be a single line")
        return value


class FundamentalFilingPackageManifest(FundamentalModel):
    """Atomic publication pointer for one complete discovery snapshot."""

    schema_version: Literal["jarvis.fundamental_filing_package_manifest.v1"] = (
        "jarvis.fundamental_filing_package_manifest.v1"
    )
    operation_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    requested_at: datetime
    committed_at: datetime
    discovery_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    artifact_ids: tuple[str, ...] = Field(default=(), max_length=500)
    recheck_after: datetime

    @field_validator("requested_at", "committed_at", "recheck_after")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing package timestamp")

    @field_validator("artifact_ids")
    @classmethod
    def validate_artifact_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value
            or len(value) > 180
            or not value.startswith("filing_artifact:")
            for value in values
        ):
            raise ValueError("filing package artifact IDs must be bounded")
        return _require_unique(values, "filing package artifact IDs")

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.committed_at < self.requested_at:
            raise ValueError("filing package commit cannot predate its request")
        if self.recheck_after <= self.committed_at:
            raise ValueError("filing package recheck must follow commit")
        return self

    @computed_field
    @property
    def manifest_fingerprint(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "connection": self.connection.model_dump(mode="json"),
            "issuer": self.issuer.model_dump(mode="json"),
            "requested_at": self.requested_at.isoformat(),
            "committed_at": self.committed_at.isoformat(),
            "discovery_fingerprint": self.discovery_fingerprint,
            "artifact_ids": sorted(self.artifact_ids),
            "recheck_after": self.recheck_after.isoformat(),
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @computed_field
    @property
    def package_id(self) -> str:
        return f"filing_package:{self.manifest_fingerprint}"

    def is_recheck_due(self, *, as_of: datetime) -> bool:
        return _require_timezone(as_of, "filing package freshness time") >= (
            self.recheck_after
        )


class FundamentalFilingPackage(FundamentalModel):
    """Immutable full aggregate activated only after every PDF validates."""

    schema_version: Literal["jarvis.fundamental_filing_package.v1"] = (
        "jarvis.fundamental_filing_package.v1"
    )
    manifest: FundamentalFilingPackageManifest
    discovery: FundamentalFilingDiscoverySnapshot
    artifacts: tuple[FundamentalFilingArtifactManifest, ...] = Field(
        default=(),
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_atomic_package(self) -> Self:
        if (
            self.discovery.validation_status
            is not FundamentalValidationStatus.VALIDATED
        ):
            raise ValueError("filing package requires validated discovery")
        if (
            self.manifest.connection != self.discovery.connection
            or self.manifest.issuer != self.discovery.issuer
        ):
            raise ValueError("filing package scope must match discovery")
        if self.manifest.discovery_fingerprint != self.discovery.discovery_fingerprint:
            raise ValueError("filing package discovery fingerprint does not match")

        artifact_ids = tuple(artifact.artifact_id for artifact in self.artifacts)
        _require_unique(artifact_ids, "filing artifact IDs")
        if set(artifact_ids) != set(self.manifest.artifact_ids):
            raise ValueError("filing package artifacts must match its manifest")

        reference_ids = tuple(
            artifact.reference.filing_reference_id for artifact in self.artifacts
        )
        _require_unique(reference_ids, "packaged filing reference IDs")
        discovered_references = {
            reference.filing_reference_id: reference
            for reference in self.discovery.references
        }
        if set(reference_ids) != set(discovered_references):
            raise ValueError(
                "filing package requires every discovered available document"
            )

        for artifact in self.artifacts:
            if (
                artifact.reference
                != discovered_references[artifact.reference.filing_reference_id]
            ):
                raise ValueError(
                    "filing artifact must exactly match its discovered reference"
                )
            if (
                artifact.reference.connection != self.manifest.connection
                or artifact.reference.issuer != self.manifest.issuer
            ):
                raise ValueError("filing package artifacts must share its scope")
            if artifact.downloaded_at < self.manifest.requested_at:
                raise ValueError("filing package artifact predates its request")
            if artifact.last_checked_at > self.manifest.committed_at:
                raise ValueError("filing package commit predates artifact validation")
        if self.artifacts and self.manifest.recheck_after != min(
            artifact.recheck_after for artifact in self.artifacts
        ):
            raise ValueError("filing package recheck must match its earliest artifact")
        return self

    @computed_field
    @property
    def package_id(self) -> str:
        return self.manifest.package_id
