"""Fail-closed adapter from a scoped Tijori MCP transport to Jarvis evidence.

This module intentionally contains no subprocess, browser, network, login, or
credential code.  Those concerns belong behind ``TijoriMcpTransport`` and are
not part of the offline vertical slice.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import TypeVar

from pydantic import ValidationError

from app.exceptions import (
    FundamentalGatewayAuthenticationError,
    FundamentalGatewayConfigurationError,
    FundamentalGatewayEntitlementError,
    FundamentalGatewayError,
    FundamentalProviderRateLimitError,
    FundamentalProviderUnavailableError,
    FundamentalResponseValidationError,
)
from app.fundamentals.tijori_mcp_contracts import (
    TIJORI_APPROVED_TOOLS,
    TijoriCompanyRecord,
    TijoriCompanySearchPayload,
    TijoriEvidenceFactRecord,
    TijoriEvidencePayload,
    TijoriEvidenceSourceRecord,
    TijoriIssuerResolutionPayload,
    TijoriMcpAdapterSettings,
    TijoriMcpToolResult,
    TijoriMcpTransport,
    TijoriMcpTransportError,
    TijoriMcpTransportInspection,
    TijoriToolName,
    TijoriToolStatus,
    TijoriTransportFailureKind,
)
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
    FundamentalGatewayRequest,
    FundamentalGatewayResponse,
    FundamentalIssuerCandidate,
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
    FundamentalReportingPeriod,
    FundamentalSourceRank,
    FundamentalSourceReference,
    FundamentalSourceType,
    FundamentalValidationStatus,
    NormalizedFundamentalFact,
    ProviderConnectionScope,
)


_PayloadT = TypeVar(
    "_PayloadT",
    TijoriCompanySearchPayload,
    TijoriIssuerResolutionPayload,
    TijoriEvidencePayload,
)
_ResponseT = TypeVar("_ResponseT", bound=FundamentalGatewayResponse)
_FINGERPRINT_CHARS = frozenset("0123456789abcdef")
_PROVIDER_NOTE = (
    "Provider-standardized secondary evidence; reconcile material values "
    "against primary company or exchange disclosures."
)
_STATUS_LIMITATIONS = {
    TijoriToolStatus.NOT_FOUND: "Tijori did not return evidence for this issuer.",
    TijoriToolStatus.NOT_ENTITLED: (
        "The scoped Tijori subscription is not entitled to this capability."
    ),
    TijoriToolStatus.PAYWALLED: (
        "The requested Tijori evidence is behind a provider paywall."
    ),
    TijoriToolStatus.UNAVAILABLE: (
        "Tijori could not provide this evidence at the time of the request."
    ),
}


class TijoriMcpAdapter(FundamentalEvidenceGateway):
    """Normalize only five audited, read-only Tijori tools."""

    def __init__(
        self,
        *,
        transport: TijoriMcpTransport,
        settings: TijoriMcpAdapterSettings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(transport, TijoriMcpTransport):
            raise TypeError("Tijori adapter requires a compatible transport")
        self._transport = transport
        self._settings = settings or TijoriMcpAdapterSettings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._transport_fingerprint = self._validate_fingerprint(
            transport.configuration_fingerprint
        )

    @property
    def configuration_fingerprint(self) -> str:
        material = (
            f"{self._settings.configuration_fingerprint}:"
            f"{self._transport_fingerprint}"
        )
        return sha256(material.encode("utf-8")).hexdigest()

    def inspect_capabilities(
        self,
        *,
        connection: ProviderConnectionScope,
    ) -> FundamentalCapabilityManifest:
        self._validate_connection(connection)
        try:
            inspection = self._transport.inspect(connection=connection)
        except TijoriMcpTransportError as exc:
            raise self._mapped_transport_error(exc, connection=connection) from exc
        except Exception as exc:
            raise FundamentalProviderUnavailableError(
                "Tijori capability inspection is currently unavailable",
                provider="tijori",
                provider_connection_id=connection.provider_connection_id,
            ) from exc

        if not isinstance(inspection, TijoriMcpTransportInspection):
            raise FundamentalResponseValidationError(
                "Tijori transport returned an invalid capability envelope",
                provider="tijori",
                provider_connection_id=connection.provider_connection_id,
            )

        if inspection.provider_contract_version != (
            self._settings.provider_contract_version
        ):
            raise FundamentalResponseValidationError(
                "Tijori provider contract version does not match the adapter",
                provider="tijori",
                provider_connection_id=connection.provider_connection_id,
            )
        if (
            connection.entitlement_checked_at is not None
            and inspection.checked_at < connection.entitlement_checked_at
        ):
            raise FundamentalResponseValidationError(
                "Tijori capability inspection predates the scoped entitlement",
                provider="tijori",
                provider_connection_id=connection.provider_connection_id,
            )

        tool_by_capability: tuple[
            tuple[FundamentalCapability, TijoriToolName, bool, int | None], ...
        ] = (
            (FundamentalCapability.COMPANY_SEARCH, "search_company", False, None),
            (
                FundamentalCapability.ISSUER_RESOLUTION,
                "resolve_company_ids",
                False,
                None,
            ),
            (
                FundamentalCapability.COMPANY_OVERVIEW,
                "get_company_overview",
                False,
                None,
            ),
            (
                FundamentalCapability.FINANCIAL_STATEMENTS,
                "get_financials",
                True,
                40,
            ),
            (
                FundamentalCapability.SHAREHOLDING_HISTORY,
                "get_shareholding",
                True,
                40,
            ),
        )
        available = set(inspection.available_tools)
        descriptors = []
        for capability, tool_name, supports_history, max_periods in tool_by_capability:
            is_available = inspection.authenticated and tool_name in available
            if not inspection.authenticated:
                notes = ("The scoped Tijori session is not authenticated.",)
            elif tool_name not in available:
                notes = ("The audited read-only provider tool is unavailable.",)
            else:
                notes = ()
            descriptors.append(
                FundamentalCapabilityDescriptor(
                    capability=capability,
                    status=(
                        FundamentalCapabilityStatus.AVAILABLE
                        if is_available
                        else FundamentalCapabilityStatus.UNAVAILABLE
                    ),
                    supports_history=supports_history,
                    max_periods=max_periods,
                    notes=notes,
                )
            )
        return FundamentalCapabilityManifest(
            connection=connection,
            capabilities=tuple(descriptors),
            checked_at=inspection.checked_at,
            provider_contract_version=inspection.provider_contract_version,
            adapter_fingerprint=self.configuration_fingerprint,
        )

    def search_companies(
        self,
        *,
        request: FundamentalCompanySearchRequest,
    ) -> FundamentalCompanySearchResult:
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="search_company",
            arguments={
                "query": request.query,
                "exchanges": list(request.exchanges),
                "max_results": request.max_results,
            },
        )
        if result.status is TijoriToolStatus.NOT_FOUND:
            candidates: tuple[FundamentalIssuerCandidate, ...] = ()
        else:
            self._raise_terminal_tool_status(result.status, request=request)
            payload = self._parse_payload(
                TijoriCompanySearchPayload,
                result,
                request=request,
            )
            if len(payload.companies) > request.max_results:
                self._raise_invalid_response(
                    request,
                    "Tijori search exceeded the requested result limit",
                )
            if request.exchanges and any(
                company.exchange not in request.exchanges
                for company in payload.companies
            ):
                self._raise_invalid_response(
                    request,
                    "Tijori search returned an exchange outside the request",
                )
            candidates = tuple(
                self._candidate(company) for company in payload.companies
            )
        response = FundamentalCompanySearchResult(
            **self._response_metadata(request, started_at, completed_at),
            candidates=candidates,
        )
        return self._bind(request, response)

    def resolve_issuer(
        self,
        *,
        request: FundamentalIssuerResolutionRequest,
    ) -> FundamentalIssuerResolutionResult:
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="resolve_company_ids",
            arguments={
                "locator": request.locator.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                "max_candidates": request.max_candidates,
            },
        )
        if result.status is TijoriToolStatus.NOT_FOUND:
            response = FundamentalIssuerResolutionResult(
                **self._response_metadata(request, started_at, completed_at),
                status=FundamentalResolutionStatus.NOT_FOUND,
                candidates=(),
                limitation="Tijori did not resolve the requested issuer.",
            )
            return self._bind(request, response)

        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriIssuerResolutionPayload,
            result,
            request=request,
        )
        if len(payload.companies) > request.max_candidates:
            self._raise_invalid_response(
                request,
                "Tijori resolution exceeded the candidate limit",
            )
        candidates = tuple(
            self._candidate(company) for company in payload.companies
        )
        issuer = None
        if payload.selected_company_id is not None:
            issuer = next(
                candidate.issuer
                for candidate in candidates
                if candidate.issuer.provider_company_id
                == payload.selected_company_id
            )
        limitation = None
        if payload.status is FundamentalResolutionStatus.AMBIGUOUS:
            limitation = "Tijori returned multiple issuer matches."
        elif payload.status is FundamentalResolutionStatus.NOT_FOUND:
            limitation = "Tijori did not resolve the requested issuer."
        response = FundamentalIssuerResolutionResult(
            **self._response_metadata(request, started_at, completed_at),
            status=payload.status,
            issuer=issuer,
            candidates=candidates,
            limitation=limitation,
        )
        return self._bind(request, response)

    def retrieve_company_overview(
        self,
        *,
        request: FundamentalCompanyOverviewRequest,
    ) -> FundamentalEvidenceRetrieval:
        return self._retrieve(
            request=request,
            tool_name="get_company_overview",
            arguments=self._evidence_arguments(request),
        )

    def retrieve_financials(
        self,
        *,
        request: FundamentalFinancialsRequest,
    ) -> FundamentalEvidenceRetrieval:
        arguments = self._evidence_arguments(request)
        arguments.update(
            {
                "statements": [value.value for value in request.statements],
                "period_types": [value.value for value in request.period_types],
                "max_periods": request.max_periods,
            }
        )
        return self._retrieve(
            request=request,
            tool_name="get_financials",
            arguments=arguments,
        )

    def retrieve_shareholding(
        self,
        *,
        request: FundamentalShareholdingRequest,
    ) -> FundamentalEvidenceRetrieval:
        arguments = self._evidence_arguments(request)
        arguments.update(
            {
                "quarters": request.quarters,
                "include_promoter_pledge": request.include_promoter_pledge,
            }
        )
        return self._retrieve(
            request=request,
            tool_name="get_shareholding",
            arguments=arguments,
        )

    def _retrieve(
        self,
        *,
        request: (
            FundamentalCompanyOverviewRequest
            | FundamentalFinancialsRequest
            | FundamentalShareholdingRequest
        ),
        tool_name: TijoriToolName,
        arguments: dict[str, object],
    ) -> FundamentalEvidenceRetrieval:
        started_at, result, completed_at = self._call(
            request=request,
            tool_name=tool_name,
            arguments=arguments,
        )
        failed_status = {
            TijoriToolStatus.NOT_FOUND: FundamentalRetrievalStatus.NOT_FOUND,
            TijoriToolStatus.NOT_ENTITLED: (
                FundamentalRetrievalStatus.NOT_ENTITLED
            ),
            TijoriToolStatus.PAYWALLED: FundamentalRetrievalStatus.PAYWALLED,
            TijoriToolStatus.UNAVAILABLE: (
                FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE
            ),
        }
        if result.status in failed_status:
            response = FundamentalEvidenceRetrieval(
                **self._response_metadata(request, started_at, completed_at),
                issuer=request.issuer,
                status=failed_status[result.status],
                limitations=(_STATUS_LIMITATIONS[result.status],),
            )
            return self._bind(request, response)

        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriEvidencePayload,
            result,
            request=request,
        )
        self._validate_evidence_identity(request, payload)
        try:
            snapshot = self._normalize_snapshot(
                request=request,
                payload=payload,
                completed_at=completed_at,
                payload_fingerprint=result.payload_fingerprint,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori evidence could not be safely normalized",
                **self._error_context(request),
            ) from exc
        partial_facts = any(
            fact.availability_status
            is not FundamentalAvailabilityStatus.AVAILABLE
            for fact in snapshot.facts
        )
        limitations = payload.limitations
        if partial_facts and not limitations:
            limitations = (
                "One or more requested provider facts were unavailable.",
            )
        status = (
            FundamentalRetrievalStatus.PARTIAL
            if partial_facts or limitations
            else FundamentalRetrievalStatus.COMPLETED
        )
        try:
            response = FundamentalEvidenceRetrieval(
                **self._response_metadata(request, started_at, completed_at),
                issuer=request.issuer,
                status=status,
                snapshot=snapshot,
                limitations=limitations,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori evidence violated its requested capability scope",
                **self._error_context(request),
            ) from exc
        return self._bind(request, response)

    def _normalize_snapshot(
        self,
        *,
        request: FundamentalGatewayRequest,
        payload: TijoriEvidencePayload,
        completed_at: datetime,
        payload_fingerprint: str | None,
    ) -> FundamentalEvidenceSnapshot:
        sources = tuple(
            self._normalize_source(source, completed_at=completed_at)
            for source in payload.sources
        )
        source_by_provider_id = dict(zip(
            (source.provider_source_id for source in payload.sources),
            sources,
            strict=True,
        ))
        facts = tuple(
            self._normalize_fact(
                fact,
                issuer_symbol=payload.symbol,
                source_by_provider_id=source_by_provider_id,
            )
            for fact in payload.facts
        )
        partial = any(
            fact.availability_status
            is not FundamentalAvailabilityStatus.AVAILABLE
            for fact in facts
        ) or bool(payload.limitations)
        fingerprint = payload_fingerprint or sha256(
            payload.model_dump_json().encode("utf-8")
        ).hexdigest()
        return FundamentalEvidenceSnapshot(
            snapshot_id=(
                f"snapshot.tijori.{request.request_id}.{fingerprint[:16]}"
            ),
            connection=request.connection,
            issuer=request.issuer,  # type: ignore[attr-defined]
            sources=sources,
            facts=facts,
            conflicts=(),
            evidence_posture=FundamentalEvidencePosture.RESEARCH_GRADE,
            validation_status=(
                FundamentalValidationStatus.PARTIAL
                if partial
                else FundamentalValidationStatus.VALIDATED
            ),
            assembled_at=completed_at,
            limitations=payload.limitations,
        )

    def _normalize_source(
        self,
        record: TijoriEvidenceSourceRecord,
        *,
        completed_at: datetime,
    ) -> FundamentalEvidenceSource:
        fingerprint = sha256(
            record.model_dump_json().encode("utf-8")
        ).hexdigest()
        return FundamentalEvidenceSource(
            source_id=f"SRC-TIJORI-{fingerprint[:24].upper()}",
            source_name=record.source_name,
            source_type=FundamentalSourceType.PROVIDER_STANDARDIZED,
            source_rank=FundamentalSourceRank.STANDARDIZED_PROVIDER,
            owner_or_provider="Tijori Finance",
            location=record.location,
            period_covered=record.period_covered,
            as_of_date=record.as_of_date,
            published_at=record.published_at,
            retrieved_at=completed_at,
            freshness_status=FundamentalFreshnessStatus.ACCEPTABLE_FOR_PERIOD,
            content_fingerprint=fingerprint,
            parser_name="jarvis.tijori.normalizer",
            parser_version=self._settings.parser_version,
            provider_schema_version=self._settings.provider_contract_version,
            validation_status=FundamentalValidationStatus.VALIDATED,
            notes=(_PROVIDER_NOTE,),
        )

    def _normalize_fact(
        self,
        record: TijoriEvidenceFactRecord,
        *,
        issuer_symbol: str,
        source_by_provider_id: dict[str, FundamentalEvidenceSource],
    ) -> NormalizedFundamentalFact:
        source = (
            source_by_provider_id.get(record.provider_source_id)
            if record.provider_source_id is not None
            else None
        )
        available = (
            record.availability_status is FundamentalAvailabilityStatus.AVAILABLE
        )
        fingerprint = sha256(
            record.model_dump_json().encode("utf-8")
        ).hexdigest()
        evidence_id = (
            "fundamental:tijori."
            f"{issuer_symbol.lower()}.{record.statement.value}."
            f"{record.line_item_id}.{record.period_type.value}."
            f"{fingerprint[:12]}"
        )
        if available:
            assert source is not None
            references = (
                FundamentalSourceReference(
                    source_id=source.source_id,
                    content_fingerprint=source.content_fingerprint,
                ),
            )
            label = FundamentalEvidenceLabel.FACT_PROVIDER_STANDARDIZED
            confidence = FundamentalEvidenceConfidence.MEDIUM
            validation = FundamentalValidationStatus.VALIDATED
            source_location = source.location
            note = _PROVIDER_NOTE
        else:
            references = ()
            label = (
                FundamentalEvidenceLabel.MISSING_REQUIRED_SOURCE
                if record.availability_status
                is FundamentalAvailabilityStatus.MISSING_REQUIRED_SOURCE
                else FundamentalEvidenceLabel.UNKNOWN
            )
            confidence = FundamentalEvidenceConfidence.LOW
            validation = FundamentalValidationStatus.PARTIAL
            source_location = (
                source.location
                if source is not None
                else "https://tijorifinance.com"
            )
            note = "The provider did not supply a validated value for this fact."
        return NormalizedFundamentalFact(
            evidence_id=evidence_id,
            statement=record.statement,
            line_item_original=record.line_item_original,
            line_item_standard=record.line_item_standard,
            line_item_id=record.line_item_id,
            period=FundamentalReportingPeriod(
                label=record.period_label,
                period_type=record.period_type,
                start_date=record.period_start,
                end_date=record.period_end,
            ),
            value_kind=record.value_kind,
            source_value=record.source_value,
            normalized_value=record.normalized_value,
            currency=record.currency,
            source_unit=record.source_unit,
            normalized_unit=record.normalized_unit,
            normalization_method=FundamentalNormalizationMethod.MAPPED,
            source_location=source_location,
            evidence_label=label,
            confidence=confidence,
            availability_status=record.availability_status,
            freshness_status=FundamentalFreshnessStatus.ACCEPTABLE_FOR_PERIOD,
            validation_status=validation,
            conflict_status=FundamentalConflictStatus.NONE,
            lineage=FundamentalEvidenceLineage(
                source_references=references,
                transformation_id="jarvis.tijori.mapping",
                transformation_version=self._settings.parser_version,
            ),
            normalization_note=note,
        )

    def _call(
        self,
        *,
        request: FundamentalGatewayRequest,
        tool_name: TijoriToolName,
        arguments: dict[str, object],
    ) -> tuple[datetime, TijoriMcpToolResult, datetime]:
        self._validate_connection(request.connection, request=request)
        if tool_name not in TIJORI_APPROVED_TOOLS:
            raise FundamentalGatewayConfigurationError(
                "Tijori tool is outside the audited allow-list",
                **self._error_context(request),
            )
        started_at = self._clock()
        self._validate_clock(started_at, minimum=request.requested_at, request=request)
        try:
            result = self._transport.call_tool(
                connection=request.connection,
                tool_name=tool_name,
                arguments=arguments,
            )
        except TijoriMcpTransportError as exc:
            raise self._mapped_transport_error(exc, request=request) from exc
        except Exception as exc:
            raise FundamentalProviderUnavailableError(
                "Tijori could not complete the requested capability",
                **self._error_context(request),
            ) from exc
        completed_at = self._clock()
        self._validate_clock(completed_at, minimum=started_at, request=request)
        if not isinstance(result, TijoriMcpToolResult):
            self._raise_invalid_response(
                request,
                "Tijori transport returned an invalid result envelope",
            )
        if result.tool_name != tool_name:
            self._raise_invalid_response(
                request,
                "Tijori response tool does not match the request",
            )
        return started_at, result, completed_at

    def _parse_payload(
        self,
        model: type[_PayloadT],
        result: TijoriMcpToolResult,
        *,
        request: FundamentalGatewayRequest,
    ) -> _PayloadT:
        try:
            return model.model_validate_json(
                json.dumps(
                    result.payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori response failed the normalized evidence contract",
                **self._error_context(request),
            ) from exc

    def _raise_terminal_tool_status(
        self,
        status: TijoriToolStatus,
        *,
        request: FundamentalGatewayRequest,
    ) -> None:
        if status is TijoriToolStatus.SUCCESS:
            return
        if status is TijoriToolStatus.AUTHENTICATION_REQUIRED:
            error_type: type[FundamentalGatewayError] = (
                FundamentalGatewayAuthenticationError
            )
            message = "The scoped Tijori session requires authentication"
        elif status in {
            TijoriToolStatus.NOT_ENTITLED,
            TijoriToolStatus.PAYWALLED,
        }:
            error_type = FundamentalGatewayEntitlementError
            message = "The scoped Tijori subscription cannot use this capability"
        elif status is TijoriToolStatus.RATE_LIMITED:
            error_type = FundamentalProviderRateLimitError
            message = "Tijori rate-limited the scoped provider connection"
        elif status is TijoriToolStatus.UNAVAILABLE:
            error_type = FundamentalProviderUnavailableError
            message = "Tijori cannot currently complete this capability"
        else:
            error_type = FundamentalResponseValidationError
            message = "Tijori returned an unsupported result status"
        raise error_type(message, **self._error_context(request))

    def _mapped_transport_error(
        self,
        error: TijoriMcpTransportError,
        *,
        request: FundamentalGatewayRequest | None = None,
        connection: ProviderConnectionScope | None = None,
    ) -> FundamentalGatewayError:
        context = (
            self._error_context(request)
            if request is not None
            else {
                "provider": "tijori",
                "provider_connection_id": (
                    connection.provider_connection_id
                    if connection is not None
                    else None
                ),
            }
        )
        mapping: dict[
            TijoriTransportFailureKind,
            tuple[type[FundamentalGatewayError], str],
        ] = {
            TijoriTransportFailureKind.CONFIGURATION: (
                FundamentalGatewayConfigurationError,
                "The Tijori transport is not configured",
            ),
            TijoriTransportFailureKind.AUTHENTICATION: (
                FundamentalGatewayAuthenticationError,
                "The scoped Tijori session could not authenticate",
            ),
            TijoriTransportFailureKind.ENTITLEMENT: (
                FundamentalGatewayEntitlementError,
                "The scoped Tijori subscription lacks this capability",
            ),
            TijoriTransportFailureKind.RATE_LIMIT: (
                FundamentalProviderRateLimitError,
                "Tijori rate-limited the scoped provider connection",
            ),
            TijoriTransportFailureKind.UNAVAILABLE: (
                FundamentalProviderUnavailableError,
                "The Tijori transport is currently unavailable",
            ),
            TijoriTransportFailureKind.PROTOCOL: (
                FundamentalResponseValidationError,
                "The Tijori transport violated its response contract",
            ),
        }
        error_type, message = mapping[error.kind]
        return error_type(message, **context)

    def _candidate(self, company: TijoriCompanyRecord) -> FundamentalIssuerCandidate:
        fingerprint = sha256(
            company.model_dump_json().encode("utf-8")
        ).hexdigest()
        return FundamentalIssuerCandidate(
            issuer=FundamentalIssuerIdentity(
                exchange=company.exchange,
                symbol=company.symbol,
                legal_name=company.legal_name,
                isin=company.isin,
                provider_company_id=company.company_id,
                provider_slug=company.slug,
            ),
            match_kind=company.match_kind,
            match_score=company.match_score,
            matched_on=company.matched_on,
            provider_record_fingerprint=fingerprint,
        )

    @staticmethod
    def _evidence_arguments(request: FundamentalGatewayRequest) -> dict[str, object]:
        issuer = request.issuer  # type: ignore[attr-defined]
        return {
            "issuer": {
                "exchange": issuer.exchange,
                "symbol": issuer.symbol,
                "provider_company_id": issuer.provider_company_id,
                "provider_slug": issuer.provider_slug,
            },
            "as_of_date": (
                request.as_of_date.isoformat()  # type: ignore[attr-defined]
                if request.as_of_date is not None  # type: ignore[attr-defined]
                else None
            ),
        }

    def _validate_evidence_identity(
        self,
        request: (
            FundamentalCompanyOverviewRequest
            | FundamentalFinancialsRequest
            | FundamentalShareholdingRequest
        ),
        payload: TijoriEvidencePayload,
    ) -> None:
        issuer = request.issuer
        if (
            payload.exchange != issuer.exchange
            or payload.symbol != issuer.symbol
            or (
                issuer.provider_company_id is not None
                and payload.company_id != issuer.provider_company_id
            )
        ):
            self._raise_invalid_response(
                request,
                "Tijori evidence belongs to a different issuer",
            )

    def _response_metadata(
        self,
        request: FundamentalGatewayRequest,
        started_at: datetime,
        completed_at: datetime,
    ) -> dict[str, object]:
        return {
            "capability": request.capability,
            "request_id": request.request_id,
            "request_fingerprint": request.request_fingerprint,
            "connection": request.connection,
            "requested_at": request.requested_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "provider_contract_version": (
                self._settings.provider_contract_version
            ),
            "adapter_fingerprint": self.configuration_fingerprint,
        }

    @staticmethod
    def _bind(
        request: FundamentalGatewayRequest,
        response: _ResponseT,
    ) -> _ResponseT:
        validate_fundamental_response_binding(request, response)
        return response

    def _validate_connection(
        self,
        connection: ProviderConnectionScope,
        request: FundamentalGatewayRequest | None = None,
    ) -> None:
        if connection.provider.casefold() != self._settings.provider:
            context = (
                self._error_context(request)
                if request is not None
                else {
                    "provider": "tijori",
                    "provider_connection_id": connection.provider_connection_id,
                }
            )
            raise FundamentalGatewayConfigurationError(
                "The provider connection is not a Tijori connection",
                **context,
            )

    def _validate_clock(
        self,
        value: datetime,
        *,
        minimum: datetime,
        request: FundamentalGatewayRequest,
    ) -> None:
        if value.tzinfo is None or value.utcoffset() is None or value < minimum:
            raise FundamentalGatewayConfigurationError(
                "Tijori adapter clock returned an invalid timestamp",
                **self._error_context(request),
            )

    @staticmethod
    def _validate_fingerprint(value: object) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or not set(value).issubset(_FINGERPRINT_CHARS)
        ):
            raise FundamentalGatewayConfigurationError(
                "Tijori transport fingerprint is invalid",
                provider="tijori",
            )
        return value

    @staticmethod
    def _error_context(
        request: FundamentalGatewayRequest | None,
    ) -> dict[str, object]:
        if request is None:
            return {"provider": "tijori"}
        return {
            "capability": request.capability.value,
            "provider": "tijori",
            "provider_connection_id": (
                request.connection.provider_connection_id
            ),
            "operation_id": request.operation_id,
        }

    def _raise_invalid_response(
        self,
        request: FundamentalGatewayRequest,
        message: str,
    ) -> None:
        raise FundamentalResponseValidationError(
            message,
            **self._error_context(request),
        )
