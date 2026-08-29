from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from pydantic import ValidationError

from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalConflictRecord,
    FundamentalConflictStatus,
    FundamentalConflictType,
    FundamentalEvidenceConfidence,
    FundamentalEvidenceLabel,
    FundamentalEvidenceLineage,
    FundamentalEvidencePosture,
    FundamentalEvidenceSnapshot,
    FundamentalEvidenceSource,
    FundamentalFreshnessStatus,
    FundamentalIssuerIdentity,
    FundamentalNormalizationMethod,
    FundamentalPeriodType,
    FundamentalReportingPeriod,
    FundamentalSourceRank,
    FundamentalSourceReference,
    FundamentalSourceType,
    FundamentalStatement,
    FundamentalValidationStatus,
    FundamentalValueKind,
    NormalizedFundamentalFact,
    ProviderConnectionScope,
    ProviderEntitlementStatus,
    ProviderSubscriptionTier,
)


IST = timezone(timedelta(hours=5, minutes=30))
RETRIEVED_AT = datetime(2026, 8, 28, 12, 0, tzinfo=IST)
ASSEMBLED_AT = datetime(2026, 8, 28, 12, 5, tzinfo=IST)
SOURCE_HASH = "a" * 64


def build_connection(**overrides) -> ProviderConnectionScope:
    values = {
        "tenant_id": "tenant.prateek",
        "provider_connection_id": "provider.tijori.primary",
        "provider": "tijori",
        "account_reference_hash": "b" * 64,
        "subscription_tier": ProviderSubscriptionTier.PAID,
        "entitlement_status": ProviderEntitlementStatus.VERIFIED,
        "capabilities": ("financials.history", "shareholding.history"),
        "entitlement_checked_at": RETRIEVED_AT,
    }
    values.update(overrides)
    return ProviderConnectionScope(**values)


def build_issuer(**overrides) -> FundamentalIssuerIdentity:
    values = {
        "exchange": "NSE",
        "symbol": "TCS-EQ",
        "legal_name": "Tata Consultancy Services Limited",
        "isin": "INE467B01029",
        "provider_company_id": "1364",
        "provider_slug": "tata-consultancy-services-limited",
    }
    values.update(overrides)
    return FundamentalIssuerIdentity(**values)


def build_source(**overrides) -> FundamentalEvidenceSource:
    values = {
        "source_id": "SRC-TIJORI-001",
        "source_name": "Tijori normalized financials",
        "source_type": FundamentalSourceType.SECONDARY_AGGREGATOR,
        "source_rank": FundamentalSourceRank.SECONDARY_SOURCE,
        "owner_or_provider": "Tijori Finance",
        "location": (
            "https://www.tijorifinance.com/company/"
            "tata-consultancy-services-limited/financials/"
        ),
        "period_covered": "FY2026",
        "as_of_date": date(2026, 3, 31),
        "published_at": datetime(2026, 4, 15, 18, 0, tzinfo=IST),
        "retrieved_at": RETRIEVED_AT,
        "freshness_status": FundamentalFreshnessStatus.CURRENT,
        "content_fingerprint": SOURCE_HASH,
        "parser_name": "jarvis.tijori.financials",
        "parser_version": "1.0.0",
        "provider_schema_version": "tijori.financials.v1",
        "validation_status": FundamentalValidationStatus.VALIDATED,
    }
    values.update(overrides)
    return FundamentalEvidenceSource(**values)


def build_primary_source(**overrides) -> FundamentalEvidenceSource:
    values = {
        "source_id": "SRC-TCS-ANNUAL-REPORT-2026",
        "source_name": "TCS FY2026 annual report",
        "source_type": FundamentalSourceType.ANNUAL_REPORT,
        "source_rank": FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
        "owner_or_provider": "Tata Consultancy Services Limited",
        "location": "https://www.tcs.com/investor-relations/annual-report-2026",
        "parser_name": "jarvis.annual_report.financials",
        "provider_schema_version": "issuer.annual_report.v1",
        "content_fingerprint": "d" * 64,
    }
    values.update(overrides)
    return build_source(**values)


