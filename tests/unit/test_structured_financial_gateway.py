from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalFinancialDocumentPackage,
    FundamentalFinancialDocumentPackageGateway,
    FundamentalFinancialDocumentPackageRequest,
    FundamentalFinancialDocumentPackageResult,
    FundamentalRetrievalStatus,
    FundamentalStructuredDocumentGateway,
    FundamentalStructuredDocumentRequest,
    FundamentalStructuredDocumentResult,
    validate_fundamental_response_binding,
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
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalIssuerIdentity,
    FundamentalPeriodType,
    FundamentalValidationStatus,
    FundamentalValueKind,
    ProviderConnectionScope,
)


IST = timezone(timedelta(hours=5, minutes=30))
REQUESTED_AT = datetime(2026, 8, 30, 18, 0, tzinfo=IST)
COMPLETED_AT = REQUESTED_AT + timedelta(seconds=2)
ADAPTER_HASH = "a" * 64


def connection() -> ProviderConnectionScope:
    return ProviderConnectionScope(
        tenant_id="tenant.prateek",
        provider_connection_id="provider.tijori.prateek",
        provider="tijori",
    )


def issuer(**overrides) -> FundamentalIssuerIdentity:
    values = {
        "exchange": "NSE",
        "symbol": "COFORGE",
        "legal_name": "Coforge Ltd.",
        "provider_company_id": "4502",
        "provider_slug": "niit-technologies-limited",
    }
    values.update(overrides)
    return FundamentalIssuerIdentity(**values)


def request(**overrides) -> FundamentalStructuredDocumentRequest:
    values = {
        "request_id": "request.coforge.balance-sheet.consolidated",
        "operation_id": "operation.coforge.fundamentals",
        "connection": connection(),
        "requested_at": REQUESTED_AT,
        "issuer": issuer(),
        "document_type": FinancialDocumentType.BALANCE_SHEET,
        "reporting_basis": FinancialReportingBasis.CONSOLIDATED,
    }
    values.update(overrides)
    return FundamentalStructuredDocumentRequest(**values)


def document(**overrides) -> StructuredFinancialDocument:
    values = {
        "document_id": "tijori.NSE.COFORGE.balance_sheet.consolidated",
        "connection": connection(),
        "issuer": issuer(),
        "document_type": FinancialDocumentType.BALANCE_SHEET,
        "reporting_basis": FinancialReportingBasis.CONSOLIDATED,
        "currency": "INR",
        "source_unit": "crore",
        "periods": (
            FinancialDocumentPeriod(
                period_key="fy2026",
                source_label="MAR'26",
                period_type=FundamentalPeriodType.ANNUAL,
                end_date=date(2026, 3, 31),
                display_order=0,
            ),
        ),
        "rows": (
            FinancialDocumentRow(
                row_key="assets",
                original_label="Assets",
                depth=0,
                row_kind=FinancialDocumentRowKind.TOTAL,
                value_kind=FundamentalValueKind.MONETARY,
                display_order=0,
                cells=(
                    FinancialDocumentCell(
                        period_key="fy2026",
                        source_value="16408",
                        normalized_value=Decimal("16408"),
                        source_unit="crore",
                        normalized_unit="INR crore",
                        availability_status=(
                            FundamentalAvailabilityStatus.AVAILABLE
                        ),
                    ),
                ),
            ),
        ),
        "source_location": (
            "https://www.tijorifinance.com/company/"
            "niit-technologies-limited/financials/"
        ),
        "retrieved_at": REQUESTED_AT + timedelta(seconds=1),
        "expires_at": REQUESTED_AT + timedelta(days=10),
        "all_sections_expanded": True,
        "validation_status": FundamentalValidationStatus.VALIDATED,
    }
    values.update(overrides)
    return StructuredFinancialDocument(**values)


def package_request() -> FundamentalFinancialDocumentPackageRequest:
    return FundamentalFinancialDocumentPackageRequest(
        request_id="request.coforge.financial-package",
        operation_id="operation.coforge.fundamentals",
        connection=connection(),
        requested_at=REQUESTED_AT,
        issuer=issuer(),
    )


