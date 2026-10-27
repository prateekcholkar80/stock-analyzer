import threading
import time
import unittest
from datetime import datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.exceptions import (
    FundamentalProviderUnavailableError,
    IntentRecognitionError,
    LLMError,
    MarketDataError,
)
from app.analytics.fundamental_checks import FinancialHealthSectorProfile
from app.analytics.fundamental_sector_profile import (
    FundamentalSectorEvidence,
    FundamentalSectorSourceAuthority,
    FundamentalSectorTaxonomy,
)
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
    FundamentalPeerComparisonResult,
)
from app.models.browser_operations import (
    BrowserAgentExecutionStatus,
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    BrowserFundamentalPackageReference,
    BrowserStructuredDocumentReference,
    WorkflowEventReplayCursor,
)
from app.models.fundamental_filings import (
    FundamentalFilingDiscoverySnapshot,
    FundamentalFilingFailure,
    FundamentalFilingFailureCategory,
    FundamentalFilingFailureStage,
    FundamentalFilingPackage,
    FundamentalFilingPackageManifest,
)
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.models.fundamental_analysis import FundamentalAnalysis
from app.models.conversation import InputChannel
from app.models.interaction import (
    JarvisSwingAnalysisResponse,
    SwingAnalysisCommand,
)
from app.models.llm import JarvisLLMFailureResponse, LLMFailureCode
from app.models.fundamental_storage import FundamentalSnapshotQuery
from app.models.fundamentals import FundamentalValidationStatus
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.services.fundamental_cache_policy import FundamentalCachePolicy
from app.services.fundamental_evidence import (
    FundamentalEvidenceLoadResult,
    FundamentalEvidenceSource,
)
from app.storage.adapters.fundamental_in_memory import (
    InMemoryFundamentalSnapshotRepository,
)
from app.storage.adapters.financial_document_in_memory import (
    InMemoryStructuredFinancialDocumentRepository,
)
from app.storage.adapters.benchmarking_financials_in_memory import (
    InMemoryBenchmarkingFinancialsRepository,
)
from app.storage.adapters.peer_comparison_in_memory import (
    InMemoryPeerComparisonRepository,
)
from app.storage.adapters.fundamental_package_in_memory import (
    InMemoryFundamentalPackageRepository,
)
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepositoryScope,
)
from app.services.fundamental_evidence import FundamentalEvidenceCoordinator
from app.services.fundamental_filing_evidence import (
    FundamentalFilingEvidenceCoordinationResult,
    FundamentalFilingEvidenceCoordinationStatus,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepositoryScope,
)
from app.workflow.agent_outcomes import build_agent_execution_outcomes
from app.workflow.browser_runner import (
    AsyncBrowserOperationRunner,
    BrowserOperationHandledFailure,
    InMemoryBrowserOperationResultStore,
    JarvisBrowserOperationHandler,
)
from app.workflow.operations import InMemoryBrowserOperationRegistry
from tests.unit.test_jarvis_conversation import (
    RecordingJudgeFollowUpExecutor,
    RecordingPresenter,
    _completed_response,
    _multi_timeframe_response,
)
from tests.unit.test_fundamental_in_memory_repository import (
    MutableClock,
    build_entry,
)
from tests.unit.test_fundamental_evidence_coordinator import (
    FakeFundamentalGateway,
)
from tests.unit.test_fundamental_gateway import build_connection
from tests.unit.test_benchmarking_financials_gateway import (
    document as benchmarking_document,
    result_values as benchmarking_result_values,
)
from tests.unit.test_peer_comparison_gateway import (
    document as peer_document,
    result_values as peer_result_values,
)


IST = ZoneInfo("Asia/Kolkata")


class _BrowserFundamentalGateway(FakeFundamentalGateway):
    def retrieve_peer_comparison(self, *, request):
        self.calls.append("peer_comparison")
        retrieved_at = request.requested_at + timedelta(seconds=1)
        template = peer_document()
        subject = template.peers[0]
        peers = (
            subject.model_copy(
                update={
                    "peer_key": (
                        "peer_" + request.issuer.provider_slug.replace("-", "_")
                    ),
                    "legal_name": request.issuer.legal_name,
                    "provider_slug": request.issuer.provider_slug,
                }
            ),
            *template.peers[1:],
        )
        result_document = peer_document(
            connection=request.connection,
            issuer=request.issuer,
            observation_date=(
                request.as_of_date or request.requested_at.date()
            ),
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + timedelta(days=10),
            peers=peers,
        )
        values = peer_result_values(request)
        values.update(
            {
                "connection": request.connection,
                "requested_at": request.requested_at,
                "started_at": request.requested_at,
                "completed_at": request.requested_at + timedelta(seconds=2),
                "issuer": request.issuer,
                "document": result_document,
            }
        )
        return FundamentalPeerComparisonResult(**values)

    def retrieve_benchmarking_financials(self, *, request):
        self.calls.append("benchmarking_financials")
        retrieved_at = request.requested_at + timedelta(seconds=1)
        template = benchmarking_document()
        subject = template.companies[0]
        subject_key = (
            "company_"
            + request.issuer.provider_slug.replace("-", "_")
        )
        companies = (
            subject.model_copy(
                update={
                    "company_key": subject_key,
                    "legal_name": request.issuer.legal_name,
                    "provider_slug": request.issuer.provider_slug,
                }
            ),
            *template.companies[1:],
        )
        rows = tuple(
            row.model_copy(
                update={
                    "cells": tuple(
                        cell.model_copy(
                            update={"company_key": subject_key}
                        )
                        if cell.company_key == subject.company_key
                        else cell
                        for cell in row.cells
                    )
                }
            )
            for row in template.rows
        )
        result_document = benchmarking_document(
            connection=request.connection,
            issuer=request.issuer,
            observation_date=(
                request.as_of_date or request.requested_at.date()
            ),
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + timedelta(days=10),
            companies=companies,
            rows=rows,
        )
        values = benchmarking_result_values(request)
        values.update(
            {
                "connection": request.connection,
                "requested_at": request.requested_at,
                "started_at": request.requested_at,
                "completed_at": request.requested_at
                + timedelta(seconds=2),
                "issuer": request.issuer,
                "document": result_document,
            }
        )
        return FundamentalBenchmarkingFinancialsResult(**values)


class _RecordingStructuredFundamentalGateway(_BrowserFundamentalGateway):
    def __init__(self):
        super().__init__()
        self.structured_scenarios = []

    def retrieve_structured_financial_document(self, *, request):
        self.structured_scenarios.append(
            (request.document_type.value, request.reporting_basis.value)
        )
        return super().retrieve_structured_financial_document(request=request)

    def retrieve_financial_document_package(self, *, request):
        result = super().retrieve_financial_document_package(request=request)
        if result.package is not None:
            self.structured_scenarios.extend(
                (
                    document.document_type.value,
                    document.reporting_basis.value,
                )
                for document in result.package.documents
            )
        return result


