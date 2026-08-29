import math
import unittest
from datetime import UTC, date, datetime, timedelta

from pydantic import ValidationError

from app.exceptions import (
    FundamentalGatewayAuthenticationError,
    FundamentalGatewayConfigurationError,
    FundamentalProviderRateLimitError,
    FundamentalProviderUnavailableError,
    FundamentalResponseValidationError,
)
from app.fundamentals.adapters.tijori_mcp import TijoriMcpAdapter
from app.fundamentals.tijori_mcp_contracts import (
    TIJORI_APPROVED_TOOLS,
    TijoriMcpAdapterSettings,
    TijoriMcpToolResult,
    TijoriMcpTransportError,
    TijoriMcpTransportInspection,
    TijoriToolStatus,
    TijoriTransportFailureKind,
)
from app.gateways.fundamentals import (
    FundamentalCapabilityStatus,
    FundamentalCompanyOverviewRequest,
    FundamentalCompanySearchRequest,
    FundamentalEvidenceGateway,
    FundamentalFinancialsRequest,
    FundamentalIssuerLocator,
    FundamentalIssuerResolutionRequest,
    FundamentalResolutionStatus,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
)
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalEvidenceLabel,
    FundamentalEvidencePosture,
    FundamentalPeriodType,
    FundamentalSourceRank,
    FundamentalSourceType,
    FundamentalStatement,
    FundamentalValueKind,
    ProviderConnectionScope,
    ProviderEntitlementStatus,
    ProviderSubscriptionTier,
)


NOW = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


class FakeTijoriTransport:
    configuration_fingerprint = "a" * 64

    def __init__(self) -> None:
        self.result = TijoriMcpToolResult(
            tool_name="search_company",
            status=TijoriToolStatus.SUCCESS,
            payload={"companies": []},
        )
        self.inspection = TijoriMcpTransportInspection(
            available_tools=TIJORI_APPROVED_TOOLS,
            authenticated=True,
            checked_at=NOW,
            provider_contract_version="tijori.synthetic_contract.v1",
        )
        self.failure: Exception | None = None
        self.calls: list[tuple[ProviderConnectionScope, str, dict]] = []

    def inspect(self, *, connection):
        if self.failure is not None:
            raise self.failure
        return self.inspection

    def call_tool(self, *, connection, tool_name, arguments):
        self.calls.append((connection, tool_name, arguments))
        if self.failure is not None:
            raise self.failure
        return self.result


def company_payload() -> dict:
    return {
        "company_id": "tcs-1",
        "slug": "tata-consultancy-services",
        "legal_name": "Tata Consultancy Services Limited",
        "exchange": "nse",
        "symbol": "tcs",
        "isin": "INE467B01029",
        "match_kind": "exact_symbol",
        "match_score": "1",
        "matched_on": ["symbol", "exchange"],
    }


def evidence_payload(
    *,
    statement: str = "company_profile",
    availability: str = "available",
) -> dict:
    available = availability == "available"
    return {
        "company_id": "tcs-1",
        "exchange": "nse",
        "symbol": "tcs",
        "sources": [
            {
                "provider_source_id": "provider-source-1",
                "source_name": "Tijori standardized company record",
                "location": "https://www.tijorifinance.com/company/tcs",
                "period_covered": "FY 2025-26",
                "as_of_date": "2026-03-31",
                "published_at": "2026-04-20T09:00:00+05:30",
            }
        ],
        "facts": [
            {
                "provider_fact_id": "fact-1",
                "provider_source_id": (
                    "provider-source-1" if available else None
                ),
                "statement": statement,
                "line_item_original": "Revenue",
                "line_item_standard": "Revenue from operations",
                "line_item_id": "revenue",
                "period_label": "FY 2025-26",
                "period_type": "annual",
                "period_start": "2025-04-01",
                "period_end": "2026-03-31",
                "value_kind": "monetary",
                "source_value": "255324 crore" if available else None,
                "normalized_value": "255324" if available else None,
                "currency": "inr",
                "source_unit": "crore" if available else None,
                "normalized_unit": "INR crore" if available else None,
                "availability_status": availability,
            }
        ],
        "limitations": [],
    }


class TijoriMcpAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeTijoriTransport()
        self.adapter = TijoriMcpAdapter(
            transport=self.transport,
            clock=lambda: NOW,
        )
        self.connection = ProviderConnectionScope(
            tenant_id="tenant-1",
            provider_connection_id="tijori-user-1",
            provider="tijori",
            account_reference_hash="b" * 64,
            subscription_tier=ProviderSubscriptionTier.PAID,
            entitlement_status=ProviderEntitlementStatus.VERIFIED,
            capabilities=TIJORI_APPROVED_TOOLS,
            entitlement_checked_at=NOW,
        )
        from app.models.fundamentals import FundamentalIssuerIdentity

        self.issuer = FundamentalIssuerIdentity(
            exchange="NSE",
            symbol="TCS",
            legal_name="Tata Consultancy Services Limited",
            isin="INE467B01029",
            provider_company_id="tcs-1",
            provider_slug="tata-consultancy-services",
        )

    def search_request(self, **changes):
        values = {
            "request_id": "request-search-1",
            "operation_id": "operation-1",
            "connection": self.connection,
            "requested_at": NOW,
            "query": "TCS",
            "exchanges": ("NSE",),
            "max_results": 10,
        }
        values.update(changes)
        return FundamentalCompanySearchRequest(**values)

    def overview_request(self, **changes):
        values = {
            "request_id": "request-overview-1",
            "operation_id": "operation-1",
            "connection": self.connection,
            "requested_at": NOW,
            "issuer": self.issuer,
            "as_of_date": date(2026, 8, 28),
        }
        values.update(changes)
        return FundamentalCompanyOverviewRequest(**values)

    def test_implements_provider_neutral_gateway(self):
        self.assertIsInstance(self.adapter, FundamentalEvidenceGateway)
        self.assertEqual(len(self.adapter.configuration_fingerprint), 64)
        self.assertNotIn("tijori-user-1", self.adapter.configuration_fingerprint)

    def test_settings_enforce_exact_audited_allow_list(self):
        with self.assertRaises(ValidationError):
            TijoriMcpAdapterSettings(approved_tools=("search_company",))

    def test_tool_envelope_rejects_unapproved_name_and_failed_payload(self):
        with self.assertRaises(ValidationError):
            TijoriMcpToolResult(
                tool_name="delete_company",
                status=TijoriToolStatus.SUCCESS,
                payload={},
            )
        with self.assertRaises(ValidationError):
            TijoriMcpToolResult(
                tool_name="search_company",
                status=TijoriToolStatus.NOT_FOUND,
                payload={"secret": "must-not-cross"},
            )

    def test_tool_envelope_rejects_nonfinite_and_deep_payloads(self):
        with self.assertRaises(ValidationError):
            TijoriMcpToolResult(
                tool_name="search_company",
                status=TijoriToolStatus.SUCCESS,
                payload={"value": math.nan},
            )
        nested = {}
        cursor = nested
        for _ in range(10):
            cursor["next"] = {}
            cursor = cursor["next"]
        with self.assertRaises(ValidationError):
            TijoriMcpToolResult(
                tool_name="search_company",
                status=TijoriToolStatus.SUCCESS,
                payload=nested,
            )

    def test_capability_inspection_maps_all_five_tools(self):
        manifest = self.adapter.inspect_capabilities(connection=self.connection)
        self.assertEqual(len(manifest.capabilities), 5)
        self.assertTrue(all(
            item.status is FundamentalCapabilityStatus.AVAILABLE
            for item in manifest.capabilities
        ))
        history = [item for item in manifest.capabilities if item.supports_history]
        self.assertEqual([item.max_periods for item in history], [40, 40])

    def test_unauthenticated_inspection_marks_every_capability_unavailable(self):
        self.transport.inspection = TijoriMcpTransportInspection(
            available_tools=TIJORI_APPROVED_TOOLS,
            authenticated=False,
            checked_at=NOW,
            provider_contract_version="tijori.synthetic_contract.v1",
        )
        manifest = self.adapter.inspect_capabilities(connection=self.connection)
        self.assertTrue(all(
            item.status is FundamentalCapabilityStatus.UNAVAILABLE
            for item in manifest.capabilities
        ))

    def test_inspection_rejects_contract_version_drift(self):
        self.transport.inspection = TijoriMcpTransportInspection(
            available_tools=TIJORI_APPROVED_TOOLS,
            authenticated=True,
            checked_at=NOW,
            provider_contract_version="tijori.synthetic_contract.v2",
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.inspect_capabilities(connection=self.connection)

    def test_inspection_rejects_invalid_transport_envelope(self):
        self.transport.inspection = {"authenticated": True}
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.inspect_capabilities(connection=self.connection)

    def test_search_normalizes_candidate_and_sends_only_business_arguments(self):
        self.transport.result = TijoriMcpToolResult(
            tool_name="search_company",
            status=TijoriToolStatus.SUCCESS,
            payload={"companies": [company_payload()]},
        )
        response = self.adapter.search_companies(request=self.search_request())
        self.assertEqual(response.candidates[0].issuer.symbol, "TCS")
        connection, tool, arguments = self.transport.calls[0]
        self.assertEqual(connection, self.connection)
        self.assertEqual(tool, "search_company")
        self.assertEqual(
            arguments,
            {"query": "TCS", "exchanges": ["NSE"], "max_results": 10},
        )
        serialized = repr(arguments).lower()
        self.assertNotIn("tenant", serialized)
        self.assertNotIn("account", serialized)
        self.assertNotIn("secret", serialized)

    def test_search_rejects_exchange_escape_and_tool_mismatch(self):
        company = company_payload()
        company["exchange"] = "BSE"
        self.transport.result = TijoriMcpToolResult(
            tool_name="search_company",
            status=TijoriToolStatus.SUCCESS,
            payload={"companies": [company]},
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.search_companies(request=self.search_request())

        self.transport.result = TijoriMcpToolResult(
            tool_name="resolve_company_ids",
            status=TijoriToolStatus.SUCCESS,
            payload={"companies": []},
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.search_companies(request=self.search_request())

    def test_resolution_supports_resolved_ambiguous_and_not_found(self):
        request = FundamentalIssuerResolutionRequest(
            request_id="request-resolve-1",
            connection=self.connection,
            requested_at=NOW,
            locator=FundamentalIssuerLocator(exchange="NSE", symbol="TCS"),
            max_candidates=10,
        )
        self.transport.result = TijoriMcpToolResult(
            tool_name="resolve_company_ids",
            status=TijoriToolStatus.SUCCESS,
            payload={
                "status": "resolved",
                "companies": [company_payload()],
                "selected_company_id": "tcs-1",
            },
        )
        resolved = self.adapter.resolve_issuer(request=request)
        self.assertEqual(resolved.status, FundamentalResolutionStatus.RESOLVED)
        self.assertEqual(resolved.issuer.symbol, "TCS")

        second = company_payload()
        second.update({"company_id": "tcs-2", "symbol": "TCS2"})
        self.transport.result = TijoriMcpToolResult(
            tool_name="resolve_company_ids",
            status=TijoriToolStatus.SUCCESS,
            payload={
                "status": "ambiguous",
                "companies": [company_payload(), second],
                "selected_company_id": None,
            },
        )
        ambiguous = self.adapter.resolve_issuer(request=request)
        self.assertEqual(ambiguous.status, FundamentalResolutionStatus.AMBIGUOUS)
        self.assertIsNotNone(ambiguous.limitation)

        self.transport.result = TijoriMcpToolResult(
            tool_name="resolve_company_ids",
            status=TijoriToolStatus.NOT_FOUND,
        )
        missing = self.adapter.resolve_issuer(request=request)
        self.assertEqual(missing.status, FundamentalResolutionStatus.NOT_FOUND)

    def test_overview_normalizes_secondary_research_grade_evidence(self):
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=evidence_payload(),
        )
        response = self.adapter.retrieve_company_overview(
            request=self.overview_request()
        )
        self.assertEqual(response.status, FundamentalRetrievalStatus.COMPLETED)
        self.assertEqual(
            response.snapshot.evidence_posture,
            FundamentalEvidencePosture.RESEARCH_GRADE,
        )
        source = response.snapshot.sources[0]
        fact = response.snapshot.facts[0]
        self.assertEqual(source.source_type, FundamentalSourceType.PROVIDER_STANDARDIZED)
        self.assertEqual(source.source_rank, FundamentalSourceRank.STANDARDIZED_PROVIDER)
        self.assertEqual(fact.evidence_label, FundamentalEvidenceLabel.FACT_PROVIDER_STANDARDIZED)
        self.assertNotIn("provider-source-1", response.model_dump_json())

    def test_missing_fact_returns_explicit_partial_evidence(self):
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=evidence_payload(
                availability=FundamentalAvailabilityStatus.MISSING_REQUIRED_SOURCE.value
            ),
        )
        response = self.adapter.retrieve_company_overview(
            request=self.overview_request()
        )
        self.assertEqual(response.status, FundamentalRetrievalStatus.PARTIAL)
        self.assertTrue(response.limitations)
        fact = response.snapshot.facts[0]
        self.assertEqual(fact.evidence_label, FundamentalEvidenceLabel.MISSING_REQUIRED_SOURCE)
        self.assertFalse(fact.lineage.source_references)

    def test_semantic_retrieval_failures_release_no_snapshot(self):
        mapping = {
            TijoriToolStatus.NOT_FOUND: FundamentalRetrievalStatus.NOT_FOUND,
            TijoriToolStatus.NOT_ENTITLED: FundamentalRetrievalStatus.NOT_ENTITLED,
            TijoriToolStatus.PAYWALLED: FundamentalRetrievalStatus.PAYWALLED,
            TijoriToolStatus.UNAVAILABLE: FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
        }
        for tool_status, retrieval_status in mapping.items():
            with self.subTest(tool_status=tool_status):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_company_overview",
                    status=tool_status,
                )
                response = self.adapter.retrieve_company_overview(
                    request=self.overview_request()
                )
                self.assertEqual(response.status, retrieval_status)
                self.assertIsNone(response.snapshot)
                self.assertTrue(response.limitations)

    def test_financial_and_shareholding_requests_use_separate_tools(self):
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=evidence_payload(statement="income_statement"),
        )
        financial_request = FundamentalFinancialsRequest(
            request_id="request-financials-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            statements=(FundamentalStatement.INCOME_STATEMENT,),
            period_types=(FundamentalPeriodType.ANNUAL,),
            max_periods=5,
        )
        financials = self.adapter.retrieve_financials(request=financial_request)
        self.assertEqual(financials.status, FundamentalRetrievalStatus.COMPLETED)
        self.assertEqual(self.transport.calls[-1][1], "get_financials")
        self.assertEqual(self.transport.calls[-1][2]["max_periods"], 5)

        self.transport.result = TijoriMcpToolResult(
            tool_name="get_shareholding",
            status=TijoriToolStatus.SUCCESS,
            payload=evidence_payload(statement="ownership"),
        )
        shareholding_request = FundamentalShareholdingRequest(
            request_id="request-shareholding-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            quarters=8,
            include_promoter_pledge=True,
        )
        shareholding = self.adapter.retrieve_shareholding(
            request=shareholding_request
        )
        self.assertEqual(shareholding.status, FundamentalRetrievalStatus.COMPLETED)
        self.assertEqual(self.transport.calls[-1][1], "get_shareholding")
        self.assertEqual(self.transport.calls[-1][2]["quarters"], 8)

    def test_cross_issuer_and_out_of_scope_statement_are_rejected(self):
        wrong = evidence_payload()
        wrong["symbol"] = "INFY"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=wrong,
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_company_overview(request=self.overview_request())

        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=evidence_payload(statement="cash_flow"),
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_company_overview(request=self.overview_request())

    def test_unsafe_source_url_and_schema_drift_are_safely_rejected(self):
        unsafe = evidence_payload()
        unsafe["sources"][0]["location"] = (
            "https://user:password@tijorifinance.com/company/tcs?token=secret"
        )
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=unsafe,
        )
        with self.assertRaises(FundamentalResponseValidationError) as caught:
            self.adapter.retrieve_company_overview(request=self.overview_request())
        self.assertNotIn("password", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

        drift = evidence_payload()
        drift["unexpected"] = "raw-provider-value"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=drift,
        )
        with self.assertRaises(FundamentalResponseValidationError) as caught:
            self.adapter.retrieve_company_overview(request=self.overview_request())
        self.assertNotIn("raw-provider-value", str(caught.exception))

    def test_transport_failures_map_to_sanitized_application_errors(self):
        mapping = {
            TijoriTransportFailureKind.AUTHENTICATION: FundamentalGatewayAuthenticationError,
            TijoriTransportFailureKind.RATE_LIMIT: FundamentalProviderRateLimitError,
            TijoriTransportFailureKind.UNAVAILABLE: FundamentalProviderUnavailableError,
            TijoriTransportFailureKind.PROTOCOL: FundamentalResponseValidationError,
        }
        for failure, error_type in mapping.items():
            with self.subTest(failure=failure):
                self.transport.failure = TijoriMcpTransportError(failure)
                with self.assertRaises(error_type) as caught:
                    self.adapter.search_companies(request=self.search_request())
                self.assertNotIn("token", str(caught.exception).lower())
                self.transport.failure = None

    def test_unexpected_transport_error_does_not_leak_raw_text(self):
        self.transport.failure = RuntimeError("password=raw-secret")
        with self.assertRaises(FundamentalProviderUnavailableError) as caught:
            self.adapter.search_companies(request=self.search_request())
        self.assertNotIn("raw-secret", str(caught.exception))

    def test_rejects_wrong_provider_and_invalid_clock(self):
        wrong_connection = self.connection.model_copy(
            update={"provider": "another-provider"}
        )
        with self.assertRaises(FundamentalGatewayConfigurationError):
            self.adapter.search_companies(
                request=self.search_request(connection=wrong_connection)
            )

        adapter = TijoriMcpAdapter(
            transport=self.transport,
            clock=lambda: NOW - timedelta(seconds=1),
        )
        with self.assertRaises(FundamentalGatewayConfigurationError):
            adapter.search_companies(request=self.search_request())

    def test_rejects_invalid_transport_fingerprint(self):
        self.transport.configuration_fingerprint = "session-secret"
        with self.assertRaises(FundamentalGatewayConfigurationError):
            TijoriMcpAdapter(transport=self.transport)


if __name__ == "__main__":
    unittest.main()