def financial_package(**overrides) -> FundamentalFinancialDocumentPackage:
    scenarios = (
        (
            FinancialDocumentType.GROWTH_TABLE,
            FinancialReportingBasis.NOT_APPLICABLE,
        ),
        *(
            (document_type, reporting_basis)
            for document_type in (
                FinancialDocumentType.BALANCE_SHEET,
                FinancialDocumentType.PROFIT_AND_LOSS,
                FinancialDocumentType.CASH_FLOW,
                FinancialDocumentType.RATIOS,
                FinancialDocumentType.QUARTERLY_RESULTS,
            )
            for reporting_basis in (
                FinancialReportingBasis.CONSOLIDATED,
                FinancialReportingBasis.STANDALONE,
            )
        ),
    )
    values = {
        "connection": connection(),
        "issuer": issuer(),
        "source_location": document().source_location,
        "retrieved_at": document().retrieved_at,
        "expires_at": document().expires_at,
        "documents": tuple(
            document(
                document_id=(
                    "tijori.NSE.COFORGE."
                    f"{document_type.value}.{reporting_basis.value}"
                ),
                document_type=document_type,
                reporting_basis=reporting_basis,
            )
            for document_type, reporting_basis in scenarios
        ),
    }
    values.update(overrides)
    return FundamentalFinancialDocumentPackage(**values)


def result_values(source_request, **overrides) -> dict:
    values = {
        "request_id": source_request.request_id,
        "request_fingerprint": source_request.request_fingerprint,
        "connection": source_request.connection,
        "requested_at": source_request.requested_at,
        "started_at": source_request.requested_at,
        "completed_at": COMPLETED_AT,
        "provider_contract_version": "provider.contract.v1",
        "adapter_fingerprint": ADAPTER_HASH,
        "issuer": source_request.issuer,
        "document_type": source_request.document_type,
        "reporting_basis": source_request.reporting_basis,
        "status": FundamentalRetrievalStatus.COMPLETED,
        "document": document(),
    }
    values.update(overrides)
    return values


class FakeStructuredDocumentGateway:
    configuration_fingerprint = ADAPTER_HASH

    def retrieve_structured_financial_document(self, *, request):
        return FundamentalStructuredDocumentResult(
            **result_values(request)
        )


class FakeFinancialDocumentPackageGateway:
    configuration_fingerprint = ADAPTER_HASH

    def retrieve_financial_document_package(self, *, request):
        return FundamentalFinancialDocumentPackageResult(
            request_id=request.request_id,
            request_fingerprint=request.request_fingerprint,
            connection=request.connection,
            requested_at=request.requested_at,
            started_at=request.requested_at,
            completed_at=COMPLETED_AT,
            provider_contract_version="provider.contract.v1",
            adapter_fingerprint=ADAPTER_HASH,
            issuer=request.issuer,
            status=FundamentalRetrievalStatus.COMPLETED,
            package=financial_package(),
        )


