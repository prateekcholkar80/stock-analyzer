import threading
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.exceptions import IntentRecognitionError, MarketDataError
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
)
from app.models.browser_operations import (
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationStatus,
    BrowserSessionSnapshot,
    BrowserStructuredDocumentReference,
    WorkflowEventReplayCursor,
)
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.models.conversation import InputChannel
from app.models.interaction import (
    JarvisSwingAnalysisResponse,
    SwingAnalysisCommand,
)
from app.models.llm import JarvisLLMFailureResponse, LLMFailureCode
from app.models.fundamental_storage import FundamentalSnapshotQuery
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
from app.services.fundamental_evidence import FundamentalEvidenceCoordinator
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


IST = ZoneInfo("Asia/Kolkata")


class _BrowserFundamentalGateway(FakeFundamentalGateway):
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

    def test_requested_fundamentals_dispatch_all_capabilities_and_retain_results(self):
        clock = _AdvancingClock()
        gateway = _RecordingStructuredFundamentalGateway()
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
                "benchmarking_financials",
            ],
        )
        self.assertEqual(len(output.fundamental_evidence), 3)
        self.assertEqual(len(output.structured_document_references), 11)
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
        self.assertTrue(
            all(
                item.decision.reason == "explicit_refresh"
                for item in output.fundamental_evidence
            )
        )

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
                "benchmarking_financials",
            ],
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

    def test_llm_failure_becomes_safe_browser_failure(self):
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

        with self.assertRaises(BrowserOperationHandledFailure) as captured:
            handler.execute(
                _request(),
                event_emitter=WorkflowEventEmitter(
                    "operation-1",
                    InMemoryWorkflowEventSink(),
                ),
                cancellation_token=BrowserCancellationToken(),
            )

        self.assertEqual(
            captured.exception.failure.code,
            "llm.provider_unavailable",
        )
        self.assertTrue(captured.exception.failure.retryable)

    def test_fundamentals_persist_before_subsequent_llm_failure(self):
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

        with self.assertRaises(BrowserOperationHandledFailure) as captured:
            handler.execute(
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
                "benchmarking_financials",
            ],
        )
        self.assertEqual(
            captured.exception.failure.code,
            "llm.authentication",
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