def build_period(**overrides) -> FundamentalReportingPeriod:
    values = {
        "label": "FY2026",
        "period_type": FundamentalPeriodType.ANNUAL,
        "start_date": date(2025, 4, 1),
        "end_date": date(2026, 3, 31),
    }
    values.update(overrides)
    return FundamentalReportingPeriod(**values)


def build_fact(
    source: FundamentalEvidenceSource | None = None,
    **overrides,
) -> NormalizedFundamentalFact:
    source = source or build_source()
    reference = FundamentalSourceReference(
        source_id=source.source_id,
        content_fingerprint=source.content_fingerprint,
    )
    values = {
        "evidence_id": "fundamental:tcs.revenue.fy2026",
        "statement": FundamentalStatement.INCOME_STATEMENT,
        "line_item_original": "Net Sales",
        "line_item_standard": "Revenue",
        "line_item_id": "income_statement.revenue",
        "period": build_period(),
        "value_kind": FundamentalValueKind.MONETARY,
        "source_value": "255,324",
        "normalized_value": Decimal("255324"),
        "currency": "INR",
        "source_unit": "INR crore",
        "normalized_unit": "INR crore",
        "normalization_method": (
            FundamentalNormalizationMethod.SCALED_OR_SIGN_NORMALIZED
        ),
        "source_location": "P&L / Net Sales / FY2026",
        "evidence_label": (
            FundamentalEvidenceLabel.FACT_PROVIDER_STANDARDIZED
        ),
        "confidence": FundamentalEvidenceConfidence.HIGH,
        "availability_status": FundamentalAvailabilityStatus.AVAILABLE,
        "freshness_status": FundamentalFreshnessStatus.CURRENT,
        "validation_status": FundamentalValidationStatus.VALIDATED,
        "conflict_status": FundamentalConflictStatus.NONE,
        "lineage": FundamentalEvidenceLineage(
            source_references=(reference,),
            transformation_id="jarvis.normalize.financial_fact",
            transformation_version="1.0.0",
        ),
    }
    values.update(overrides)
    return NormalizedFundamentalFact(**values)


def build_missing_fact(**overrides) -> NormalizedFundamentalFact:
    values = {
        "evidence_id": "fundamental:tcs.segment.margin.missing",
        "statement": FundamentalStatement.SEGMENT,
        "line_item_original": "Segment margin",
        "line_item_standard": "Segment margin",
        "line_item_id": "segment.margin",
        "period": build_period(),
        "value_kind": FundamentalValueKind.PERCENTAGE,
        "source_value": None,
        "normalized_value": None,
        "currency": None,
        "source_unit": None,
        "normalized_unit": None,
        "normalization_method": FundamentalNormalizationMethod.MAPPED,
        "source_location": "not supplied",
        "evidence_label": (
            FundamentalEvidenceLabel.MISSING_REQUIRED_SOURCE
        ),
        "confidence": FundamentalEvidenceConfidence.LOW,
        "availability_status": (
            FundamentalAvailabilityStatus.MISSING_REQUIRED_SOURCE
        ),
        "freshness_status": FundamentalFreshnessStatus.UNKNOWN,
        "validation_status": FundamentalValidationStatus.REJECTED,
        "conflict_status": FundamentalConflictStatus.NONE,
        "lineage": FundamentalEvidenceLineage(
            transformation_id="jarvis.missing_source_marker",
            transformation_version="1.0.0",
        ),
        "normalization_note": "Required segment disclosure was unavailable.",
    }
    values.update(overrides)
    return NormalizedFundamentalFact(**values)


def build_snapshot(**overrides) -> FundamentalEvidenceSnapshot:
    source = overrides.pop("source", build_source())
    facts = overrides.pop("facts", (build_fact(source),))
    values = {
        "snapshot_id": "fundamentals.tcs.2026-08-28",
        "connection": build_connection(),
        "issuer": build_issuer(),
        "sources": (source,),
        "facts": facts,
        "evidence_posture": FundamentalEvidencePosture.RESEARCH_GRADE,
        "validation_status": FundamentalValidationStatus.VALIDATED,
        "assembled_at": ASSEMBLED_AT,
    }
    values.update(overrides)
    return FundamentalEvidenceSnapshot(**values)


