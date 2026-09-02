from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from pydantic import ValidationError

from app.models.financial_documents import (
    FinancialDocumentCell,
    FinancialDocumentPeriod,
    FinancialDocumentRow,
    FinancialDocumentRowKind,
    FinancialDocumentType,
    FinancialReportingBasis,
    StructuredFinancialDocument,
)
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalIssuerIdentity,
    FundamentalPeriodType,
    FundamentalValidationStatus,
    FundamentalValueKind,
    ProviderConnectionScope,
)


IST = timezone(timedelta(hours=5, minutes=30))
RETRIEVED_AT = datetime(2026, 8, 30, 18, 0, tzinfo=IST)


def cell(
    period_key: str,
    source_value: str | None,
    normalized_value: Decimal | None,
) -> FinancialDocumentCell:
    available = source_value is not None
    return FinancialDocumentCell(
        period_key=period_key,
        source_value=source_value,
        normalized_value=normalized_value,
        source_unit="percent" if available else None,
        normalized_unit="percent" if available else None,
        availability_status=(
            FundamentalAvailabilityStatus.AVAILABLE
            if available
            else FundamentalAvailabilityStatus.UNKNOWN
        ),
    )


def document(**overrides) -> StructuredFinancialDocument:
    periods = (
        FinancialDocumentPeriod(
            period_key="fy2025",
            source_label="MAR'25",
            period_type=FundamentalPeriodType.ANNUAL,
            end_date=date(2025, 3, 31),
            display_order=0,
        ),
        FinancialDocumentPeriod(
            period_key="fy2026",
            source_label="MAR'26",
            period_type=FundamentalPeriodType.ANNUAL,
            end_date=date(2026, 3, 31),
            display_order=1,
        ),
    )
    values = {
        "document_id": "tijori.NSE.COFORGE.growth_table.consolidated",
        "connection": ProviderConnectionScope(
            tenant_id="tenant.local_user",
            provider_connection_id="provider.tijori.local_user",
            provider="tijori",
        ),
        "issuer": FundamentalIssuerIdentity(
            exchange="NSE",
            symbol="COFORGE",
            legal_name="Coforge Ltd.",
            provider_company_id="4502",
            provider_slug="niit-technologies-limited",
        ),
        "document_type": FinancialDocumentType.GROWTH_TABLE,
        "reporting_basis": FinancialReportingBasis.CONSOLIDATED,
        "currency": None,
        "source_unit": "percent",
        "periods": periods,
        "rows": (
            FinancialDocumentRow(
                row_key="growth",
                original_label="Growth",
                depth=0,
                row_kind=FinancialDocumentRowKind.SECTION,
                value_kind=FundamentalValueKind.PERCENTAGE,
                display_order=0,
                cells=(cell("fy2025", None, None), cell("fy2026", None, None)),
            ),
            FinancialDocumentRow(
                row_key="growth.revenue_growth",
                original_label="Revenue Growth",
                standardized_label="revenue_growth",
                parent_row_key="growth",
                depth=1,
                row_kind=FinancialDocumentRowKind.METRIC,
                value_kind=FundamentalValueKind.PERCENTAGE,
                display_order=1,
                cells=(
                    cell("fy2025", "0.00", Decimal("0")),
                    cell("fy2026", "12.40", Decimal("12.40")),
                ),
            ),
        ),
        "source_location": (
            "https://www.tijorifinance.com/company/"
            "niit-technologies-limited/financials/"
        ),
        "retrieved_at": RETRIEVED_AT,
        "expires_at": RETRIEVED_AT + timedelta(days=10),
        "all_sections_expanded": True,
        "validation_status": FundamentalValidationStatus.VALIDATED,
    }
    values.update(overrides)
    return StructuredFinancialDocument(**values)


