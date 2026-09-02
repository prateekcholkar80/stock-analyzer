import math
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

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
    FundamentalBenchmarkingFinancialsGateway,
    FundamentalCapabilityStatus,
    FundamentalCompanyOverviewRequest,
    FundamentalCompanySearchRequest,
    FundamentalEvidenceGateway,
    FundamentalFinancialsRequest,
    FundamentalIssuerLocator,
    FundamentalIssuerResolutionRequest,
    FundamentalPeerComparisonGateway,
    FundamentalResolutionStatus,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
    FundamentalStructuredDocumentGateway,
    FundamentalStructuredDocumentRequest,
)
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
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
from tests.unit.test_tijori_benchmarking_financials_contract import (
    benchmarking_payload,
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


def growth_table_payload() -> dict:
    return {
        "document": {
            "schema_version": "tijori.financial_document.v1",
            "document_type": "growth_table",
            "reporting_basis": "not_applicable",
            "issuer": {
                "exchange": "NSE",
                "symbol": "TCS",
                "legal_name": "Tata Consultancy Services Limited",
                "provider_company_id": "tcs-1",
                "provider_slug": "tata-consultancy-services",
            },
            "source": {
                "provider": "tijori",
                "location": (
                    "https://www.tijorifinance.com/company/"
                    "tata-consultancy-services/financials/"
                ),
                "retrieved_at": NOW.isoformat(),
            },
            "unit": "percent",
            "all_sections_expanded": True,
            "columns": [
                {
                    "column_key": "period_1yr",
                    "source_label": "1yr",
                    "display_order": 0,
                }
            ],
            "rows": [
                {
                    "row_key": "sales_cagr",
                    "original_label": "Sales CAGR",
                    "parent_row_key": None,
                    "depth": 0,
                    "row_kind": "metric",
                    "display_order": 0,
                    "values": [
                        {
                            "column_key": "period_1yr",
                            "source_value": "12.4",
                            "yoy_change": "8%",
                            "percentage_of_parent": None,
                            "availability_status": "available",
                        }
                    ],
                }
            ],
            "extraction": {
                "column_count": 1,
                "row_count": 1,
                "status": "complete",
            },
        }
    }


def balance_sheet_payload(reporting_basis: str = "consolidated") -> dict:
    return {
        "document": {
            "schema_version": "tijori.financial_document.v1",
            "document_type": "balance_sheet",
            "reporting_basis": reporting_basis,
            "issuer": {
                "exchange": "NSE",
                "symbol": "TCS",
                "legal_name": "Tata Consultancy Services Limited",
                "provider_company_id": "tcs-1",
                "provider_slug": "tata-consultancy-services",
            },
            "source": {
                "provider": "tijori",
                "location": (
                    "https://www.tijorifinance.com/company/"
                    "tata-consultancy-services/financials/"
                ),
                "retrieved_at": NOW.isoformat(),
            },
            "source_unit": "Rs. Cr.",
            "normalized_unit": "INR crore",
            "skipped_report_dates": ["Mar 2023"],
            "all_sections_expanded": True,
            "periods": [
                {
                    "period_key": "period_mar_2024",
                    "source_label": "Mar 2024",
                    "display_order": 0,
                },
                {
                    "period_key": "period_mar_2025",
                    "source_label": "Mar 2025",
                    "display_order": 1,
                },
            ],
            "rows": [
                {
                    "row_key": "assets",
                    "original_label": "Assets",
                    "parent_row_key": None,
                    "depth": 0,
                    "row_kind": "section",
                    "display_order": 0,
                    "values": [
                        {
                            "period_key": "period_mar_2024",
                            "source_value": "1,000",
                            "yoy_change": None,
                            "percentage_of_parent": "100%",
                            "availability_status": "available",
                        },
                        {
                            "period_key": "period_mar_2025",
                            "source_value": "1200.50",
                            "yoy_change": "20%",
                            "percentage_of_parent": "100%",
                            "availability_status": "available",
                        },
                    ],
                },
                {
                    "row_key": "cash_and_bank_balances",
                    "original_label": "Cash and Bank Balances",
                    "parent_row_key": "assets",
                    "depth": 1,
                    "row_kind": "metric",
                    "display_order": 1,
                    "values": [
                        {
                            "period_key": "period_mar_2024",
                            "source_value": "0",
                            "yoy_change": None,
                            "percentage_of_parent": "0%",
                            "availability_status": "available",
                        },
                        {
                            "period_key": "period_mar_2025",
                            "source_value": None,
                            "yoy_change": None,
                            "percentage_of_parent": None,
                            "availability_status": "unknown",
                        },
                    ],
                },
            ],
            "extraction": {
                "period_count": 2,
                "row_count": 2,
                "cell_count": 4,
                "maximum_depth": 1,
                "status": "complete",
            },
        }
    }


def cash_flow_payload(reporting_basis: str = "consolidated") -> dict:
    payload = balance_sheet_payload(reporting_basis)
    document = payload["document"]
    document["document_type"] = "cash_flow"

    operating_cash, working_capital = document["rows"]
    operating_cash.update({
        "row_key": "cash_from_operating_activity",
        "original_label": "Cash from Operating Activity",
    })
    operating_cash["values"][0]["source_value"] = "100"
    operating_cash["values"][1]["source_value"] = "120"
    working_capital.update({
        "row_key": "working_capital_changes",
        "original_label": "Working Capital Changes",
        "parent_row_key": "cash_from_operating_activity",
    })
    working_capital["values"][0]["source_value"] = "-12"
    working_capital["values"][0]["percentage_of_parent"] = None
    net_cash = {
        "row_key": "net_cash_flow",
        "original_label": "Net Cash Flow",
        "parent_row_key": None,
        "depth": 0,
        "row_kind": "metric",
        "display_order": 2,
        "values": [
            {
                "period_key": "period_mar_2024",
                "source_value": "0",
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "available",
            },
            {
                "period_key": "period_mar_2025",
                "source_value": None,
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "unknown",
            },
        ],
    }
    document["rows"].append(net_cash)
    document["extraction"].update({
        "row_count": 3,
        "cell_count": 6,
    })
    return payload


def profit_and_loss_payload(reporting_basis: str = "consolidated") -> dict:
    payload = balance_sheet_payload(reporting_basis)
    document = payload["document"]
    document["document_type"] = "profit_and_loss"
    document["source_unit"] = "mixed"
    document["normalized_unit"] = "mixed"

    sales, margin = document["rows"]
    sales.update({
        "row_key": "sales",
        "original_label": "Sales",
        "row_kind": "metric",
        "value_kind": "monetary",
        "source_unit": "Rs. Cr.",
        "normalized_unit": "INR crore",
    })
    margin.update({
        "row_key": "opm",
        "original_label": "OPM (%)",
        "parent_row_key": None,
        "depth": 0,
        "value_kind": "percentage",
        "source_unit": "percent",
        "normalized_unit": "percent",
    })
    margin["values"][0]["source_value"] = "18"
    margin["values"][0]["percentage_of_parent"] = None
    shares = {
        "row_key": "number_of_shares",
        "original_label": "Number of shares (Crs)",
        "parent_row_key": None,
        "depth": 0,
        "row_kind": "metric",
        "display_order": 2,
        "value_kind": "count",
        "source_unit": "crore shares",
        "normalized_unit": "crore shares",
        "values": [
            {
                "period_key": "period_mar_2024",
                "source_value": "10",
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "available",
            },
            {
                "period_key": "period_mar_2025",
                "source_value": None,
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "unknown",
            },
        ],
    }
    document["rows"].append(shares)
    document["extraction"].update({
        "row_count": 3,
        "cell_count": 6,
        "maximum_depth": 0,
    })
    return payload


def ratios_payload(reporting_basis: str = "consolidated") -> dict:
    payload = profit_and_loss_payload(reporting_basis)
    document = payload["document"]
    document["document_type"] = "ratios"
    monetary, percentage, per_share = document["rows"]
    monetary.update({
        "row_key": "current_assets",
        "original_label": "Current Assets (Crs)",
    })
    percentage.update({
        "row_key": "gross_margin",
        "original_label": "Gross Margin (%)",
    })
    per_share.update({
        "row_key": "adjusted_eps",
        "original_label": "Adjusted EPS",
        "value_kind": "per_share",
        "source_unit": "per share",
        "normalized_unit": "per share",
    })
    ratio = {
        "row_key": "current_ratio",
        "original_label": "Current Ratio",
        "parent_row_key": None,
        "depth": 0,
        "row_kind": "metric",
        "display_order": 3,
        "value_kind": "ratio",
        "source_unit": "ratio",
        "normalized_unit": "ratio",
        "values": [
            {
                "period_key": "period_mar_2024",
                "source_value": "1.1",
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "available",
            },
            {
                "period_key": "period_mar_2025",
                "source_value": "1.2",
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "available",
            },
        ],
    }
    days = {
        **ratio,
        "row_key": "cash_conversion_cycle",
        "original_label": "Cash Conversion Cycle",
        "display_order": 4,
        "value_kind": "other",
        "source_unit": "days",
        "normalized_unit": "days",
        "values": [
            {**ratio["values"][0], "source_value": "-30"},
            {**ratio["values"][1], "source_value": "-20"},
        ],
    }
    section = {
        **ratio,
        "row_key": "valuation_ratios",
        "original_label": "Valuation Ratios",
        "display_order": 5,
        "row_kind": "section",
        "value_kind": "other",
        "source_unit": "not applicable",
        "normalized_unit": "not applicable",
        "values": [
            {**ratio["values"][0], "source_value": "0"},
            {**ratio["values"][1], "source_value": "0"},
        ],
    }
    document["rows"].extend((ratio, days, section))
    document["extraction"].update({
        "row_count": 6,
        "cell_count": 12,
    })
    return payload


def quarterly_results_payload(reporting_basis: str = "consolidated") -> dict:
    payload = profit_and_loss_payload(reporting_basis)
    document = payload["document"]
    document["document_type"] = "quarterly_results"
    sales, margin, shares = document["rows"]
    sales.update({
        "row_key": "net_sales",
        "original_label": "Net Sales",
    })
    margin.update({
        "row_key": "quarterly_ratios",
        "original_label": "Quarterly Ratios",
        "row_kind": "section",
        "value_kind": "other",
        "source_unit": "not applicable",
        "normalized_unit": "not applicable",
    })
    margin["values"][0]["source_value"] = "0"
    margin["values"][1]["source_value"] = "0"
    margin["values"][1]["availability_status"] = "available"
    shares.update({
        "row_key": "eps",
        "original_label": "EPS",
        "parent_row_key": "quarterly_ratios",
        "depth": 1,
        "value_kind": "per_share",
        "source_unit": "per share",
        "normalized_unit": "per share",
    })
    shares["values"][1]["source_value"] = "11"
    shares["values"][1]["availability_status"] = "available"
    operating_margin = {
        **shares,
        "row_key": "operating_profit_margin",
        "original_label": "Operating Profit Margin",
        "display_order": 3,
        "value_kind": "percentage",
        "source_unit": "percent",
        "normalized_unit": "percent",
        "values": [
            {**shares["values"][0], "source_value": "18"},
            {**shares["values"][1], "source_value": "20"},
        ],
    }
    document["rows"].append(operating_margin)
    document["extraction"].update({
        "row_count": 4,
        "cell_count": 8,
        "maximum_depth": 1,
    })
    return payload


def peer_comparison_payload() -> dict:
    return {
        "document": {
            "schema_version": "tijori.peer_comparison.v1",
            "document_type": "peer_comparison",
            "issuer": {
                "exchange": "nse",
                "symbol": "tcs",
                "legal_name": "Tata Consultancy Services Limited",
                "provider_company_id": "tcs-1",
                "provider_slug": "tata-consultancy-services",
            },
            "source": {
                "provider": "tijori",
                "location": (
                    "https://www.tijorifinance.com/company/"
                    "tata-consultancy-services/"
                ),
                "retrieved_at": NOW.isoformat(),
            },
            "observation_date": "2026-08-28",
            "metrics": [
                {
                    "metric_key": "latest_price",
                    "standardized_label": "Latest Price",
                    "value_kind": "monetary",
                    "source_unit": "INR",
                    "source_label": "Latest Price",
                    "display_order": 0,
                },
                {
                    "metric_key": "pe",
                    "standardized_label": "P/E",
                    "value_kind": "ratio",
                    "source_unit": "ratio",
                    "source_label": "PE",
                    "display_order": 1,
                },
                {
                    "metric_key": "promoter_holding",
                    "standardized_label": "Promoter Holding",
                    "value_kind": "percentage",
                    "source_unit": "percent",
                    "source_label": "Prom Holding(%)",
                    "display_order": 2,
                },
            ],
            "peers": [
                {
                    "peer_key": "peer_tata_consultancy_services",
                    "legal_name": "Tata Consultancy Services Limited",
                    "provider_slug": "tata-consultancy-services",
                    "is_subject": True,
                    "display_order": 0,
                    "values": [
                        {
                            "metric_key": "latest_price",
                            "source_value": "₹3,100.50",
                            "availability_status": "available",
                        },
                        {
                            "metric_key": "pe",
                            "source_value": "24.1",
                            "availability_status": "available",
                        },
                        {
                            "metric_key": "promoter_holding",
                            "source_value": None,
                            "availability_status": "unknown",
                        },
                    ],
                },
                {
                    "peer_key": "peer_infosys",
                    "legal_name": "Infosys Limited",
                    "provider_slug": "infosys",
                    "is_subject": False,
                    "display_order": 1,
                    "values": [
                        {
                            "metric_key": "latest_price",
                            "source_value": "₹1,500",
                            "availability_status": "available",
                        },
                        {
                            "metric_key": "pe",
                            "source_value": "22x",
                            "availability_status": "available",
                        },
                        {
                            "metric_key": "promoter_holding",
                            "source_value": "0%",
                            "availability_status": "available",
                        },
                    ],
                },
            ],
            "extraction": {
                "metric_count": 3,
                "peer_count": 2,
                "cell_count": 6,
                "status": "complete",
            },
        }
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
        self.assertIsInstance(
            self.adapter,
            FundamentalStructuredDocumentGateway,
        )
        self.assertIsInstance(
            self.adapter,
            FundamentalPeerComparisonGateway,
        )
        self.assertIsInstance(
            self.adapter,
            FundamentalBenchmarkingFinancialsGateway,
        )
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
        for _ in range(18):
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
        self.assertEqual(
            self.transport.calls[-1][2]["issuer"],
            {
                "exchange": "NSE",
                "symbol": "TCS",
                "legal_name": "Tata Consultancy Services Limited",
                "isin": "INE467B01029",
                "provider_company_id": "tcs-1",
                "provider_slug": "tata-consultancy-services",
            },
        )

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

    def test_growth_table_document_crosses_adapter_boundary_separately(self):
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=growth_table_payload(),
        )
        request = FundamentalFinancialsRequest(
            request_id="request-growth-table-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
        )

        document = self.adapter.retrieve_growth_table_document(
            request=request,
            reporting_basis="consolidated",
        )

        self.assertEqual(document.document_type, "growth_table")
        self.assertEqual(document.reporting_basis, "not_applicable")
        self.assertEqual(document.rows[0].values[0].yoy_change, "8%")
        arguments = self.transport.calls[-1][2]
        self.assertEqual(arguments["document_type"], "growth_table")
        self.assertEqual(arguments["reporting_basis"], "consolidated")
        self.assertNotIn("statements", arguments)
        self.assertNotIn("period_types", arguments)
        self.assertNotIn("max_periods", arguments)

    def test_peer_comparison_returns_provider_neutral_document(self):
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=peer_comparison_payload(),
        )

        result = self.adapter.retrieve_peer_comparison(
            request=self.overview_request()
        )
        document = result.document

        self.assertEqual(result.status, FundamentalRetrievalStatus.COMPLETED)
        self.assertEqual(document.observation_date, date(2026, 8, 28))
        self.assertEqual(document.metrics[0].source_label, "Latest Price")
        self.assertEqual(document.metrics[0].currency, "INR")
        self.assertEqual(
            document.peers[0].cells[0].normalized_value,
            Decimal("3100.50"),
        )
        self.assertIsNone(document.peers[0].cells[2].normalized_value)
        self.assertEqual(
            document.peers[1].cells[2].normalized_value,
            Decimal("0"),
        )
        self.assertEqual(
            document.expires_at - document.retrieved_at,
            timedelta(days=10),
        )
        arguments = self.transport.calls[-1][2]
        self.assertEqual(self.transport.calls[-1][1], "get_company_overview")
        self.assertEqual(arguments["document_type"], "peer_comparison")
        self.assertNotIn("reporting_basis", arguments)

    def test_peer_comparison_rejects_identity_date_and_numeric_drift(self):
        for mutation in ("issuer", "date", "numeric"):
            with self.subTest(mutation=mutation):
                payload = peer_comparison_payload()
                if mutation == "issuer":
                    payload["document"]["issuer"]["symbol"] = "INFY"
                elif mutation == "date":
                    payload["document"]["observation_date"] = "2026-08-27"
                else:
                    payload["document"]["peers"][0]["values"][0][
                        "source_value"
                    ] = "secret-number"
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_company_overview",
                    status=TijoriToolStatus.SUCCESS,
                    payload=payload,
                )
                with self.assertRaises(FundamentalResponseValidationError):
                    self.adapter.retrieve_peer_comparison(
                        request=self.overview_request()
                    )

    def test_benchmarking_financials_returns_provider_neutral_document(self):
        payload = benchmarking_payload()
        payload["document"]["source"]["retrieved_at"] = NOW.isoformat()
        payload["document"]["observation_date"] = "2026-08-28"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_company_overview",
            status=TijoriToolStatus.SUCCESS,
            payload=payload,
        )
        issuer = self.issuer.model_copy(
            update={
                "symbol": "COFORGE",
                "legal_name": "Coforge Ltd.",
                "isin": None,
                "provider_company_id": "4502",
                "provider_slug": "niit-technologies-limited",
            }
        )

        result = self.adapter.retrieve_benchmarking_financials(
            request=self.overview_request(issuer=issuer)
        )
        document = result.document

        self.assertEqual(result.status, FundamentalRetrievalStatus.COMPLETED)
        self.assertEqual(
            document.schema_version,
            "jarvis.benchmarking_financials.v1",
        )
        self.assertEqual(document.companies[0].provider_slug, issuer.provider_slug)
        self.assertEqual(document.rows[0].section, "financials")
        self.assertEqual(document.rows[1].cells[0].source_value, "19.61 %")
        self.assertEqual(
            document.rows[1].cells[0].normalized_value,
            Decimal("19.61"),
        )
        self.assertTrue(document.rows[2].provider_hidden)
        self.assertIsNone(document.rows[2].cells[1].normalized_value)
        self.assertEqual(
            document.expires_at - document.retrieved_at,
            timedelta(days=10),
        )
        arguments = self.transport.calls[-1][2]
        self.assertEqual(self.transport.calls[-1][1], "get_company_overview")
        self.assertEqual(arguments["document_type"], "benchmarking_financials")
        self.assertNotIn("reporting_basis", arguments)

    def test_benchmarking_financials_rejects_request_and_numeric_drift(self):
        issuer = self.issuer.model_copy(
            update={
                "symbol": "COFORGE",
                "legal_name": "Coforge Ltd.",
                "isin": None,
                "provider_company_id": "4502",
                "provider_slug": "niit-technologies-limited",
            }
        )
        for mutation in ("issuer", "date", "numeric"):
            with self.subTest(mutation=mutation):
                payload = benchmarking_payload()
                payload["document"]["source"]["retrieved_at"] = NOW.isoformat()
                payload["document"]["observation_date"] = "2026-08-28"
                if mutation == "issuer":
                    payload["document"]["issuer"]["symbol"] = "INFY"
                elif mutation == "date":
                    payload["document"]["observation_date"] = "2026-08-27"
                else:
                    payload["document"]["rows"][2]["values"][0][
                        "source_value"
                    ] = "not-a-number"
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_company_overview",
                    status=TijoriToolStatus.SUCCESS,
                    payload=payload,
                )
                with self.assertRaises(FundamentalResponseValidationError):
                    self.adapter.retrieve_benchmarking_financials(
                        request=self.overview_request(issuer=issuer)
                    )

    def test_growth_table_returns_provider_neutral_structured_result(self):
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=growth_table_payload(),
        )
        request = FundamentalStructuredDocumentRequest(
            request_id="request-growth-table-neutral-1",
            operation_id="operation-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
        )

        result = self.adapter.retrieve_structured_financial_document(
            request=request,
        )

        self.assertEqual(result.status, FundamentalRetrievalStatus.COMPLETED)
        self.assertEqual(
            result.document.document_type,
            FinancialDocumentType.GROWTH_TABLE,
        )
        self.assertEqual(result.document.periods[0].source_label, "1yr")
        cell = result.document.rows[0].cells[0]
        self.assertEqual(cell.normalized_value, Decimal("12.4"))
        self.assertEqual(cell.yoy_change, "8%")
        self.assertEqual(
            result.document.expires_at - result.document.retrieved_at,
            timedelta(days=10),
        )
        arguments = self.transport.calls[-1][2]
        self.assertEqual(arguments["document_type"], "growth_table")
        self.assertEqual(arguments["reporting_basis"], "not_applicable")

    def test_neutral_growth_table_rejects_wrong_basis_and_bad_numeric_value(self):
        request = FundamentalStructuredDocumentRequest(
            request_id="request-growth-table-neutral-invalid-1",
            operation_id="operation-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.CONSOLIDATED,
        )
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=growth_table_payload(),
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_structured_financial_document(request=request)

        malformed = growth_table_payload()
        malformed["document"]["rows"][0]["values"][0]["source_value"] = "n/a"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=malformed,
        )
        valid_request = request.model_copy(
            update={
                "request_id": "request-growth-table-neutral-invalid-2",
                "reporting_basis": FinancialReportingBasis.NOT_APPLICABLE,
            }
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_structured_financial_document(
                request=valid_request,
            )

    def test_balance_sheet_returns_complete_provider_neutral_documents(self):
        for basis in (
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        ):
            with self.subTest(basis=basis):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=balance_sheet_payload(basis.value),
                )
                request = FundamentalStructuredDocumentRequest(
                    request_id=f"request-balance-sheet-{basis.value}",
                    operation_id="operation-1",
                    connection=self.connection,
                    requested_at=NOW,
                    issuer=self.issuer,
                    document_type=FinancialDocumentType.BALANCE_SHEET,
                    reporting_basis=basis,
                )

                result = self.adapter.retrieve_structured_financial_document(
                    request=request,
                )

                self.assertEqual(
                    result.status,
                    FundamentalRetrievalStatus.COMPLETED,
                )
                document = result.document
                self.assertEqual(
                    document.document_type,
                    FinancialDocumentType.BALANCE_SHEET,
                )
                self.assertEqual(document.reporting_basis, basis)
                self.assertEqual(document.currency, "INR")
                self.assertEqual(document.source_unit, "Rs. Cr.")
                self.assertEqual(
                    document.skipped_period_labels,
                    ("Mar 2023",),
                )
                self.assertEqual(document.periods[1].source_label, "Mar 2025")
                self.assertEqual(
                    document.rows[0].cells[0].normalized_value,
                    Decimal("1000"),
                )
                self.assertEqual(
                    document.rows[1].cells[0].normalized_value,
                    Decimal("0"),
                )
                self.assertIsNone(document.rows[1].cells[1].normalized_value)
                self.assertEqual(
                    document.expires_at - document.retrieved_at,
                    timedelta(days=10),
                )
                arguments = self.transport.calls[-1][2]
                self.assertEqual(arguments["document_type"], "balance_sheet")
                self.assertEqual(arguments["reporting_basis"], basis.value)

    def test_balance_sheet_rejects_wrong_basis_issuer_and_numeric_value(self):
        request = FundamentalStructuredDocumentRequest(
            request_id="request-balance-sheet-invalid-1",
            operation_id="operation-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            document_type=FinancialDocumentType.BALANCE_SHEET,
            reporting_basis=FinancialReportingBasis.CONSOLIDATED,
        )

        wrong_basis = balance_sheet_payload("standalone")
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=wrong_basis,
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_structured_financial_document(request=request)

        wrong_issuer = balance_sheet_payload()
        wrong_issuer["document"]["issuer"]["symbol"] = "INFY"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=wrong_issuer,
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_structured_financial_document(request=request)

        malformed = balance_sheet_payload()
        malformed["document"]["rows"][0]["values"][0][
            "source_value"
        ] = "not-a-number"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=malformed,
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_structured_financial_document(request=request)

    def test_cash_flow_returns_complete_provider_neutral_documents(self):
        for basis in (
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        ):
            with self.subTest(basis=basis):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=cash_flow_payload(basis.value),
                )
                request = FundamentalStructuredDocumentRequest(
                    request_id=f"request-cash-flow-{basis.value}",
                    operation_id="operation-1",
                    connection=self.connection,
                    requested_at=NOW,
                    issuer=self.issuer,
                    document_type=FinancialDocumentType.CASH_FLOW,
                    reporting_basis=basis,
                )

                result = self.adapter.retrieve_structured_financial_document(
                    request=request,
                )

                document = result.document
                self.assertEqual(
                    document.document_type,
                    FinancialDocumentType.CASH_FLOW,
                )
                self.assertEqual(document.reporting_basis, basis)
                self.assertEqual(document.currency, "INR")
                self.assertEqual(document.source_unit, "Rs. Cr.")
                self.assertEqual(
                    document.rows[1].cells[0].normalized_value,
                    Decimal("-12"),
                )
                self.assertEqual(
                    document.rows[2].cells[0].normalized_value,
                    Decimal("0"),
                )
                self.assertIsNone(document.rows[2].cells[1].normalized_value)
                self.assertEqual(
                    document.expires_at - document.retrieved_at,
                    timedelta(days=10),
                )
                arguments = self.transport.calls[-1][2]
                self.assertEqual(arguments["document_type"], "cash_flow")
                self.assertEqual(arguments["reporting_basis"], basis.value)

    def test_cash_flow_rejects_basis_and_numeric_drift(self):
        request = FundamentalStructuredDocumentRequest(
            request_id="request-cash-flow-invalid-1",
            operation_id="operation-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            document_type=FinancialDocumentType.CASH_FLOW,
            reporting_basis=FinancialReportingBasis.CONSOLIDATED,
        )
        candidates = [cash_flow_payload("standalone")]
        malformed = cash_flow_payload()
        malformed["document"]["rows"][0]["values"][0][
            "source_value"
        ] = "not-a-number"
        candidates.append(malformed)

        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=candidate,
                )
                with self.assertRaises(FundamentalResponseValidationError):
                    self.adapter.retrieve_structured_financial_document(
                        request=request,
                    )

    def test_profit_and_loss_returns_unit_aware_neutral_documents(self):
        for basis in (
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        ):
            with self.subTest(basis=basis):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=profit_and_loss_payload(basis.value),
                )
                request = FundamentalStructuredDocumentRequest(
                    request_id=f"request-profit-loss-{basis.value}",
                    operation_id="operation-1",
                    connection=self.connection,
                    requested_at=NOW,
                    issuer=self.issuer,
                    document_type=FinancialDocumentType.PROFIT_AND_LOSS,
                    reporting_basis=basis,
                )

                result = self.adapter.retrieve_structured_financial_document(
                    request=request,
                )

                document = result.document
                self.assertEqual(
                    document.document_type,
                    FinancialDocumentType.PROFIT_AND_LOSS,
                )
                self.assertEqual(document.reporting_basis, basis)
                self.assertEqual(document.source_unit, "mixed")
                self.assertEqual(document.currency, "INR")
                self.assertEqual(
                    document.rows[0].value_kind,
                    FundamentalValueKind.MONETARY,
                )
                self.assertEqual(
                    document.rows[1].value_kind,
                    FundamentalValueKind.PERCENTAGE,
                )
                self.assertEqual(
                    document.rows[1].cells[0].normalized_unit,
                    "percent",
                )
                self.assertEqual(
                    document.rows[2].value_kind,
                    FundamentalValueKind.COUNT,
                )
                self.assertEqual(
                    document.rows[2].cells[0].normalized_value,
                    Decimal("10"),
                )
                self.assertIsNone(document.rows[2].cells[1].normalized_value)
                arguments = self.transport.calls[-1][2]
                self.assertEqual(arguments["document_type"], "profit_and_loss")
                self.assertEqual(arguments["reporting_basis"], basis.value)

    def test_profit_and_loss_rejects_basis_units_and_numeric_drift(self):
        request = FundamentalStructuredDocumentRequest(
            request_id="request-profit-loss-invalid-1",
            operation_id="operation-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            document_type=FinancialDocumentType.PROFIT_AND_LOSS,
            reporting_basis=FinancialReportingBasis.CONSOLIDATED,
        )
        candidates = []

        wrong_basis = profit_and_loss_payload("standalone")
        candidates.append(wrong_basis)

        wrong_units = profit_and_loss_payload()
        wrong_units["document"]["rows"][1]["normalized_unit"] = "INR crore"
        candidates.append(wrong_units)

        malformed_value = profit_and_loss_payload()
        malformed_value["document"]["rows"][0]["values"][0][
            "source_value"
        ] = "not-a-number"
        candidates.append(malformed_value)

        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=candidate,
                )
                with self.assertRaises(FundamentalResponseValidationError):
                    self.adapter.retrieve_structured_financial_document(
                        request=request,
                    )

    def test_ratios_returns_unit_aware_neutral_documents(self):
        for basis in (
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        ):
            with self.subTest(basis=basis):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=ratios_payload(basis.value),
                )
                request = FundamentalStructuredDocumentRequest(
                    request_id=f"request-ratios-{basis.value}",
                    operation_id="operation-1",
                    connection=self.connection,
                    requested_at=NOW,
                    issuer=self.issuer,
                    document_type=FinancialDocumentType.RATIOS,
                    reporting_basis=basis,
                )

                result = self.adapter.retrieve_structured_financial_document(
                    request=request,
                )

                document = result.document
                self.assertEqual(
                    document.document_type,
                    FinancialDocumentType.RATIOS,
                )
                self.assertEqual(document.reporting_basis, basis)
                self.assertEqual(document.source_unit, "mixed")
                self.assertEqual(
                    document.rows[0].value_kind,
                    FundamentalValueKind.MONETARY,
                )
                self.assertEqual(
                    document.rows[1].value_kind,
                    FundamentalValueKind.PERCENTAGE,
                )
                self.assertEqual(
                    document.rows[2].value_kind,
                    FundamentalValueKind.PER_SHARE,
                )
                self.assertEqual(
                    document.rows[3].value_kind,
                    FundamentalValueKind.RATIO,
                )
                self.assertEqual(
                    document.rows[4].cells[0].normalized_unit,
                    "days",
                )
                self.assertEqual(
                    document.rows[4].cells[0].normalized_value,
                    Decimal("-30"),
                )
                self.assertEqual(
                    document.rows[5].value_kind,
                    FundamentalValueKind.OTHER,
                )
                arguments = self.transport.calls[-1][2]
                self.assertEqual(arguments["document_type"], "ratios")
                self.assertEqual(arguments["reporting_basis"], basis.value)

    def test_ratios_rejects_basis_units_and_numeric_drift(self):
        request = FundamentalStructuredDocumentRequest(
            request_id="request-ratios-invalid-1",
            operation_id="operation-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            document_type=FinancialDocumentType.RATIOS,
            reporting_basis=FinancialReportingBasis.CONSOLIDATED,
        )
        candidates = [ratios_payload("standalone")]
        wrong_units = ratios_payload()
        wrong_units["document"]["rows"][3]["normalized_unit"] = "percent"
        candidates.append(wrong_units)
        malformed = ratios_payload()
        malformed["document"]["rows"][3]["values"][0][
            "source_value"
        ] = "not-a-number"
        candidates.append(malformed)

        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=candidate,
                )
                with self.assertRaises(FundamentalResponseValidationError):
                    self.adapter.retrieve_structured_financial_document(
                        request=request,
                    )

    def test_quarterly_results_returns_unit_aware_neutral_documents(self):
        for basis in (
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        ):
            with self.subTest(basis=basis):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=quarterly_results_payload(basis.value),
                )
                request = FundamentalStructuredDocumentRequest(
                    request_id=f"request-quarterly-results-{basis.value}",
                    operation_id="operation-1",
                    connection=self.connection,
                    requested_at=NOW,
                    issuer=self.issuer,
                    document_type=FinancialDocumentType.QUARTERLY_RESULTS,
                    reporting_basis=basis,
                )

                result = self.adapter.retrieve_structured_financial_document(
                    request=request,
                )

                document = result.document
                self.assertEqual(
                    document.document_type,
                    FinancialDocumentType.QUARTERLY_RESULTS,
                )
                self.assertEqual(document.reporting_basis, basis)
                self.assertEqual(document.source_unit, "mixed")
                self.assertEqual(
                    document.rows[0].value_kind,
                    FundamentalValueKind.MONETARY,
                )
                self.assertEqual(
                    document.rows[1].value_kind,
                    FundamentalValueKind.OTHER,
                )
                self.assertEqual(
                    document.rows[2].value_kind,
                    FundamentalValueKind.PER_SHARE,
                )
                self.assertEqual(
                    document.rows[3].value_kind,
                    FundamentalValueKind.PERCENTAGE,
                )
                self.assertEqual(
                    document.rows[0].cells[1].normalized_value,
                    Decimal("1200.50"),
                )
                self.assertEqual(
                    document.rows[3].cells[1].normalized_unit,
                    "percent",
                )
                self.assertEqual(
                    document.expires_at,
                    NOW + timedelta(days=10),
                )
                arguments = self.transport.calls[-1][2]
                self.assertEqual(
                    arguments["document_type"],
                    "quarterly_results",
                )
                self.assertEqual(arguments["reporting_basis"], basis.value)

    def test_quarterly_results_rejects_basis_units_and_numeric_drift(self):
        request = FundamentalStructuredDocumentRequest(
            request_id="request-quarterly-results-invalid-1",
            operation_id="operation-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
            document_type=FinancialDocumentType.QUARTERLY_RESULTS,
            reporting_basis=FinancialReportingBasis.CONSOLIDATED,
        )
        candidates = [quarterly_results_payload("standalone")]
        wrong_units = quarterly_results_payload()
        wrong_units["document"]["rows"][2]["normalized_unit"] = "percent"
        candidates.append(wrong_units)
        malformed = quarterly_results_payload()
        malformed["document"]["rows"][3]["values"][0][
            "source_value"
        ] = "not-a-number"
        candidates.append(malformed)

        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                self.transport.result = TijoriMcpToolResult(
                    tool_name="get_financials",
                    status=TijoriToolStatus.SUCCESS,
                    payload=candidate,
                )
                with self.assertRaises(FundamentalResponseValidationError):
                    self.adapter.retrieve_structured_financial_document(
                        request=request,
                    )

    def test_growth_table_document_rejects_cross_issuer_and_schema_drift(self):
        request = FundamentalFinancialsRequest(
            request_id="request-growth-table-invalid-1",
            connection=self.connection,
            requested_at=NOW,
            issuer=self.issuer,
        )
        wrong_issuer = growth_table_payload()
        wrong_issuer["document"]["issuer"]["symbol"] = "INFY"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=wrong_issuer,
        )
        with self.assertRaises(FundamentalResponseValidationError):
            self.adapter.retrieve_growth_table_document(
                request=request,
                reporting_basis="consolidated",
            )

        drift = growth_table_payload()
        drift["document"]["rows"][0]["unexpected"] = "provider-drift"
        self.transport.result = TijoriMcpToolResult(
            tool_name="get_financials",
            status=TijoriToolStatus.SUCCESS,
            payload=drift,
        )
        with self.assertRaises(FundamentalResponseValidationError) as caught:
            self.adapter.retrieve_growth_table_document(
                request=request,
                reporting_basis="standalone",
            )
        self.assertNotIn("provider-drift", str(caught.exception))

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