def rebuild(model, **overrides):
    values = model.model_dump(exclude_computed_fields=True)
    values.update(overrides)
    return type(model)(**values)


class ProviderConnectionScopeTests(unittest.TestCase):
    def test_builds_tenant_scoped_non_secret_connection(self):
        connection = build_connection()

        self.assertEqual(connection.tenant_id, "tenant.prateek")
        self.assertEqual(connection.provider, "tijori")
        self.assertEqual(connection.subscription_tier.value, "paid")
        self.assertNotIn("password", connection.model_dump())

    def test_rejects_secret_fields_without_echoing_secret_value(self):
        with self.assertRaises(ValidationError) as context:
            ProviderConnectionScope(
                **build_connection().model_dump(),
                password="never-print-this-secret",
            )

        self.assertNotIn("never-print-this-secret", str(context.exception))

    def test_rejects_duplicate_capabilities_case_insensitively(self):
        with self.assertRaises(ValidationError):
            build_connection(capabilities=("Financials", "financials"))

    def test_rejects_verified_entitlement_without_check_time(self):
        with self.assertRaises(ValidationError):
            build_connection(entitlement_checked_at=None)

    def test_rejects_naive_entitlement_check_time(self):
        with self.assertRaises(ValidationError):
            build_connection(
                entitlement_checked_at=datetime(2026, 8, 28, 12, 0)
            )

    def test_connection_is_immutable(self):
        connection = build_connection()

        with self.assertRaises(ValidationError):
            connection.provider = "angel_one"


class FundamentalIdentityAndSourceTests(unittest.TestCase):
    def test_normalizes_exchange_symbol_and_isin(self):
        issuer = build_issuer(exchange="nse", symbol="tcs-eq", isin="ine467b01029")

        self.assertEqual(issuer.exchange, "NSE")
        self.assertEqual(issuer.symbol, "TCS-EQ")
        self.assertEqual(issuer.isin, "INE467B01029")

    def test_rejects_invalid_isin(self):
        with self.assertRaises(ValidationError):
            build_issuer(isin="INVALID")

    def test_builds_valid_secondary_source(self):
        source = build_source()

        self.assertEqual(source.source_rank, FundamentalSourceRank.SECONDARY_SOURCE)
        self.assertEqual(
            source.retrieved_at.utcoffset(),
            timedelta(hours=5, minutes=30),
        )

    def test_rejects_rank_that_misrepresents_source_type(self):
        with self.assertRaises(ValidationError):
            build_source(
                source_rank=FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE
            )

    def test_rejects_unspecified_inventory_source(self):
        with self.assertRaises(ValidationError):
            build_source(source_id="SRC-UNSPECIFIED")

    def test_rejects_validated_source_without_as_of_date(self):
        with self.assertRaises(ValidationError):
            build_source(
                as_of_date=None,
                freshness_status=FundamentalFreshnessStatus.UNKNOWN,
            )

    def test_requires_unknown_freshness_when_as_of_date_is_missing(self):
        with self.assertRaises(ValidationError):
            build_source(as_of_date=None)

    def test_rejects_future_source_dates(self):
        with self.assertRaises(ValidationError):
            build_source(as_of_date=date(2026, 8, 29))

        with self.assertRaises(ValidationError):
            build_source(
                published_at=RETRIEVED_AT + timedelta(minutes=1)
            )

    def test_rejects_naive_source_timestamp(self):
        with self.assertRaises(ValidationError):
            build_source(retrieved_at=datetime(2026, 8, 28, 12, 0))

    def test_reporting_period_rejects_reversed_dates(self):
        with self.assertRaises(ValidationError):
            build_period(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 3, 31),
            )


