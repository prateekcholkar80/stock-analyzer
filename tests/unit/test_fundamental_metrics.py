import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.analytics.fundamental_metrics import (
    DERIVED_FORMULA_POLICY_VERSION,
    DerivedFundamentalMetricIssueCode,
    FundamentalMetricNormalizationIssueCode,
    derive_fundamental_metrics,
    normalize_financial_document_metrics,
    parse_indian_financial_number,
)
from app.models.financial_documents import (
    FinancialDocumentCell,
    FinancialDocumentPeriod,
    FinancialDocumentRow,
    FinancialDocumentRowKind,
    FinancialDocumentType,
    FinancialReportingBasis,
    StructuredFinancialDocument,
)
from app.models.fundamental_metrics import (
    FundamentalMetricAvailability,
    FundamentalMetricCalculation,
    FundamentalMetricCitation,
    FundamentalMetricKey,
    FundamentalMetricPeriod,
    FundamentalMetricPeriodKind,
    FundamentalMetricQualityFlag,
    FundamentalMetricSourceBinding,
    FundamentalMetricUnit,
    FundamentalMetricValueStatus,
    NormalizedFinancialEvidence,
    NormalizedFundamentalMetric,
)
from app.models.fundamental_package import (
    FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
    FundamentalPackageDataset,
    FundamentalPackageItemKey,
)
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalNormalizationMethod,
    FundamentalPeriodType,
    FundamentalValidationStatus,
    FundamentalValueKind,
    ProviderConnectionScope,
)
from tests.unit.test_fundamental_gateway import build_issuer


IST = ZoneInfo("Asia/Kolkata")
COMMITTED_AT = datetime(2026, 9, 12, 10, 0, tzinfo=IST)
NORMALIZED_AT = COMMITTED_AT + timedelta(seconds=1)
EXPIRES_AT = COMMITTED_AT + timedelta(days=10)
MANIFEST_FINGERPRINT = "a" * 64


def sources():
    return tuple(
        FundamentalMetricSourceBinding(
            package_item_key=key,
            source_reference_id=f"fundamental_source:item-{index}",
            content_fingerprint=f"{index + 1:064x}",
            retrieved_at=COMMITTED_AT - timedelta(seconds=20 - index),
            expires_at=EXPIRES_AT,
        )
        for index, key in enumerate(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS)
    )


