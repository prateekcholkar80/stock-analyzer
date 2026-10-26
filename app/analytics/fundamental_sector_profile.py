"""Deterministic, citation-preserving issuer sector classification.

The classifier deliberately uses reviewed exact taxonomy labels. It never
infers a sector from an issuer name, business description, peer set, or an LLM.
Unsupported, expired, or conflicting evidence therefore remains ``UNKNOWN``.
"""

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.analytics.fundamental_checks import FinancialHealthSectorProfile
from app.models.fundamentals import FundamentalIssuerIdentity, FundamentalModel


SECTOR_PROFILE_POLICY_ID = "jarvis.fundamental_sector_profile"
SECTOR_PROFILE_POLICY_VERSION = "1.0.0"

_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_POLICY_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_SEMVER_PATTERN = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"


class FundamentalSectorTaxonomy(StrEnum):
    """Taxonomies for which exact labels have been reviewed."""

    NSE_SECTOR_INDUSTRY = "nse_sector_industry"
    PROVIDER_SECTOR_INDUSTRY = "provider_sector_industry"


class FundamentalSectorSourceAuthority(StrEnum):
    """Authority of a classification source; never an implicit tie-breaker."""

    EXCHANGE_OFFICIAL = "exchange_official"
    PROVIDER_STANDARDIZED = "provider_standardized"


class FundamentalSectorClassificationStatus(StrEnum):
    CLASSIFIED = "classified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class FundamentalSectorMatchField(StrEnum):
    SECTOR = "sector"
    INDUSTRY = "industry"