class NormalizedFundamentalFactTests(unittest.TestCase):
    def test_builds_available_normalized_monetary_fact(self):
        fact = build_fact()

        self.assertEqual(fact.normalized_value, Decimal("255324"))
        self.assertEqual(fact.currency, "INR")
        self.assertEqual(len(fact.lineage.source_references), 1)

    def test_rejects_float_for_decimal_value_in_strict_model(self):
        with self.assertRaises(ValidationError):
            build_fact(normalized_value=255324.0)

    def test_rejects_non_finite_decimal(self):
        with self.assertRaises(ValidationError):
            build_fact(normalized_value=Decimal("NaN"))

    def test_rejects_available_fact_without_value_units_or_lineage(self):
        with self.assertRaises(ValidationError):
            build_fact(normalized_value=None)

        with self.assertRaises(ValidationError):
            build_fact(normalized_unit=None)

        with self.assertRaises(ValidationError):
            build_fact(
                lineage=FundamentalEvidenceLineage(
                    transformation_id="jarvis.normalize.financial_fact",
                    transformation_version="1.0.0",
                )
            )

    def test_rejects_unavailable_fact_with_normalized_value(self):
        with self.assertRaises(ValidationError):
            build_fact(
                availability_status=(
                    FundamentalAvailabilityStatus.PROVIDER_UNAVAILABLE
                )
            )

    def test_rejects_available_monetary_fact_without_currency(self):
        with self.assertRaises(ValidationError):
            build_fact(currency=None)

    def test_builds_explicit_missing_required_source_marker(self):
        fact = build_missing_fact()

        self.assertIsNone(fact.normalized_value)
        self.assertEqual(
            fact.evidence_label,
            FundamentalEvidenceLabel.MISSING_REQUIRED_SOURCE,
        )

    def test_rejects_missing_marker_that_claims_confidence_or_source(self):
        with self.assertRaises(ValidationError):
            build_missing_fact(confidence=FundamentalEvidenceConfidence.HIGH)

        with self.assertRaises(ValidationError):
            build_missing_fact(lineage=build_fact().lineage)

    def test_derived_calculation_requires_parents_and_formula(self):
        with self.assertRaises(ValidationError):
            build_fact(
                evidence_label=FundamentalEvidenceLabel.DERIVED_CALCULATION,
                normalization_method=FundamentalNormalizationMethod.CALCULATED,
            )

        lineage = rebuild(
            build_fact().lineage,
            parent_evidence_ids=("fundamental:tcs.parent",),
            formula="revenue / shares",
        )
        fact = build_fact(
            evidence_label=FundamentalEvidenceLabel.DERIVED_CALCULATION,
            normalization_method=FundamentalNormalizationMethod.CALCULATED,
            lineage=lineage,
        )
        self.assertEqual(fact.lineage.formula, "revenue / shares")

    def test_source_reported_fact_cannot_claim_parent_evidence(self):
        lineage = rebuild(
            build_fact().lineage,
            parent_evidence_ids=("fundamental:tcs.parent",),
        )
        with self.assertRaises(ValidationError):
            build_fact(
                evidence_label=FundamentalEvidenceLabel.FACT_SOURCE_REPORTED,
                lineage=lineage,
            )

    def test_stale_and_contradicted_labels_require_matching_state(self):
        with self.assertRaises(ValidationError):
            build_fact(evidence_label=FundamentalEvidenceLabel.STALE_SOURCE)

        with self.assertRaises(ValidationError):
            build_fact(
                evidence_label=(
                    FundamentalEvidenceLabel.CONTRADICTED_SOURCE
                )
            )


