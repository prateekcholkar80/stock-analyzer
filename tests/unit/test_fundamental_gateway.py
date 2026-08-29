from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalCapability,
    FundamentalCapabilityDescriptor,
    FundamentalCapabilityManifest,
    FundamentalCapabilityStatus,
    FundamentalCompanyOverviewRequest,
    FundamentalCompanySearchRequest,
    FundamentalCompanySearchResult,
    FundamentalEvidenceGateway,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialsRequest,
    FundamentalIssuerCandidate,
    FundamentalIssuerLocator,
    FundamentalIssuerMatchKind,
    FundamentalIssuerResolutionRequest,
    FundamentalIssuerResolutionResult,
    FundamentalResolutionStatus,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
    validate_fundamental_response_binding,
)
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalConflictStatus,
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
REQUESTED_AT = datetime(2026, 8, 28, 14, 0, tzinfo=IST)
COMPLETED_AT = datetime(2026, 8, 28, 14, 0, 2, tzinfo=IST)
SOURCE_HASH = "a" * 64
ADAPTER_HASH = "b" * 64
RECORD_HASH = "c" * 64


def build_connection(**overrides) -> ProviderConnectionScope:
    values = {
        "tenant_id": "tenant.prateek",
        "provider_connection_id": "provider.tijori.prateek",
        "provider": "tijori",
        "account_reference_hash": "d" * 64,
        "subscription_tier": ProviderSubscriptionTier.PAID,
        "entitlement_status": ProviderEntitlementStatus.VERIFIED,
        "capabilities": (
            "company_search",
            "financial_statements",
            "shareholding_history",
        ),
        "entitlement_checked_at": REQUESTED_AT - timedelta(minutes=1),
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
        "source_id": "SRC-TCS-FILING-2026",
        "source_name": "TCS FY2026 annual report",
        "source_type": FundamentalSourceType.ANNUAL_REPORT,
        "source_rank": FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
        "owner_or_provider": "Tata Consultancy Services Limited",
        "location": "https://www.tcs.com/annual-report-2026",
        "period_covered": "FY2026",
        "as_of_date": date(2026, 3, 31),
        "published_at": datetime(2026, 4, 15, 18, 0, tzinfo=IST),
        "retrieved_at": REQUESTED_AT,
        "freshness_status": FundamentalFreshnessStatus.CURRENT,
        "content_fingerprint": SOURCE_HASH,
        "parser_name": "jarvis.primary.financials",
        "parser_version": "1.0.0",
        "provider_schema_version": "issuer.report.v1",
        "validation_status": FundamentalValidationStatus.VALIDATED,
    }
    values.update(overrides)
    return FundamentalEvidenceSource(**values)


def build_fact(
    statement: FundamentalStatement = FundamentalStatement.INCOME_STATEMENT,
    *,
    source: FundamentalEvidenceSource | None = None,
    **overrides,
) -> NormalizedFundamentalFact:
    source = source or build_source()
    values = {
        "evidence_id": f"fundamental:tcs.{statement.value}.fy2026",
        "statement": statement,
        "line_item_original": "Reported value",
        "line_item_standard": "Normalized value",
        "line_item_id": f"{statement.value}.reported_value",
        "period": FundamentalReportingPeriod(
            label="FY2026",
            period_type=FundamentalPeriodType.ANNUAL,
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        ),
        "value_kind": FundamentalValueKind.RATIO,
        "source_value": "42.5",
        "normalized_value": Decimal("42.5"),
        "source_unit": "ratio",
        "normalized_unit": "ratio",
        "normalization_method": FundamentalNormalizationMethod.AS_REPORTED,
        "source_location": "FY2026 report / reported value",
        "evidence_label": FundamentalEvidenceLabel.FACT_SOURCE_REPORTED,
        "confidence": FundamentalEvidenceConfidence.HIGH,
        "availability_status": FundamentalAvailabilityStatus.AVAILABLE,
        "freshness_status": FundamentalFreshnessStatus.CURRENT,
        "validation_status": FundamentalValidationStatus.VALIDATED,
        "conflict_status": FundamentalConflictStatus.NONE,
        "lineage": FundamentalEvidenceLineage(
            source_references=(
                FundamentalSourceReference(
                    source_id=source.source_id,
                    content_fingerprint=source.content_fingerprint,
                ),
            ),
            transformation_id="jarvis.normalize.primary_fact",
            transformation_version="1.0.0",
        ),
    }
    values.update(overrides)
    return NormalizedFundamentalFact(**values)