class StructuredFinancialDocumentTests(unittest.TestCase):
    def test_supports_undated_horizon_and_preserves_auxiliary_values(self):
        horizon = FinancialDocumentPeriod(
            period_key="period_1yr",
            source_label="1yr",
            display_order=0,
        )
        value = FinancialDocumentCell(
            period_key="period_1yr",
            source_value="12.4",
            normalized_value=Decimal("12.4"),
            source_unit="percent",
            normalized_unit="percent",
            yoy_change="8%",
            percentage_of_parent="23%",
            availability_status=FundamentalAvailabilityStatus.AVAILABLE,
        )

        self.assertIsNone(horizon.period_type)
        self.assertIsNone(horizon.end_date)
        self.assertEqual(value.yoy_change, "8%")
        self.assertEqual(value.percentage_of_parent, "23%")

    def test_undated_or_unavailable_cells_remain_fail_closed(self):
        with self.assertRaisesRegex(ValidationError, "start requires an end"):
            FinancialDocumentPeriod(
                period_key="broken",
                source_label="Broken",
                start_date=date(2025, 4, 1),
                display_order=0,
            )
        with self.assertRaisesRegex(ValidationError, "auxiliary values"):
            FinancialDocumentCell(
                period_key="period_1yr",
                yoy_change="8%",
                availability_status=FundamentalAvailabilityStatus.UNKNOWN,
            )

    def test_preserves_growth_scenario_metadata_missing_values_and_zero(self):
        result = document()

        self.assertEqual(result.document_type, FinancialDocumentType.GROWTH_TABLE)
        self.assertEqual(
            result.reporting_basis,
            FinancialReportingBasis.CONSOLIDATED,
        )
        self.assertIsNone(result.rows[0].cells[0].normalized_value)
        self.assertEqual(result.rows[1].cells[0].normalized_value, Decimal("0"))
        self.assertEqual(len(result.document_fingerprint), 64)
        self.assertEqual(
            StructuredFinancialDocument.model_validate_json(
                result.model_dump_json(exclude_computed_fields=True)
            ),
            result,
        )

    def test_preserves_and_validates_provider_skipped_period_labels(self):
        result = document(skipped_period_labels=("Mar 2023", "Mar 2022"))

        self.assertEqual(
            result.skipped_period_labels,
            ("Mar 2023", "Mar 2022"),
        )
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            document(skipped_period_labels=("Mar 2023", "Mar 2023"))
        with self.assertRaisesRegex(ValidationError, "must be bounded"):
            document(skipped_period_labels=("",))

    def test_reporting_basis_changes_document_identity(self):
        consolidated = document()
        standalone = document(
            document_id="tijori.NSE.COFORGE.growth_table.standalone",
            reporting_basis=FinancialReportingBasis.STANDALONE,
        )

        self.assertNotEqual(
            consolidated.document_fingerprint,
            standalone.document_fingerprint,
        )

    def test_rejects_incomplete_period_coverage(self):
        source = document()
        broken_row = source.rows[1].model_copy(
            update={"cells": source.rows[1].cells[:1]}
        )

        with self.assertRaisesRegex(ValidationError, "cover every period"):
            document(rows=(source.rows[0], broken_row))

    def test_rejects_missing_or_inconsistent_parent(self):
        source = document()
        missing_parent = source.rows[1].model_copy(
            update={"parent_row_key": "growth.missing"}
        )
        with self.assertRaisesRegex(ValidationError, "parent must exist"):
            document(rows=(source.rows[0], missing_parent))

        wrong_depth = source.rows[1].model_copy(update={"depth": 2})
        with self.assertRaisesRegex(ValidationError, "depth is inconsistent"):
            document(rows=(source.rows[0], wrong_depth))

    def test_rejects_unexpanded_or_unvalidated_active_document(self):
        with self.assertRaisesRegex(ValidationError, "fully expanded"):
            document(all_sections_expanded=False)
        with self.assertRaisesRegex(ValidationError, "must be validated"):
            document(validation_status=FundamentalValidationStatus.PARTIAL)

    def test_rejects_naive_or_invalid_retention_window(self):
        with self.assertRaisesRegex(ValidationError, "require timezone"):
            document(retrieved_at=datetime(2026, 8, 30, 18, 0))
        with self.assertRaisesRegex(ValidationError, "expiry must follow"):
            document(expires_at=RETRIEVED_AT)


if __name__ == "__main__":
    unittest.main()