class FundamentalConflictRecordTests(unittest.TestCase):
    def test_builds_explained_resolved_conflict(self):
        conflict = FundamentalConflictRecord(
            conflict_id="conflict.revenue.fy2026",
            conflict_type=FundamentalConflictType.PROVIDER_STANDARDIZATION,
            evidence_ids=(
                "fundamental:tcs.revenue.reported",
                "fundamental:tcs.revenue.provider",
            ),
            status=FundamentalConflictStatus.RESOLVED,
            working_evidence_id="fundamental:tcs.revenue.reported",
            resolution_basis="The exchange filing is authoritative.",
        )

        self.assertEqual(conflict.status, FundamentalConflictStatus.RESOLVED)

    def test_unresolved_conflict_cannot_select_working_fact(self):
        with self.assertRaises(ValidationError):
            FundamentalConflictRecord(
                conflict_id="conflict.revenue.fy2026",
                conflict_type=FundamentalConflictType.UNKNOWN,
                evidence_ids=(
                    "fundamental:tcs.revenue.a",
                    "fundamental:tcs.revenue.b",
                ),
                status=FundamentalConflictStatus.UNRESOLVED,
                working_evidence_id="fundamental:tcs.revenue.a",
            )

    def test_conflict_requires_unique_evidence_ids(self):
        with self.assertRaises(ValidationError):
            FundamentalConflictRecord(
                conflict_id="conflict.revenue.fy2026",
                conflict_type=FundamentalConflictType.UNKNOWN,
                evidence_ids=(
                    "fundamental:tcs.revenue.a",
                    "fundamental:tcs.revenue.a",
                ),
                status=FundamentalConflictStatus.UNRESOLVED,
            )