def build_snapshot(
    statement: FundamentalStatement = FundamentalStatement.INCOME_STATEMENT,
    *,
    connection: ProviderConnectionScope | None = None,
    issuer: FundamentalIssuerIdentity | None = None,
    assembled_at: datetime = REQUESTED_AT + timedelta(seconds=1),
    validation_status: FundamentalValidationStatus = (
        FundamentalValidationStatus.VALIDATED
    ),
) -> FundamentalEvidenceSnapshot:
    source = build_source()
    return FundamentalEvidenceSnapshot(
        snapshot_id=f"snapshot.tcs.{statement.value}.2026-08-28",
        connection=connection or build_connection(),
        issuer=issuer or build_issuer(),
        sources=(source,),
        facts=(build_fact(statement, source=source),),
        evidence_posture=FundamentalEvidencePosture.RESEARCH_GRADE,
        validation_status=validation_status,
        assembled_at=assembled_at,
    )


def build_candidate(**overrides) -> FundamentalIssuerCandidate:
    values = {
        "issuer": build_issuer(),
        "match_kind": FundamentalIssuerMatchKind.EXACT_SYMBOL,
        "match_score": Decimal("1"),
        "matched_on": ("exchange", "symbol"),
        "provider_record_fingerprint": RECORD_HASH,
    }
    values.update(overrides)
    return FundamentalIssuerCandidate(**values)


def response_values(request, **overrides):
    values = {
        "request_id": request.request_id,
        "request_fingerprint": request.request_fingerprint,
        "connection": request.connection,
        "requested_at": request.requested_at,
        "started_at": request.requested_at,
        "completed_at": COMPLETED_AT,
        "provider_contract_version": "tijori.contract.v1",
        "adapter_fingerprint": ADAPTER_HASH,
    }
    values.update(overrides)
    return values


class FundamentalCapabilityContractTests(unittest.TestCase):
    def test_builds_read_only_capability_manifest(self):
        descriptor = FundamentalCapabilityDescriptor(
            capability=FundamentalCapability.FINANCIAL_STATEMENTS,
            status=FundamentalCapabilityStatus.AVAILABLE,
            supports_history=True,
            max_periods=12,
        )
        manifest = FundamentalCapabilityManifest(
            connection=build_connection(),
            capabilities=(descriptor,),
            checked_at=REQUESTED_AT,
            provider_contract_version="tijori.contract.v1",
            adapter_fingerprint=ADAPTER_HASH,
        )

        self.assertTrue(descriptor.read_only)
        self.assertTrue(descriptor.idempotent)
        self.assertRegex(manifest.manifest_fingerprint, r"^[a-f0-9]{64}$")

    def test_manifest_rejects_duplicate_capability(self):
        descriptor = FundamentalCapabilityDescriptor(
            capability=FundamentalCapability.COMPANY_SEARCH,
            status=FundamentalCapabilityStatus.AVAILABLE,
            supports_history=False,
        )
        with self.assertRaises(ValidationError):
            FundamentalCapabilityManifest(
                connection=build_connection(),
                capabilities=(descriptor, descriptor),
                checked_at=REQUESTED_AT,
                provider_contract_version="tijori.contract.v1",
                adapter_fingerprint=ADAPTER_HASH,
            )

    def test_manifest_cannot_predate_entitlement_check(self):
        descriptor = FundamentalCapabilityDescriptor(
            capability=FundamentalCapability.COMPANY_SEARCH,
            status=FundamentalCapabilityStatus.UNKNOWN,
            supports_history=False,
        )
        with self.assertRaises(ValidationError):
            FundamentalCapabilityManifest(
                connection=build_connection(),
                capabilities=(descriptor,),
                checked_at=REQUESTED_AT - timedelta(minutes=2),
                provider_contract_version="tijori.contract.v1",
                adapter_fingerprint=ADAPTER_HASH,
            )

    def test_non_historical_capability_rejects_period_limit(self):
        with self.assertRaises(ValidationError):
            FundamentalCapabilityDescriptor(
                capability=FundamentalCapability.COMPANY_SEARCH,
                status=FundamentalCapabilityStatus.AVAILABLE,
                supports_history=False,
                max_periods=10,
            )

    def test_manifest_requires_timezone_aware_check_time(self):
        descriptor = FundamentalCapabilityDescriptor(
            capability=FundamentalCapability.COMPANY_SEARCH,
            status=FundamentalCapabilityStatus.AVAILABLE,
            supports_history=False,
        )
        with self.assertRaises(ValidationError):
            FundamentalCapabilityManifest(
                connection=build_connection(),
                capabilities=(descriptor,),
                checked_at=datetime(2026, 8, 28, 14, 0),
                provider_contract_version="tijori.contract.v1",
                adapter_fingerprint=ADAPTER_HASH,
            )


