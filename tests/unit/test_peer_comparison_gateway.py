import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalCompanyOverviewRequest,
    FundamentalPeerComparisonGateway,
    FundamentalPeerComparisonResult,
    FundamentalRetrievalStatus,
    validate_fundamental_response_binding,
)
from app.models.financial_documents import (
    PeerComparisonCell,
    PeerComparisonCompany,
    PeerComparisonMetric,
    StructuredPeerComparisonDocument,
)
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalIssuerIdentity,
    FundamentalValidationStatus,
    FundamentalValueKind,
    ProviderConnectionScope,
)


IST = timezone(timedelta(hours=5, minutes=30))
REQUESTED_AT = datetime(2026, 9, 1, 10, 0, tzinfo=IST)
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


def request(**overrides) -> FundamentalCompanyOverviewRequest:
    values = {
        "request_id": "request.coforge.peer-comparison",
        "operation_id": "operation.coforge.fundamentals",
        "connection": connection(),
        "requested_at": REQUESTED_AT,
        "issuer": issuer(),
        "as_of_date": date(2026, 9, 1),
    }
    values.update(overrides)
    return FundamentalCompanyOverviewRequest(**values)


def document(**overrides) -> StructuredPeerComparisonDocument:
    metric = PeerComparisonMetric(
        metric_key="pe",
        source_label="PE",
        standardized_label="P/E",
        value_kind=FundamentalValueKind.RATIO,
        source_unit="ratio",
        normalized_unit="ratio",
        display_order=0,
    )
    values = {
        "document_id": "tijori.NSE.COFORGE.peer_comparison.not_applicable",
        "connection": connection(),
        "issuer": issuer(),
        "observation_date": date(2026, 9, 1),
        "metrics": (metric,),
        "peers": (
            PeerComparisonCompany(
                peer_key="peer_niit_technologies_limited",
                legal_name="Coforge Ltd.",
                provider_slug="niit-technologies-limited",
                is_subject=True,
                display_order=0,
                cells=(
                    PeerComparisonCell(
                        metric_key="pe",
                        source_value="24.1",
                        normalized_value=Decimal("24.1"),
                        availability_status=(
                            FundamentalAvailabilityStatus.AVAILABLE
                        ),
                    ),
                ),
            ),
        ),
        "source_location": (
            "https://www.tijorifinance.com/company/niit-technologies-limited/"
        ),
        "retrieved_at": REQUESTED_AT + timedelta(seconds=1),
        "expires_at": REQUESTED_AT + timedelta(days=10),
        "validation_status": FundamentalValidationStatus.VALIDATED,
    }
    values.update(overrides)
    return StructuredPeerComparisonDocument(**values)


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
        "status": FundamentalRetrievalStatus.COMPLETED,
        "document": document(),
    }
    values.update(overrides)
    return values


class FakePeerComparisonGateway:
    configuration_fingerprint = ADAPTER_HASH

    def retrieve_peer_comparison(self, *, request):
        return FundamentalPeerComparisonResult(**result_values(request))


class FundamentalPeerComparisonGatewayTests(unittest.TestCase):
    def test_completed_result_is_bound_and_fingerprinted(self):
        source_request = request()
        result = FundamentalPeerComparisonResult(
            **result_values(source_request)
        )

        validate_fundamental_response_binding(source_request, result)
        self.assertEqual(result.document.peers[0].legal_name, "Coforge Ltd.")
        self.assertEqual(len(result.result_fingerprint), 64)

    def test_failed_result_is_explained_and_releases_no_document(self):
        source_request = request()
        result = FundamentalPeerComparisonResult(
            **result_values(
                source_request,
                status=FundamentalRetrievalStatus.NOT_ENTITLED,
                document=None,
                limitations=("Provider plan does not expose peers.",),
            )
        )
        self.assertIsNone(result.document)

        with self.assertRaises(ValidationError):
            FundamentalPeerComparisonResult(
                **result_values(
                    source_request,
                    status=FundamentalRetrievalStatus.NOT_FOUND,
                    document=None,
                    limitations=(),
                )
            )

    def test_rejects_cross_scope_or_cross_issuer_document(self):
        source_request = request()
        with self.assertRaises(ValidationError):
            FundamentalPeerComparisonResult(
                **result_values(
                    source_request,
                    document=document(issuer=issuer(symbol="INFY")),
                )
            )

        with self.assertRaises(ValidationError):
            FundamentalPeerComparisonResult(
                **result_values(
                    source_request,
                    connection=ProviderConnectionScope(
                        tenant_id="tenant.other",
                        provider_connection_id="provider.tijori.other",
                        provider="tijori",
                    ),
                )
            )

    def test_protocol_keeps_coordinator_provider_agnostic(self):
        gateway = FakePeerComparisonGateway()

        self.assertIsInstance(gateway, FundamentalPeerComparisonGateway)
        result = gateway.retrieve_peer_comparison(request=request())
        self.assertEqual(result.status, FundamentalRetrievalStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