class FundamentalEvidenceSnapshotTests(unittest.TestCase):
    def test_builds_tenant_isolated_snapshot_with_stable_fingerprint(self):
        snapshot = build_snapshot()
        duplicate = build_snapshot()

        self.assertEqual(snapshot.connection.tenant_id, "tenant.prateek")
        self.assertEqual(snapshot.snapshot_fingerprint, duplicate.snapshot_fingerprint)
        self.assertRegex(snapshot.snapshot_fingerprint, r"^[a-f0-9]{64}$")

    def test_fingerprint_changes_when_evidence_changes(self):
        source = build_source()
        first = build_snapshot(source=source)
        changed_fact = rebuild(
            build_fact(source),
            normalized_value=Decimal("255325"),
        )
        second = build_snapshot(source=source, facts=(changed_fact,))

        self.assertNotEqual(first.snapshot_fingerprint, second.snapshot_fingerprint)

    def test_rejects_duplicate_source_or_evidence_ids(self):
        source = build_source()
        with self.assertRaises(ValidationError):
            build_snapshot(sources=(source, source))

        fact = build_fact(source)
        with self.assertRaises(ValidationError):
            build_snapshot(source=source, facts=(fact, fact))

    def test_rejects_unknown_or_mismatched_source_lineage(self):
        source = build_source()
        unknown_reference = FundamentalSourceReference(
            source_id="SRC-UNKNOWN-001",
            content_fingerprint="c" * 64,
        )
        unknown_lineage = rebuild(
            build_fact(source).lineage,
            source_references=(unknown_reference,),
        )
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(build_fact(source, lineage=unknown_lineage),),
            )

        bad_reference = FundamentalSourceReference(
            source_id=source.source_id,
            content_fingerprint="c" * 64,
        )
        bad_lineage = rebuild(
            build_fact(source).lineage,
            source_references=(bad_reference,),
        )
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(build_fact(source, lineage=bad_lineage),),
            )

    def test_rejects_unknown_self_or_cyclic_parent_lineage(self):
        source = build_source()
        base = build_fact(source)
        unknown_parent_lineage = rebuild(
            base.lineage,
            parent_evidence_ids=("fundamental:tcs.unknown",),
            formula="unknown",
        )
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(
                    rebuild(
                        base,
                        evidence_label=(
                            FundamentalEvidenceLabel.DERIVED_CALCULATION
                        ),
                        normalization_method=(
                            FundamentalNormalizationMethod.CALCULATED
                        ),
                        lineage=unknown_parent_lineage,
                    ),
                ),
            )

        self_parent_lineage = rebuild(
            base.lineage,
            parent_evidence_ids=(base.evidence_id,),
            formula="self",
        )
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(
                    rebuild(
                        base,
                        evidence_label=(
                            FundamentalEvidenceLabel.DERIVED_CALCULATION
                        ),
                        normalization_method=(
                            FundamentalNormalizationMethod.CALCULATED
                        ),
                        lineage=self_parent_lineage,
                    ),
                ),
            )

        fact_a_id = "fundamental:tcs.cycle.a"
        fact_b_id = "fundamental:tcs.cycle.b"
        fact_a = rebuild(
            base,
            evidence_id=fact_a_id,
            evidence_label=FundamentalEvidenceLabel.DERIVED_CALCULATION,
            normalization_method=FundamentalNormalizationMethod.CALCULATED,
            lineage=rebuild(
                base.lineage,
                parent_evidence_ids=(fact_b_id,),
                formula="b",
            ),
        )
        fact_b = rebuild(
            base,
            evidence_id=fact_b_id,
            evidence_label=FundamentalEvidenceLabel.DERIVED_CALCULATION,
            normalization_method=FundamentalNormalizationMethod.CALCULATED,
            lineage=rebuild(
                base.lineage,
                parent_evidence_ids=(fact_a_id,),
                formula="a",
            ),
        )
        with self.assertRaises(ValidationError):
            build_snapshot(source=source, facts=(fact_a, fact_b))

    def test_rejects_source_reported_label_on_aggregator_data(self):
        source = build_source()
        fact = build_fact(
            source,
            evidence_label=FundamentalEvidenceLabel.FACT_SOURCE_REPORTED,
        )

        with self.assertRaises(ValidationError):
            build_snapshot(source=source, facts=(fact,))

    def test_rejects_unsourced_source_reported_marker(self):
        fact = build_missing_fact(
            evidence_label=FundamentalEvidenceLabel.FACT_SOURCE_REPORTED,
        )

        with self.assertRaises(ValidationError):
            build_snapshot(
                facts=(fact,),
                validation_status=FundamentalValidationStatus.REJECTED,
            )

    def test_rejects_validated_fact_from_unvalidated_source(self):
        source = build_source(
            validation_status=FundamentalValidationStatus.PARTIAL
        )
        fact = build_fact(source)

        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(fact,),
                validation_status=FundamentalValidationStatus.PARTIAL,
            )

    def test_rejects_current_fact_that_upgrades_stale_source(self):
        source = build_source(
            freshness_status=FundamentalFreshnessStatus.STALE
        )
        fact = build_fact(source)

        with self.assertRaises(ValidationError):
            build_snapshot(source=source, facts=(fact,))

    def test_rejects_snapshot_assembled_before_source_retrieval(self):
        with self.assertRaises(ValidationError):
            build_snapshot(assembled_at=RETRIEVED_AT - timedelta(seconds=1))

    def test_rejects_validated_snapshot_with_rejected_fact(self):
        source = build_source()
        with self.assertRaises(ValidationError):
            build_snapshot(source=source, facts=(build_missing_fact(),))

    def test_research_grade_preserves_unresolved_conflict(self):
        source = build_source()
        first = build_fact(
            source,
            evidence_id="fundamental:tcs.revenue.source_a",
            evidence_label=FundamentalEvidenceLabel.CONTRADICTED_SOURCE,
            conflict_status=FundamentalConflictStatus.UNRESOLVED,
        )
        second = build_fact(
            source,
            evidence_id="fundamental:tcs.revenue.source_b",
            normalized_value=Decimal("255300"),
            evidence_label=FundamentalEvidenceLabel.CONTRADICTED_SOURCE,
            conflict_status=FundamentalConflictStatus.UNRESOLVED,
        )
        conflict = FundamentalConflictRecord(
            conflict_id="conflict.tcs.revenue.fy2026",
            conflict_type=FundamentalConflictType.PROVIDER_STANDARDIZATION,
            evidence_ids=(first.evidence_id, second.evidence_id),
            status=FundamentalConflictStatus.UNRESOLVED,
        )
        snapshot = build_snapshot(
            source=source,
            facts=(first, second),
            conflicts=(conflict,),
        )

        self.assertEqual(len(snapshot.conflicts), 1)
        self.assertEqual(
            snapshot.conflicts[0].status,
            FundamentalConflictStatus.UNRESOLVED,
        )

    def test_conflict_must_reference_same_metric_period_and_matching_state(self):
        source = build_source()
        first = build_fact(
            source,
            evidence_id="fundamental:tcs.revenue.source_a",
            evidence_label=FundamentalEvidenceLabel.CONTRADICTED_SOURCE,
            conflict_status=FundamentalConflictStatus.UNRESOLVED,
        )
        second = build_fact(
            source,
            evidence_id="fundamental:tcs.profit.source_b",
            line_item_original="Net profit",
            line_item_standard="Net income",
            line_item_id="income_statement.net_income",
            evidence_label=FundamentalEvidenceLabel.CONTRADICTED_SOURCE,
            conflict_status=FundamentalConflictStatus.UNRESOLVED,
        )
        conflict = FundamentalConflictRecord(
            conflict_id="conflict.tcs.bad",
            conflict_type=FundamentalConflictType.MAPPING,
            evidence_ids=(first.evidence_id, second.evidence_id),
            status=FundamentalConflictStatus.UNRESOLVED,
        )
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(first, second),
                conflicts=(conflict,),
            )

        mismatch = rebuild(
            second,
            line_item_original=first.line_item_original,
            line_item_standard=first.line_item_standard,
            line_item_id=first.line_item_id,
            conflict_status=FundamentalConflictStatus.RESOLVED,
            evidence_label=(
                FundamentalEvidenceLabel.FACT_PROVIDER_STANDARDIZED
            ),
        )
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(first, mismatch),
                conflicts=(conflict,),
            )

    def test_decision_grade_rejects_stale_missing_or_unresolved_evidence(self):
        source = build_source()
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=source,
                facts=(build_missing_fact(),),
                evidence_posture=FundamentalEvidencePosture.DECISION_GRADE,
                validation_status=FundamentalValidationStatus.REJECTED,
            )

        stale_source = build_source(
            freshness_status=FundamentalFreshnessStatus.STALE
        )
        stale_fact = build_fact(
            stale_source,
            freshness_status=FundamentalFreshnessStatus.STALE,
            evidence_label=FundamentalEvidenceLabel.STALE_SOURCE,
        )
        with self.assertRaises(ValidationError):
            build_snapshot(
                source=stale_source,
                facts=(stale_fact,),
                evidence_posture=FundamentalEvidencePosture.DECISION_GRADE,
            )

    def test_decision_grade_allows_explained_resolved_conflict(self):
        primary_source = build_primary_source()
        provider_source = build_source()
        first = build_fact(
            primary_source,
            evidence_id="fundamental:tcs.revenue.reported",
            evidence_label=FundamentalEvidenceLabel.FACT_SOURCE_REPORTED,
            conflict_status=FundamentalConflictStatus.RESOLVED,
        )
        provider_lineage = rebuild(
            build_fact(provider_source).lineage,
            parent_evidence_ids=(first.evidence_id,),
        )
        second = build_fact(
            provider_source,
            evidence_id="fundamental:tcs.revenue.provider",
            normalized_value=Decimal("255300"),
            conflict_status=FundamentalConflictStatus.RESOLVED,
            lineage=provider_lineage,
        )
        conflict = FundamentalConflictRecord(
            conflict_id="conflict.tcs.revenue.resolved",
            conflict_type=FundamentalConflictType.PROVIDER_STANDARDIZATION,
            evidence_ids=(first.evidence_id, second.evidence_id),
            status=FundamentalConflictStatus.RESOLVED,
            working_evidence_id=first.evidence_id,
            resolution_basis="The primary-period value was selected.",
        )
        snapshot = build_snapshot(
            source=primary_source,
            sources=(primary_source, provider_source),
            facts=(first, second),
            conflicts=(conflict,),
            evidence_posture=FundamentalEvidencePosture.DECISION_GRADE,
        )

        self.assertEqual(
            snapshot.evidence_posture,
            FundamentalEvidencePosture.DECISION_GRADE,
        )

    def test_decision_grade_rejects_secondary_only_evidence(self):
        source = build_source()

        with self.assertRaisesRegex(
            ValidationError,
            "provider data cannot be the sole source",
        ):
            build_snapshot(
                source=source,
                evidence_posture=FundamentalEvidencePosture.DECISION_GRADE,
            )


if __name__ == "__main__":
    unittest.main()