class FundamentalRequestContractTests(unittest.TestCase):
    def test_search_request_is_tenant_scoped_normalized_and_fingerprinted(self):
        request = FundamentalCompanySearchRequest(
            request_id="request.search.tcs",
            operation_id="operation.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            query="  Tata   Consultancy  ",
            exchanges=("nse",),
            max_results=5,
        )

        self.assertEqual(request.query, "Tata Consultancy")
        self.assertEqual(request.exchanges, ("NSE",))
        self.assertEqual(request.connection.tenant_id, "tenant.prateek")
        self.assertRegex(request.request_fingerprint, r"^[a-f0-9]{64}$")

    def test_search_request_rejects_blank_oversized_or_duplicate_filters(self):
        base = {
            "request_id": "request.search.tcs",
            "connection": build_connection(),
            "requested_at": REQUESTED_AT,
        }
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchRequest(**base, query="   ")
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchRequest(**base, query="x" * 201)
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchRequest(
                **base,
                query="TCS",
                exchanges=("NSE", "nse"),
            )

    def test_request_rejects_naive_timestamp_and_is_immutable(self):
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchRequest(
                request_id="request.search.tcs",
                connection=build_connection(),
                requested_at=datetime(2026, 8, 28, 14, 0),
                query="TCS",
            )

        request = FundamentalCompanySearchRequest(
            request_id="request.search.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            query="TCS",
        )
        with self.assertRaises(ValidationError):
            request.query = "Reliance"

    def test_request_contract_rejects_secret_fields_without_echoing_value(self):
        provider_secret = "password-must-not-cross-gateway"

        with self.assertRaises(ValidationError) as captured:
            FundamentalCompanySearchRequest(
                request_id="request.search.tcs",
                connection=build_connection(),
                requested_at=REQUESTED_AT,
                query="TCS",
                password=provider_secret,
            )

        self.assertNotIn(provider_secret, str(captured.exception))

    def test_locator_requires_real_hint_and_exchange_requires_symbol(self):
        with self.assertRaises(ValidationError):
            FundamentalIssuerLocator()
        with self.assertRaises(ValidationError):
            FundamentalIssuerLocator(exchange="NSE", legal_name="TCS")

    def test_locator_normalizes_market_identity(self):
        locator = FundamentalIssuerLocator(
            exchange="nse",
            symbol="tcs-eq",
            isin="ine467b01029",
        )

        self.assertEqual(locator.exchange, "NSE")
        self.assertEqual(locator.symbol, "TCS-EQ")
        self.assertEqual(locator.isin, "INE467B01029")

    def test_resolution_request_has_fixed_capability(self):
        request = FundamentalIssuerResolutionRequest(
            request_id="request.resolve.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            locator=FundamentalIssuerLocator(symbol="TCS-EQ"),
        )

        self.assertEqual(
            request.capability,
            FundamentalCapability.ISSUER_RESOLUTION,
        )

    def test_issuer_requests_reject_future_as_of_date(self):
        with self.assertRaises(ValidationError):
            FundamentalCompanyOverviewRequest(
                request_id="request.overview.tcs",
                connection=build_connection(),
                requested_at=REQUESTED_AT,
                issuer=build_issuer(),
                as_of_date=date(2026, 8, 29),
            )

    def test_financial_request_accepts_only_unique_financial_statements(self):
        base = {
            "request_id": "request.financials.tcs",
            "connection": build_connection(),
            "requested_at": REQUESTED_AT,
            "issuer": build_issuer(),
        }
        request = FundamentalFinancialsRequest(
            **base,
            statements=(
                FundamentalStatement.INCOME_STATEMENT,
                FundamentalStatement.BALANCE_SHEET,
            ),
            period_types=(FundamentalPeriodType.ANNUAL,),
        )
        self.assertEqual(request.max_periods, 12)

        with self.assertRaises(ValidationError):
            FundamentalFinancialsRequest(
                **base,
                statements=(FundamentalStatement.OWNERSHIP,),
            )
        with self.assertRaises(ValidationError):
            FundamentalFinancialsRequest(
                **base,
                statements=(
                    FundamentalStatement.CASH_FLOW,
                    FundamentalStatement.CASH_FLOW,
                ),
            )

    def test_financial_request_rejects_duplicate_or_empty_period_types(self):
        base = {
            "request_id": "request.financials.tcs",
            "connection": build_connection(),
            "requested_at": REQUESTED_AT,
            "issuer": build_issuer(),
        }
        with self.assertRaises(ValidationError):
            FundamentalFinancialsRequest(**base, period_types=())
        with self.assertRaises(ValidationError):
            FundamentalFinancialsRequest(
                **base,
                period_types=(
                    FundamentalPeriodType.ANNUAL,
                    FundamentalPeriodType.ANNUAL,
                ),
            )
        with self.assertRaises(ValidationError):
            FundamentalFinancialsRequest(
                **base,
                period_types=(FundamentalPeriodType.FORECAST,),
            )

    def test_shareholding_request_is_bounded(self):
        base = {
            "request_id": "request.shareholding.tcs",
            "connection": build_connection(),
            "requested_at": REQUESTED_AT,
            "issuer": build_issuer(),
        }
        request = FundamentalShareholdingRequest(**base, quarters=8)
        self.assertTrue(request.include_promoter_pledge)

        with self.assertRaises(ValidationError):
            FundamentalShareholdingRequest(**base, quarters=0)
        with self.assertRaises(ValidationError):
            FundamentalShareholdingRequest(**base, quarters=41)


