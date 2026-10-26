"""Deterministic fundamental-intelligence workflow orchestration.

This module owns the ordered boundary between normalized package evidence and
the shadow scorecard supplied to the Fundamental Analyst.  It deliberately
contains no LLM, provider, storage, or trade-decision dependency.
"""

from dataclasses import dataclass
from datetime import datetime
from collections import Counter

from app.analytics.fundamental_applicability import (
    assess_fundamental_framework_applicability,
)
from app.analytics.fundamental_checks import (
    assess_earnings_quality,
    assess_financial_health,
    assess_forensic_risk,
)
from app.analytics.fundamental_score_policies import (
    INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1,
)
from app.analytics.fundamental_scorecard import build_fundamental_scorecard
from app.analytics.fundamental_sector_profile import (
    FundamentalSectorProfileClassification,
)
from app.analytics.fundamental_metrics import (
    derive_fundamental_metrics,
    normalize_financial_document_metrics,
)
from app.models.fundamental_assessment import FundamentalMandatoryAssessment
from app.models.fundamental_frameworks import (
    FundamentalFrameworkApplicabilityAssessment,
)
from app.models.fundamental_metrics import (
    FundamentalMetricSourceBinding,
    NormalizedFinancialEvidence,
)
from app.models.fundamental_package import (
    FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
    FundamentalEvidencePackage,
    FundamentalPackageDataset,
    FundamentalPackageItemKey,
)
from app.models.fundamental_scoring import (
    FundamentalDecisionInfluenceMode,
    FundamentalScorePolicyBundle,
    FundamentalScorecardResult,
)


MANDATORY_ASSESSMENT_POLICY_BUNDLE_ID = (
    "jarvis.mandatory_fundamental_checks"
)
MANDATORY_ASSESSMENT_POLICY_BUNDLE_VERSION = "1.0.0"
FUNDAMENTAL_NORMALIZATION_POLICY_ID = "jarvis.fundamental_normalization"
FUNDAMENTAL_NORMALIZATION_POLICY_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class FundamentalIntelligenceResult:
    """One lineage-preserving deterministic result for analyst consumption."""

    mandatory_assessment: FundamentalMandatoryAssessment
    applicability_assessment: FundamentalFrameworkApplicabilityAssessment
    scorecard: FundamentalScorecardResult
    sector_classification: FundamentalSectorProfileClassification

    def __post_init__(self) -> None:
        if self.scorecard.decision_influence_mode is not (
            FundamentalDecisionInfluenceMode.SHADOW
        ):
            raise ValueError(
                "fundamental intelligence is shadow-only until separately approved"
            )
        if (
            self.applicability_assessment.mandatory_assessment_fingerprint
            != self.mandatory_assessment.assessment_fingerprint
            or self.scorecard.mandatory_assessment_fingerprint
            != self.mandatory_assessment.assessment_fingerprint
            or self.scorecard.applicability_assessment_fingerprint
            != self.applicability_assessment.assessment_fingerprint
        ):
            raise ValueError(
                "fundamental intelligence stages must share immutable lineage"
            )
        classification = self.sector_classification
        if (
            classification.package_id != self.scorecard.package_id
            or classification.manifest_fingerprint
            != self.scorecard.manifest_fingerprint
            or classification.issuer != self.scorecard.issuer
        ):
            raise ValueError(
                "sector classification must share intelligence lineage"
            )