def package_aggregate_fingerprint(source_bindings):
    payload = {
        "schema_version": "jarvis.fundamental_evidence_package.v1",
        "manifest_fingerprint": MANIFEST_FINGERPRINT,
        "artifacts": sorted(
            (
                {
                    "key": source.package_item_key.canonical_key,
                    "cache_entry_id": source.source_reference_id,
                    "content_fingerprint": source.content_fingerprint,
                }
                for source in source_bindings
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


def period(**updates):
    values = {
        "period_key": "fy2026",
        "source_label": "MAR'26",
        "period_kind": FundamentalMetricPeriodKind.ANNUAL,
        "start_date": date(2025, 4, 1),
        "end_date": date(2026, 3, 31),
        "availability": FundamentalMetricAvailability.UNKNOWN,
        "available_at": None,
    }
    values.update(updates)
    return FundamentalMetricPeriod(**values)


def unit(**updates):
    values = {
        "value_kind": FundamentalValueKind.MONETARY,
        "normalized_unit": "INR_CRORE",
        "scale": Decimal("10000000"),
        "currency": "INR",
    }
    values.update(updates)
    return FundamentalMetricUnit(**values)


def citation(source_index=4, **updates):
    source = sources()[source_index]
    values = {
        "package_item_key": source.package_item_key,
        "source_reference_id": source.source_reference_id,
        "content_fingerprint": source.content_fingerprint,
        "source_path": (
            "rows",
            "revenue",
            "cells",
            "fy2026",
        ),
        "source_label": "Revenue",
        "period_key": "fy2026",
    }
    values.update(updates)
    return FundamentalMetricCitation(**values)


def metric(**updates):
    values = {
        "key": FundamentalMetricKey(
            metric_id="income.revenue",
            reporting_basis=FinancialReportingBasis.CONSOLIDATED,
            period_key="fy2026",
        ),
        "period": period(),
        "value_status": FundamentalMetricValueStatus.PRESENT,
        "value": Decimal("1234.50"),
        "unit": unit(),
        "source_values": ("1,234.50",),
        "normalization_method": (
            FundamentalNormalizationMethod.SCALED_OR_SIGN_NORMALIZED
        ),
        "normalization_rule_id": "normalize.inr_crore",
        "normalization_rule_version": "1.0.0",
        "calculation": None,
        "citations": (citation(),),
        "quality_flags": (
            FundamentalMetricQualityFlag.SOURCE_AVAILABILITY_UNKNOWN,
            FundamentalMetricQualityFlag.PROVIDER_STANDARDIZED,
        ),
    }
    values.update(updates)
    return NormalizedFundamentalMetric(**values)


def evidence(**updates):
    source_bindings = updates.pop("sources", sources())
    values = {
        "package_id": f"fundamental_package:{MANIFEST_FINGERPRINT}",
        "manifest_fingerprint": MANIFEST_FINGERPRINT,
        "package_aggregate_fingerprint": package_aggregate_fingerprint(
            source_bindings
        ),
        "issuer": build_issuer(),
        "package_committed_at": COMMITTED_AT,
        "package_expires_at": EXPIRES_AT,
        "normalized_at": NORMALIZED_AT,
        "normalization_policy_id": "jarvis.fundamental_normalization",
        "normalization_policy_version": "1.0.0",
        "sources": source_bindings,
        "metrics": (metric(),),
    }
    values.update(updates)
    return NormalizedFinancialEvidence(**values)


def document_cell(
    period_key: str,
    source_value: str | None,
    normalized_value: Decimal | None,
    *,
    source_unit: str | None,
    normalized_unit: str | None,
    availability_status: FundamentalAvailabilityStatus | None = None,
) -> FinancialDocumentCell:
    return FinancialDocumentCell(
        period_key=period_key,
        source_value=source_value,
        normalized_value=normalized_value,
        source_unit=source_unit,
        normalized_unit=normalized_unit,
        availability_status=availability_status or (
            FundamentalAvailabilityStatus.AVAILABLE
            if source_value is not None
            else FundamentalAvailabilityStatus.UNKNOWN
        ),
    )


def document_row(
    *,
    row_key: str,
    label: str,
    display_order: int,
    value_kind: FundamentalValueKind,
    values: tuple[tuple[str | None, Decimal | None], ...],
    normalized_unit: str,
    standardized_label: str | None = None,
) -> FinancialDocumentRow:
    period_keys = ("fy2025", "fy2026")[: len(values)]
    return FinancialDocumentRow(
        row_key=row_key,
        original_label=label,
        standardized_label=standardized_label,
        depth=0,
        row_kind=FinancialDocumentRowKind.METRIC,
        value_kind=value_kind,
        display_order=display_order,
        cells=tuple(
            document_cell(
                period_key,
                source_value,
                normalized_value,
                source_unit=normalized_unit if source_value is not None else None,
                normalized_unit=(
                    normalized_unit if source_value is not None else None
                ),
            )
            for period_key, (source_value, normalized_value) in zip(
                period_keys,
                values,
                strict=True,
            )
        ),
    )


def financial_document(
    *rows: FinancialDocumentRow,
    document_type: FinancialDocumentType = FinancialDocumentType.PROFIT_AND_LOSS,
    reporting_basis: FinancialReportingBasis = FinancialReportingBasis.CONSOLIDATED,
    periods: tuple[FinancialDocumentPeriod, ...] | None = None,
) -> StructuredFinancialDocument:
    selected_periods = periods or tuple(
        FinancialDocumentPeriod(
            period_key=period_key,
            source_label=source_label,
            period_type=FundamentalPeriodType.ANNUAL,
            end_date=end_date,
            display_order=index,
        )
        for index, (period_key, source_label, end_date) in enumerate(
            (
                ("fy2025", "MAR'25", date(2025, 3, 31)),
                ("fy2026", "MAR'26", date(2026, 3, 31)),
            )[: len(rows[0].cells)]
        )
    )
    return StructuredFinancialDocument(
        document_id=(
            f"tijori.NSE.COFORGE.{document_type.value}."
            f"{reporting_basis.value}"
        ),
        connection=ProviderConnectionScope(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
        ),
        issuer=build_issuer(),
        document_type=document_type,
        reporting_basis=reporting_basis,
        currency=(
            None
            if document_type is FinancialDocumentType.GROWTH_TABLE
            else "INR"
        ),
        source_unit=(
            "percent"
            if document_type is FinancialDocumentType.GROWTH_TABLE
            else "mixed"
        ),
        periods=selected_periods,
        rows=rows,
        source_location=(
            "https://www.tijorifinance.com/company/"
            "niit-technologies-limited/financials/"
        ),
        retrieved_at=COMMITTED_AT - timedelta(seconds=1),
        expires_at=EXPIRES_AT,
        all_sections_expanded=True,
        validation_status=FundamentalValidationStatus.VALIDATED,
    )


def financial_source(
    document: StructuredFinancialDocument,
    **updates,
) -> FundamentalMetricSourceBinding:
    values = {
        "package_item_key": FundamentalPackageItemKey(
            dataset=FundamentalPackageDataset(document.document_type.value),
            reporting_basis=document.reporting_basis,
        ),
        "source_reference_id": (
            f"fundamental_source:{document.document_type.value}."
            f"{document.reporting_basis.value}"
        ),
        "content_fingerprint": document.document_fingerprint,
        "retrieved_at": document.retrieved_at,
        "expires_at": document.expires_at,
    }
    values.update(updates)
    return FundamentalMetricSourceBinding(**values)


def normalized_input(
    metric_id: str,
    value: Decimal | None,
    *,
    period_key: str = "annual:fy2026",
    period_end: date = date(2026, 3, 31),
    period_kind: FundamentalMetricPeriodKind = (
        FundamentalMetricPeriodKind.ANNUAL
    ),
    reporting_basis: FinancialReportingBasis = (
        FinancialReportingBasis.CONSOLIDATED
    ),
    metric_unit: FundamentalMetricUnit | None = None,
    source_index: int = 4,
    value_status: FundamentalMetricValueStatus | None = None,
) -> NormalizedFundamentalMetric:
    selected_unit = metric_unit or unit()
    selected_status = value_status or (
        FundamentalMetricValueStatus.ZERO
        if value == 0
        else FundamentalMetricValueStatus.PRESENT
        if value is not None
        else FundamentalMetricValueStatus.MISSING
    )
    source_value = str(value) if value is not None else None
    flags = [
        FundamentalMetricQualityFlag.SOURCE_AVAILABILITY_UNKNOWN,
        FundamentalMetricQualityFlag.PROVIDER_STANDARDIZED,
    ]
    if selected_status is FundamentalMetricValueStatus.MISSING:
        flags.append(FundamentalMetricQualityFlag.SOURCE_VALUE_MISSING)
    source_binding = sources()[source_index]
    source_period_key = period_key.split(":", maxsplit=1)[-1]
    return NormalizedFundamentalMetric(
        key=FundamentalMetricKey(
            metric_id=metric_id,
            reporting_basis=reporting_basis,
            period_key=period_key,
        ),
        period=FundamentalMetricPeriod(
            period_key=period_key,
            source_label=period_end.isoformat(),
            period_kind=period_kind,
            end_date=period_end,
            availability=FundamentalMetricAvailability.UNKNOWN,
        ),
        value_status=selected_status,
        value=value,
        unit=selected_unit,
        source_values=((source_value,) if source_value is not None else ()),
        normalization_method=FundamentalNormalizationMethod.MAPPED,
        normalization_rule_id="normalize.test.fixture",
        normalization_rule_version="1.0.0",
        citations=(
            FundamentalMetricCitation(
                package_item_key=source_binding.package_item_key,
                source_reference_id=source_binding.source_reference_id,
                content_fingerprint=source_binding.content_fingerprint,
                source_path=(
                    "rows",
                    metric_id,
                    "cells",
                    source_period_key,
                ),
                source_label=metric_id,
                period_key=source_period_key,
            ),
        ),
        quality_flags=tuple(flags),
    )


def ratio_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.RATIO,
        normalized_unit="RATIO",
        scale=Decimal("1"),
    )


class FundamentalMetricContractTests(unittest.TestCase):
    def test_builds_immutable_package_bound_evidence(self):
        result = evidence()

        self.assertEqual(
            result.schema_version,
            "jarvis.normalized_financial_evidence.v1",
        )
        self.assertEqual(len(result.sources), 16)
        self.assertEqual(len(result.evidence_fingerprint), 64)
        self.assertEqual(result.metrics[0].value, Decimal("1234.50"))
        with self.assertRaises(ValidationError):
            result.metrics = ()

    def test_preserves_explicit_zero_separately_from_missing(self):
        zero = metric(
            value_status=FundamentalMetricValueStatus.ZERO,
            value=Decimal("0"),
            source_values=("0.00",),
        )
        missing = metric(
            value_status=FundamentalMetricValueStatus.MISSING,
            value=None,
            source_values=("—",),
            quality_flags=(
                FundamentalMetricQualityFlag.SOURCE_AVAILABILITY_UNKNOWN,
                FundamentalMetricQualityFlag.SOURCE_VALUE_MISSING,
                FundamentalMetricQualityFlag.PROVIDER_STANDARDIZED,
            ),
        )

        self.assertIsNotNone(zero.value)
        self.assertIsNone(missing.value)
        self.assertNotEqual(zero.value_status, missing.value_status)

    def test_rejects_value_status_contradictions_and_non_finite_values(self):
        cases = (
            {
                "value_status": FundamentalMetricValueStatus.PRESENT,
                "value": Decimal("0"),
            },
            {
                "value_status": FundamentalMetricValueStatus.ZERO,
                "value": Decimal("1"),
            },
            {
                "value_status": FundamentalMetricValueStatus.MISSING,
                "value": Decimal("1"),
            },
            {"value": Decimal("NaN")},
        )

        for updates in cases:
            with self.subTest(updates=updates):
                with self.assertRaises(ValidationError):
                    metric(**updates)

    def test_requires_matching_quality_flags_for_unavailable_states(self):
        cases = (
            FundamentalMetricValueStatus.MISSING,
            FundamentalMetricValueStatus.PARSE_ERROR,
            FundamentalMetricValueStatus.CONFLICT,
        )

        for status in cases:
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValidationError, "quality flag"):
                    metric(
                        value_status=status,
                        value=None,
                        source_values=("unavailable", "conflicting"),
                        citations=(citation(), citation(5)),
                    )

    def test_requires_formula_lineage_only_for_calculated_metrics(self):
        calculation = FundamentalMetricCalculation(
            formula_id="cash_conversion.ocf_to_pat",
            formula_version="1.0.0",
            input_metric_keys=(
                "cash_flow.operating:consolidated:fy2026",
                "income.pat:consolidated:fy2026",
            ),
        )
        calculated = metric(
            normalization_method=FundamentalNormalizationMethod.CALCULATED,
            calculation=calculation,
            source_values=(),
        )
        self.assertEqual(calculated.calculation, calculation)

        with self.assertRaisesRegex(ValidationError, "formula lineage"):
            metric(calculation=calculation)
        with self.assertRaisesRegex(ValidationError, "formula lineage"):
            metric(
                normalization_method=FundamentalNormalizationMethod.CALCULATED,
                calculation=None,
                source_values=(),
            )

    def test_enforces_period_availability_and_forecast_markers(self):
        with self.assertRaisesRegex(ValidationError, "availability status"):
            period(
                availability=FundamentalMetricAvailability.KNOWN,
                available_at=None,
            )
        with self.assertRaisesRegex(ValidationError, "requires timezone"):
            period(
                availability=FundamentalMetricAvailability.KNOWN,
                available_at=datetime(2026, 5, 1, 12, 0),
            )
        with self.assertRaisesRegex(ValidationError, "forecast"):
            metric(period=period(is_forecast=True))

    def test_enforces_unit_currency_semantics(self):
        with self.assertRaisesRegex(ValidationError, "require currency"):
            unit(currency=None)
        with self.assertRaisesRegex(ValidationError, "require currency"):
            unit(
                value_kind=FundamentalValueKind.RATIO,
                normalized_unit="RATIO",
                scale=Decimal("1"),
                currency="INR",
            )

    def test_requires_all_sixteen_unique_active_sources(self):
        with self.assertRaises(ValidationError):
            evidence(sources=sources()[:-1])

        duplicate = sources()[:-1] + (sources()[0],)
        with self.assertRaisesRegex(ValidationError, "scenarios must be unique"):
            evidence(sources=duplicate)

        expired = sources()[0].model_copy(
            update={"expires_at": NORMALIZED_AT}
        )
        with self.assertRaisesRegex(ValidationError, "expired source"):
            evidence(sources=(expired, *sources()[1:]))

    def test_cryptographically_binds_sources_to_the_committed_package(self):
        with self.assertRaisesRegex(ValidationError, "package ID"):
            evidence(package_id="fundamental_package:wrong")

        with self.assertRaisesRegex(ValidationError, "package fingerprint"):
            evidence(package_aggregate_fingerprint="f" * 64)

        source_bindings = sources()
        mismatched_expiry = source_bindings[-1].model_copy(
            update={"expires_at": EXPIRES_AT - timedelta(seconds=1)}
        )
        changed_sources = (*source_bindings[:-1], mismatched_expiry)
        with self.assertRaisesRegex(ValidationError, "expiry must match"):
            evidence(
                sources=changed_sources,
                package_expires_at=EXPIRES_AT,
            )

        after_commit = source_bindings[0].model_copy(
            update={"retrieved_at": COMMITTED_AT + timedelta(microseconds=1)}
        )
        with self.assertRaisesRegex(ValidationError, "source retrieval"):
            evidence(sources=(after_commit, *source_bindings[1:]))

    def test_rejects_unbound_or_tampered_citations(self):
        unbound = metric(
            citations=(
                citation(
                    source_reference_id="fundamental_source:outside-package"
                ),
            )
        )
        with self.assertRaisesRegex(ValidationError, "package source"):
            evidence(metrics=(unbound,))

        tampered = metric(
            citations=(citation(content_fingerprint="f" * 64),)
        )
        with self.assertRaisesRegex(ValidationError, "match its package source"):
            evidence(metrics=(tampered,))

    def test_rejects_duplicate_or_noncanonical_metric_order(self):
        first = metric()
        second = metric(
            key=FundamentalMetricKey(
                metric_id="balance.cash",
                reporting_basis=FinancialReportingBasis.CONSOLIDATED,
                period_key="fy2026",
            )
        )
        with self.assertRaisesRegex(ValidationError, "canonical order"):
            evidence(metrics=(first, second))
        with self.assertRaisesRegex(ValidationError, "keys must be unique"):
            evidence(metrics=(first, first))

    def test_rejects_naive_or_inactive_normalization_time(self):
        with self.assertRaisesRegex(ValidationError, "require timezone"):
            evidence(normalized_at=NORMALIZED_AT.replace(tzinfo=None))
        with self.assertRaisesRegex(ValidationError, "active committed"):
            evidence(normalized_at=EXPIRES_AT)


class FundamentalMetricNormalizerTests(unittest.TestCase):
    def test_parses_indian_and_western_grouping_without_applying_unit_scale(self):
        cases = {
            "7,06,802": Decimal("706802"),
            "1,234,567.50": Decimal("1234567.50"),
            "(₹ 1,234 Cr.)": Decimal("-1234"),
            "12.40%": Decimal("12.40"),
            "0": Decimal("0"),
            "—": None,
        }

        for source_value, expected in cases.items():
            with self.subTest(source_value=source_value):
                self.assertEqual(
                    parse_indian_financial_number(source_value),
                    expected,
                )
        for malformed in ("1,2,3", "twelve", "1.2.3", "(+12)"):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    parse_indian_financial_number(malformed)

    def test_maps_reviewed_labels_and_preserves_source_lineage_and_units(self):
        source_document = financial_document(
            document_row(
                row_key="sales",
                label="Sales",
                display_order=0,
                value_kind=FundamentalValueKind.MONETARY,
                normalized_unit="INR crore",
                values=(
                    ("6,50,000", Decimal("650000")),
                    ("7,06,802", Decimal("706802")),
                ),
            ),
            document_row(
                row_key="opm",
                label="OPM (%)",
                display_order=1,
                value_kind=FundamentalValueKind.PERCENTAGE,
                normalized_unit="percent",
                values=(("18.5", Decimal("18.5")), ("0", Decimal("0"))),
            ),
        )

        first = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )
        second = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )

        self.assertEqual(first, second)
        self.assertEqual(first.issues, ())
        self.assertEqual(
            tuple(item.key.canonical_key for item in first.metrics),
            tuple(sorted(item.key.canonical_key for item in first.metrics)),
        )
        revenue = next(
            item
            for item in first.metrics
            if item.key.metric_id == "income.revenue"
            and item.key.period_key == "annual:fy2026"
        )
        self.assertEqual(revenue.value, Decimal("706802"))
        self.assertEqual(revenue.source_values, ("7,06,802",))
        self.assertEqual(revenue.unit.normalized_unit, "INR_CRORE")
        self.assertEqual(revenue.unit.scale, Decimal("10000000"))
        self.assertEqual(
            revenue.citations[0].source_path,
            ("rows", "sales", "cells", "fy2026"),
        )
        margin = next(
            item
            for item in first.metrics
            if item.key.metric_id == "margin.ebitda"
            and item.key.period_key == "annual:fy2026"
        )
        self.assertIs(margin.value_status, FundamentalMetricValueStatus.ZERO)
        self.assertEqual(margin.unit.scale, Decimal("0.01"))

    def test_preserves_missing_value_without_turning_it_into_zero(self):
        source_document = financial_document(
            document_row(
                row_key="eps",
                label="EPS",
                display_order=0,
                value_kind=FundamentalValueKind.PER_SHARE,
                normalized_unit="INR per share",
                values=(("12.5", Decimal("12.5")), (None, None)),
            )
        )

        result = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )

        missing = next(
            item
            for item in result.metrics
            if item.key.period_key == "annual:fy2026"
        )
        self.assertIs(missing.value_status, FundamentalMetricValueStatus.MISSING)
        self.assertIsNone(missing.value)
        self.assertEqual(missing.source_values, ())
        self.assertIn(
            FundamentalMetricQualityFlag.SOURCE_VALUE_MISSING,
            missing.quality_flags,
        )

    def test_rejects_ambiguous_provider_labels_instead_of_guessing(self):
        source_document = financial_document(
            document_row(
                row_key="ambiguous",
                label="Sales",
                standardized_label="net_profit",
                display_order=0,
                value_kind=FundamentalValueKind.MONETARY,
                normalized_unit="INR crore",
                values=(("100", Decimal("100")),),
            )
        )

        result = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )

        self.assertEqual(result.metrics, ())
        self.assertEqual(
            result.issues[0].code,
            FundamentalMetricNormalizationIssueCode.AMBIGUOUS_LABEL,
        )

    def test_normalizes_growth_horizons_despite_provider_page_unit(self):
        growth_periods = (
            FinancialDocumentPeriod(
                period_key="period_3yr",
                source_label="3Y",
                display_order=0,
            ),
        )
        source_document = financial_document(
            FinancialDocumentRow(
                row_key="sales_growth",
                original_label="Sales Growth",
                depth=0,
                row_kind=FinancialDocumentRowKind.METRIC,
                value_kind=FundamentalValueKind.PERCENTAGE,
                display_order=0,
                cells=(
                    document_cell(
                        "period_3yr",
                        "18.2%",
                        Decimal("18.2"),
                        source_unit="In Rs. Cr.",
                        normalized_unit="In Rs. Cr.",
                    ),
                ),
            ),
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
            periods=growth_periods,
        )

        result = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )

        self.assertEqual(result.issues, ())
        self.assertEqual(result.metrics[0].key.metric_id, "growth.revenue")
        self.assertIs(
            result.metrics[0].period.period_kind,
            FundamentalMetricPeriodKind.HORIZON,
        )
        self.assertEqual(result.metrics[0].unit.normalized_unit, "PERCENT")

    def test_keeps_statement_scope_basis_and_period_semantics(self):
        cases = (
            (
                FinancialDocumentType.BALANCE_SHEET,
                "Assets",
                FundamentalValueKind.MONETARY,
                "INR crore",
                "balance.total_assets",
                FundamentalMetricPeriodKind.ANNUAL,
            ),
            (
                FinancialDocumentType.CASH_FLOW,
                "Cash from Operating Activity",
                FundamentalValueKind.MONETARY,
                "INR crore",
                "cash_flow.operating",
                FundamentalMetricPeriodKind.ANNUAL,
            ),
            (
                FinancialDocumentType.RATIOS,
                "Current Ratio",
                FundamentalValueKind.RATIO,
                "ratio",
                "ratio.current",
                FundamentalMetricPeriodKind.ANNUAL,
            ),
            (
                FinancialDocumentType.QUARTERLY_RESULTS,
                "Net Sales",
                FundamentalValueKind.MONETARY,
                "INR crore",
                "income.revenue",
                FundamentalMetricPeriodKind.QUARTERLY,
            ),
        )

        for (
            document_type,
            label,
            value_kind,
            normalized_unit,
            metric_id,
            period_kind,
        ) in cases:
            with self.subTest(document_type=document_type):
                source_document = financial_document(
                    document_row(
                        row_key="reviewed_metric",
                        label=label,
                        display_order=0,
                        value_kind=value_kind,
                        normalized_unit=normalized_unit,
                        values=(("100", Decimal("100")),),
                    ),
                    document_type=document_type,
                    reporting_basis=FinancialReportingBasis.STANDALONE,
                    periods=(
                        FinancialDocumentPeriod(
                            period_key="fy2025",
                            source_label="Mar 2025",
                            display_order=0,
                        ),
                    ),
                )
                result = normalize_financial_document_metrics(
                    source_document,
                    financial_source(source_document),
                )

                self.assertEqual(result.issues, ())
                self.assertEqual(result.metrics[0].key.metric_id, metric_id)
                self.assertIs(
                    result.metrics[0].key.reporting_basis,
                    FinancialReportingBasis.STANDALONE,
                )
                self.assertIs(
                    result.metrics[0].period.period_kind,
                    period_kind,
                )
                self.assertEqual(
                    result.metrics[0].key.period_key,
                    f"{period_kind.value}:fy2025",
                )
                self.assertEqual(
                    result.metrics[0].period.end_date,
                    date(2025, 3, 31),
                )
                self.assertEqual(
                    result.metrics[0].period.start_date,
                    (
                        date(2025, 1, 1)
                        if period_kind is FundamentalMetricPeriodKind.QUARTERLY
                        else date(2024, 4, 1)
                    ),
                )

    def test_rejects_a_known_label_with_a_contradictory_value_kind(self):
        source_document = financial_document(
            document_row(
                row_key="sales",
                label="Sales",
                display_order=0,
                value_kind=FundamentalValueKind.PERCENTAGE,
                normalized_unit="percent",
                values=(("12%", Decimal("12")),),
            )
        )

        result = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )

        self.assertEqual(result.metrics, ())
        self.assertEqual(
            result.issues[0].code,
            FundamentalMetricNormalizationIssueCode.VALUE_KIND_MISMATCH,
        )

    def test_namespaces_equal_provider_keys_by_annual_and_quarterly_period(self):
        source_period = (
            FinancialDocumentPeriod(
                period_key="period_mar_2026",
                source_label="Mar 2026",
                display_order=0,
            ),
        )
        annual = financial_document(
            FinancialDocumentRow(
                row_key="sales",
                original_label="Sales",
                depth=0,
                row_kind=FinancialDocumentRowKind.METRIC,
                value_kind=FundamentalValueKind.MONETARY,
                display_order=0,
                cells=(
                    document_cell(
                        "period_mar_2026",
                        "100",
                        Decimal("100"),
                        source_unit="INR crore",
                        normalized_unit="INR crore",
                    ),
                ),
            ),
            periods=source_period,
        )
        quarterly = financial_document(
            FinancialDocumentRow(
                row_key="net_sales",
                original_label="Net Sales",
                depth=0,
                row_kind=FinancialDocumentRowKind.METRIC,
                value_kind=FundamentalValueKind.MONETARY,
                display_order=0,
                cells=(
                    document_cell(
                        "period_mar_2026",
                        "25",
                        Decimal("25"),
                        source_unit="INR crore",
                        normalized_unit="INR crore",
                    ),
                ),
            ),
            document_type=FinancialDocumentType.QUARTERLY_RESULTS,
            periods=source_period,
        )

        annual_metric = normalize_financial_document_metrics(
            annual,
            financial_source(annual),
        ).metrics[0]
        quarterly_metric = normalize_financial_document_metrics(
            quarterly,
            financial_source(quarterly),
        ).metrics[0]

        self.assertEqual(annual_metric.key.period_key, "annual:period_mar_2026")
        self.assertEqual(
            quarterly_metric.key.period_key,
            "quarterly:period_mar_2026",
        )
        self.assertNotEqual(
            annual_metric.key.canonical_key,
            quarterly_metric.key.canonical_key,
        )

    def test_emits_conflict_for_duplicate_metric_values(self):
        source_document = financial_document(
            document_row(
                row_key="sales",
                label="Sales",
                display_order=0,
                value_kind=FundamentalValueKind.MONETARY,
                normalized_unit="INR crore",
                values=(("100", Decimal("100")),),
            ),
            document_row(
                row_key="revenue",
                label="Revenue",
                display_order=1,
                value_kind=FundamentalValueKind.MONETARY,
                normalized_unit="INR crore",
                values=(("101", Decimal("101")),),
            ),
        )

        result = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )

        self.assertEqual(len(result.metrics), 1)
        conflict = result.metrics[0]
        self.assertIs(
            conflict.value_status,
            FundamentalMetricValueStatus.CONFLICT,
        )
        self.assertIsNone(conflict.value)
        self.assertEqual(conflict.source_values, ("100", "101"))
        self.assertEqual(len(conflict.citations), 2)

    def test_withholds_unmapped_units_and_source_value_mismatches(self):
        source_document = financial_document(
            document_row(
                row_key="sales",
                label="Sales",
                display_order=0,
                value_kind=FundamentalValueKind.MONETARY,
                normalized_unit="USD thousand",
                values=(("100", Decimal("100")),),
            ),
            document_row(
                row_key="net_profit",
                label="Net Profit",
                display_order=1,
                value_kind=FundamentalValueKind.MONETARY,
                normalized_unit="INR crore",
                values=(("101", Decimal("999")),),
            ),
        )

        result = normalize_financial_document_metrics(
            source_document,
            financial_source(source_document),
        )

        self.assertEqual(len(result.metrics), 1)
        self.assertIs(
            result.metrics[0].value_status,
            FundamentalMetricValueStatus.PARSE_ERROR,
        )
        self.assertEqual(
            {issue.code for issue in result.issues},
            {
                FundamentalMetricNormalizationIssueCode.UNSUPPORTED_UNIT,
                FundamentalMetricNormalizationIssueCode.SOURCE_VALUE_MISMATCH,
            },
        )

    def test_rejects_document_and_package_binding_mismatch(self):
        source_document = financial_document(
            document_row(
                row_key="sales",
                label="Sales",
                display_order=0,
                value_kind=FundamentalValueKind.MONETARY,
                normalized_unit="INR crore",
                values=(("100", Decimal("100")),),
            )
        )
        wrong_source = financial_source(
            source_document,
            content_fingerprint="f" * 64,
        )

        with self.assertRaisesRegex(ValueError, "fingerprint"):
            normalize_financial_document_metrics(source_document, wrong_source)


