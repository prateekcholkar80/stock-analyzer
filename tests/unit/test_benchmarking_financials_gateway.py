import unittest
from datetime import timedelta

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsGateway,
    FundamentalBenchmarkingFinancialsResult,
    FundamentalCompanyOverviewRequest,
    FundamentalRetrievalStatus,
    validate_fundamental_response_binding,
)
from app.models.financial_documents import (
    StructuredBenchmarkingFinancialsDocument,
)
from app.models.fundamentals import ProviderConnectionScope
from tests.unit.test_benchmarking_financials_documents import (
    RETRIEVED_AT,
    document_payload,
)


COMPLETED_AT = RETRIEVED_AT + timedelta(seconds=2)
ADAPTER_HASH = "b" * 64


def document(**overrides) -> StructuredBenchmarkingFinancialsDocument:
    values = document_payload()
    values.update(overrides)
    return StructuredBenchmarkingFinancialsDocument(**values)


def request(**overrides) -> FundamentalCompanyOverviewRequest:
    source = document_payload()
    values = {
        "request_id": "request.coforge.benchmarking-financials",
        "operation_id": "operation.coforge.fundamentals",
        "connection": source["connection"],
        "requested_at": RETRIEVED_AT,
        "issuer": source["issuer"],
        "as_of_date": source["observation_date"],
    }
    values.update(overrides)
    return FundamentalCompanyOverviewRequest(**values)


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


class FakeBenchmarkingFinancialsGateway:
    configuration_fingerprint = ADAPTER_HASH

    def retrieve_benchmarking_financials(self, *, request):
        return FundamentalBenchmarkingFinancialsResult(
            **result_values(request)
        )


class FundamentalBenchmarkingFinancialsGatewayTests(unittest.TestCase):
    def test_completed_result_is_bound_and_fingerprinted(self):
        source_request = request()
        result = FundamentalBenchmarkingFinancialsResult(
            **result_values(source_request)
        )

        validate_fundamental_response_binding(source_request, result)
        self.assertEqual(
            result.document.companies[0].legal_name,
            "Coforge",
        )
        self.assertEqual(len(result.result_fingerprint), 64)

    def test_failed_result_is_explained_and_releases_no_document(self):
        source_request = request()
        result = FundamentalBenchmarkingFinancialsResult(
            **result_values(
                source_request,
                status=FundamentalRetrievalStatus.NOT_ENTITLED,
                document=None,
                limitations=(
                    "Provider plan does not expose Financial benchmarking.",
                ),
            )
        )
        self.assertIsNone(result.document)

        with self.assertRaises(ValidationError):
            FundamentalBenchmarkingFinancialsResult(
                **result_values(
                    source_request,
                    status=FundamentalRetrievalStatus.NOT_FOUND,
                    document=None,
                    limitations=(),
                )
            )

    def test_rejects_cross_scope_issuer_and_late_document(self):
        source_request = request()
        other_connection = ProviderConnectionScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        mutations = (
            {"connection": other_connection},
            {
                "document": document(
                    issuer=source_request.issuer.model_copy(
                        update={"symbol": "INFY"}
                    )
                )
            },
            {
                "document": document(
                    retrieved_at=COMPLETED_AT + timedelta(seconds=1),
                    expires_at=COMPLETED_AT + timedelta(days=10),
                )
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValidationError):
                    FundamentalBenchmarkingFinancialsResult(
                        **result_values(source_request, **mutation)
                    )

    def test_protocol_keeps_coordinator_provider_agnostic(self):
        gateway = FakeBenchmarkingFinancialsGateway()

        self.assertIsInstance(
            gateway,
            FundamentalBenchmarkingFinancialsGateway,
        )
        result = gateway.retrieve_benchmarking_financials(request=request())
        self.assertEqual(result.status, FundamentalRetrievalStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