class _FailingCashFlowGateway(_RecordingStructuredFundamentalGateway):
    def retrieve_financial_document_package(self, *, request):
        raise FundamentalProviderUnavailableError(
            "raw provider details must not cross the boundary",
            capability=request.capability.value,
        )


class _RecordingFundamentalAnalyst:
    def __init__(self):
        self.calls = []

    def analyze(self, *, evidence, intelligence, as_of):
        self.calls.append((evidence, intelligence, as_of))
        return FundamentalAnalysis(
            analysis_id="fundamental-analysis-1",
            evidence_coverage_and_freshness="All cached documents are current.",
            financial_strengths=("Revenue growth is positive.",),
            financial_risks_and_conflicts=(),
            preliminary_financial_health="Preliminary health is stable.",
            confidence_percentage=60.0,
            valuation_observations=(),
            missing_evidence_and_limitations=("Annual report unavailable.",),
            document_citations=(evidence[0].reference_id,),
            runtime_skill_id="jarvis-fundamental-analyst",
            runtime_skill_version="1.0.0",
            runtime_skill_prompt_sha256="a" * 64,
            model_id="test-model",
            generated_at=as_of,
        )


class _InvalidFundamentalAnalyst:
    def analyze(self, *, evidence, intelligence, as_of):
        return object()


class _FailingFundamentalAnalyst:
    def analyze(self, *, evidence, intelligence, as_of):
        raise LLMError(
            "private provider credential detail",
            role="fundamental_analyst",
            retryable=False,
        )


class _RecordingFilingEvidenceCoordinator:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def coordinate(self, request, *, refresh_requested=False):
        self.calls.append((request, refresh_requested))
        completed_at = request.requested_at + timedelta(seconds=1)
        scope = FundamentalFilingRepositoryScope(
            connection=request.connection,
            issuer=request.issuer,
        )
        if self.fail:
            return FundamentalFilingEvidenceCoordinationResult(
                status=(
                    FundamentalFilingEvidenceCoordinationStatus.
                    REFRESH_FAILED_NO_ACTIVE
                ),
                request_fingerprint=request.request_fingerprint,
                scope=scope,
                refresh_requested=refresh_requested,
                provider_called=True,
                started_at=request.requested_at,
                completed_at=completed_at,
                failure=FundamentalFilingFailure(
                    operation_id=request.operation_id,
                    connection=request.connection,
                    issuer=request.issuer,
                    stage=FundamentalFilingFailureStage.DISCOVERY,
                    category=(
                        FundamentalFilingFailureCategory.PROVIDER_UNAVAILABLE
                    ),
                    retryable=True,
                    occurred_at=completed_at,
                    sanitized_message=(
                        "Corporate filing discovery is temporarily unavailable."
                    ),
                ),
            )

        discovery = FundamentalFilingDiscoverySnapshot(
            discovery_id=(
                f"filing.discovery.{request.issuer.symbol.casefold()}"
            ),
            connection=request.connection,
            issuer=request.issuer,
            categories=request.categories,
            from_fiscal_year=request.from_fiscal_year,
            through_date=request.through_date,
            max_documents=request.max_documents,
            discovered_at=completed_at,
            references=(),
            validation_status=FundamentalValidationStatus.VALIDATED,
        )
        manifest = FundamentalFilingPackageManifest(
            operation_id=request.operation_id,
            connection=request.connection,
            issuer=request.issuer,
            requested_at=request.requested_at,
            committed_at=completed_at,
            discovery_fingerprint=discovery.discovery_fingerprint,
            artifact_ids=(),
            recheck_after=completed_at + timedelta(days=10),
        )
        package = FundamentalFilingPackage(
            manifest=manifest,
            discovery=discovery,
            artifacts=(),
        )
        return FundamentalFilingEvidenceCoordinationResult(
            status=(
                FundamentalFilingEvidenceCoordinationStatus.PACKAGE_PUBLISHED
            ),
            request_fingerprint=request.request_fingerprint,
            scope=scope,
            refresh_requested=refresh_requested,
            provider_called=True,
            started_at=request.requested_at,
            completed_at=completed_at,
            selected_package=package,
            evidence_records=(),
        )


class _TcsSectorEvidenceResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, *, issuer, symbol, as_of):
        self.calls.append((issuer, symbol, as_of))
        return (
            FundamentalSectorEvidence(
                issuer=issuer,
                taxonomy=FundamentalSectorTaxonomy.NSE_SECTOR_INDUSTRY,
                source_authority=(
                    FundamentalSectorSourceAuthority.EXCHANGE_OFFICIAL
                ),
                source_reference_id="nse-sector-master:TCS",
                source_name="NSE equity sector master",
                source_location="https://www.nseindia.com/market-data",
                source_record_fingerprint=sha256(
                    b"TCS|IT|Software"
                ).hexdigest(),
                sector="IT",
                industry="Software",
                retrieved_at=as_of - timedelta(days=1),
                expires_at=as_of + timedelta(days=30),
            ),
        )


def _now():
    return datetime.now(IST)


class _AdvancingClock:
    def __init__(self):
        self.now = _now()

    def __call__(self):
        self.now += timedelta(seconds=3)
        return self.now


def _completed_response_with_series():
    response = _completed_response()
    result = response.result.model_copy(
        update={
            "fetch": SimpleNamespace(
                stored=SimpleNamespace(
                    series=SimpleNamespace(
                        exchange="NSE",
                        symbol="TCS-EQ",
                        candles=(),
                    )
                )
            )
        }
    )
    return response.model_copy(update={"result": result})


def _request(
    operation_id="operation-1",
    *,
    kind=BrowserOperationKind.SWING_ANALYSIS,
    idempotency_key="request-1",
    message="Analyze Reliance for me",
):
    return BrowserOperationRequest(
        operation_id=operation_id,
        session_id="session-1",
        idempotency_key=idempotency_key,
        kind=kind,
        input_channel=InputChannel.TEXT,
        message=message,
        requested_at=_now(),
    )