class DerivedFundamentalMetricTests(unittest.TestCase):
    def test_calculates_initial_same_period_metric_families(self):
        inputs = (
            normalized_input("income.revenue", Decimal("1000")),
            normalized_input("income.ebitda", Decimal("200")),
            normalized_input("income.ebit", Decimal("150")),
            normalized_input("income.pat", Decimal("100")),
            normalized_input("balance.borrowings", Decimal("300")),
            normalized_input("balance.cash_and_equivalents", Decimal("50")),
            normalized_input("balance.shareholders_equity", Decimal("500")),
            normalized_input("cash_flow.operating", Decimal("140")),
            normalized_input("cash_flow.capex", Decimal("-40")),
            normalized_input("balance.current_assets", Decimal("400")),
            normalized_input("balance.current_liabilities", Decimal("200")),
            normalized_input("balance.trade_receivables", Decimal("100")),
            normalized_input("balance.inventory", Decimal("50")),
            normalized_input(
                "valuation.pe",
                Decimal("20"),
                metric_unit=ratio_unit(),
            ),
            normalized_input(
                "valuation.pb",
                Decimal("4"),
                metric_unit=ratio_unit(),
            ),
            normalized_input(
                "valuation.ev_to_ebitda",
                Decimal("10"),
                metric_unit=ratio_unit(),
            ),
        )

        result = derive_fundamental_metrics(inputs)
        values = {item.key.metric_id: item.value for item in result.metrics}

        self.assertEqual(result.formula_policy_version, "1.0.0")
        self.assertEqual(values["derived.margin.ebitda_pct"], Decimal("20"))
        self.assertEqual(values["derived.margin.ebit_pct"], Decimal("15"))
        self.assertEqual(values["derived.margin.pat_pct"], Decimal("10"))
        self.assertEqual(
            values["derived.leverage.debt_to_equity"],
            Decimal("0.6"),
        )
        self.assertEqual(values["derived.leverage.net_debt"], Decimal("250"))
        self.assertEqual(
            values["derived.leverage.net_debt_to_ebitda"],
            Decimal("1.25"),
        )
        self.assertEqual(
            values["derived.cash_conversion.ocf_to_pat"],
            Decimal("1.4"),
        )
        self.assertEqual(
            values["derived.cash_conversion.free_cash_flow"],
            Decimal("100"),
        )
        self.assertEqual(
            values["derived.cash_conversion.fcf_to_pat"],
            Decimal("1"),
        )
        self.assertEqual(
            values["derived.cash_conversion.fcf_margin_pct"],
            Decimal("10"),
        )
        self.assertEqual(
            values["derived.working_capital.net_working_capital"],
            Decimal("200"),
        )
        self.assertEqual(
            values["derived.working_capital.current_ratio"],
            Decimal("2"),
        )
        self.assertEqual(
            values[
                "derived.working_capital.receivables_to_revenue_pct"
            ],
            Decimal("10"),
        )
        self.assertEqual(
            values["derived.working_capital.inventory_to_revenue_pct"],
            Decimal("5"),
        )
        self.assertEqual(
            values["derived.valuation.earnings_yield_pct"],
            Decimal("5"),
        )
        self.assertEqual(
            values["derived.valuation.book_yield_pct"],
            Decimal("25"),
        )
        self.assertEqual(
            values["derived.valuation.ebitda_yield_pct"],
            Decimal("10"),
        )
        self.assertEqual(
            tuple(item.key.canonical_key for item in result.metrics),
            tuple(sorted(item.key.canonical_key for item in result.metrics)),
        )
        calculated = next(
            item
            for item in result.metrics
            if item.key.metric_id == "derived.cash_conversion.fcf_to_pat"
        )
        self.assertIs(
            calculated.normalization_method,
            FundamentalNormalizationMethod.CALCULATED,
        )
        self.assertEqual(
            calculated.calculation.formula_version,
            DERIVED_FORMULA_POLICY_VERSION,
        )
        self.assertEqual(len(calculated.calculation.input_metric_keys), 3)

    def test_calculates_consecutive_growth_without_skipping_periods(self):
        inputs = (
            normalized_input(
                "income.revenue",
                Decimal("100"),
                period_key="annual:fy2025",
                period_end=date(2025, 3, 31),
            ),
            normalized_input(
                "income.revenue",
                Decimal("120"),
                period_key="annual:fy2026",
                period_end=date(2026, 3, 31),
            ),
        )

        first = derive_fundamental_metrics(inputs)
        second = derive_fundamental_metrics(inputs)
        growth = next(
            item
            for item in first.metrics
            if item.key.metric_id == "derived.growth.revenue_yoy_pct"
        )

        self.assertEqual(first, second)
        self.assertEqual(growth.value, Decimal("20.0"))
        self.assertEqual(growth.key.period_key, "annual:fy2026")
        self.assertEqual(
            growth.calculation.input_metric_keys,
            (
                "income.revenue:consolidated:annual:fy2025",
                "income.revenue:consolidated:annual:fy2026",
            ),
        )

    def test_calculates_versioned_cagr_from_complete_annual_history(self):
        inputs = (
            normalized_input(
                "income.revenue",
                Decimal("100"),
                period_key="annual:fy2024",
                period_end=date(2024, 3, 31),
            ),
            normalized_input(
                "income.revenue",
                Decimal("121"),
                period_key="annual:fy2025",
                period_end=date(2025, 3, 31),
            ),
            normalized_input(
                "income.revenue",
                Decimal("144"),
                period_key="annual:fy2026",
                period_end=date(2026, 3, 31),
            ),
        )

        result = derive_fundamental_metrics(inputs)
        cagr = next(
            item
            for item in result.metrics
            if item.key.metric_id == "derived.growth.revenue_cagr_pct"
        )

        self.assertEqual(cagr.value, Decimal("20.0"))
        self.assertEqual(
            cagr.calculation.formula_id,
            "derive.growth.revenue_cagr_pct",
        )
        self.assertEqual(len(cagr.calculation.input_metric_keys), 3)

    def test_rejects_zero_denominators_and_non_positive_growth_bases(self):
        inputs = (
            normalized_input("income.revenue", Decimal("0")),
            normalized_input("income.pat", Decimal("10")),
            normalized_input(
                "income.revenue",
                Decimal("-100"),
                period_key="annual:fy2025",
                period_end=date(2025, 3, 31),
            ),
        )

        result = derive_fundamental_metrics(inputs)

        self.assertNotIn(
            "derived.margin.pat_pct",
            {item.key.metric_id for item in result.metrics},
        )
        self.assertIn(
            DerivedFundamentalMetricIssueCode.ZERO_DENOMINATOR,
            {item.code for item in result.issues},
        )
        self.assertIn(
            DerivedFundamentalMetricIssueCode.NON_POSITIVE_GROWTH_BASE,
            {item.code for item in result.issues},
        )

    def test_missing_and_non_consecutive_inputs_remain_explicit(self):
        inputs = (
            normalized_input(
                "income.revenue",
                Decimal("100"),
                period_key="annual:fy2024",
                period_end=date(2024, 3, 31),
            ),
            normalized_input(
                "income.revenue",
                Decimal("140"),
                period_key="annual:fy2026",
                period_end=date(2026, 3, 31),
            ),
            normalized_input(
                "income.pat",
                None,
                value_status=FundamentalMetricValueStatus.MISSING,
            ),
        )

        result = derive_fundamental_metrics(inputs)
        issue_codes = {item.code for item in result.issues}

        self.assertIn(
            DerivedFundamentalMetricIssueCode.NON_CONSECUTIVE_PERIOD,
            issue_codes,
        )
        self.assertIn(
            DerivedFundamentalMetricIssueCode.UNAVAILABLE_INPUT,
            issue_codes,
        )
        self.assertNotIn(
            "derived.growth.revenue_yoy_pct",
            {item.key.metric_id for item in result.metrics},
        )

    def test_converts_compatible_monetary_scales_before_subtraction(self):
        lakh_unit = FundamentalMetricUnit(
            value_kind=FundamentalValueKind.MONETARY,
            normalized_unit="INR_LAKH",
            scale=Decimal("100000"),
            currency="INR",
        )
        inputs = (
            normalized_input("balance.borrowings", Decimal("300")),
            normalized_input(
                "balance.cash_and_equivalents",
                Decimal("5000"),
                metric_unit=lakh_unit,
            ),
        )

        result = derive_fundamental_metrics(inputs)
        net_debt = next(
            item
            for item in result.metrics
            if item.key.metric_id == "derived.leverage.net_debt"
        )

        self.assertEqual(net_debt.value, Decimal("250"))
        self.assertEqual(net_debt.unit.normalized_unit, "INR_CRORE")

    def test_uses_explicit_debt_components_only_when_total_debt_is_absent(self):
        inputs = (
            normalized_input("balance.secured_debt", Decimal("100")),
            normalized_input("balance.unsecured_debt", Decimal("200")),
            normalized_input("balance.cash_and_equivalents", Decimal("50")),
            normalized_input("balance.shareholders_equity", Decimal("500")),
            normalized_input("income.ebitda", Decimal("200")),
        )

        result = derive_fundamental_metrics(inputs)
        metrics = {item.key.metric_id: item for item in result.metrics}

        self.assertEqual(
            metrics["derived.leverage.total_debt_proxy"].value,
            Decimal("300"),
        )
        self.assertEqual(
            metrics["derived.leverage.debt_to_equity"].value,
            Decimal("0.6"),
        )
        self.assertEqual(
            metrics["derived.leverage.net_debt"].value,
            Decimal("250"),
        )
        self.assertEqual(
            metrics["derived.leverage.net_debt_to_ebitda"].value,
            Decimal("1.25"),
        )
        self.assertIn(
            "only mapped secured and unsecured debt",
            metrics["derived.leverage.net_debt"].limitations[0],
        )

    def test_never_combines_consolidated_and_standalone_inputs(self):
        inputs = (
            normalized_input("income.revenue", Decimal("1000")),
            normalized_input("income.pat", Decimal("100")),
            normalized_input(
                "income.revenue",
                Decimal("400"),
                reporting_basis=FinancialReportingBasis.STANDALONE,
            ),
            normalized_input(
                "income.pat",
                Decimal("20"),
                reporting_basis=FinancialReportingBasis.STANDALONE,
            ),
        )

        result = derive_fundamental_metrics(inputs)
        margins = {
            item.key.reporting_basis: item.value
            for item in result.metrics
            if item.key.metric_id == "derived.margin.pat_pct"
        }

        self.assertEqual(
            margins,
            {
                FinancialReportingBasis.CONSOLIDATED: Decimal("10"),
                FinancialReportingBasis.STANDALONE: Decimal("5"),
            },
        )

    def test_rejects_duplicate_normalized_input_keys(self):
        duplicate = normalized_input("income.revenue", Decimal("100"))

        with self.assertRaisesRegex(ValueError, "unique canonical"):
            derive_fundamental_metrics((duplicate, duplicate))


if __name__ == "__main__":
    unittest.main()
