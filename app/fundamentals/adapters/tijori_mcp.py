"""Fail-closed adapter from a scoped Tijori MCP transport to Jarvis evidence.

This module contains no subprocess, browser, network, login, or credential
code. Those concerns belong behind ``TijoriMcpTransport``. The optional filing
importer handles only validated files inside explicitly configured local roots.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat as stat_module
from typing import Literal, TypeVar
from uuid import uuid4

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
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
    TijoriBalanceSheetDocument,
    TijoriBalanceSheetPayload,
    TijoriBenchmarkingFinancialsDocument,
    TijoriBenchmarkingFinancialsPayload,
    TijoriBenchmarkingRow,
    TijoriBenchmarkingCell,
    TijoriCashFlowDocument,
    TijoriCashFlowPayload,
    TijoriCompanyFilingDownloadPayload,
    TijoriCompanyFilingsPayload,
    TijoriCompanyFilingReference,
    TijoriCompanyRecord,
    TijoriCompanySearchPayload,
    TijoriEvidenceFactRecord,
    TijoriEvidencePayload,
    TijoriEvidenceSourceRecord,
    TijoriFinancialDocumentCell,
    TijoriFinancialDocumentPackagePayload,
    TijoriFinancialStatementCell,
    TijoriGrowthTableDocument,
    TijoriGrowthTablePayload,
    TijoriIssuerResolutionPayload,
    TijoriMcpAdapterSettings,
    TijoriMcpToolResult,
    TijoriMcpTransport,
    TijoriMcpTransportError,
    TijoriMcpTransportInspection,
    TijoriPeerComparisonDocument,
    TijoriPeerComparisonPayload,
    TijoriProfitAndLossDocument,
    TijoriProfitAndLossPayload,
    TijoriQuarterlyResultsDocument,
    TijoriQuarterlyResultsPayload,
    TijoriRatiosDocument,
    TijoriRatiosPayload,
    TijoriToolName,
    TijoriToolStatus,
    TijoriTransportFailureKind,
)
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsGateway,
    FundamentalBenchmarkingFinancialsResult,
    FundamentalCapability,
    FundamentalCapabilityDescriptor,
    FundamentalCapabilityManifest,
    FundamentalCapabilityStatus,
    FundamentalCompanyOverviewRequest,
    FundamentalCompanySearchRequest,
    FundamentalCompanySearchResult,
    FundamentalEvidenceGateway,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialDocumentPackage,
    FundamentalFinancialDocumentPackageGateway,
    FundamentalFinancialDocumentPackageRequest,
    FundamentalFinancialDocumentPackageResult,
    FundamentalFinancialsRequest,
    FundamentalFilingAcquisitionRequest,
    FundamentalFilingAcquisitionResult,
    FundamentalFilingDiscoveryRequest,
    FundamentalFilingDiscoveryResult,
    FundamentalFilingGateway,
    FundamentalGatewayRequest,
    FundamentalGatewayResponse,
    FundamentalIssuerCandidate,
    FundamentalIssuerResolutionRequest,
    FundamentalIssuerResolutionResult,
    FundamentalPeerComparisonGateway,
    FundamentalPeerComparisonResult,
    FundamentalResolutionStatus,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
    FundamentalStructuredDocumentGateway,
    FundamentalStructuredDocumentRequest,
    FundamentalStructuredDocumentResult,
    validate_fundamental_response_binding,
)
from app.models.financial_documents import (
    BenchmarkingCell,
    BenchmarkingCompany,
    BenchmarkingRow,
    BenchmarkingSection,
    FinancialDocumentCell,
    FinancialDocumentPeriod,
    FinancialDocumentRow,
    FinancialDocumentRowKind,
    FinancialDocumentType,
    FinancialReportingBasis,
    PeerComparisonCell,
    PeerComparisonCompany,
    PeerComparisonMetric,
    StructuredBenchmarkingFinancialsDocument,
    StructuredFinancialDocument,
    StructuredPeerComparisonDocument,
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
    FundamentalValueKind,
    NormalizedFundamentalFact,
    ProviderConnectionScope,
)
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingCategory,
    FundamentalFilingDiscoverySnapshot,
    FundamentalFilingPeriod,
    FundamentalFilingPeriodState,
    FundamentalFilingPeriodType,
    FundamentalFilingReference,
    FundamentalFilingSourceClass,
)


_PayloadT = TypeVar(
    "_PayloadT",
    TijoriCompanySearchPayload,
    TijoriIssuerResolutionPayload,
    TijoriEvidencePayload,
    TijoriFinancialDocumentPackagePayload,
    TijoriGrowthTablePayload,
    TijoriBalanceSheetPayload,
    TijoriCashFlowPayload,
    TijoriProfitAndLossPayload,
    TijoriRatiosPayload,
    TijoriQuarterlyResultsPayload,
    TijoriPeerComparisonPayload,
    TijoriBenchmarkingFinancialsPayload,
    TijoriCompanyFilingsPayload,
    TijoriCompanyFilingDownloadPayload,
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
_PDF_HEADER_PATTERN = re.compile(rb"^%PDF-([0-9]+(?:\.[0-9]+)?)")
_PDF_EOF_TAIL_BYTES = 1_024
_MAXIMUM_FILING_BYTES = 100_000_000
_MAXIMUM_FILING_PAGES = 5_000
_FILING_RECHECK_INTERVAL = timedelta(days=10)


class TijoriFilingArtifactImporter:
    """Import validated staged PDFs into an owner-only artifact boundary."""

    def __init__(
        self,
        *,
        staging_root: Path,
        artifact_root: Path,
    ) -> None:
        self._staging_root = self._validate_root(
            staging_root,
            "filing staging",
        )
        self._artifact_root = self._validate_root(
            artifact_root,
            "filing artifact",
        )
        if self._staging_root == self._artifact_root:
            raise ValueError("filing staging and artifact roots must differ")

    @property
    def configuration_fingerprint(self) -> str:
        material = f"{self._staging_root}\n{self._artifact_root}"
        return sha256(material.encode("utf-8")).hexdigest()

    def import_staged_artifact(
        self,
        *,
        payload: TijoriCompanyFilingDownloadPayload,
        reference: FundamentalFilingReference,
        checked_at: datetime,
    ) -> FundamentalFilingArtifactManifest:
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("filing artifact check time requires timezone")
        if payload.downloaded_at > checked_at:
            raise ValueError("filing download cannot follow its validation")
        self._validate_payload_reference(payload, reference)

        relative_path = self._relative_artifact_path(reference, payload)
        destination = self._artifact_root.joinpath(*relative_path.parts)
        self._require_inside_root(destination, self._artifact_root)
        self._create_owner_only_parents(destination.parent)
        staged_path = self._staging_root / payload.staged_artifact_key
        self._require_inside_root(staged_path, self._staging_root)
        temporary_path = destination.parent / f".part-{uuid4().hex}"

        source_descriptor = self._open_regular_owner_file(staged_path)
        try:
            metadata = self._copy_and_validate_pdf(
                source_descriptor=source_descriptor,
                temporary_path=temporary_path,
                expected_hash=payload.content_sha256,
                expected_length=payload.byte_length,
            )
            manifest = FundamentalFilingArtifactManifest(
                reference=reference,
                content_sha256=payload.content_sha256,
                safe_relative_path=relative_path.as_posix(),
                byte_length=payload.byte_length,
                page_count=metadata["page_count"],
                pdf_version=metadata["pdf_version"],
                downloaded_at=payload.downloaded_at,
                last_checked_at=checked_at,
                recheck_after=checked_at + _FILING_RECHECK_INTERVAL,
            )
            self._publish_without_overwrite(
                temporary_path=temporary_path,
                destination=destination,
                expected_hash=payload.content_sha256,
                expected_length=payload.byte_length,
            )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            os.close(source_descriptor)

        staged_path.unlink()
        return manifest

    @staticmethod
    def _validate_root(value: Path, label: str) -> Path:
        if not isinstance(value, Path) or not value.is_absolute():
            raise ValueError(f"{label} root must be an absolute path")
        metadata = value.lstat()
        if not stat_module.S_ISDIR(metadata.st_mode) or value.is_symlink():
            raise ValueError(f"{label} root must be a real directory")
        if metadata.st_mode & 0o077:
            raise ValueError(f"{label} root must be owner-only")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ValueError(f"{label} root owner is invalid")
        return value.resolve(strict=True)

    @staticmethod
    def _validate_payload_reference(
        payload: TijoriCompanyFilingDownloadPayload,
        reference: FundamentalFilingReference,
    ) -> None:
        if (
            payload.filing_reference_id != reference.filing_reference_id
            or payload.category != reference.category.value
            or payload.provider_company_id
            != reference.issuer.provider_company_id
            or payload.provider_slug != reference.issuer.provider_slug
            or payload.source_page_url != reference.source_page_url
            or payload.artifact_url != reference.artifact_url
            or payload.discovery_fingerprint
            != reference.source_discovery_fingerprint
        ):
            raise ValueError("staged filing does not match its exact reference")

    @staticmethod
    def _relative_artifact_path(
        reference: FundamentalFilingReference,
        payload: TijoriCompanyFilingDownloadPayload,
    ) -> PurePosixPath:
        tenant_key = sha256(
            reference.connection.tenant_id.encode("utf-8")
        ).hexdigest()
        connection_key = sha256(
            reference.connection.provider_connection_id.encode("utf-8")
        ).hexdigest()
        issuer_key = reference.issuer.isin
        if issuer_key is None:
            identity_hash = sha256(
                (
                    f"{reference.issuer.exchange}:"
                    f"{reference.issuer.symbol}"
                ).encode("utf-8")
            ).hexdigest()
            issuer_key = f"instrument-{identity_hash}"
        if reference.period.fiscal_year is None:
            fiscal_year = "unclassified"
        else:
            fiscal_year = f"FY{reference.period.fiscal_year}"
        if reference.period.fiscal_quarter is not None:
            period = f"Q{reference.period.fiscal_quarter}"
        elif (
            reference.period.period_type
            is FundamentalFilingPeriodType.ANNUAL
        ):
            period = "annual"
        else:
            period = "unclassified"
        return PurePosixPath(
            f"tenant-{tenant_key}",
            f"connection-{connection_key}",
            issuer_key,
            fiscal_year,
            period,
            reference.category.value.replace("_", "-"),
            payload.staged_artifact_key,
        )

    @staticmethod
    def _require_inside_root(path: Path, root: Path) -> None:
        if not path.is_relative_to(root):
            raise ValueError("filing artifact escaped its configured boundary")

    def _create_owner_only_parents(self, directory: Path) -> None:
        relative = directory.relative_to(self._artifact_root)
        cursor = self._artifact_root
        for part in relative.parts:
            cursor /= part
            cursor.mkdir(mode=0o700, exist_ok=True)
            metadata = cursor.lstat()
            if (
                not stat_module.S_ISDIR(metadata.st_mode)
                or cursor.is_symlink()
                or metadata.st_mode & 0o077
                or (
                    hasattr(os, "getuid")
                    and metadata.st_uid != os.getuid()
                )
            ):
                raise ValueError("filing artifact directory is not owner-only")

    @staticmethod
    def _open_regular_owner_file(path: Path) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat_module.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or (
                    hasattr(os, "getuid")
                    and metadata.st_uid != os.getuid()
                )
            ):
                raise ValueError("staged filing must be an owner-only file")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _copy_and_validate_pdf(
        *,
        source_descriptor: int,
        temporary_path: Path,
        expected_hash: str,
        expected_length: int,
    ) -> dict[str, object]:
        digest = sha256()
        byte_length = 0
        header = b""
        eof_tail = b""
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        destination_descriptor = os.open(
            temporary_path,
            destination_flags,
            0o600,
        )
        try:
            with os.fdopen(destination_descriptor, "wb", closefd=False) as target:
                while True:
                    chunk = os.read(source_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    byte_length += len(chunk)
                    if byte_length > _MAXIMUM_FILING_BYTES:
                        raise ValueError("staged filing exceeds the byte limit")
                    digest.update(chunk)
                    target.write(chunk)
                    if len(header) < 32:
                        header = (header + chunk)[:32]
                    eof_tail = (eof_tail + chunk)[-_PDF_EOF_TAIL_BYTES:]
                target.flush()
                os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)

        header_match = _PDF_HEADER_PATTERN.match(header)
        if header_match is None or b"%%EOF" not in eof_tail:
            raise ValueError("staged filing failed PDF structure validation")
        if byte_length != expected_length or digest.hexdigest() != expected_hash:
            raise ValueError("staged filing content metadata does not match")
        page_count = TijoriFilingArtifactImporter._parsed_pdf_page_count(
            temporary_path
        )
        return {
            "page_count": page_count,
            "pdf_version": header_match.group(1).decode("ascii"),
        }

    @staticmethod
    def _parsed_pdf_page_count(path: Path) -> int:
        """Count the page tree, excluding obsolete objects and embedded markers."""

        try:
            with pdfplumber.open(path) as document:
                page_count = len(document.pages)
        except (OSError, PDFPasswordIncorrect, PDFSyntaxError, ValueError) as exc:
            raise ValueError(
                "staged filing failed PDF structure validation"
            ) from exc
        if page_count < 1 or page_count > _MAXIMUM_FILING_PAGES:
            raise ValueError("staged filing failed PDF structure validation")
        return page_count

    @classmethod
    def _publish_without_overwrite(
        cls,
        *,
        temporary_path: Path,
        destination: Path,
        expected_hash: str,
        expected_length: int,
    ) -> None:
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            descriptor = cls._open_regular_owner_file(destination)
            try:
                digest = sha256()
                byte_length = 0
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    byte_length += len(chunk)
                    digest.update(chunk)
                if (
                    byte_length != expected_length
                    or digest.hexdigest() != expected_hash
                ):
                    raise ValueError(
                        "existing filing artifact conflicts with its hash"
                    )
            finally:
                os.close(descriptor)
        finally:
            temporary_path.unlink(missing_ok=True)


class TijoriMcpAdapter(
    FundamentalBenchmarkingFinancialsGateway,
    FundamentalEvidenceGateway,
    FundamentalFilingGateway,
    FundamentalFinancialDocumentPackageGateway,
    FundamentalPeerComparisonGateway,
    FundamentalStructuredDocumentGateway,
):
    """Normalize the audited Tijori tools behind provider-neutral contracts."""

    def __init__(
        self,
        *,
        transport: TijoriMcpTransport,
        settings: TijoriMcpAdapterSettings | None = None,
        clock: Callable[[], datetime] | None = None,
        filing_artifact_importer: TijoriFilingArtifactImporter | None = None,
    ) -> None:
        if not isinstance(transport, TijoriMcpTransport):
            raise TypeError("Tijori adapter requires a compatible transport")
        self._transport = transport
        self._settings = settings or TijoriMcpAdapterSettings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._filing_artifact_importer = filing_artifact_importer
        self._transport_fingerprint = self._validate_fingerprint(
            transport.configuration_fingerprint
        )

    @property
    def configuration_fingerprint(self) -> str:
        material = (
            f"{self._settings.configuration_fingerprint}:"
            f"{self._transport_fingerprint}:"
            f"{self._filing_importer_fingerprint}"
        )
        return sha256(material.encode("utf-8")).hexdigest()

    @property
    def _filing_importer_fingerprint(self) -> str:
        if self._filing_artifact_importer is None:
            return "filing-import-disabled"
        return self._filing_artifact_importer.configuration_fingerprint

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
            tuple[
                FundamentalCapability,
                TijoriToolName,
                bool,
                int | None,
                bool,
            ], ...
        ] = (
            (
                FundamentalCapability.COMPANY_SEARCH,
                "search_company",
                False,
                None,
                True,
            ),
            (
                FundamentalCapability.ISSUER_RESOLUTION,
                "resolve_company_ids",
                False,
                None,
                True,
            ),
            (
                FundamentalCapability.COMPANY_OVERVIEW,
                "get_company_overview",
                False,
                None,
                True,
            ),
            (
                FundamentalCapability.FINANCIAL_STATEMENTS,
                "get_financials",
                True,
                40,
                True,
            ),
            (
                FundamentalCapability.SHAREHOLDING_HISTORY,
                "get_shareholding",
                True,
                40,
                True,
            ),
            (
                FundamentalCapability.FILING_DISCOVERY,
                "list_company_filings",
                True,
                None,
                True,
            ),
            (
                FundamentalCapability.FILING_ACQUISITION,
                "download_company_filing",
                False,
                None,
                False,
            ),
        )
        available = set(inspection.available_tools)
        descriptors = []
        for (
            capability,
            tool_name,
            supports_history,
            max_periods,
            read_only,
        ) in tool_by_capability:
            importer_ready = (
                capability is not FundamentalCapability.FILING_ACQUISITION
                or self._filing_artifact_importer is not None
            )
            is_available = (
                inspection.authenticated
                and tool_name in available
                and importer_ready
            )
            if not inspection.authenticated:
                notes = ("The scoped Tijori session is not authenticated.",)
            elif tool_name not in available:
                notes = ("The audited provider tool is unavailable.",)
            elif not importer_ready:
                notes = (
                    "The secure filing artifact importer is not configured.",
                )
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
                    read_only=read_only,
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

    def discover_filings(
        self,
        *,
        request: FundamentalFilingDiscoveryRequest,
    ) -> FundamentalFilingDiscoveryResult:
        """Return a validated, provider-neutral filing discovery snapshot."""

        if request.max_documents > 200:
            raise FundamentalGatewayConfigurationError(
                "Tijori filing discovery supports at most 200 documents",
                **self._error_context(request),
            )
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="list_company_filings",
            arguments={
                "issuer": self._filing_issuer_arguments(request.issuer),
                "categories": [
                    category.value for category in request.categories
                ],
                "from_fiscal_year": request.from_fiscal_year,
                "through_date": request.through_date.isoformat(),
                "max_documents": request.max_documents,
            },
        )
        failed_status = self._filing_failure_status(result.status)
        if failed_status is not None:
            response = FundamentalFilingDiscoveryResult(
                **self._response_metadata(
                    request,
                    started_at,
                    completed_at,
                ),
                issuer=request.issuer,
                status=failed_status,
                limitations=(_STATUS_LIMITATIONS[result.status],),
            )
            return self._bind(request, response)
        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriCompanyFilingsPayload,
            result,
            request=request,
        )
        self._validate_filing_discovery_identity(request, payload)
        if payload.requested_filters.categories != tuple(
            category.value for category in request.categories
        ) or (
            payload.requested_filters.from_fiscal_year
            != request.from_fiscal_year
            or payload.requested_filters.through_date != request.through_date
            or payload.requested_filters.max_documents
            != request.max_documents
        ):
            self._raise_invalid_response(
                request,
                "Tijori filing discovery scope does not match the request",
            )
        if not (
            started_at <= payload.source.discovered_at <= completed_at
        ):
            self._raise_invalid_response(
                request,
                "Tijori filing discovery time is outside the request",
            )
        try:
            references = tuple(
                self._normalize_filing_reference(request, item)
                for item in payload.filings
            )
            snapshot = FundamentalFilingDiscoverySnapshot(
                discovery_id=(
                    f"filing.discovery.{request.issuer.exchange}."
                    f"{request.issuer.symbol}."
                    f"{payload.discovery_fingerprint[:24]}"
                ),
                connection=request.connection,
                issuer=request.issuer,
                categories=request.categories,
                from_fiscal_year=request.from_fiscal_year,
                through_date=request.through_date,
                max_documents=request.max_documents,
                discovered_at=payload.source.discovered_at,
                references=references,
                unavailable_period_labels=payload.unavailable_period_labels,
                validation_status=FundamentalValidationStatus.VALIDATED,
                limitations=payload.limitations,
            )
            response = FundamentalFilingDiscoveryResult(
                **self._response_metadata(
                    request,
                    started_at,
                    completed_at,
                ),
                issuer=request.issuer,
                status=FundamentalRetrievalStatus.COMPLETED,
                discovery=snapshot,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori filing discovery could not be normalized safely",
                **self._error_context(request),
            ) from exc
        return self._bind(request, response)

    def acquire_filing(
        self,
        *,
        request: FundamentalFilingAcquisitionRequest,
    ) -> FundamentalFilingAcquisitionResult:
        """Acquire and securely import one exact discovered filing PDF."""

        importer = self._filing_artifact_importer
        if importer is None:
            raise FundamentalGatewayConfigurationError(
                "The secure filing artifact importer is not configured",
                **self._error_context(request),
            )
        reference = request.reference
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="download_company_filing",
            arguments={
                "issuer": self._filing_issuer_arguments(request.issuer),
                "filing_reference": {
                    "filing_reference_id": reference.filing_reference_id,
                    "category": reference.category.value,
                    "provider_company_id": (
                        reference.issuer.provider_company_id
                    ),
                    "provider_slug": reference.issuer.provider_slug,
                    "source_page_url": reference.source_page_url,
                    "artifact_url": reference.artifact_url,
                    "discovery_fingerprint": (
                        reference.source_discovery_fingerprint
                    ),
                },
                "expected_source_url": reference.artifact_url,
                "expected_provider_company_id": (
                    reference.issuer.provider_company_id
                ),
            },
        )
        failed_status = self._filing_failure_status(result.status)
        if failed_status is not None:
            response = FundamentalFilingAcquisitionResult(
                **self._response_metadata(
                    request,
                    started_at,
                    completed_at,
                ),
                issuer=request.issuer,
                status=failed_status,
                limitations=(_STATUS_LIMITATIONS[result.status],),
            )
            return self._bind(request, response)
        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriCompanyFilingDownloadPayload,
            result,
            request=request,
        )
        if not started_at <= payload.downloaded_at <= completed_at:
            self._raise_invalid_response(
                request,
                "Tijori filing download time is outside the request",
            )
        try:
            artifact = importer.import_staged_artifact(
                payload=payload,
                reference=reference,
                checked_at=completed_at,
            )
            response = FundamentalFilingAcquisitionResult(
                **self._response_metadata(
                    request,
                    started_at,
                    completed_at,
                ),
                issuer=request.issuer,
                status=FundamentalRetrievalStatus.COMPLETED,
                artifact=artifact,
            )
        except (OSError, ValidationError, TypeError, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori staged filing could not be imported safely",
                **self._error_context(request),
            ) from exc
        return self._bind(request, response)

    def retrieve_peer_comparison(
        self,
        *,
        request: FundamentalCompanyOverviewRequest,
    ) -> FundamentalPeerComparisonResult:
        """Return one bound, provider-neutral Peer Comparison result."""

        arguments = self._evidence_arguments(request)
        arguments["document_type"] = "peer_comparison"
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="get_company_overview",
            arguments=arguments,
        )
        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriPeerComparisonPayload,
            result,
            request=request,
        )
        provider_document = payload.document
        issuer = request.issuer
        if (
            provider_document.issuer.exchange != issuer.exchange
            or provider_document.issuer.symbol != issuer.symbol
            or (
                issuer.provider_company_id is not None
                and provider_document.issuer.provider_company_id
                != issuer.provider_company_id
            )
            or (
                issuer.provider_slug is not None
                and provider_document.issuer.provider_slug
                != issuer.provider_slug
            )
        ):
            self._raise_invalid_response(
                request,
                "Tijori Peer Comparison belongs to a different issuer",
            )
        if (
            request.as_of_date is not None
            and provider_document.observation_date != request.as_of_date
        ):
            self._raise_invalid_response(
                request,
                "Tijori Peer Comparison observation date does not match",
            )
        if not (
            started_at
            <= provider_document.source.retrieved_at
            <= completed_at
        ):
            self._raise_invalid_response(
                request,
                "Tijori Peer Comparison retrieval time is outside the request",
            )
        try:
            document = self._normalize_peer_comparison_document(
                request,
                provider_document,
            )
            response = FundamentalPeerComparisonResult(
                **self._response_metadata(
                    request,
                    started_at,
                    completed_at,
                ),
                issuer=request.issuer,
                status=FundamentalRetrievalStatus.COMPLETED,
                document=document,
            )
        except (ValidationError, InvalidOperation, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori Peer Comparison could not be normalized safely",
                **self._error_context(request),
            ) from exc
        return self._bind(request, response)

    def retrieve_benchmarking_financials(
        self,
        *,
        request: FundamentalCompanyOverviewRequest,
    ) -> FundamentalBenchmarkingFinancialsResult:
        """Return one bound, provider-neutral Financial benchmark result."""

        arguments = self._evidence_arguments(request)
        arguments["document_type"] = "benchmarking_financials"
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="get_company_overview",
            arguments=arguments,
        )
        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriBenchmarkingFinancialsPayload,
            result,
            request=request,
        )
        provider_document = payload.document
        issuer = request.issuer
        if (
            provider_document.issuer.exchange != issuer.exchange
            or provider_document.issuer.symbol != issuer.symbol
            or (
                issuer.provider_company_id is not None
                and provider_document.issuer.provider_company_id
                != issuer.provider_company_id
            )
            or (
                issuer.provider_slug is not None
                and provider_document.issuer.provider_slug
                != issuer.provider_slug
            )
        ):
            self._raise_invalid_response(
                request,
                "Tijori Benchmarking Financials belongs to a different issuer",
            )
        if (
            request.as_of_date is not None
            and provider_document.observation_date != request.as_of_date
        ):
            self._raise_invalid_response(
                request,
                "Tijori Benchmarking Financials observation date does not match",
            )
        if not (
            started_at
            <= provider_document.source.retrieved_at
            <= completed_at
        ):
            self._raise_invalid_response(
                request,
                "Tijori Benchmarking Financials retrieval time is outside the request",
            )
        try:
            document = self._normalize_benchmarking_financials_document(
                request,
                provider_document,
            )
            response = FundamentalBenchmarkingFinancialsResult(
                **self._response_metadata(
                    request,
                    started_at,
                    completed_at,
                ),
                issuer=request.issuer,
                status=FundamentalRetrievalStatus.COMPLETED,
                document=document,
            )
        except (ValidationError, InvalidOperation, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori Benchmarking Financials could not be normalized safely",
                **self._error_context(request),
            ) from exc
        return self._bind(request, response)

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

    def retrieve_growth_table_document(
        self,
        *,
        request: FundamentalFinancialsRequest,
        reporting_basis: Literal["consolidated", "standalone"],
    ) -> TijoriGrowthTableDocument:
        """Retrieve one validated structured Growth Table document.

        This provider-specific bridge is deliberately separate from the
        legacy evidence retrieval path until the provider-neutral structured
        document gateway is introduced.
        """

        if reporting_basis not in {"consolidated", "standalone"}:
            raise TypeError(
                "Growth Table request basis must be consolidated or standalone"
            )
        arguments = self._evidence_arguments(request)
        arguments.update(
            {
                "document_type": "growth_table",
                "reporting_basis": reporting_basis,
            }
        )
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="get_financials",
            arguments=arguments,
        )
        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriGrowthTablePayload,
            result,
            request=request,
        )
        document = payload.document
        issuer = request.issuer
        if (
            document.issuer.exchange != issuer.exchange
            or document.issuer.symbol != issuer.symbol
            or (
                issuer.provider_company_id is not None
                and document.issuer.provider_company_id
                != issuer.provider_company_id
            )
            or (
                issuer.provider_slug is not None
                and document.issuer.provider_slug != issuer.provider_slug
            )
        ):
            self._raise_invalid_response(
                request,
                "Tijori Growth Table belongs to a different issuer",
            )
        if not (
            started_at <= document.source.retrieved_at <= completed_at
        ):
            self._raise_invalid_response(
                request,
                "Tijori Growth Table retrieval time is outside the request",
            )
        return document

    def retrieve_structured_financial_document(
        self,
        *,
        request: FundamentalStructuredDocumentRequest,
    ) -> FundamentalStructuredDocumentResult:
        """Return one validated document without exposing Tijori contracts."""

        if request.document_type not in {
            FinancialDocumentType.GROWTH_TABLE,
            FinancialDocumentType.BALANCE_SHEET,
            FinancialDocumentType.PROFIT_AND_LOSS,
            FinancialDocumentType.CASH_FLOW,
            FinancialDocumentType.RATIOS,
            FinancialDocumentType.QUARTERLY_RESULTS,
        }:
            raise FundamentalGatewayConfigurationError(
                "Tijori structured retrieval does not support this document type",
                **self._error_context(request),
            )
        arguments = self._evidence_arguments(request)
        arguments.update(
            {
                "document_type": request.document_type.value,
                "reporting_basis": request.reporting_basis.value,
            }
        )
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="get_financials",
            arguments=arguments,
        )
        self._raise_terminal_tool_status(result.status, request=request)
        payload_model = {
            FinancialDocumentType.GROWTH_TABLE: TijoriGrowthTablePayload,
            FinancialDocumentType.BALANCE_SHEET: TijoriBalanceSheetPayload,
            FinancialDocumentType.PROFIT_AND_LOSS: TijoriProfitAndLossPayload,
            FinancialDocumentType.CASH_FLOW: TijoriCashFlowPayload,
            FinancialDocumentType.RATIOS: TijoriRatiosPayload,
            FinancialDocumentType.QUARTERLY_RESULTS: (
                TijoriQuarterlyResultsPayload
            ),
        }[request.document_type]
        payload = self._parse_payload(payload_model, result, request=request)
        provider_document = payload.document
        self._validate_structured_document_identity(request, provider_document)
        if provider_document.reporting_basis != request.reporting_basis.value:
            self._raise_invalid_response(
                request,
                "Tijori document reporting basis does not match the request",
            )
        if not (
            started_at <= provider_document.source.retrieved_at <= completed_at
        ):
            self._raise_invalid_response(
                request,
                "Tijori document retrieval time is outside the request",
            )
        try:
            if isinstance(provider_document, TijoriGrowthTableDocument):
                document = self._normalize_growth_table_document(
                    request,
                    provider_document,
                )
            elif isinstance(provider_document, TijoriProfitAndLossDocument):
                document = self._normalize_mixed_unit_statement_document(
                    request,
                    provider_document,
                )
            elif isinstance(provider_document, TijoriRatiosDocument):
                document = self._normalize_mixed_unit_statement_document(
                    request,
                    provider_document,
                )
            elif isinstance(
                provider_document,
                TijoriQuarterlyResultsDocument,
            ):
                document = self._normalize_mixed_unit_statement_document(
                    request,
                    provider_document,
                )
            elif isinstance(provider_document, TijoriCashFlowDocument):
                document = self._normalize_cash_flow_document(
                    request,
                    provider_document,
                )
            else:
                document = self._normalize_balance_sheet_document(
                    request,
                    provider_document,
                )
            response = FundamentalStructuredDocumentResult(
                **self._response_metadata(request, started_at, completed_at),
                issuer=request.issuer,
                document_type=request.document_type,
                reporting_basis=request.reporting_basis,
                status=FundamentalRetrievalStatus.COMPLETED,
                document=document,
            )
        except (ValidationError, InvalidOperation, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori structured document could not be normalized safely",
                **self._error_context(request),
            ) from exc
        return self._bind(request, response)

    def retrieve_financial_document_package(
        self,
        *,
        request: FundamentalFinancialDocumentPackageRequest,
    ) -> FundamentalFinancialDocumentPackageResult:
        """Return all financial documents from one validated provider snapshot."""

        arguments = self._evidence_arguments(request)
        arguments.update(
            {
                "document_type": "financial_package",
                "reporting_basis": "not_applicable",
            }
        )
        started_at, result, completed_at = self._call(
            request=request,
            tool_name="get_financials",
            arguments=arguments,
        )
        self._raise_terminal_tool_status(result.status, request=request)
        payload = self._parse_payload(
            TijoriFinancialDocumentPackagePayload,
            result,
            request=request,
        )
        provider_package = payload.package
        if not (
            started_at <= provider_package.source.retrieved_at <= completed_at
        ):
            self._raise_invalid_response(
                request,
                "Tijori financial package retrieval time is outside the request",
            )
        try:
            documents = tuple(
                self._normalize_package_document(request, provider_document)
                for provider_document in provider_package.documents
            )
            retrieved_at = provider_package.source.retrieved_at
            package = FundamentalFinancialDocumentPackage(
                connection=request.connection,
                issuer=request.issuer,
                source_location=provider_package.source.location,
                retrieved_at=retrieved_at,
                expires_at=retrieved_at + timedelta(days=10),
                documents=documents,
            )
            response = FundamentalFinancialDocumentPackageResult(
                **self._response_metadata(request, started_at, completed_at),
                issuer=request.issuer,
                status=FundamentalRetrievalStatus.COMPLETED,
                package=package,
            )
        except (ValidationError, InvalidOperation, ValueError) as exc:
            raise FundamentalResponseValidationError(
                "Tijori financial package could not be normalized safely",
                **self._error_context(request),
            ) from exc
        return self._bind(request, response)

    def _normalize_package_document(
        self,
        package_request: FundamentalFinancialDocumentPackageRequest,
        provider_document,
    ) -> StructuredFinancialDocument:
        document_type = FinancialDocumentType(provider_document.document_type)
        reporting_basis = FinancialReportingBasis(
            provider_document.reporting_basis
        )
        request = FundamentalStructuredDocumentRequest(
            request_id=package_request.request_id,
            operation_id=package_request.operation_id,
            connection=package_request.connection,
            requested_at=package_request.requested_at,
            issuer=package_request.issuer,
            as_of_date=package_request.as_of_date,
            document_type=document_type,
            reporting_basis=reporting_basis,
        )
        self._validate_structured_document_identity(request, provider_document)
        if isinstance(provider_document, TijoriGrowthTableDocument):
            return self._normalize_growth_table_document(request, provider_document)
        if isinstance(provider_document, TijoriProfitAndLossDocument):
            return self._normalize_mixed_unit_statement_document(
                request,
                provider_document,
            )
        if isinstance(provider_document, TijoriRatiosDocument):
            return self._normalize_mixed_unit_statement_document(
                request,
                provider_document,
            )
        if isinstance(provider_document, TijoriQuarterlyResultsDocument):
            return self._normalize_mixed_unit_statement_document(
                request,
                provider_document,
            )
        if isinstance(provider_document, TijoriCashFlowDocument):
            return self._normalize_cash_flow_document(request, provider_document)
        return self._normalize_balance_sheet_document(request, provider_document)

    def _validate_structured_document_identity(
        self,
        request: FundamentalStructuredDocumentRequest,
        document: (
            TijoriGrowthTableDocument
            | TijoriBalanceSheetDocument
            | TijoriCashFlowDocument
            | TijoriProfitAndLossDocument
            | TijoriRatiosDocument
            | TijoriQuarterlyResultsDocument
        ),
    ) -> None:
        issuer = request.issuer
        if (
            document.issuer.exchange != issuer.exchange
            or document.issuer.symbol != issuer.symbol
            or (
                issuer.provider_company_id is not None
                and document.issuer.provider_company_id
                != issuer.provider_company_id
            )
            or (
                issuer.provider_slug is not None
                and document.issuer.provider_slug != issuer.provider_slug
            )
        ):
            self._raise_invalid_response(
                request,
                "Tijori structured document belongs to a different issuer",
            )

    @staticmethod
    def _normalize_growth_table_document(
        request: FundamentalStructuredDocumentRequest,
        source: TijoriGrowthTableDocument,
    ) -> StructuredFinancialDocument:
        periods = tuple(
            FinancialDocumentPeriod(
                period_key=column.column_key,
                source_label=column.source_label,
                display_order=column.display_order,
            )
            for column in source.columns
        )
        rows = tuple(
            FinancialDocumentRow(
                row_key=row.row_key,
                original_label=row.original_label,
                parent_row_key=row.parent_row_key,
                depth=row.depth,
                row_kind=FinancialDocumentRowKind(row.row_kind),
                value_kind=FundamentalValueKind.PERCENTAGE,
                display_order=row.display_order,
                cells=tuple(
                    TijoriMcpAdapter._normalize_growth_table_cell(
                        cell,
                        source_unit=source.unit,
                    )
                    for cell in row.values
                ),
            )
            for row in source.rows
        )
        return StructuredFinancialDocument(
            document_id=(
                f"tijori.{request.issuer.exchange}.{request.issuer.symbol}."
                f"{request.document_type.value}.{request.reporting_basis.value}"
            ),
            connection=request.connection,
            issuer=request.issuer,
            document_type=request.document_type,
            reporting_basis=request.reporting_basis,
            currency=None,
            source_unit=source.unit,
            periods=periods,
            rows=rows,
            source_location=source.source.location,
            retrieved_at=source.source.retrieved_at,
            expires_at=source.source.retrieved_at + timedelta(days=10),
            all_sections_expanded=source.all_sections_expanded,
            validation_status=FundamentalValidationStatus.VALIDATED,
        )

    @staticmethod
    def _normalize_peer_comparison_document(
        request: FundamentalCompanyOverviewRequest,
        source: TijoriPeerComparisonDocument,
    ) -> StructuredPeerComparisonDocument:
        metrics = tuple(
            PeerComparisonMetric(
                metric_key=metric.metric_key,
                source_label=metric.source_label,
                standardized_label=metric.standardized_label,
                value_kind=FundamentalValueKind(metric.value_kind),
                source_unit=metric.source_unit,
                normalized_unit=metric.source_unit,
                currency=(
                    "INR" if metric.value_kind == "monetary" else None
                ),
                display_order=metric.display_order,
            )
            for metric in source.metrics
        )
        metrics_by_key = {metric.metric_key: metric for metric in metrics}
        peers = tuple(
            PeerComparisonCompany(
                peer_key=peer.peer_key,
                legal_name=peer.legal_name,
                provider_slug=peer.provider_slug,
                is_subject=peer.is_subject,
                display_order=peer.display_order,
                cells=tuple(
                    TijoriMcpAdapter._normalize_peer_comparison_cell(
                        cell.metric_key,
                        cell.source_value,
                        cell.availability_status,
                        metrics_by_key[cell.metric_key].source_unit,
                    )
                    for cell in peer.values
                ),
            )
            for peer in source.peers
        )
        return StructuredPeerComparisonDocument(
            document_id=(
                f"tijori.{request.issuer.exchange}.{request.issuer.symbol}."
                "peer_comparison.not_applicable"
            ),
            connection=request.connection,
            issuer=request.issuer,
            observation_date=source.observation_date,
            metrics=metrics,
            peers=peers,
            source_location=source.source.location,
            retrieved_at=source.source.retrieved_at,
            expires_at=source.source.retrieved_at + timedelta(days=10),
            validation_status=FundamentalValidationStatus.VALIDATED,
        )

    @staticmethod
    def _normalize_benchmarking_financials_document(
        request: FundamentalCompanyOverviewRequest,
        source: TijoriBenchmarkingFinancialsDocument,
    ) -> StructuredBenchmarkingFinancialsDocument:
        companies = tuple(
            BenchmarkingCompany(
                company_key=company.company_key,
                legal_name=company.legal_name,
                provider_slug=company.provider_slug,
                is_subject=company.is_subject,
                display_order=company.display_order,
            )
            for company in source.companies
        )
        rows = tuple(
            TijoriMcpAdapter._normalize_benchmarking_row(row)
            for row in source.rows
        )
        return StructuredBenchmarkingFinancialsDocument(
            document_id=(
                f"tijori.{request.issuer.exchange}.{request.issuer.symbol}."
                "benchmarking_financials.not_applicable"
            ),
            connection=request.connection,
            issuer=request.issuer,
            observation_date=source.observation_date,
            companies=companies,
            rows=rows,
            source_location=source.source.location,
            retrieved_at=source.source.retrieved_at,
            expires_at=source.source.retrieved_at + timedelta(days=10),
            all_rows_captured=source.all_rows_captured,
            validation_status=FundamentalValidationStatus.VALIDATED,
        )

    @staticmethod
    def _normalize_benchmarking_row(
        source: TijoriBenchmarkingRow,
    ) -> BenchmarkingRow:
        section = {
            "bch_op_metric": BenchmarkingSection.OPERATIONAL_METRICS,
            "bch_financial": BenchmarkingSection.FINANCIALS,
            "bch_shareholdings": BenchmarkingSection.SHAREHOLDINGS,
        }[source.provider_section]
        value_kind, source_unit, normalized_unit = (
            TijoriMcpAdapter._benchmarking_value_semantics(source)
        )
        return BenchmarkingRow(
            row_key=source.row_key,
            parent_row_key=source.parent_row_key,
            original_label=source.original_label,
            depth=source.depth,
            row_kind=source.row_kind,
            section=section,
            value_kind=value_kind,
            source_unit=source_unit,
            normalized_unit=normalized_unit,
            provider_hidden=source.provider_hidden,
            display_order=source.display_order,
            cells=tuple(
                TijoriMcpAdapter._normalize_benchmarking_cell(
                    cell,
                    value_kind=value_kind,
                )
                for cell in source.values
            ),
        )

    @staticmethod
    def _benchmarking_value_semantics(source) -> tuple[
        FundamentalValueKind,
        str,
        str,
    ]:
        if source.row_kind == "section":
            return (
                FundamentalValueKind.OTHER,
                "not applicable",
                "not applicable",
            )
        values = tuple(
            cell.source_value
            for cell in source.values
            if cell.source_value is not None
        )
        if any("%" in value for value in values):
            return FundamentalValueKind.PERCENTAGE, "percent", "percent"
        if any(
            "₹" in value or re.search(r"\s+crs?\.?\s*\)?$", value, re.I)
            for value in values
        ):
            return FundamentalValueKind.MONETARY, "INR crore", "INR crore"
        if any(value.casefold().endswith("x") for value in values):
            return FundamentalValueKind.RATIO, "ratio", "ratio"
        if any(
            value.casefold().endswith((" day", " days"))
            for value in values
        ):
            return FundamentalValueKind.OTHER, "days", "days"
        return FundamentalValueKind.OTHER, "provider scalar", "provider scalar"

    @staticmethod
    def _normalize_benchmarking_cell(
        source: TijoriBenchmarkingCell,
        *,
        value_kind: FundamentalValueKind,
    ) -> BenchmarkingCell:
        available = source.availability_status == "available"
        normalized_value = None
        if available:
            numeric = source.source_value.replace(",", "").replace("₹", "")
            numeric = numeric.replace("%", "")
            numeric = numeric.strip()
            accounting_negative = numeric.startswith("(") and numeric.endswith(")")
            if accounting_negative:
                numeric = numeric[1:-1].strip()
            numeric = re.sub(r"\s+crs?\.?$", "", numeric, flags=re.I).strip()
            for suffix in ("days", "day", "x"):
                if numeric.casefold().endswith(suffix):
                    numeric = numeric[: -len(suffix)].strip()
                    break
            numeric_parts = numeric.rsplit(maxsplit=1)
            if len(numeric_parts) == 2 and numeric_parts[1] == ".":
                numeric = numeric_parts[0]
            normalized_value = Decimal(numeric)
            if accounting_negative:
                normalized_value = -abs(normalized_value)
        return BenchmarkingCell(
            company_key=source.company_key,
            source_value=source.source_value,
            normalized_value=normalized_value,
            availability_status=(
                FundamentalAvailabilityStatus.AVAILABLE
                if available
                else FundamentalAvailabilityStatus.UNKNOWN
            ),
            is_best=source.is_best,
        )

    @staticmethod
    def _normalize_peer_comparison_cell(
        metric_key: str,
        source_value: str | None,
        availability_status: Literal["available", "unknown"],
        source_unit: str,
    ) -> PeerComparisonCell:
        available = availability_status == "available"
        normalized_value = None
        if available:
            if source_value is None:
                raise ValueError("available peer value is missing")
            numeric = source_value.replace(",", "").replace("₹", "").strip()
            suffix = {
                "INR crore": "cr",
                "percent": "%",
                "ratio": "x",
            }.get(source_unit)
            if suffix is not None and numeric.casefold().endswith(suffix):
                numeric = numeric[: -len(suffix)].strip()
            normalized_value = Decimal(numeric)
        return PeerComparisonCell(
            metric_key=metric_key,
            source_value=source_value,
            normalized_value=normalized_value,
            availability_status=(
                FundamentalAvailabilityStatus.AVAILABLE
                if available
                else FundamentalAvailabilityStatus.UNKNOWN
            ),
        )

    @staticmethod
    def _normalize_growth_table_cell(
        cell: TijoriFinancialDocumentCell,
        *,
        source_unit: str,
    ) -> FinancialDocumentCell:
        source_value = cell.source_value
        available = cell.availability_status == "available"
        normalized_value = None
        if available:
            normalized_value = Decimal(
                source_value.replace(",", "").removesuffix("%").strip()
            )
        return FinancialDocumentCell(
            period_key=cell.column_key,
            source_value=source_value,
            normalized_value=normalized_value,
            source_unit=source_unit if available else None,
            normalized_unit=source_unit if available else None,
            yoy_change=cell.yoy_change,
            percentage_of_parent=cell.percentage_of_parent,
            availability_status=(
                FundamentalAvailabilityStatus.AVAILABLE
                if available
                else FundamentalAvailabilityStatus.UNKNOWN
            ),
        )

    @staticmethod
    def _normalize_balance_sheet_document(
        request: FundamentalStructuredDocumentRequest,
        source: TijoriBalanceSheetDocument,
    ) -> StructuredFinancialDocument:
        periods = tuple(
            FinancialDocumentPeriod(
                period_key=period.period_key,
                source_label=period.source_label,
                display_order=period.display_order,
            )
            for period in source.periods
        )
        rows = tuple(
            FinancialDocumentRow(
                row_key=row.row_key,
                original_label=row.original_label,
                parent_row_key=row.parent_row_key,
                depth=row.depth,
                row_kind=FinancialDocumentRowKind(row.row_kind),
                value_kind=FundamentalValueKind.MONETARY,
                display_order=row.display_order,
                cells=tuple(
                    TijoriMcpAdapter._normalize_balance_sheet_cell(
                        cell,
                        source_unit=source.source_unit,
                        normalized_unit=source.normalized_unit,
                    )
                    for cell in row.values
                ),
            )
            for row in source.rows
        )
        return StructuredFinancialDocument(
            document_id=(
                f"tijori.{request.issuer.exchange}.{request.issuer.symbol}."
                f"{request.document_type.value}.{request.reporting_basis.value}"
            ),
            connection=request.connection,
            issuer=request.issuer,
            document_type=request.document_type,
            reporting_basis=request.reporting_basis,
            currency="INR",
            source_unit=source.source_unit,
            skipped_period_labels=source.skipped_report_dates,
            periods=periods,
            rows=rows,
            source_location=source.source.location,
            retrieved_at=source.source.retrieved_at,
            expires_at=source.source.retrieved_at + timedelta(days=10),
            all_sections_expanded=source.all_sections_expanded,
            validation_status=FundamentalValidationStatus.VALIDATED,
        )

    @staticmethod
    def _normalize_balance_sheet_cell(
        cell: TijoriFinancialStatementCell,
        *,
        source_unit: str,
        normalized_unit: str,
    ) -> FinancialDocumentCell:
        available = cell.availability_status == "available"
        normalized_value = (
            Decimal(cell.source_value.replace(",", "").strip())
            if available and cell.source_value is not None
            else None
        )
        return FinancialDocumentCell(
            period_key=cell.period_key,
            source_value=cell.source_value,
            normalized_value=normalized_value,
            source_unit=source_unit if available else None,
            normalized_unit=normalized_unit if available else None,
            yoy_change=cell.yoy_change,
            percentage_of_parent=cell.percentage_of_parent,
            availability_status=(
                FundamentalAvailabilityStatus.AVAILABLE
                if available
                else FundamentalAvailabilityStatus.UNKNOWN
            ),
        )

    @staticmethod
    def _normalize_cash_flow_document(
        request: FundamentalStructuredDocumentRequest,
        source: TijoriCashFlowDocument,
    ) -> StructuredFinancialDocument:
        return TijoriMcpAdapter._normalize_balance_sheet_document(
            request,
            source,
        )

    @staticmethod
    def _normalize_mixed_unit_statement_document(
        request: FundamentalStructuredDocumentRequest,
        source: (
            TijoriProfitAndLossDocument
            | TijoriRatiosDocument
            | TijoriQuarterlyResultsDocument
        ),
    ) -> StructuredFinancialDocument:
        periods = tuple(
            FinancialDocumentPeriod(
                period_key=period.period_key,
                source_label=period.source_label,
                display_order=period.display_order,
            )
            for period in source.periods
        )
        rows = tuple(
            FinancialDocumentRow(
                row_key=row.row_key,
                original_label=row.original_label,
                parent_row_key=row.parent_row_key,
                depth=row.depth,
                row_kind=FinancialDocumentRowKind(row.row_kind),
                value_kind=FundamentalValueKind(row.value_kind),
                display_order=row.display_order,
                cells=tuple(
                    TijoriMcpAdapter._normalize_balance_sheet_cell(
                        cell,
                        source_unit=row.source_unit,
                        normalized_unit=row.normalized_unit,
                    )
                    for cell in row.values
                ),
            )
            for row in source.rows
        )
        return StructuredFinancialDocument(
            document_id=(
                f"tijori.{request.issuer.exchange}.{request.issuer.symbol}."
                f"{request.document_type.value}.{request.reporting_basis.value}"
            ),
            connection=request.connection,
            issuer=request.issuer,
            document_type=request.document_type,
            reporting_basis=request.reporting_basis,
            currency="INR",
            source_unit=source.source_unit,
            skipped_period_labels=source.skipped_report_dates,
            periods=periods,
            rows=rows,
            source_location=source.source.location,
            retrieved_at=source.source.retrieved_at,
            expires_at=source.source.retrieved_at + timedelta(days=10),
            all_sections_expanded=source.all_sections_expanded,
            validation_status=FundamentalValidationStatus.VALIDATED,
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

    @staticmethod
    def _filing_failure_status(
        status: TijoriToolStatus,
    ) -> FundamentalRetrievalStatus | None:
        return {
            TijoriToolStatus.NOT_FOUND: FundamentalRetrievalStatus.NOT_FOUND,
            TijoriToolStatus.NOT_ENTITLED: (
                FundamentalRetrievalStatus.NOT_ENTITLED
            ),
            TijoriToolStatus.PAYWALLED: FundamentalRetrievalStatus.PAYWALLED,
            TijoriToolStatus.UNAVAILABLE: (
                FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE
            ),
        }.get(status)

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
                "legal_name": issuer.legal_name,
                "isin": issuer.isin,
                "provider_company_id": issuer.provider_company_id,
                "provider_slug": issuer.provider_slug,
            },
            "as_of_date": (
                request.as_of_date.isoformat()  # type: ignore[attr-defined]
                if request.as_of_date is not None  # type: ignore[attr-defined]
                else None
            ),
        }

    @staticmethod
    def _filing_issuer_arguments(
        issuer: FundamentalIssuerIdentity,
    ) -> dict[str, object]:
        if (
            issuer.provider_company_id is None
            or issuer.provider_slug is None
        ):
            raise ValueError(
                "filing operations require a resolved provider issuer"
            )
        return {
            "exchange": issuer.exchange,
            "symbol": issuer.symbol,
            "legal_name": issuer.legal_name,
            "isin": issuer.isin,
            "provider_company_id": issuer.provider_company_id,
            "provider_slug": issuer.provider_slug,
        }

    def _validate_filing_discovery_identity(
        self,
        request: FundamentalFilingDiscoveryRequest,
        payload: TijoriCompanyFilingsPayload,
    ) -> None:
        issuer = request.issuer
        if (
            payload.issuer.exchange != issuer.exchange
            or payload.issuer.symbol != issuer.symbol
            or payload.issuer.legal_name != issuer.legal_name
            or payload.issuer.isin != issuer.isin
            or payload.issuer.provider_company_id
            != issuer.provider_company_id
            or payload.issuer.provider_slug != issuer.provider_slug
        ):
            self._raise_invalid_response(
                request,
                "Tijori filing discovery belongs to a different issuer",
            )

    @staticmethod
    def _normalize_filing_reference(
        request: FundamentalFilingDiscoveryRequest,
        source: TijoriCompanyFilingReference,
    ) -> FundamentalFilingReference:
        return FundamentalFilingReference(
            filing_reference_id=source.filing_reference_id,
            connection=request.connection,
            issuer=request.issuer,
            provider=request.connection.provider,
            source_discovery_fingerprint=source.discovery_fingerprint,
            category=FundamentalFilingCategory(source.category),
            source_class=FundamentalFilingSourceClass(source.source_class),
            period=FundamentalFilingPeriod(
                state=FundamentalFilingPeriodState(source.period.state),
                period_type=FundamentalFilingPeriodType(
                    source.period.period_type
                ),
                source_label=source.period.source_label,
                fiscal_year=source.period.fiscal_year,
                fiscal_quarter=source.period.fiscal_quarter,
                period_start=source.period.period_start,
                period_end=source.period.period_end,
            ),
            title=source.title,
            provider_label=source.provider_label,
            original_filename=source.original_filename,
            source_page_url=source.source_page_url,
            artifact_url=source.artifact_url,
            provider_published_at=source.provider_published_at,
            available_at=source.available_at,
            discovered_at=source.discovered_at,
        )

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