class FundamentalIdentityResultTests(unittest.TestCase):
    def setUp(self):
        self.search_request = FundamentalCompanySearchRequest(
            request_id="request.search.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            query="TCS",
        )
        self.resolve_request = FundamentalIssuerResolutionRequest(
            request_id="request.resolve.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            locator=FundamentalIssuerLocator(symbol="TCS-EQ"),
        )

    def test_candidate_requires_strict_finite_bounded_decimal_score(self):
        self.assertEqual(build_candidate().match_score, Decimal("1"))

        with self.assertRaises(ValidationError):
            build_candidate(match_score=1.0)
        with self.assertRaises(ValidationError):
            build_candidate(match_score=Decimal("NaN"))
        with self.assertRaises(ValidationError):
            build_candidate(match_score=Decimal("1.01"))

    def test_search_result_allows_empty_result_and_is_fingerprinted(self):
        result = FundamentalCompanySearchResult(
            **response_values(self.search_request),
            candidates=(),
        )

        self.assertEqual(result.candidates, ())
        self.assertRegex(result.result_fingerprint, r"^[a-f0-9]{64}$")

    def test_result_contract_rejects_raw_provider_payload(self):
        provider_secret = "session-cookie-must-not-cross-gateway"

        with self.assertRaises(ValidationError) as captured:
            FundamentalCompanySearchResult(
                **response_values(self.search_request),
                candidates=(),
                raw_payload={"cookie": provider_secret},
            )

        self.assertNotIn(provider_secret, str(captured.exception))

    def test_search_result_rejects_duplicate_identity(self):
        candidate = build_candidate()
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchResult(
                **response_values(self.search_request),
                candidates=(candidate, candidate),
            )

    def test_response_rejects_naive_or_reversed_timestamps(self):
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchResult(
                **response_values(
                    self.search_request,
                    completed_at=datetime(2026, 8, 28, 14, 1),
                ),
                candidates=(),
            )
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchResult(
                **response_values(
                    self.search_request,
                    requested_at=REQUESTED_AT + timedelta(seconds=1),
                ),
                candidates=(),
            )

    def test_response_binding_rejects_cross_request_or_scope_data(self):
        result = FundamentalCompanySearchResult(
            **response_values(self.search_request),
            candidates=(),
        )
        validate_fundamental_response_binding(self.search_request, result)

        another_request = FundamentalCompanySearchRequest(
            request_id="request.search.other",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            query="TCS",
        )
        with self.assertRaises(ValueError):
            validate_fundamental_response_binding(another_request, result)
        with self.assertRaises(ValidationError):
            FundamentalCompanySearchResult(
                **response_values(
                    self.search_request,
                    started_at=COMPLETED_AT + timedelta(seconds=1),
                ),
                candidates=(),
            )

    def test_resolved_result_requires_selected_issuer(self):
        with self.assertRaises(ValidationError):
            FundamentalIssuerResolutionResult(
                **response_values(self.resolve_request),
                status=FundamentalResolutionStatus.RESOLVED,
                issuer=None,
                candidates=(),
            )

    def test_resolved_result_must_select_returned_candidate(self):
        with self.assertRaises(ValidationError):
            FundamentalIssuerResolutionResult(
                **response_values(self.resolve_request),
                status=FundamentalResolutionStatus.RESOLVED,
                issuer=build_issuer(symbol="RELIANCE-EQ", isin=None),
                candidates=(build_candidate(),),
            )

    def test_ambiguous_result_requires_two_candidates_and_explanation(self):
        second = build_candidate(
            issuer=build_issuer(
                symbol="TCS-BE",
                isin=None,
                provider_company_id="1365",
                provider_slug="tata-consultancy-services-be",
            ),
            match_score=Decimal("0.8"),
        )
        result = FundamentalIssuerResolutionResult(
            **response_values(self.resolve_request),
            status=FundamentalResolutionStatus.AMBIGUOUS,
            candidates=(build_candidate(), second),
            limitation="More than one listing matched the supplied name.",
        )
        self.assertIsNone(result.issuer)

        with self.assertRaises(ValidationError):
            FundamentalIssuerResolutionResult(
                **response_values(self.resolve_request),
                status=FundamentalResolutionStatus.AMBIGUOUS,
                candidates=(build_candidate(),),
                limitation="Ambiguous.",
            )

    def test_not_found_result_is_empty_and_explained(self):
        result = FundamentalIssuerResolutionResult(
            **response_values(self.resolve_request),
            status=FundamentalResolutionStatus.NOT_FOUND,
            candidates=(),
            limitation="No provider record matched the supplied identity.",
        )

        self.assertIsNone(result.issuer)
        self.assertRegex(result.result_fingerprint, r"^[a-f0-9]{64}$")


class FundamentalEvidenceRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.request = FundamentalFinancialsRequest(
            request_id="request.financials.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
        )

    def build_result(self, **overrides) -> FundamentalEvidenceRetrieval:
        values = response_values(
            self.request,
            capability=FundamentalCapability.FINANCIAL_STATEMENTS,
            issuer=build_issuer(),
            status=FundamentalRetrievalStatus.COMPLETED,
            snapshot=build_snapshot(),
        )
        values.update(overrides)
        return FundamentalEvidenceRetrieval(**values)

    def test_completed_retrieval_releases_validated_snapshot(self):
        result = self.build_result()

        self.assertEqual(result.snapshot.connection.tenant_id, "tenant.prateek")
        self.assertRegex(result.result_fingerprint, r"^[a-f0-9]{64}$")

    def test_completed_retrieval_requires_snapshot(self):
        with self.assertRaises(ValidationError):
            self.build_result(snapshot=None)

    def test_failed_retrieval_cannot_release_snapshot(self):
        with self.assertRaises(ValidationError):
            self.build_result(
                status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                limitations=("Provider did not complete the request.",),
            )

    def test_failed_retrieval_requires_safe_explanation(self):
        result = self.build_result(
            status=FundamentalRetrievalStatus.NOT_ENTITLED,
            snapshot=None,
            limitations=("The user's plan does not expose this dataset.",),
        )
        self.assertIsNone(result.snapshot)

        with self.assertRaises(ValidationError):
            self.build_result(
                status=FundamentalRetrievalStatus.PAYWALLED,
                snapshot=None,
                limitations=(),
            )

    def test_partial_retrieval_requires_snapshot_and_limitations(self):
        partial_snapshot = build_snapshot(
            validation_status=FundamentalValidationStatus.PARTIAL
        )
        result = self.build_result(
            status=FundamentalRetrievalStatus.PARTIAL,
            snapshot=partial_snapshot,
            limitations=("Cash-flow periods were incomplete.",),
        )
        self.assertEqual(result.status, FundamentalRetrievalStatus.PARTIAL)

        with self.assertRaises(ValidationError):
            self.build_result(
                status=FundamentalRetrievalStatus.PARTIAL,
                limitations=(),
            )
        with self.assertRaises(ValidationError):
            self.build_result(
                status=FundamentalRetrievalStatus.PARTIAL,
                snapshot=build_snapshot(
                    validation_status=FundamentalValidationStatus.REJECTED
                ),
                limitations=("Provider response failed validation.",),
            )

    def test_completed_retrieval_requires_validated_snapshot(self):
        with self.assertRaises(ValidationError):
            self.build_result(
                snapshot=build_snapshot(
                    validation_status=FundamentalValidationStatus.PARTIAL
                )
            )

    def test_retrieval_rejects_connection_scope_mismatch(self):
        other_connection = build_connection(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
        )
        with self.assertRaises(ValidationError):
            self.build_result(
                snapshot=build_snapshot(connection=other_connection)
            )

    def test_retrieval_rejects_issuer_mismatch(self):
        other_issuer = build_issuer(symbol="INFY-EQ", isin=None)
        with self.assertRaises(ValidationError):
            self.build_result(snapshot=build_snapshot(issuer=other_issuer))

    def test_retrieval_rejects_completion_before_snapshot_assembly(self):
        with self.assertRaises(ValidationError):
            self.build_result(
                snapshot=build_snapshot(
                    assembled_at=COMPLETED_AT + timedelta(seconds=1)
                )
            )

    def test_retrieval_rejects_non_evidence_capability(self):
        with self.assertRaises(ValidationError):
            self.build_result(capability=FundamentalCapability.COMPANY_SEARCH)

    def test_financial_retrieval_rejects_shareholding_facts(self):
        with self.assertRaises(ValidationError):
            self.build_result(
                snapshot=build_snapshot(FundamentalStatement.OWNERSHIP)
            )

    def test_overview_and_shareholding_accept_only_their_statement_scopes(self):
        overview = self.build_result(
            capability=FundamentalCapability.COMPANY_OVERVIEW,
            snapshot=build_snapshot(FundamentalStatement.VALUATION),
        )
        self.assertEqual(
            overview.capability,
            FundamentalCapability.COMPANY_OVERVIEW,
        )

        shareholding = self.build_result(
            capability=FundamentalCapability.SHAREHOLDING_HISTORY,
            snapshot=build_snapshot(FundamentalStatement.OWNERSHIP),
        )
        self.assertEqual(
            shareholding.capability,
            FundamentalCapability.SHAREHOLDING_HISTORY,
        )

        with self.assertRaises(ValidationError):
            self.build_result(
                capability=FundamentalCapability.COMPANY_OVERVIEW,
                snapshot=build_snapshot(FundamentalStatement.CASH_FLOW),
            )

    def test_result_is_frozen_and_rejects_duplicate_limitations(self):
        result = self.build_result()
        with self.assertRaises(ValidationError):
            result.status = FundamentalRetrievalStatus.PARTIAL

        with self.assertRaises(ValidationError):
            self.build_result(
                status=FundamentalRetrievalStatus.PARTIAL,
                limitations=("Incomplete.", "incomplete."),
            )