class FundamentalSectorEvidence(FundamentalModel):
    """One exact issuer-sector observation with source-level provenance."""

    schema_version: Literal["jarvis.fundamental_sector_evidence.v1"] = (
        "jarvis.fundamental_sector_evidence.v1"
    )
    issuer: FundamentalIssuerIdentity
    taxonomy: FundamentalSectorTaxonomy
    source_authority: FundamentalSectorSourceAuthority
    source_reference_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    source_name: str = Field(min_length=1, max_length=240)
    source_location: str = Field(min_length=1, max_length=2_000)
    source_record_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    sector: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    retrieved_at: datetime
    expires_at: datetime

    @field_validator("sector", "industry")
    @classmethod
    def normalize_optional_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("retrieved_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("sector-evidence timestamps require timezone")
        return value

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.sector is None and self.industry is None:
            raise ValueError("sector evidence requires a sector or industry")
        if self.expires_at <= self.retrieved_at:
            raise ValueError("sector evidence expiry must follow retrieval")
        expected_authority = {
            FundamentalSectorTaxonomy.NSE_SECTOR_INDUSTRY: (
                FundamentalSectorSourceAuthority.EXCHANGE_OFFICIAL
            ),
            FundamentalSectorTaxonomy.PROVIDER_SECTOR_INDUSTRY: (
                FundamentalSectorSourceAuthority.PROVIDER_STANDARDIZED
            ),
        }[self.taxonomy]
        if self.source_authority is not expected_authority:
            raise ValueError(
                "sector taxonomy must use its approved source authority"
            )
        return self

    @computed_field
    @property
    def citation_key(self) -> str:
        return (
            f"{self.source_reference_id}:"
            f"{self.source_record_fingerprint}"
        )


class FundamentalSectorProfileMatch(FundamentalModel):
    """Exact policy-label match linked to its source citation."""

    citation_key: str = Field(min_length=66, max_length=260)
    profile: FinancialHealthSectorProfile
    matched_field: FundamentalSectorMatchField
    observed_label: str = Field(min_length=1, max_length=200)
    canonical_label: str = Field(min_length=1, max_length=200)


class FundamentalSectorProfileClassification(FundamentalModel):
    """Fail-closed sector profile bound to one committed evidence package."""

    schema_version: Literal["jarvis.fundamental_sector_profile.v1"] = (
        "jarvis.fundamental_sector_profile.v1"
    )
    package_id: str = Field(min_length=1, max_length=240)
    manifest_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    issuer: FundamentalIssuerIdentity
    policy_id: str = Field(
        default=SECTOR_PROFILE_POLICY_ID,
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    policy_version: str = Field(
        default=SECTOR_PROFILE_POLICY_VERSION,
        pattern=_SEMVER_PATTERN,
    )
    status: FundamentalSectorClassificationStatus
    profile: FinancialHealthSectorProfile
    evidence: tuple[FundamentalSectorEvidence, ...] = Field(
        default=(),
        max_length=16,
    )
    matches: tuple[FundamentalSectorProfileMatch, ...] = Field(
        default=(),
        max_length=32,
    )
    reasons: tuple[str, ...] = Field(min_length=1, max_length=32)
    classified_at: datetime
    package_expires_at: datetime

    @field_validator("classified_at", "package_expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("sector classification timestamps require timezone")
        return value

    @field_validator("reasons")
    @classmethod
    def require_unique_reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("sector classification reasons must be unique")
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("sector classification reasons must be bounded")
        return values

    @model_validator(mode="after")
    def validate_classification(self) -> Self:
        if self.package_id != f"fundamental_package:{self.manifest_fingerprint}":
            raise ValueError("sector profile package ID must match its manifest")
        if self.classified_at >= self.package_expires_at:
            raise ValueError("sector classification requires an active package")
        citation_keys = tuple(item.citation_key for item in self.evidence)
        if citation_keys != tuple(sorted(citation_keys)):
            raise ValueError("sector evidence must use canonical citation order")
        if len(citation_keys) != len(set(citation_keys)):
            raise ValueError("sector evidence citations must be unique")
        match_keys = tuple(
            (item.citation_key, item.matched_field.value, item.profile.value)
            for item in self.matches
        )
        if match_keys != tuple(sorted(match_keys)):
            raise ValueError("sector matches must use canonical order")
        if any(item.citation_key not in citation_keys for item in self.matches):
            raise ValueError("sector match must cite supplied evidence")
        if self.status is FundamentalSectorClassificationStatus.CLASSIFIED:
            if self.profile is FinancialHealthSectorProfile.UNKNOWN:
                raise ValueError("classified sector profile cannot be unknown")
            if not self.matches:
                raise ValueError("classified sector profile requires a match")
        elif self.profile is not FinancialHealthSectorProfile.UNKNOWN:
            raise ValueError("unresolved sector classification must be unknown")
        return self

    @computed_field
    @property
    def classification_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"classification_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


_BFSI_SECTOR_LABELS = frozenset(
    {
        "banking financial services and insurance",
        "bfsi",
        "financial services",
        "financials",
    }
)

_NON_FINANCIAL_SECTOR_LABELS = frozenset(
    {
        "automobile and auto components",
        "capital goods",
        "chemicals",
        "commodities",
        "construction",
        "construction materials",
        "consumer discretionary",
        "consumer durables",
        "consumer services",
        "consumer staples",
        "diversified",
        "energy",
        "fast moving consumer goods",
        "fmcg",
        "healthcare",
        "industrial products",
        "industrials",
        "information technology",
        "it",
        "media entertainment and publication",
        "metals and mining",
        "oil gas and consumable fuels",
        "power",
        "realty",
        "services",
        "telecommunication",
        "textiles",
        "utilities",
    }
)

_BFSI_INDUSTRY_LABELS = frozenset(
    {
        "asset management company",
        "bank",
        "banks",
        "financial institution",
        "general insurance",
        "housing finance company",
        "insurance",
        "life insurance",
        "microfinance institutions",
        "non banking financial company",
        "other bank",
        "private sector bank",
        "public sector bank",
        "stockbroking and allied",
    }
)


def _normalize_taxonomy_label(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _matches_for_evidence(
    evidence: FundamentalSectorEvidence,
) -> tuple[FundamentalSectorProfileMatch, ...]:
    matches: list[FundamentalSectorProfileMatch] = []
    if evidence.sector is not None:
        sector = _normalize_taxonomy_label(evidence.sector)
        if sector in _BFSI_SECTOR_LABELS:
            profile = FinancialHealthSectorProfile.BFSI
        elif sector in _NON_FINANCIAL_SECTOR_LABELS:
            profile = FinancialHealthSectorProfile.NON_FINANCIAL
        else:
            profile = None
        if profile is not None:
            matches.append(
                FundamentalSectorProfileMatch(
                    citation_key=evidence.citation_key,
                    profile=profile,
                    matched_field=FundamentalSectorMatchField.SECTOR,
                    observed_label=evidence.sector,
                    canonical_label=sector,
                )
            )
    if evidence.industry is not None:
        industry = _normalize_taxonomy_label(evidence.industry)
        if industry in _BFSI_INDUSTRY_LABELS:
            matches.append(
                FundamentalSectorProfileMatch(
                    citation_key=evidence.citation_key,
                    profile=FinancialHealthSectorProfile.BFSI,
                    matched_field=FundamentalSectorMatchField.INDUSTRY,
                    observed_label=evidence.industry,
                    canonical_label=industry,
                )
            )
    return tuple(matches)


def classify_fundamental_sector_profile(
    *,
    package_id: str,
    manifest_fingerprint: str,
    issuer: FundamentalIssuerIdentity,
    package_expires_at: datetime,
    evidence: tuple[FundamentalSectorEvidence, ...],
    classified_at: datetime,
) -> FundamentalSectorProfileClassification:
    """Classify an issuer using only current, exact, cited taxonomy labels."""

    if any(item.issuer != issuer for item in evidence):
        raise ValueError("sector evidence issuer must match package issuer")

    ordered_evidence = tuple(sorted(evidence, key=lambda item: item.citation_key))
    current_evidence = tuple(
        item
        for item in ordered_evidence
        if item.retrieved_at <= classified_at < item.expires_at
    )
    matches = tuple(
        sorted(
            (
                match
                for item in current_evidence
                for match in _matches_for_evidence(item)
            ),
            key=lambda item: (
                item.citation_key,
                item.matched_field.value,
                item.profile.value,
            ),
        )
    )
    matched_profiles = frozenset(item.profile for item in matches)

    if len(matched_profiles) == 1:
        profile = next(iter(matched_profiles))
        status = FundamentalSectorClassificationStatus.CLASSIFIED
        reasons = (f"exact_{profile.value}_taxonomy_match",)
    elif len(matched_profiles) > 1:
        profile = FinancialHealthSectorProfile.UNKNOWN
        status = FundamentalSectorClassificationStatus.CONFLICTING_EVIDENCE
        reasons = ("conflicting_current_sector_evidence",)
    else:
        profile = FinancialHealthSectorProfile.UNKNOWN
        status = FundamentalSectorClassificationStatus.INSUFFICIENT_EVIDENCE
        if not evidence:
            reasons = ("sector_evidence_missing",)
        elif not current_evidence:
            reasons = ("current_sector_evidence_missing",)
        else:
            reasons = ("unsupported_taxonomy_label",)

    return FundamentalSectorProfileClassification(
        package_id=package_id,
        manifest_fingerprint=manifest_fingerprint,
        issuer=issuer,
        status=status,
        profile=profile,
        # Preserve even stale/future observations as citations explaining why
        # the classifier withheld a profile. Only current observations may
        # contribute matches above.
        evidence=ordered_evidence,
        matches=matches,
        reasons=reasons,
        classified_at=classified_at,
        package_expires_at=package_expires_at,
    )