def _wait_for_terminal(runner, operation_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = runner.get_operation(operation_id)
        if snapshot is not None and snapshot.status.terminal:
            return snapshot
        time.sleep(0.005)
    raise AssertionError("browser operation did not become terminal")


class _ResearchExecutor:
    def __init__(self, response=None, *, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def execute(
        self,
        text,
        *,
        operation_id=None,
        event_emitter=None,
        manage_terminal_events=True,
        instrument_resolved_callback=None,
    ):
        self.calls.append(
            (text, operation_id, event_emitter, manage_terminal_events)
        )
        if instrument_resolved_callback is not None:
            instrument_resolved_callback(
                SwingAnalysisCommand(
                    exchange="NSE",
                    symbol_token="11536",
                    symbol="TCS-EQ",
                    interval="ONE_HOUR",
                )
            )
        if self.failure is not None:
            raise self.failure
        return self.response


class _SimpleHandler:
    def __init__(self, *, failure=None, blocking=False):
        self.failure = failure
        self.blocking = blocking
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request, *, event_emitter, cancellation_token):
        self.calls += 1
        self.started.set()
        if self.blocking:
            self.release.wait(timeout=2)
        if self.failure is not None:
            raise self.failure
        event_emitter.emit(
            WorkflowStage.REQUEST_RECEIVED,
            WorkflowEventState.COMPLETED,
        )
        return BrowserOperationOutput.model_construct(
            operation_id=request.operation_id,
            session_id=request.session_id,
            kind=request.kind,
            completed_at=_now(),
        )


class BrowserOperationOutputTests(unittest.TestCase):
    @staticmethod
    def _cached_fundamental_result():
        stored = build_entry()
        clock = MutableClock(stored.stored_at)
        repository = InMemoryFundamentalSnapshotRepository(clock=clock)
        repository.save_fundamental_snapshot(stored)
        decision = FundamentalCachePolicy(repository, clock=clock).decide(
            stored.request
        )
        return FundamentalEvidenceLoadResult(
            source=FundamentalEvidenceSource.CACHE,
            decision=decision,
            retrieval=stored.retrieval,
            stored_snapshot=stored,
        )

    def test_requires_kind_specific_payload(self):
        response = _completed_response()
        explanation = RecordingPresenter().explain(
            response.result,
            user_name="Prateek",
        )
        output = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=_now(),
            research_response=response,
            research_explanation=explanation,
        )

        self.assertIs(output.research_response, response)
        with self.assertRaisesRegex(ValueError, "either an explanation"):
            BrowserOperationOutput(
                operation_id="operation-1",
                session_id="session-1",
                kind=BrowserOperationKind.SWING_ANALYSIS,
                completed_at=_now(),
                research_response=response,
                research_explanation=explanation,
                presentation_failure=BrowserOperationFailure(
                    code="presentation.unavailable",
                    message="Unavailable",
                ),
            )

    def test_decision_failure_retains_completed_fundamental_package(self):
        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-1",
            failure=JarvisLLMFailureResponse(
                code=LLMFailureCode.AUTHENTICATION,
                title="The debate could not start",
                display_message="The debate provider rejected its credential.",
                spoken_message="The debate could not start.",
                recovery_action="Update the configured LLM credential.",
                retryable=False,
                operation_id="operation-1",
            ),
        )
        completed_at = _now()
        package_reference = BrowserFundamentalPackageReference(
            package_id="fundamental_package:" + "a" * 64,
            manifest_fingerprint="a" * 64,
            expires_at=completed_at + timedelta(days=10),
        )

        output = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=completed_at,
            research_response=response,
            decision_failure=BrowserOperationFailure(
                code="llm.authentication",
                message="The debate provider rejected its credential.",
                retryable=False,
            ),
            fundamental_package_reference=package_reference,
        )

        self.assertEqual(output.decision_failure.code, "llm.authentication")
        self.assertEqual(
            output.fundamental_package_reference,
            package_reference,
        )

        with self.assertRaisesRegex(
            ValueError,
            "decision failure must match",
        ):
            BrowserOperationOutput(
                operation_id="operation-1",
                session_id="session-1",
                kind=BrowserOperationKind.SWING_ANALYSIS,
                completed_at=completed_at,
                research_response=response,
                decision_failure=BrowserOperationFailure(
                    code="llm.provider_unavailable",
                    message=(
                        "The debate provider rejected its credential."
                    ),
                    retryable=False,
                ),
                fundamental_package_reference=package_reference,
            )

    def test_swing_output_retains_unique_fundamental_evidence(self):
        response = _completed_response()
        explanation = RecordingPresenter().explain(
            response.result,
            user_name="Prateek",
        )
        fundamental = self._cached_fundamental_result()

        output = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=_now(),
            research_response=response,
            research_explanation=explanation,
            fundamental_evidence=(fundamental,),
        )

        self.assertEqual(output.fundamental_evidence, (fundamental,))
        with self.assertRaisesRegex(ValueError, "capabilities must be unique"):
            BrowserOperationOutput(
                operation_id="operation-1",
                session_id="session-1",
                kind=BrowserOperationKind.SWING_ANALYSIS,
                completed_at=_now(),
                research_response=response,
                research_explanation=explanation,
                fundamental_evidence=(fundamental, fundamental),
            )

    def test_fundamental_package_reference_is_bound_and_unexpired(self):
        response = _completed_response()
        explanation = RecordingPresenter().explain(
            response.result,
            user_name="Prateek",
        )
        completed_at = _now()
        reference = BrowserFundamentalPackageReference(
            package_id="fundamental_package:" + "a" * 64,
            manifest_fingerprint="a" * 64,
            expires_at=completed_at + timedelta(days=10),
        )
        output = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=completed_at,
            research_response=response,
            research_explanation=explanation,
            fundamental_package_reference=reference,
        )

        self.assertEqual(output.fundamental_package_reference, reference)
        with self.assertRaisesRegex(ValueError, "manifest fingerprint"):
            BrowserFundamentalPackageReference(
                package_id="fundamental_package:" + "b" * 64,
                manifest_fingerprint="a" * 64,
                expires_at=completed_at + timedelta(days=10),
            )
        with self.assertRaisesRegex(ValueError, "expired fundamental package"):
            BrowserOperationOutput(
                operation_id="operation-1",
                session_id="session-1",
                kind=BrowserOperationKind.SWING_ANALYSIS,
                completed_at=completed_at,
                research_response=response,
                research_explanation=explanation,
                fundamental_package_reference=reference.model_copy(
                    update={"expires_at": completed_at}
                ),
            )

    @staticmethod
    def _structured_reference(
        *,
        document_type=FinancialDocumentType.BALANCE_SHEET,
        reporting_basis=FinancialReportingBasis.CONSOLIDATED,
        cache_entry_id="financial_document:aaaaaaaa",
    ):
        now = _now()
        return BrowserStructuredDocumentReference(
            cache_entry_id=cache_entry_id,
            document_id=(
                f"tijori.NSE.TCS.{document_type.value}."
                f"{reporting_basis.value}"
            ),
            document_type=document_type,
            reporting_basis=reporting_basis,
            exchange="nse",
            symbol="tcs",
            source=FundamentalEvidenceSource.CACHE,
            document_fingerprint="a" * 64,
            retrieved_at=now,
            stored_at=now + timedelta(seconds=1),
            expires_at=now + timedelta(days=10),
            all_sections_expanded=True,
        )

    def test_swing_output_retains_unique_structured_document_references(self):
        response = _completed_response()
        explanation = RecordingPresenter().explain(
            response.result,
            user_name="Prateek",
        )
        reference = self._structured_reference()
        output = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=_now(),
            research_response=response,
            research_explanation=explanation,
            structured_document_references=(reference,),
        )

        self.assertEqual(
            output.structured_document_references,
            (reference,),
        )
        self.assertEqual(reference.exchange, "NSE")
        self.assertEqual(reference.symbol, "TCS")

        for duplicate in (
            reference.model_copy(
                update={"cache_entry_id": "financial_document:bbbbbbbb"}
            ),
            reference.model_copy(
                update={
                    "document_type": FinancialDocumentType.CASH_FLOW,
                    "cache_entry_id": reference.cache_entry_id,
                }
            ),
        ):
            with self.subTest(duplicate=duplicate):
                with self.assertRaisesRegex(ValueError, "must be unique"):
                    BrowserOperationOutput(
                        operation_id="operation-1",
                        session_id="session-1",
                        kind=BrowserOperationKind.SWING_ANALYSIS,
                        completed_at=_now(),
                        research_response=response,
                        research_explanation=explanation,
                        structured_document_references=(reference, duplicate),
                    )

    @staticmethod
    def _fundamental_analysis(*, citations):
        return FundamentalAnalysis(
            analysis_id="fundamental-analysis-1",
            evidence_coverage_and_freshness="Current cached financial data.",
            financial_strengths=("Revenue growth is positive.",),
            financial_risks_and_conflicts=(),
            preliminary_financial_health="Preliminary health is stable.",
            confidence_percentage=60.0,
            valuation_observations=(),
            missing_evidence_and_limitations=("Annual report unavailable.",),
            document_citations=citations,
            runtime_skill_id="jarvis-fundamental-analyst",
            runtime_skill_version="1.0.0",
            runtime_skill_prompt_sha256="a" * 64,
            model_id="test-model",
            generated_at=_now(),
        )

    def test_swing_output_binds_fundamental_analysis_to_exposed_reference(self):
        response = _completed_response()
        explanation = RecordingPresenter().explain(
            response.result,
            user_name="Prateek",
        )
        reference = self._structured_reference()
        analysis = self._fundamental_analysis(
            citations=(reference.cache_entry_id,)
        )

        output = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=_now(),
            research_response=response,
            research_explanation=explanation,
            structured_document_references=(reference,),
            fundamental_analysis=analysis,
        )

        self.assertIs(output.fundamental_analysis, analysis)

        invented = analysis.model_copy(
            update={"document_citations": ("financial_document:invented",)}
        )
        with self.assertRaisesRegex(ValueError, "only exposed cached"):
            BrowserOperationOutput(
                operation_id="operation-1",
                session_id="session-1",
                kind=BrowserOperationKind.SWING_ANALYSIS,
                completed_at=_now(),
                research_response=response,
                research_explanation=explanation,
                structured_document_references=(reference,),
                fundamental_analysis=invented,
            )