class FakeFundamentalGateway:
    def __init__(self):
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return ADAPTER_HASH

    def inspect_capabilities(self, *, connection):
        self.calls.append(("inspect_capabilities", connection))

    def search_companies(self, *, request):
        self.calls.append(("search_companies", request))

    def resolve_issuer(self, *, request):
        self.calls.append(("resolve_issuer", request))

    def retrieve_company_overview(self, *, request):
        self.calls.append(("retrieve_company_overview", request))

    def retrieve_financials(self, *, request):
        self.calls.append(("retrieve_financials", request))

    def retrieve_shareholding(self, *, request):
        self.calls.append(("retrieve_shareholding", request))


class IncompleteFundamentalGateway:
    def search_companies(self, *, request):
        return request


class FundamentalEvidenceGatewayProtocolTests(unittest.TestCase):
    def test_complete_fake_satisfies_runtime_protocol(self):
        gateway = FakeFundamentalGateway()

        self.assertIsInstance(gateway, FundamentalEvidenceGateway)

    def test_incomplete_object_does_not_satisfy_runtime_protocol(self):
        self.assertNotIsInstance(
            IncompleteFundamentalGateway(),
            FundamentalEvidenceGateway,
        )

    def test_protocol_exposes_only_the_five_read_capabilities(self):
        expected_methods = {
            "inspect_capabilities",
            "search_companies",
            "resolve_issuer",
            "retrieve_company_overview",
            "retrieve_financials",
            "retrieve_shareholding",
        }

        self.assertTrue(
            expected_methods.issubset(set(dir(FundamentalEvidenceGateway)))
        )
        self.assertNotIn("fetch_document", dir(FundamentalEvidenceGateway))
        self.assertNotIn("screen_companies", dir(FundamentalEvidenceGateway))
        self.assertNotIn("place_order", dir(FundamentalEvidenceGateway))


if __name__ == "__main__":
    unittest.main()