def normalize_committed_fundamental_package(
    package: FundamentalEvidencePackage,
    *,
    normalized_at: datetime,
) -> NormalizedFinancialEvidence:
    """Build canonical metrics from one complete, active package.

    Every one of the sixteen package artifacts remains represented in the
    source boundary.  Only reviewed financial-document mappings release
    numeric metrics; provider snapshots, peer data, benchmarking data, and
    unmapped rows remain explicit sources or summarized limitations.
    """

    if not isinstance(package, FundamentalEvidencePackage):
        raise TypeError("fundamental normalization requires a committed package")
    if normalized_at.tzinfo is None or normalized_at.utcoffset() is None:
        raise ValueError("fundamental normalization time requires timezone")
    if not (
        package.manifest.committed_at
        <= normalized_at
        < package.manifest.expires_at
    ):
        raise ValueError(
            "fundamental normalization requires an active committed package"
        )

    items_by_key = {
        item.key.canonical_key: item for item in package.manifest.items
    }
    sources = tuple(
        FundamentalMetricSourceBinding(
            package_item_key=key,
            source_reference_id=items_by_key[key.canonical_key].cache_entry_id,
            content_fingerprint=(
                items_by_key[key.canonical_key].content_fingerprint
            ),
            retrieved_at=items_by_key[key.canonical_key].retrieved_at,
            expires_at=items_by_key[key.canonical_key].expires_at,
        )
        for key in FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS
    )
    source_by_key = {
        source.package_item_key.canonical_key: source for source in sources
    }

    metrics = []
    limitations = []
    for stored in sorted(
        package.structured_documents,
        key=lambda item: (
            item.request.document_type.value,
            item.request.reporting_basis.value,
        ),
    ):
        document = stored.result.document
        if document is None:
            raise ValueError(
                "committed package contains a missing financial document"
            )
        key = FundamentalPackageItemKey(
            dataset=FundamentalPackageDataset(document.document_type.value),
            reporting_basis=document.reporting_basis,
        )
        normalized = normalize_financial_document_metrics(
            document,
            source_by_key[key.canonical_key],
        )
        metrics.extend(normalized.metrics)
        if normalized.issues:
            counts = Counter(issue.code.value for issue in normalized.issues)
            summary = ", ".join(
                f"{code}={count}" for code, count in sorted(counts.items())
            )
            limitations.append(
                f"{key.canonical_key}: withheld normalization issues ({summary})."
            )

    metric_keys = tuple(metric.key.canonical_key for metric in metrics)
    if len(metric_keys) != len(set(metric_keys)):
        raise ValueError(
            "committed package produced duplicate canonical reported metrics"
        )
    derived = derive_fundamental_metrics(
        tuple(sorted(metrics, key=lambda metric: metric.key.canonical_key))
    )
    if derived.issues:
        counts = Counter(issue.code.value for issue in derived.issues)
        summary = ", ".join(
            f"{code}={count}" for code, count in sorted(counts.items())
        )
        limitations.append(
            f"Derived metrics withheld where inputs were incomplete ({summary})."
        )
    complete_metrics = (*metrics, *derived.metrics)
    complete_keys = tuple(metric.key.canonical_key for metric in complete_metrics)
    if len(complete_keys) != len(set(complete_keys)):
        raise ValueError(
            "reported and derived metrics produced duplicate canonical identities"
        )

    return NormalizedFinancialEvidence(
        package_id=package.package_id,
        manifest_fingerprint=package.manifest.manifest_fingerprint,
        package_aggregate_fingerprint=package.aggregate_fingerprint,
        issuer=package.manifest.issuer,
        package_committed_at=package.manifest.committed_at,
        package_expires_at=package.manifest.expires_at,
        normalized_at=normalized_at,
        normalization_policy_id=FUNDAMENTAL_NORMALIZATION_POLICY_ID,
        normalization_policy_version=FUNDAMENTAL_NORMALIZATION_POLICY_VERSION,
        sources=sources,
        metrics=tuple(
            sorted(complete_metrics, key=lambda metric: metric.key.canonical_key)
        ),
        limitations=tuple(dict.fromkeys(limitations)),
    )
def build_shadow_fundamental_intelligence(
    evidence: NormalizedFinancialEvidence,
    *,
    sector_classification: FundamentalSectorProfileClassification,
    assessed_at: datetime,
    policy_bundle: FundamentalScorePolicyBundle = (
        INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1
    ),
) -> FundamentalIntelligenceResult:
    """Run mandatory checks, applicability, then shadow scoring in order.

    The score is observational only.  A caller cannot use this entry point
    with an advisory or gate-enabled policy bundle, even if such a bundle is
    otherwise structurally valid.
    """

    if policy_bundle.decision_influence_mode is not (
        FundamentalDecisionInfluenceMode.SHADOW
    ):
        raise ValueError(
            "fundamental intelligence policy bundle must remain shadow-only"
        )
    if not isinstance(
        sector_classification,
        FundamentalSectorProfileClassification,
    ):
        raise ValueError(
            "fundamental intelligence requires a cited sector classification"
        )
    if assessed_at >= evidence.package_expires_at:
        raise ValueError(
            "fundamental intelligence requires active normalized package evidence"
        )
    if (
        sector_classification.package_id != evidence.package_id
        or sector_classification.manifest_fingerprint
        != evidence.manifest_fingerprint
        or sector_classification.issuer != evidence.issuer
        or sector_classification.classified_at != assessed_at
        or sector_classification.package_expires_at
        != evidence.package_expires_at
    ):
        raise ValueError(
            "sector classification must match normalized evidence"
        )
    resolved_sector_profile = sector_classification.profile

    domains = (
        assess_financial_health(
            evidence,
            sector_profile=resolved_sector_profile,
            assessed_at=assessed_at,
        ),
        assess_earnings_quality(
            evidence,
            sector_profile=resolved_sector_profile,
            assessed_at=assessed_at,
        ),
        assess_forensic_risk(
            evidence,
            sector_profile=resolved_sector_profile,
            assessed_at=assessed_at,
        ),
    )
    mandatory_assessment = FundamentalMandatoryAssessment(
        package_id=evidence.package_id,
        manifest_fingerprint=evidence.manifest_fingerprint,
        normalized_evidence_fingerprint=evidence.evidence_fingerprint,
        issuer=evidence.issuer,
        evidence_normalized_at=evidence.normalized_at,
        package_expires_at=evidence.package_expires_at,
        assessed_at=assessed_at,
        policy_bundle_id=MANDATORY_ASSESSMENT_POLICY_BUNDLE_ID,
        policy_bundle_version=MANDATORY_ASSESSMENT_POLICY_BUNDLE_VERSION,
        domains=domains,
        limitations=tuple(
            dict.fromkeys(
                limitation
                for domain in domains
                for limitation in domain.limitations
            )
        ),
    )
    applicability_assessment = assess_fundamental_framework_applicability(
        evidence,
        mandatory_assessment,
        sector_profile=resolved_sector_profile,
        assessed_at=assessed_at,
    )
    scorecard = build_fundamental_scorecard(
        evidence,
        mandatory_assessment,
        applicability_assessment,
        policy_bundle,
        scored_at=assessed_at,
    )
    return FundamentalIntelligenceResult(
        mandatory_assessment=mandatory_assessment,
        applicability_assessment=applicability_assessment,
        scorecard=scorecard,
        sector_classification=sector_classification,
    )