class StructuredFinancialGatewayTests(unittest.TestCase):
    def test_request_is_provider_neutral_and_fingerprinted(self):
        source_request = request()

        self.assertEqual(
            source_request.document_type,
            FinancialDocumentType.BALANCE_SHEET,
        )
        self.assertEqual(
            source_request.reporting_basis,
            FinancialReportingBasis.CONSOLIDATED,
        )
        self.assertEqual(len(source_request.request_fingerprint), 64)

    def test_completed_result_releases_only_matching_validated_document(self):
        source_request = request()
        result = FundamentalStructuredDocumentResult(
            **result_values(source_request)
        )

        validate_fundamental_response_binding(source_request, result)
        self.assertEqual(result.document.rows[0].original_label, "Assets")
        self.assertEqual(len(result.result_fingerprint), 64)

    def test_failed_result_is_explained_and_releases_no_document(self):
        source_request = request()
        result = FundamentalStructuredDocumentResult(
            **result_values(
                source_request,
                status=FundamentalRetrievalStatus.NOT_ENTITLED,
                document=None,
                limitations=("Provider plan does not expose this document.",),
            )
        )

        self.assertIsNone(result.document)
        with self.assertRaises(ValidationError):
            FundamentalStructuredDocumentResult(
                **result_values(
                    source_request,
                    status=FundamentalRetrievalStatus.PARTIAL,
                    limitations=("Partial table is not publishable.",),
                )
            )

    def test_result_rejects_cross_scope_document_and_raw_payload(self):
        source_request = request()
        with self.assertRaises(ValidationError):
            FundamentalStructuredDocumentResult(
                **result_values(
                    source_request,
                    document=document(issuer=issuer(symbol="INFY")),
                )
            )

        provider_secret = "session-cookie-must-not-cross"
        with self.assertRaises(ValidationError) as caught:
            FundamentalStructuredDocumentResult(
                **result_values(source_request),
                raw_payload={"cookie": provider_secret},
            )
        self.assertNotIn(provider_secret, str(caught.exception))

    def test_binding_rejects_wrong_document_scenario(self):
        source_request = request()
        wrong_type = FundamentalStructuredDocumentResult(
            **result_values(
                source_request,
                document_type=FinancialDocumentType.PROFIT_AND_LOSS,
                document=document(
                    document_type=FinancialDocumentType.PROFIT_AND_LOSS,
                ),
            )
        )
        with self.assertRaisesRegex(ValueError, "type does not match"):
            validate_fundamental_response_binding(source_request, wrong_type)

        wrong_basis = FundamentalStructuredDocumentResult(
            **result_values(
                source_request,
                reporting_basis=FinancialReportingBasis.STANDALONE,
                document=document(
                    reporting_basis=FinancialReportingBasis.STANDALONE,
                ),
            )
        )
        with self.assertRaisesRegex(ValueError, "reporting basis does not match"):
            validate_fundamental_response_binding(source_request, wrong_basis)

    def test_separate_protocol_keeps_coordinator_provider_agnostic(self):
        gateway = FakeStructuredDocumentGateway()

        self.assertIsInstance(gateway, FundamentalStructuredDocumentGateway)
        result = gateway.retrieve_structured_financial_document(
            request=request()
        )
        self.assertEqual(result.status, FundamentalRetrievalStatus.COMPLETED)

    def test_complete_package_contract_is_single_snapshot_and_bound(self):
        source_request = package_request()
        gateway = FakeFinancialDocumentPackageGateway()

        self.assertIsInstance(
            gateway,
            FundamentalFinancialDocumentPackageGateway,
        )
        result = gateway.retrieve_financial_document_package(
            request=source_request
        )
        validate_fundamental_response_binding(source_request, result)
        self.assertEqual(len(result.package.documents), 11)
        self.assertEqual(len(result.package.package_fingerprint), 64)

    def test_package_rejects_duplicate_scenario_and_mixed_snapshot(self):
        complete = financial_package()
        duplicate_documents = list(complete.documents)
        duplicate_documents[-1] = duplicate_documents[-2]
        with self.assertRaises(ValidationError):
            financial_package(documents=tuple(duplicate_documents))

        mixed_documents = list(complete.documents)
        mixed_documents[-1] = document(
            document_id=mixed_documents[-1].document_id,
            document_type=mixed_documents[-1].document_type,
            reporting_basis=mixed_documents[-1].reporting_basis,
            retrieved_at=REQUESTED_AT + timedelta(seconds=2),
        )
        with self.assertRaises(ValidationError):
            financial_package(documents=tuple(mixed_documents))

    def test_package_result_fails_closed_without_complete_package(self):
        source_request = package_request()
        common = {
            "request_id": source_request.request_id,
            "request_fingerprint": source_request.request_fingerprint,
            "connection": source_request.connection,
            "requested_at": source_request.requested_at,
            "started_at": source_request.requested_at,
            "completed_at": COMPLETED_AT,
            "provider_contract_version": "provider.contract.v1",
            "adapter_fingerprint": ADAPTER_HASH,
            "issuer": source_request.issuer,
        }
        with self.assertRaises(ValidationError):
            FundamentalFinancialDocumentPackageResult(
                **common,
                status=FundamentalRetrievalStatus.COMPLETED,
            )
        with self.assertRaises(ValidationError):
            FundamentalFinancialDocumentPackageResult(
                **common,
                status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                package=financial_package(),
                limitations=("Provider unavailable.",),
            )


if __name__ == "__main__":
    unittest.main()