class JarvisBrowserOperationHandlerTests(unittest.TestCase):
    def test_technical_only_analysis_never_invokes_fundamental_executor(self):
        gateway = _BrowserFundamentalGateway()
        repository = InMemoryFundamentalSnapshotRepository()
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=repository,
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository()
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository()
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(),
            package_repository=InMemoryFundamentalPackageRepository(),
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        output = handler.execute(
            _request(),
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(gateway.calls, [])
        self.assertEqual(output.fundamental_evidence, ())

    def test_filing_evidence_runs_after_verified_issuer_without_browser_payload(self):
        clock = _AdvancingClock()
        gateway = _RecordingStructuredFundamentalGateway()
        filing_coordinator = _RecordingFilingEvidenceCoordinator()
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=InMemoryFundamentalSnapshotRepository(clock=clock),
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            package_repository=InMemoryFundamentalPackageRepository(
                clock=clock
            ),
            clock=clock,
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response_with_series()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            fundamental_filing_evidence_executor=filing_coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
            clock=clock,
        )
        from app.workflow.events import (
            InMemoryWorkflowEventSink,
            WorkflowEventEmitter,
        )
        from app.workflow.browser_runner import BrowserCancellationToken

        request = _request().model_copy(
            update={
                "fundamentals_requested": True,
                "refresh_requested": True,
            }
        )
        sink = InMemoryWorkflowEventSink()
        output = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter("operation-1", sink),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(len(filing_coordinator.calls), 1)
        filing_request, refresh_requested = filing_coordinator.calls[0]
        self.assertTrue(refresh_requested)
        self.assertEqual(filing_request.operation_id, request.operation_id)
        self.assertEqual(filing_request.issuer.symbol, "TCS")
        self.assertEqual(filing_request.issuer.provider_company_id, "1364")
        self.assertEqual(filing_request.through_date, filing_request.requested_at.date())
        self.assertEqual(len(filing_request.categories), 2)
        filing_events = [
            event
            for event in sink.events
            if event.activity.participant_id
            == "fundamentals.corporate_filings_analyst"
        ]
        self.assertEqual(
            [event.state for event in filing_events],
            [WorkflowEventState.STARTED, WorkflowEventState.COMPLETED],
        )
        browser_json = repr(output)
        self.assertNotIn("safe_relative_path", browser_json)
        self.assertNotIn("artifact_url", browser_json)
        self.assertNotIn("filing_artifact", browser_json)

    def test_filing_failure_is_an_independent_agent_outcome(self):
        clock = _AdvancingClock()
        gateway = _RecordingStructuredFundamentalGateway()
        filing_coordinator = _RecordingFilingEvidenceCoordinator(fail=True)
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=InMemoryFundamentalSnapshotRepository(clock=clock),
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            package_repository=InMemoryFundamentalPackageRepository(
                clock=clock
            ),
            clock=clock,
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response_with_series()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            fundamental_filing_evidence_executor=filing_coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
            clock=clock,
        )
        from app.workflow.events import (
            InMemoryWorkflowEventSink,
            WorkflowEventEmitter,
        )
        from app.workflow.browser_runner import BrowserCancellationToken

        request = _request().model_copy(
            update={
                "fundamentals_requested": True,
                "refresh_requested": True,
            }
        )
        sink = InMemoryWorkflowEventSink()
        output = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter("operation-1", sink),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertIsNone(output.fundamental_failure)
        self.assertEqual(len(output.fundamental_evidence), 3)
        snapshot = BrowserOperationSnapshot(
            request=request,
            status=BrowserOperationStatus.COMPLETED,
            updated_at=output.completed_at,
            last_event_sequence=len(sink.events),
            result_available=True,
        )
        outcomes = {
            outcome.participant_id: outcome
            for outcome in build_agent_execution_outcomes(
                snapshot,
                sink.events,
                output,
            )
        }
        filing_outcome = outcomes["fundamentals.corporate_filings_analyst"]
        self.assertIs(
            filing_outcome.status,
            BrowserAgentExecutionStatus.FAILED,
        )
        self.assertEqual(
            filing_outcome.failure.message,
            "Corporate filing discovery is temporarily unavailable.",
        )
        self.assertIs(
            outcomes["fundamentals.fundamental_analyst"].status,
            BrowserAgentExecutionStatus.COMPLETED,
        )

    def test_requested_fundamentals_dispatch_all_capabilities_and_retain_results(self):
        clock = _AdvancingClock()
        gateway = _RecordingStructuredFundamentalGateway()
        analyst = _RecordingFundamentalAnalyst()
        sector_resolver = _TcsSectorEvidenceResolver()
        repository = InMemoryFundamentalSnapshotRepository(clock=clock)
        package_repository = InMemoryFundamentalPackageRepository(clock=clock)
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=repository,
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            package_repository=package_repository,
            clock=clock,
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response_with_series()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
            fundamental_analyst=analyst,
            fundamental_sector_evidence_resolver=sector_resolver,
            clock=clock,
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken
        request = _request().model_copy(
            update={
                "fundamentals_requested": True,
                "refresh_requested": True,
            }
        )

        output = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(
            gateway.calls,
            [
                "resolve_issuer",
                "overview",
                "financials",
                "shareholding",
                "financial_package",
                "peer_comparison",
                "benchmarking_financials",
            ],
        )
        self.assertEqual(len(output.fundamental_evidence), 3)
        self.assertEqual(len(output.structured_document_references), 11)
        package_reference = output.fundamental_package_reference
        self.assertIsNotNone(package_reference)
        self.assertEqual(
            package_reference.package_id,
            "fundamental_package:" + package_reference.manifest_fingerprint,
        )
        self.assertGreater(package_reference.expires_at, output.completed_at)
        self.assertIsNotNone(output.fundamental_analysis)
        self.assertEqual(len(analyst.calls), 1)
        analyst_evidence, intelligence, analyst_as_of = analyst.calls[0]
        self.assertEqual(len(analyst_evidence), 13)
        self.assertTrue(
            all(item.document.expires_at > analyst_as_of for item in analyst_evidence)
        )
        self.assertEqual(
            intelligence.scorecard.decision_influence_mode.value,
            "shadow",
        )
        self.assertEqual(
            intelligence.scorecard.package_id,
            package_reference.package_id,
        )
        self.assertEqual(len(sector_resolver.calls), 1)
        self.assertEqual(sector_resolver.calls[0][1], "TCS-EQ")
        self.assertIsNotNone(intelligence.sector_classification)
        self.assertEqual(
            intelligence.sector_classification.profile,
            FinancialHealthSectorProfile.NON_FINANCIAL,
        )
        self.assertEqual(
            intelligence.sector_classification.evidence[0].source_reference_id,
            "nse-sector-master:TCS",
        )
        self.assertEqual(
            intelligence.scorecard.applicability_assessment_fingerprint,
            intelligence.applicability_assessment.assessment_fingerprint,
        )
        self.assertEqual(
            output.fundamental_analysis.document_citations,
            (output.structured_document_references[0].cache_entry_id,),
        )
        benchmarking_reference = output.benchmarking_financials_reference
        self.assertIsNotNone(benchmarking_reference)
        self.assertEqual(
            benchmarking_reference.document_type,
            "benchmarking_financials",
        )
        self.assertIs(
            benchmarking_reference.source,
            FundamentalEvidenceSource.PROVIDER,
        )
        self.assertEqual(benchmarking_reference.company_count, 2)
        self.assertEqual(benchmarking_reference.row_count, 2)
        self.assertEqual(
            gateway.structured_scenarios,
            [
                ("growth_table", "not_applicable"),
                ("balance_sheet", "consolidated"),
                ("balance_sheet", "standalone"),
                ("profit_and_loss", "consolidated"),
                ("profit_and_loss", "standalone"),
                ("cash_flow", "consolidated"),
                ("cash_flow", "standalone"),
                ("ratios", "consolidated"),
                ("ratios", "standalone"),
                ("quarterly_results", "consolidated"),
                ("quarterly_results", "standalone"),
            ],
        )
        self.assertEqual(
            [
                (item.document_type.value, item.reporting_basis.value)
                for item in output.structured_document_references
            ],
            gateway.structured_scenarios,
        )
        self.assertTrue(
            all(
                item.source is FundamentalEvidenceSource.PROVIDER
                for item in output.structured_document_references
            )
        )
        self.assertTrue(
            all(
                item.stored_snapshot is not None
                and item.stored_snapshot.request.as_of_date is None
                and item.stored_snapshot.request.issuer.symbol == "TCS"
                for item in output.fundamental_evidence
            )
        )
        expected_scope = FundamentalPackageRepositoryScope(
            connection=build_connection(),
            issuer=output.fundamental_evidence[0].retrieval.issuer,
        )
        active_package = package_repository.get_active_fundamental_package(
            scope=expected_scope,
            as_of=clock(),
        )
        self.assertIsNotNone(active_package)
        self.assertEqual(len(active_package.manifest.items), 16)
        self.assertTrue(
            all(
                item.decision.reason == "explicit_refresh"
                for item in output.fundamental_evidence
            )
        )

    def test_fundamental_failure_names_safe_dataset_without_provider_details(self):
        clock = _AdvancingClock()
        gateway = _FailingCashFlowGateway()
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=InMemoryFundamentalSnapshotRepository(clock=clock),
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            package_repository=InMemoryFundamentalPackageRepository(
                clock=clock
            ),
            clock=clock,
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response_with_series()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
            clock=clock,
        )
        from app.workflow.events import (
            InMemoryWorkflowEventSink,
            WorkflowEventEmitter,
        )
        from app.workflow.browser_runner import BrowserCancellationToken

        request = _request().model_copy(
            update={
                "fundamentals_requested": True,
                "refresh_requested": True,
            }
        )
        sink = InMemoryWorkflowEventSink()
        output = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                sink,
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        failure = output.fundamental_failure
        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, "fundamentals.unavailable")
        self.assertIsNotNone(output.research_response.result)
        self.assertIsNotNone(output.research_explanation)
        self.assertIn("financial document package", failure.message)
        self.assertNotIn("raw provider details", failure.message)
        fundamental_events = [
            event
            for event in sink.events
            if event.activity.participant_id
            == "fundamentals.fundamental_analyst"
        ]
        self.assertEqual(
            [event.state for event in fundamental_events],
            [WorkflowEventState.STARTED, WorkflowEventState.FAILED],
        )

    def test_fundamental_validation_failure_names_safe_runtime_stage(self):
        clock = _AdvancingClock()
        gateway = _RecordingStructuredFundamentalGateway()
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=InMemoryFundamentalSnapshotRepository(clock=clock),
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            package_repository=InMemoryFundamentalPackageRepository(
                clock=clock
            ),
            clock=clock,
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response_with_series()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
            fundamental_analyst=_InvalidFundamentalAnalyst(),
            clock=clock,
        )
        from app.workflow.events import (
            InMemoryWorkflowEventSink,
            WorkflowEventEmitter,
        )
        from app.workflow.browser_runner import BrowserCancellationToken

        request = _request().model_copy(
            update={
                "fundamentals_requested": True,
                "refresh_requested": True,
            }
        )
        output = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        failure = output.fundamental_failure
        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, "fundamentals.analyst_unavailable")
        self.assertIn("Fundamental Analyst", failure.message)
        self.assertNotIn("object", failure.message)
        self.assertIsNotNone(output.fundamental_package_reference)
        self.assertEqual(len(output.structured_document_references), 11)
        self.assertIsNone(output.fundamental_analysis)

    def test_repeated_fundamental_analysis_reuses_identity_and_evidence_cache(self):
        clock = _AdvancingClock()
        gateway = _BrowserFundamentalGateway()
        repository = InMemoryFundamentalSnapshotRepository(clock=clock)
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=repository,
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            package_repository=InMemoryFundamentalPackageRepository(
                clock=clock
            ),
            clock=clock,
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response_with_series()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
            clock=clock,
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken
        request = _request().model_copy(
            update={"fundamentals_requested": True}
        )

        first = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )
        self.assertEqual(
            gateway.calls,
            [
                "resolve_issuer",
                "overview",
                "financials",
                "shareholding",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "peer_comparison",
                "benchmarking_financials",
            ],
        )
        gateway.calls.clear()
        second = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(len(first.fundamental_evidence), 3)
        self.assertEqual(len(first.structured_document_references), 11)
        self.assertIsNone(first.fundamental_package_reference)
        self.assertIs(
            first.benchmarking_financials_reference.source,
            FundamentalEvidenceSource.PROVIDER,
        )
        self.assertEqual(gateway.calls, [])
        self.assertTrue(
            all(
                item.source is FundamentalEvidenceSource.CACHE
                for item in second.fundamental_evidence
            )
        )
        self.assertEqual(len(second.structured_document_references), 11)
        self.assertIsNone(second.fundamental_package_reference)
        self.assertIs(
            second.benchmarking_financials_reference.source,
            FundamentalEvidenceSource.CACHE,
        )
        self.assertEqual(
            second.benchmarking_financials_reference.cache_entry_id,
            first.benchmarking_financials_reference.cache_entry_id,
        )
        self.assertTrue(
            all(
                item.source is FundamentalEvidenceSource.CACHE
                for item in second.structured_document_references
            )
        )

        clock.now += timedelta(days=11)
        third = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(len(third.fundamental_evidence), 3)
        self.assertEqual(len(third.structured_document_references), 11)
        self.assertIsNone(third.fundamental_package_reference)
        self.assertIs(
            third.benchmarking_financials_reference.source,
            FundamentalEvidenceSource.PROVIDER,
        )
        self.assertTrue(
            all(
                item.source is FundamentalEvidenceSource.PROVIDER
                for item in third.structured_document_references
            )
        )
        self.assertEqual(
            gateway.calls,
            [
                "resolve_issuer",
                "overview",
                "financials",
                "shareholding",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "structured_document",
                "peer_comparison",
                "benchmarking_financials",
            ],
        )

    def test_incomplete_browser_refresh_preserves_active_package(self):
        clock = _AdvancingClock()
        gateway = _RecordingStructuredFundamentalGateway()
        snapshot_repository = InMemoryFundamentalSnapshotRepository(clock=clock)
        package_repository = InMemoryFundamentalPackageRepository(clock=clock)
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=snapshot_repository,
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            package_repository=package_repository,
            clock=clock,
        )
        research = _ResearchExecutor(_completed_response_with_series())
        handler = JarvisBrowserOperationHandler(
            research,
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: build_connection(),
            clock=clock,
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        first_request = _request().model_copy(
            update={"fundamentals_requested": True, "refresh_requested": True}
        )
        first_output = handler.execute(
            first_request,
            event_emitter=WorkflowEventEmitter(
                first_request.operation_id,
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )
        scope = FundamentalPackageRepositoryScope(
            connection=build_connection(),
            issuer=first_output.fundamental_evidence[0].retrieval.issuer,
        )
        first_package = package_repository.get_active_fundamental_package(
            scope=scope,
            as_of=clock(),
        )
        self.assertIsNotNone(first_package)

        gateway.failure = True
        second_request = first_request.model_copy(
            update={"operation_id": "operation-2"}
        )
        research.response = research.response.model_copy(
            update={"operation_id": second_request.operation_id}
        )
        second_output = handler.execute(
            second_request,
            event_emitter=WorkflowEventEmitter(
                second_request.operation_id,
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(
            second_output.fundamental_failure.code,
            "fundamentals.refresh_incomplete",
        )
        self.assertEqual(
            package_repository.get_active_fundamental_package(
                scope=scope,
                as_of=clock(),
            ),
            first_package,
        )

    def test_intent_failure_becomes_candid_safe_browser_failure(self):
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(
                failure=IntentRecognitionError("private parser detail")
            ),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        with self.assertRaises(BrowserOperationHandledFailure) as captured:
            handler.execute(
                _request(),
                event_emitter=WorkflowEventEmitter(
                    "operation-1",
                    InMemoryWorkflowEventSink(),
                ),
                cancellation_token=BrowserCancellationToken(),
            )

        failure = captured.exception.failure
        self.assertEqual(failure.code, "request.intent_unrecognized")
        self.assertIn("Analyse TCS for me", failure.message)
        self.assertNotIn("private parser detail", failure.message)

    def test_llm_failure_returns_safe_output_without_a_conclusion(self):
        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-1",
            failure=JarvisLLMFailureResponse(
                code=LLMFailureCode.PROVIDER_UNAVAILABLE,
                title="The panel is unavailable",
                display_message="The debate panel is taking an unscheduled tea break.",
                spoken_message="The panel is unavailable.",
                recovery_action="Please retry shortly.",
                retryable=True,
                operation_id="operation-1",
            ),
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        output = handler.execute(
            _request(),
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                InMemoryWorkflowEventSink(),
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(
            output.decision_failure.code,
            "llm.provider_unavailable",
        )
        self.assertTrue(output.decision_failure.retryable)
        self.assertIsNone(output.research_response.result)

    def test_fundamentals_persist_before_subsequent_llm_failure(self):
        clock = _AdvancingClock()
        gateway = _BrowserFundamentalGateway()
        repository = InMemoryFundamentalSnapshotRepository(clock=clock)
        package_repository = InMemoryFundamentalPackageRepository(clock=clock)
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=repository,
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(
                clock=clock
            ),
            package_repository=package_repository,
            clock=clock,
        )
        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-1",
            failure=JarvisLLMFailureResponse(
                code=LLMFailureCode.AUTHENTICATION,
                title="The panel is unavailable",
                display_message="The debate panel could not authenticate.",
                spoken_message="The panel is unavailable.",
                recovery_action="Check the configured LLM credential.",
                retryable=False,
                operation_id="operation-1",
            ),
        )
        connection = build_connection()
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: connection,
            clock=clock,
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken
        request = _request().model_copy(
            update={
                "fundamentals_requested": True,
                "refresh_requested": True,
            }
        )

        sink = InMemoryWorkflowEventSink()
        output = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter(
                "operation-1",
                sink,
            ),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(
            gateway.calls,
            [
                "resolve_issuer",
                "overview",
                "financials",
                "shareholding",
                "financial_package",
                "peer_comparison",
                "benchmarking_financials",
            ],
        )
        self.assertEqual(
            output.decision_failure.code,
            "llm.authentication",
        )
        self.assertEqual(len(output.fundamental_evidence), 3)
        self.assertEqual(len(output.structured_document_references), 11)
        self.assertIsNotNone(output.benchmarking_financials_reference)
        self.assertIsNotNone(output.fundamental_package_reference)
        fundamental_events = [
            event
            for event in sink.events
            if event.activity.participant_id
            == "fundamentals.fundamental_analyst"
        ]
        self.assertEqual(
            [event.state for event in fundamental_events],
            [WorkflowEventState.STARTED, WorkflowEventState.COMPLETED],
        )
        stored = repository.list_fundamental_snapshots(
            FundamentalSnapshotQuery(
                tenant_id=connection.tenant_id,
                provider_connection_id=connection.provider_connection_id,
                provider=connection.provider,
                exchange="NSE",
                symbol="TCS",
            ),
            as_of=clock(),
        )
        self.assertEqual(len(stored), 3)

    def test_fundamental_analyst_llm_failure_retains_committed_refresh_and_debate_failure(self):
        clock = _AdvancingClock()
        gateway = _RecordingStructuredFundamentalGateway()
        package_repository = InMemoryFundamentalPackageRepository(clock=clock)
        coordinator = FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=InMemoryFundamentalSnapshotRepository(clock=clock),
            structured_document_repository=(
                InMemoryStructuredFinancialDocumentRepository(clock=clock)
            ),
            benchmarking_financials_repository=(
                InMemoryBenchmarkingFinancialsRepository(clock=clock)
            ),
            peer_comparison_repository=InMemoryPeerComparisonRepository(clock=clock),
            package_repository=package_repository,
            clock=clock,
        )
        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-1",
            failure=JarvisLLMFailureResponse(
                code=LLMFailureCode.AUTHENTICATION,
                title="The panel is unavailable",
                display_message="The debate panel could not authenticate.",
                spoken_message="The panel is unavailable.",
                recovery_action="Check the configured LLM credential.",
                retryable=False,
                operation_id="operation-1",
            ),
        )
        connection = build_connection()
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
            fundamental_evidence_executor=coordinator,
            provider_scope_resolver=lambda session_id: connection,
            fundamental_analyst=_FailingFundamentalAnalyst(),
            clock=clock,
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        request = _request().model_copy(
            update={"fundamentals_requested": True, "refresh_requested": True}
        )
        sink = InMemoryWorkflowEventSink()
        output = handler.execute(
            request,
            event_emitter=WorkflowEventEmitter("operation-1", sink),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(output.decision_failure.code, "llm.authentication")
        self.assertEqual(
            output.fundamental_failure.code,
            "fundamentals.analyst_unavailable",
        )
        self.assertNotIn("private provider credential detail", repr(output))
        self.assertIsNone(output.fundamental_analysis)
        self.assertEqual(len(output.fundamental_evidence), 3)
        self.assertEqual(len(output.structured_document_references), 11)
        self.assertIsNotNone(output.benchmarking_financials_reference)
        reference = output.fundamental_package_reference
        self.assertIsNotNone(reference)
        active_package = package_repository.get_active_fundamental_package(
            scope=FundamentalPackageRepositoryScope(
                connection=connection,
                issuer=output.fundamental_evidence[0].retrieval.issuer,
            ),
            as_of=clock(),
        )
        self.assertIsNotNone(active_package)
        self.assertEqual(active_package.package_id, reference.package_id)
        self.assertEqual(len(active_package.manifest.items), 16)
        fundamental_events = [
            event for event in sink.events
            if event.activity.participant_id == "fundamentals.fundamental_analyst"
        ]
        self.assertEqual(
            [event.state for event in fundamental_events],
            [WorkflowEventState.STARTED, WorkflowEventState.FAILED],
        )

    def test_analysis_uses_one_operation_and_presents_result(self):
        research = _ResearchExecutor(_completed_response())
        presenter = RecordingPresenter()
        handler = JarvisBrowserOperationHandler(
            research,
            presenter,
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        registry = InMemoryBrowserOperationRegistry()
        at = _now()
        registry.open_session(
            BrowserSessionSnapshot(
                session_id="session-1",
                created_at=at,
                updated_at=at,
            )
        )
        runner = AsyncBrowserOperationRunner(
            registry,
            handler,
            InMemoryBrowserOperationResultStore(),
            max_workers=1,
        )
        try:
            runner.submit(_request())
            terminal = _wait_for_terminal(runner, "operation-1")
            output = runner.get_result("operation-1")
        finally:
            runner.shutdown()

        self.assertIs(terminal.status, BrowserOperationStatus.COMPLETED)
        self.assertIsNotNone(output.research_explanation)
        self.assertEqual(research.calls[0][1], "operation-1")
        self.assertFalse(research.calls[0][3])
        stages = [
            event.stage
            for event in registry.replay(
                WorkflowEventReplayCursor(operation_id="operation-1")
            ).events
        ]
        self.assertEqual(
            stages,
            [
                WorkflowStage.PRESENTATION,
                WorkflowStage.PRESENTATION,
                WorkflowStage.COMPLETED,
            ],
        )

    def test_presentation_failure_preserves_validated_analysis(self):
        response = _completed_response()
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(failure=MarketDataError("private detail")),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        sink = InMemoryWorkflowEventSink()
        output = handler.execute(
            _request(),
            event_emitter=WorkflowEventEmitter("operation-1", sink),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertIs(output.research_response, response)
        self.assertEqual(
            output.presentation_failure.code,
            "presentation.unavailable",
        )
        self.assertNotIn("private detail", output.presentation_failure.message)

    def test_follow_up_reuses_only_approved_session_context(self):
        response = _multi_timeframe_response().model_copy(
            update={"operation_id": "operation-1"}
        )
        follow_up = RecordingJudgeFollowUpExecutor()
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(response),
            RecordingPresenter(),
            follow_up,
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        sink = InMemoryWorkflowEventSink()
        handler.execute(
            _request(),
            event_emitter=WorkflowEventEmitter("operation-1", sink),
            cancellation_token=BrowserCancellationToken(),
        )
        answer = handler.execute(
            _request(
                "operation-2",
                kind=BrowserOperationKind.JUDGE_FOLLOW_UP,
                idempotency_key="request-2",
                message="Where is weekly support?",
            ),
            event_emitter=WorkflowEventEmitter("operation-2", sink),
            cancellation_token=BrowserCancellationToken(),
        )

        self.assertEqual(answer.judge_follow_up.question, "Where is weekly support?")
        self.assertEqual(len(follow_up.calls), 1)

    def test_follow_up_without_approved_analysis_fails_candidly(self):
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(_completed_response()),
            RecordingPresenter(),
            RecordingJudgeFollowUpExecutor(),
            user_name="Prateek",
        )
        from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
        from app.workflow.browser_runner import BrowserCancellationToken

        with self.assertRaises(BrowserOperationHandledFailure) as captured:
            handler.execute(
                _request(
                    kind=BrowserOperationKind.JUDGE_FOLLOW_UP,
                    message="Where is weekly support?",
                ),
                event_emitter=WorkflowEventEmitter(
                    "operation-1",
                    InMemoryWorkflowEventSink(),
                ),
                cancellation_token=BrowserCancellationToken(),
            )

        self.assertEqual(
            captured.exception.failure.code,
            "conversation.context_unavailable",
        )


class AsyncBrowserOperationRunnerTests(unittest.TestCase):
    def test_market_failure_retains_committed_package_and_independent_failures(self):
        clock = _AdvancingClock()
        packages = InMemoryFundamentalPackageRepository(clock=clock)
        coordinator = FundamentalEvidenceCoordinator(
            gateway=_BrowserFundamentalGateway(),
            repository=InMemoryFundamentalSnapshotRepository(clock=clock),
            structured_document_repository=InMemoryStructuredFinancialDocumentRepository(clock=clock),
            benchmarking_financials_repository=InMemoryBenchmarkingFinancialsRepository(clock=clock),
            peer_comparison_repository=InMemoryPeerComparisonRepository(clock=clock),
            package_repository=packages, clock=clock,
        )
        handler = JarvisBrowserOperationHandler(
            _ResearchExecutor(failure=MarketDataError("synthetic rate limit")),
            RecordingPresenter(), RecordingJudgeFollowUpExecutor(), user_name="Test",
            fundamental_evidence_executor=coordinator,
            fundamental_filing_evidence_executor=_RecordingFilingEvidenceCoordinator(fail=True),
            provider_scope_resolver=lambda session_id: build_connection(),
            fundamental_analyst=_FailingFundamentalAnalyst(), clock=clock,
        )
        runner = AsyncBrowserOperationRunner(self.registry, handler, self.results, max_workers=1, clock=clock)
        request = _request().model_copy(update={"fundamentals_requested": True, "refresh_requested": True})
        try:
            runner.submit(request)
            terminal = _wait_for_terminal(runner, request.operation_id)
        finally:
            runner.shutdown()
        self.assertEqual(terminal.status, BrowserOperationStatus.FAILED)
        self.assertEqual(terminal.failure.code, "market.unavailable")
        self.assertTrue(terminal.result_available)
        output = self.results.get(request.operation_id)
        self.assertIsNotNone(output.fundamental_package_reference)
        self.assertEqual(output.research_failure, terminal.failure)
        self.assertIsNone(output.research_response)
        self.assertIsNone(output.research_explanation)
        self.assertEqual(output.fundamental_failure.code, "fundamentals.analyst_unavailable")
        events = self.registry.replay(WorkflowEventReplayCursor(operation_id=request.operation_id)).events
        filing_failure = next(e for e in events if e.activity.participant_id == "fundamentals.corporate_filings_analyst" and e.state is WorkflowEventState.FAILED)
        self.assertEqual(filing_failure.message, "Corporate filing discovery is temporarily unavailable.")
        self.assertEqual(output.filing_failure.message, filing_failure.message)
        self.assertTrue(output.filing_failure.retryable)
        active_package = packages.get_active_fundamental_package(
            scope=FundamentalPackageRepositoryScope(
                connection=build_connection(), issuer=output.fundamental_evidence[0].retrieval.issuer,
            ), as_of=clock(),
        )
        self.assertEqual(active_package.package_id, output.fundamental_package_reference.package_id)
        self.assertEqual(len(active_package.manifest.items), 16)
        self.assertFalse(any(e.stage is WorkflowStage.COMPLETED for e in events))
        outcomes = {o.participant_id: o for o in build_agent_execution_outcomes(terminal, events, output)}
        self.assertEqual(outcomes["fundamentals.fundamental_analyst"].failure, output.fundamental_failure)
        filing = outcomes["fundamentals.corporate_filings_analyst"].failure
        self.assertEqual(filing.code, "fundamentals.filings_unavailable")
        self.assertEqual(filing, output.filing_failure)
        self.assertEqual(filing.message, filing_failure.message)
        self.assertNotEqual(filing.code, terminal.failure.code)

    def setUp(self):
        self.registry = InMemoryBrowserOperationRegistry()
        at = _now()
        self.registry.open_session(
            BrowserSessionSnapshot(
                session_id="session-1",
                created_at=at,
                updated_at=at,
            )
        )
        self.results = InMemoryBrowserOperationResultStore()

    def test_idempotent_submit_executes_once_and_stores_result(self):
        handler = _SimpleHandler()
        runner = AsyncBrowserOperationRunner(
            self.registry,
            handler,
            self.results,
            max_workers=1,
        )
        request = _request()
        try:
            runner.submit(request)
            runner.submit(request)
            terminal = _wait_for_terminal(runner, "operation-1")
        finally:
            runner.shutdown()

        self.assertEqual(handler.calls, 1)
        self.assertIs(terminal.status, BrowserOperationStatus.COMPLETED)
        self.assertTrue(terminal.result_available)
        self.assertIsNotNone(self.results.get("operation-1"))
        events = self.registry.replay(
            WorkflowEventReplayCursor(operation_id="operation-1")
        ).events
        self.assertEqual([event.sequence for event in events], [1, 2])
        self.assertIs(events[-1].stage, WorkflowStage.COMPLETED)

    def test_running_operation_cancels_cooperatively_without_result(self):
        handler = _SimpleHandler(blocking=True)
        runner = AsyncBrowserOperationRunner(
            self.registry,
            handler,
            self.results,
            max_workers=1,
        )
        try:
            runner.submit(_request())
            self.assertTrue(handler.started.wait(timeout=1))
            requested = runner.cancel("operation-1")
            handler.release.set()
            terminal = _wait_for_terminal(runner, "operation-1")
        finally:
            runner.shutdown()

        self.assertIs(
            requested.status,
            BrowserOperationStatus.CANCELLATION_REQUESTED,
        )
        self.assertIs(terminal.status, BrowserOperationStatus.CANCELLED)
        self.assertIsNone(self.results.get("operation-1"))

    def test_safe_failure_is_recorded_and_internal_details_are_hidden(self):
        handler = _SimpleHandler(
            failure=BrowserOperationHandledFailure(
                BrowserOperationFailure(
                    code="market.unavailable",
                    message="Market history is unavailable.",
                    retryable=True,
                )
            )
        )
        runner = AsyncBrowserOperationRunner(
            self.registry,
            handler,
            self.results,
            max_workers=1,
        )
        try:
            runner.submit(_request())
            terminal = _wait_for_terminal(runner, "operation-1")
        finally:
            runner.shutdown()

        self.assertIs(terminal.status, BrowserOperationStatus.FAILED)
        self.assertEqual(terminal.failure.code, "market.unavailable")
        events = self.registry.replay(
            WorkflowEventReplayCursor(operation_id="operation-1")
        ).events
        self.assertIs(events[-1].stage, WorkflowStage.FAILED)


if __name__ == "__main__":
    unittest.main()
