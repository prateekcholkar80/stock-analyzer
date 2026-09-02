import threading
import time
import unittest
import warnings
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=UserWarning,
)

from fastapi.testclient import TestClient

from app.api.http import create_jarvis_http_app
from app.api.models import ProviderSessionTargetRequest
from app.api.session_auth import InMemoryBrowserSessionAuthorizer
from app.conversation.browser import BrowserConversationCoordinator
from app.conversation.config import JarvisConversationConfig
from app.exceptions import (
    STTAuthenticationError,
    STTConfigurationError,
    STTProviderUnavailableError,
    TTSAuthenticationError,
    TTSConfigurationError,
    TTSProviderUnavailableError,
)
from app.fundamentals.session_provisioning import (
    ProviderSessionLifecycle,
    ProviderSessionRevocationReason,
    ProviderSessionStatus,
)
from app.fundamentals.provider_connections import (
    InMemoryProviderConnectionRegistry,
    ProviderConnectionResolutionError,
)
from app.models.browser_operations import (
    BrowserBenchmarkingFinancialsReference,
    BrowserOperationOutput,
    BrowserOperationStatus,
    BrowserStructuredDocumentReference,
)
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.storage.adapters.financial_document_in_memory import (
    InMemoryStructuredFinancialDocumentRepository,
)
from app.storage.adapters.benchmarking_financials_in_memory import (
    InMemoryBenchmarkingFinancialsRepository,
)
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
)
from app.models.benchmarking_financials_storage import (
    stored_benchmarking_financials_document,
)
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.fundamentals import ProviderConnectionScope
from app.services.provider_sessions import ProviderSessionCommandError
from app.services.fundamental_evidence import FundamentalEvidenceSource
from app.models.debate import JudgeFollowUpAnswer
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.stt.gateway import Transcription
from app.tts.gateway import SpeechSynthesis
from app.workflow.browser_runner import (
    AsyncBrowserOperationRunner,
    InMemoryBrowserOperationResultStore,
)
from app.workflow.operations import InMemoryBrowserOperationRegistry
from tests.unit.test_jarvis_conversation import (
    RecordingPresenter,
)
from tests.unit.test_financial_document_in_memory_repository import (
    MutableClock,
    build_entry,
)
from tests.unit.test_benchmarking_financials_gateway import (
    document as benchmarking_document,
    request as benchmarking_request,
    result_values as benchmarking_result_values,
)
from tests.unit.test_run_end_to_end_multi_timeframe_swing_analysis import (
    _use_case,
)


IST = ZoneInfo("Asia/Kolkata")


@lru_cache
def _serializable_result():
    return _use_case().execute(
        "NSE",
        "2885",
        "RELIANCE-EQ",
        "ONE_HOUR",
    )


class _ApiHandler:
    def __init__(
        self,
        *,
        blocking=False,
        structured_entry=None,
        benchmarking_entry=None,
    ):
        self.blocking = blocking
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.result = _serializable_result()
        self.structured_entry = structured_entry
        self.benchmarking_entry = benchmarking_entry
        self.explanation = RecordingPresenter().explain(
            self.result,
            user_name="Prateek",
        )

    def execute(self, request, *, event_emitter, cancellation_token):
        self.calls += 1
        self.started.set()
        event_emitter.emit(
            WorkflowStage.REQUEST_RECEIVED,
            WorkflowEventState.COMPLETED,
        )
        if self.blocking:
            self.release.wait(timeout=2)
        if request.kind.value == "judge_follow_up":
            package = self.result.technical_review.evidence_package
            evidence_id = package.weekly.evidence[0].qualified_evidence_id
            return BrowserOperationOutput(
                operation_id=request.operation_id,
                session_id=request.session_id,
                kind=request.kind,
                completed_at=datetime.now(IST),
                judge_follow_up=JudgeFollowUpAnswer(
                    question=request.message,
                    answer="Weekly support remains at the cited support zone.",
                    evidence_citations=(evidence_id,),
                    technical_package_fingerprint=package.package_fingerprint,
                    debate_verdict_id=(
                        self.result.debate_result.submission.verdict.verdict_id
                    ),
                    judge_agent_id="jarvis.debate_judge_agent.v1",
                    model_id="fake-judge",
                    generated_at=datetime.now(IST),
                ),
            )
        reference_time = datetime.now(IST)
        if self.structured_entry is None:
            reference_values = {
                "cache_entry_id": "financial_document:aaaaaaaa",
                "document_id": (
                    "tijori.NSE.RELIANCE.balance_sheet.consolidated"
                ),
                "document_type": FinancialDocumentType.BALANCE_SHEET,
                "reporting_basis": FinancialReportingBasis.CONSOLIDATED,
                "exchange": "NSE",
                "symbol": "RELIANCE",
                "document_fingerprint": "a" * 64,
                "retrieved_at": reference_time,
                "stored_at": reference_time + timedelta(seconds=1),
                "expires_at": reference_time + timedelta(days=10),
            }
        else:
            stored = self.structured_entry
            document = stored.result.document
            reference_values = {
                "cache_entry_id": stored.cache_key.cache_entry_id,
                "document_id": document.document_id,
                "document_type": document.document_type,
                "reporting_basis": document.reporting_basis,
                "exchange": document.issuer.exchange,
                "symbol": document.issuer.symbol,
                "document_fingerprint": document.document_fingerprint,
                "retrieved_at": stored.retrieved_at,
                "stored_at": stored.stored_at,
                "expires_at": stored.expires_at,
            }
        benchmarking_reference = None
        if self.benchmarking_entry is not None:
            benchmark_stored = self.benchmarking_entry
            benchmark = benchmark_stored.result.document
            benchmarking_reference = BrowserBenchmarkingFinancialsReference(
                cache_entry_id=benchmark_stored.cache_key.cache_entry_id,
                document_id=benchmark.document_id,
                exchange=benchmark.issuer.exchange,
                symbol=benchmark.issuer.symbol,
                source=FundamentalEvidenceSource.CACHE,
                document_fingerprint=benchmark.document_fingerprint,
                observation_date=benchmark.observation_date,
                retrieved_at=benchmark_stored.retrieved_at,
                stored_at=benchmark_stored.stored_at,
                expires_at=benchmark_stored.expires_at,
                all_rows_captured=True,
                company_count=len(benchmark.companies),
                row_count=len(benchmark.rows),
            )
        return BrowserOperationOutput(
            operation_id=request.operation_id,
            session_id=request.session_id,
            kind=request.kind,
            completed_at=datetime.now(IST),
            research_response=JarvisSwingAnalysisResponse.completed(
                operation_id=request.operation_id,
                result=self.result,
                multi_timeframe_review=self.result.technical_review,
                multi_timeframe_debate=self.result.debate_result,
            ),
            research_explanation=self.explanation,
            structured_document_references=(
                BrowserStructuredDocumentReference(
                    **reference_values,
                    source=FundamentalEvidenceSource.CACHE,
                    all_sections_expanded=True,
                ),
            ),
            benchmarking_financials_reference=benchmarking_reference,
        )


class _ProviderSessions:
    def __init__(self):
        self.calls = []

    def status(self, *, request):
        self.calls.append(("status", request))
        return ProviderSessionLifecycle(
            connection=request.connection,
            status=ProviderSessionStatus.UNCONFIGURED,
            checked_at=request.requested_at,
        )

    def provision(self, *, request):
        self.calls.append(("provision", request))
        return ProviderSessionLifecycle(
            connection=request.connection,
            status=ProviderSessionStatus.READY,
            checked_at=request.requested_at,
            session_reference_hash="b" * 64,
            provisioned_at=request.requested_at,
            expires_at=request.requested_at + timedelta(hours=12),
        )

    def revoke(self, *, request):
        self.calls.append(("revoke", request))
        return ProviderSessionLifecycle(
            connection=request.connection,
            status=ProviderSessionStatus.REVOKED,
            checked_at=request.requested_at,
            session_reference_hash="b" * 64,
            provisioned_at=request.requested_at - timedelta(hours=1),
            revoked_at=request.requested_at,
            revocation_reason=request.reason,
        )


class JarvisHttpApiTests(unittest.TestCase):
    def setUp(self):
        self.registry = InMemoryBrowserOperationRegistry()
        self.results = InMemoryBrowserOperationResultStore()
        self.structured_entry = build_entry()
        self.document_clock = MutableClock(
            self.structured_entry.stored_at + timedelta(minutes=1)
        )
        self.structured_documents = (
            InMemoryStructuredFinancialDocumentRepository(
                clock=self.document_clock,
                initial_entries=(self.structured_entry,),
            )
        )
        connection = ProviderConnectionScope(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
            account_reference_hash="a" * 64,
        )
        benchmark_request = benchmarking_request(connection=connection)
        benchmark_payload = benchmarking_document(connection=connection)
        benchmark_values = benchmarking_result_values(
            benchmark_request,
            document=benchmark_payload,
        )
        benchmark_result = FundamentalBenchmarkingFinancialsResult(
            **benchmark_values
        )
        self.benchmarking_entry = stored_benchmarking_financials_document(
            benchmark_request,
            benchmark_result,
            stored_at=benchmark_result.completed_at + timedelta(seconds=1),
        )
        self.benchmarking_documents = (
            InMemoryBenchmarkingFinancialsRepository(
                initial_entries=(self.benchmarking_entry,)
            )
        )
        self.handler = _ApiHandler(
            structured_entry=self.structured_entry,
            benchmarking_entry=self.benchmarking_entry,
        )
        self.runner = AsyncBrowserOperationRunner(
            self.registry,
            self.handler,
            self.results,
            max_workers=1,
        )
        self.authorizer = InMemoryBrowserSessionAuthorizer()
        self.provider_sessions = _ProviderSessions()
        self.resolved_scopes = []
        self.connection_registry = InMemoryProviderConnectionRegistry()
        self.connection_registry.register_connection(
            ProviderConnectionScope(
                tenant_id="tenant.prateek",
                provider_connection_id="provider.tijori.prateek",
                provider="tijori",
                account_reference_hash="a" * 64,
            )
        )

        def resolve_scope(session_id, target: ProviderSessionTargetRequest):
            self.resolved_scopes.append((session_id, target))
            return self.connection_registry(session_id, target)

        self.resolve_scope = resolve_scope
        self.conversation = BrowserConversationCoordinator(
            self.runner,
            JarvisConversationConfig(user_name="Prateek"),
        )
        session_ids = iter(("session-1", "session-2", "session-3"))
        operation_ids = iter(
            ("operation-1", "operation-retry", "operation-2")
        )
        self.client = TestClient(
            create_jarvis_http_app(
                self.runner,
                conversation=self.conversation,
                provider_sessions=self.provider_sessions,
                provider_connection_scope_resolver=self.resolve_scope,
                browser_session_ownership=self.connection_registry,
                tenant_identity_resolver=lambda request: "tenant.prateek",
                structured_document_repository=self.structured_documents,
                benchmarking_financials_repository=(
                    self.benchmarking_documents
                ),
                structured_document_scope_resolver=(
                    lambda session_id: self.connection_registry(
                        session_id,
                        ProviderSessionTargetRequest(
                            **self._provider_target()
                        ),
                    )
                ),
                authorizer=self.authorizer,
                session_id_factory=lambda: next(session_ids),
                operation_id_factory=lambda: next(operation_ids),
                allowed_origins=("http://localhost:5173",),
            )
        )

    def tearDown(self):
        self.runner.shutdown()

    def _session(self):
        response = self.client.post("/api/v1/sessions")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        return body["session"]["session_id"], body["access_token"]

    @staticmethod
    def _headers(token):
        return {"X-Jarvis-Session-Token": token}

    @staticmethod
    def _operation_body(key="request-1"):
        return {
            "idempotency_key": key,
            "kind": "swing_analysis",
            "input_channel": "text",
            "message": "Analyze Reliance for me",
        }

    @staticmethod
    def _provider_target():
        return {
            "provider_connection_id": "provider.tijori.prateek",
            "provider": "tijori",
            "account_reference_hash": "a" * 64,
        }

    def _wait_for_status(self, session_id, token, operation_id, expected):
        deadline = time.monotonic() + 2
        path = (
            f"/api/v1/sessions/{session_id}/operations/{operation_id}"
        )
        while time.monotonic() < deadline:
            response = self.client.get(path, headers=self._headers(token))
            if response.json()["status"] == expected:
                return response
            time.sleep(0.005)
        self.fail(f"operation did not reach {expected}")

    def test_health_session_and_authorization_boundaries(self):
        self.assertEqual(
            self.client.get("/health").json(),
            {"status": "ok", "service": "jarvis-research-api"},
        )
        session_id, token = self._session()

        missing = self.client.post(
            f"/api/v1/sessions/{session_id}/operations",
            json=self._operation_body(),
        )
        wrong = self.client.post(
            f"/api/v1/sessions/{session_id}/operations",
            json=self._operation_body(),
            headers=self._headers("wrong-token-value-that-is-long-enough"),
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertNotIn(token, str(self.authorizer._token_digests))
        self.assertEqual(
            self.client.get("/api/openapi.json").status_code,
            200,
        )
        preflight = self.client.options(
            "/api/v1/sessions",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(
            preflight.headers["access-control-allow-origin"],
            "http://localhost:5173",
        )

    def test_application_lifespan_invokes_injected_shutdown(self):
        calls = []
        app = create_jarvis_http_app(
            self.runner,
            shutdown=lambda: calls.append("shutdown"),
        )

        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)

        self.assertEqual(calls, ["shutdown"])

    def test_provider_session_routes_require_browser_authorization(self):
        session_id, token = self._session()
        path = f"/api/v1/sessions/{session_id}/provider-session/status"

        missing = self.client.post(path, json={"target": self._provider_target()})
        authorized = self.client.post(
            path,
            json={"target": self._provider_target()},
            headers=self._headers(token),
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json()["status"], "unconfigured")
        self.assertNotIn("tenant.session-1", authorized.text)
        self.assertNotIn("account_reference_hash", authorized.text)
        self.assertEqual(self.resolved_scopes[0][0], session_id)

    def test_provider_session_provision_and_revoke_are_typed_and_scoped(self):
        session_id, token = self._session()
        base = f"/api/v1/sessions/{session_id}/provider-session"
        headers = self._headers(token)
        provision = self.client.post(
            f"{base}/provision",
            json={
                "idempotency_key": "provider.provision.1",
                "target": self._provider_target(),
                "user_interaction_authorized": True,
            },
            headers=headers,
        )
        revoke = self.client.post(
            f"{base}/revoke",
            json={
                "idempotency_key": "provider.revoke.1",
                "target": self._provider_target(),
                "reason": "user_requested",
            },
            headers=headers,
        )
        insecure = self.client.post(
            f"{base}/revoke",
            json={
                "idempotency_key": "provider.revoke.insecure",
                "target": self._provider_target(),
                "reason": "user_requested",
                "secure_delete_required": False,
            },
            headers=headers,
        )

        self.assertEqual(provision.status_code, 200)
        self.assertEqual(provision.json()["status"], "ready")
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(revoke.json()["status"], "revoked")
        self.assertEqual(insecure.status_code, 422)
        provision_request = self.provider_sessions.calls[0][1]
        revoke_request = self.provider_sessions.calls[1][1]
        self.assertTrue(provision_request.user_interaction_authorized)
        self.assertTrue(revoke_request.secure_delete_required)
        self.assertEqual(
            revoke_request.reason,
            ProviderSessionRevocationReason.USER_REQUESTED,
        )
        self.assertEqual(
            provision_request.connection.tenant_id,
            "tenant.prateek",
        )

    def test_browser_session_close_removes_provider_connection_access(self):
        session_id, token = self._session()
        headers = self._headers(token)
        target = ProviderSessionTargetRequest(**self._provider_target())
        self.assertEqual(
            self.connection_registry(session_id, target).tenant_id,
            "tenant.prateek",
        )

        closed = self.client.delete(
            f"/api/v1/sessions/{session_id}",
            headers=headers,
        )

        self.assertEqual(closed.status_code, 200)
        with self.assertRaises(ProviderConnectionResolutionError):
            self.connection_registry(session_id, target)

    def test_provider_scope_mismatch_and_command_failure_do_not_disclose(self):
        session_id, token = self._session()
        path = f"/api/v1/sessions/{session_id}/provider-session/status"
        headers = self._headers(token)

        def mismatched_scope(session_id, target):
            return ProviderConnectionScope(
                tenant_id="tenant.other",
                provider_connection_id="provider.other",
                provider=target.provider,
                account_reference_hash=target.account_reference_hash,
            )

        mismatch_client = TestClient(
            create_jarvis_http_app(
                self.runner,
                provider_sessions=self.provider_sessions,
                provider_connection_scope_resolver=mismatched_scope,
                authorizer=self.authorizer,
            )
        )
        mismatch = mismatch_client.post(
            path,
            json={"target": self._provider_target()},
            headers=headers,
        )
        original_status = self.provider_sessions.status
        self.provider_sessions.status = lambda *, request: (
            _ for _ in ()
        ).throw(ProviderSessionCommandError())
        failed = self.client.post(
            path,
            json={"target": self._provider_target()},
            headers=headers,
        )
        self.provider_sessions.status = original_status

        self.assertEqual(mismatch.status_code, 404)
        self.assertNotIn("tenant.other", mismatch.text)
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn("session-secret", failed.text)

    def test_provider_session_dependencies_must_be_supplied_together(self):
        with self.assertRaises(ValueError):
            create_jarvis_http_app(
                self.runner,
                provider_sessions=self.provider_sessions,
            )
        with self.assertRaises(ValueError):
            create_jarvis_http_app(
                self.runner,
                browser_session_ownership=self.connection_registry,
            )

    def test_tenant_identity_failure_does_not_create_browser_session(self):
        failing_authorizer = InMemoryBrowserSessionAuthorizer()
        app = create_jarvis_http_app(
            self.runner,
            browser_session_ownership=self.connection_registry,
            tenant_identity_resolver=lambda request: (_ for _ in ()).throw(
                RuntimeError("identity-secret")
            ),
            authorizer=failing_authorizer,
            session_id_factory=lambda: "session-identity-failure",
        )

        response = TestClient(app).post("/api/v1/sessions")

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("identity-secret", response.text)
        self.assertIsNone(self.runner.get_session("session-identity-failure"))

    def test_close_revokes_access_even_when_ownership_unbind_fails(self):
        class FailingUnbindRegistry:
            def bind_browser_session(self, *, browser_session_id, tenant_id):
                return None

            def unbind_browser_session(self, browser_session_id):
                raise RuntimeError("ownership-secret")

        authorizer = InMemoryBrowserSessionAuthorizer()
        client = TestClient(
            create_jarvis_http_app(
                self.runner,
                browser_session_ownership=FailingUnbindRegistry(),
                tenant_identity_resolver=lambda request: "tenant.prateek",
                authorizer=authorizer,
                session_id_factory=lambda: "session-unbind-failure",
            )
        )
        opened = client.post("/api/v1/sessions").json()
        headers = self._headers(opened["access_token"])

        closed = client.delete(
            "/api/v1/sessions/session-unbind-failure",
            headers=headers,
        )
        retried = client.get(
            "/api/v1/sessions/session-unbind-failure/operations/unknown",
            headers=headers,
        )

        self.assertEqual(closed.status_code, 503)
        self.assertNotIn("ownership-secret", closed.text)
        self.assertEqual(retried.status_code, 401)

    def test_submit_is_idempotent_and_result_and_events_are_retrievable(self):
        session_id, token = self._session()
        path = f"/api/v1/sessions/{session_id}/operations"
        first = self.client.post(
            path,
            json=self._operation_body(),
            headers=self._headers(token),
        )
        retry = self.client.post(
            path,
            json=self._operation_body(),
            headers=self._headers(token),
        )
        conflict = self.client.post(
            path,
            json=self._operation_body()
            | {"message": "Analyze TCS for me"},
            headers=self._headers(token),
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            retry.json()["request"]["operation_id"],
            "operation-1",
        )
        self._wait_for_status(
            session_id,
            token,
            "operation-1",
            BrowserOperationStatus.COMPLETED.value,
        )
        result = self.client.get(
            f"{path}/operation-1/result",
            headers=self._headers(token),
        )
        events = self.client.get(
            f"{path}/operation-1/events?after_sequence=0&limit=1",
            headers=self._headers(token),
        )

        self.assertEqual(self.handler.calls, 1)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["output"]["operation_id"], "operation-1")
        references = result.json()["output"][
            "structured_document_references"
        ]
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["document_type"], "growth_table")
        self.assertEqual(references[0]["reporting_basis"], "not_applicable")
        self.assertEqual(references[0]["source"], "CACHE")
        self.assertNotIn("tenant_id", references[0])
        self.assertNotIn("provider_connection_id", references[0])
        self.assertNotIn("source_location", references[0])
        benchmark_reference = result.json()["output"][
            "benchmarking_financials_reference"
        ]
        self.assertEqual(
            benchmark_reference["cache_entry_id"],
            self.benchmarking_entry.cache_key.cache_entry_id,
        )
        self.assertEqual(
            benchmark_reference["document_type"],
            "benchmarking_financials",
        )
        self.assertNotIn("tenant_id", benchmark_reference)
        self.assertNotIn("provider_connection_id", benchmark_reference)
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json()["events"][0]["sequence"], 1)
        self.assertTrue(events.json()["has_more"])

    def test_financial_document_reference_requires_auth_ownership_and_scope(self):
        session_id, token = self._session()
        operations_path = f"/api/v1/sessions/{session_id}/operations"
        self.client.post(
            operations_path,
            json=self._operation_body(),
            headers=self._headers(token),
        )
        self._wait_for_status(session_id, token, "operation-1", "completed")
        cache_entry_id = self.structured_entry.cache_key.cache_entry_id
        document_path = (
            f"{operations_path}/operation-1/financial-documents/"
            f"{cache_entry_id}"
        )

        missing_auth = self.client.get(document_path)
        unknown_reference = self.client.get(
            f"{operations_path}/operation-1/financial-documents/"
            f"financial_document:{'f' * 64}",
            headers=self._headers(token),
        )
        resolved = self.client.get(
            document_path,
            headers=self._headers(token),
        )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(unknown_reference.status_code, 404)
        self.assertEqual(resolved.status_code, 200)
        body = resolved.json()
        self.assertEqual(
            body["schema_version"],
            "jarvis.http_financial_document.v1",
        )
        self.assertEqual(body["operation_id"], "operation-1")
        self.assertEqual(body["reference"]["cache_entry_id"], cache_entry_id)
        self.assertEqual(body["document"]["document_type"], "growth_table")
        self.assertTrue(body["document"]["all_sections_expanded"])
        self.assertEqual(len(body["document"]["periods"]), 1)
        self.assertEqual(len(body["document"]["rows"]), 1)
        self.assertNotIn("tenant_id", resolved.text)
        self.assertNotIn("provider_connection_id", resolved.text)
        self.assertNotIn("source_location", resolved.text)

        other_session_id, other_token = self._session()
        foreign_operation = self.client.get(
            document_path.replace(session_id, other_session_id),
            headers=self._headers(other_token),
        )
        self.assertEqual(foreign_operation.status_code, 404)

        self.structured_documents.delete_structured_financial_document(
            self.structured_entry.cache_key,
            scope=self.structured_entry.cache_key.repository_scope,
        )
        unavailable = self.client.get(
            document_path,
            headers=self._headers(token),
        )
        self.assertEqual(unavailable.status_code, 410)

    def test_benchmarking_reference_requires_auth_ownership_and_scope(self):
        session_id, token = self._session()
        operations_path = f"/api/v1/sessions/{session_id}/operations"
        self.client.post(
            operations_path,
            json=self._operation_body(),
            headers=self._headers(token),
        )
        self._wait_for_status(session_id, token, "operation-1", "completed")
        cache_entry_id = self.benchmarking_entry.cache_key.cache_entry_id
        document_path = (
            f"{operations_path}/operation-1/benchmarking-financials/"
            f"{cache_entry_id}"
        )

        missing_auth = self.client.get(document_path)
        unknown_reference = self.client.get(
            f"{operations_path}/operation-1/benchmarking-financials/"
            f"benchmarking_financials:{'f' * 64}",
            headers=self._headers(token),
        )
        resolved = self.client.get(
            document_path,
            headers=self._headers(token),
        )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(unknown_reference.status_code, 404)
        self.assertEqual(resolved.status_code, 200)
        body = resolved.json()
        self.assertEqual(
            body["schema_version"],
            "jarvis.http_benchmarking_financials.v1",
        )
        self.assertEqual(body["operation_id"], "operation-1")
        self.assertEqual(body["reference"]["cache_entry_id"], cache_entry_id)
        self.assertEqual(len(body["document"]["companies"]), 2)
        self.assertEqual(len(body["document"]["rows"]), 2)
        self.assertTrue(body["document"]["all_rows_captured"])
        self.assertNotIn("tenant_id", resolved.text)
        self.assertNotIn("provider_connection_id", resolved.text)
        self.assertNotIn("source_location", resolved.text)

        other_session_id, other_token = self._session()
        foreign_operation = self.client.get(
            document_path.replace(session_id, other_session_id),
            headers=self._headers(other_token),
        )
        self.assertEqual(foreign_operation.status_code, 404)

        self.benchmarking_documents.delete_benchmarking_financials(
            self.benchmarking_entry.cache_key,
            scope=self.benchmarking_entry.cache_key.repository_scope,
        )
        unavailable = self.client.get(
            document_path,
            headers=self._headers(token),
        )
        self.assertEqual(unavailable.status_code, 410)

    def test_completed_analysis_exposes_dashboard_read_model(self):
        session_id, token = self._session()
        path = f"/api/v1/sessions/{session_id}/operations"
        self.client.post(
            path,
            json=self._operation_body(),
            headers=self._headers(token),
        )
        self._wait_for_status(
            session_id,
            token,
            "operation-1",
            "completed",
        )

        response = self.client.get(
            f"{path}/operation-1/dashboard",
            headers=self._headers(token),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["schema_version"], "jarvis.dashboard.v1")
        self.assertEqual(body["symbol"], "RELIANCE-EQ")
        self.assertEqual(body["refresh"]["mode"], "initial")
        self.assertEqual(
            body["refresh"]["dataset_id"],
            self.handler.result.fetch.dataset_id,
        )
        self.assertEqual(
            body["refresh"]["final_candle_count"],
            len(self.handler.result.fetch.stored.series.candles),
        )
        self.assertEqual(body["daily"]["interval"], "ONE_DAY")
        self.assertEqual(body["weekly"]["interval"], "ONE_WEEK")
        self.assertEqual(
            body["interpretation"]["schema_version"],
            "jarvis.dashboard_interpretation.v1",
        )
        self.assertEqual(
            body["interpretation"]["trade_decision"]["decision"],
            "no_trade",
        )
        self.assertGreaterEqual(len(body["activities"]), 1)
        self.assertIn(body["debate"]["winner"], ("bullish", "bearish", "neutral"))

        dashboard_schema = self.client.get("/api/openapi.json").json()[
            "components"
        ]["schemas"]["JarvisDashboardView"]
        self.assertIn("interpretation", dashboard_schema["properties"])

    def test_sse_stream_replays_events_and_finishes_with_terminal_state(self):
        session_id, token = self._session()
        path = f"/api/v1/sessions/{session_id}/operations"
        self.client.post(
            path,
            json=self._operation_body(),
            headers=self._headers(token),
        )
        self._wait_for_status(
            session_id,
            token,
            "operation-1",
            "completed",
        )

        response = self.client.get(
            f"{path}/operation-1/events/stream",
            headers=self._headers(token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertIn("id: operation-1:1", response.text)
        self.assertIn("event: workflow", response.text)
        self.assertIn("event: terminal", response.text)
        self.assertIn('"status":"completed"', response.text)

    def test_sse_reconnect_cursor_avoids_duplicate_events(self):
        session_id, token = self._session()
        path = f"/api/v1/sessions/{session_id}/operations"
        self.client.post(
            path,
            json=self._operation_body(),
            headers=self._headers(token),
        )
        self._wait_for_status(
            session_id,
            token,
            "operation-1",
            "completed",
        )

        response = self.client.get(
            f"{path}/operation-1/events/stream",
            headers=self._headers(token)
            | {"Last-Event-ID": "operation-1:1"},
        )
        malformed = self.client.get(
            f"{path}/operation-1/events/stream",
            headers=self._headers(token) | {"Last-Event-ID": "not-an-id"},
        )
        beyond = self.client.get(
            f"{path}/operation-1/events/stream?after_sequence=999",
            headers=self._headers(token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("id: operation-1:1\n", response.text)
        self.assertIn("id: operation-1:2", response.text)
        self.assertIn("event: terminal", response.text)
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(beyond.status_code, 409)

    def test_cross_session_access_is_not_disclosed(self):
        session_1, token_1 = self._session()
        path = f"/api/v1/sessions/{session_1}/operations"
        self.client.post(
            path,
            json=self._operation_body(),
            headers=self._headers(token_1),
        )
        session_2, token_2 = self._session()

        response = self.client.get(
            f"/api/v1/sessions/{session_2}/operations/operation-1",
            headers=self._headers(token_2),
        )

        self.assertEqual(response.status_code, 404)

    def test_wake_conversation_dispatch_response_sleep_and_follow_up(self):
        session_id, token = self._session()
        headers = self._headers(token)
        path = f"/api/v1/sessions/{session_id}/conversation"
        ignored = self.client.post(
            f"{path}/turns",
            json={
                "idempotency_key": "turn-ignore",
                "input_channel": "text",
                "text": "Analyze Reliance",
            },
            headers=headers,
        )
        wake = self.client.post(
            f"{path}/turns",
            json={
                "idempotency_key": "turn-wake",
                "input_channel": "voice",
                "text": "Hey Jarvis",
            },
            headers=headers,
        )
        dispatched = self.client.post(
            f"{path}/turns",
            json={
                "idempotency_key": "turn-analysis",
                "input_channel": "voice",
                "text": "Analyze Reliance for a swing trade",
            },
            headers=headers,
        )
        operation_id = dispatched.json()["operation"]["request"]["operation_id"]
        self._wait_for_status(
            session_id,
            token,
            operation_id,
            "completed",
        )
        response_ready = self.client.get(path, headers=headers)
        sleeping = self.client.post(f"{path}/sleep", headers=headers)
        conversation_stream = self.client.get(
            f"{path}/events/stream",
            headers=headers,
        )
        event_page = self.client.get(
            f"{path}/events",
            headers=headers,
        ).json()
        terminal_reconnect = self.client.get(
            f"{path}/events/stream",
            headers=headers
            | {
                "Last-Event-ID": (
                    f"{session_id}:{event_page['next_sequence']}"
                )
            },
        )
        follow_up = self.client.post(
            f"{path}/turns",
            json={
                "idempotency_key": "turn-follow-up",
                "input_channel": "text",
                "text": "Hey Jarvis, where is weekly support?",
            },
            headers=headers,
        )

        self.assertEqual(ignored.json()["outcome"], "ignored")
        self.assertEqual(wake.json()["outcome"], "activated")
        self.assertEqual(
            wake.json()["conversation"]["display_message"],
            "Hello Prateek. How can I help you today?",
        )
        self.assertEqual(dispatched.json()["outcome"], "dispatched")
        self.assertEqual(response_ready.json()["state"], "responding")
        self.assertTrue(response_ready.json()["has_follow_up_context"])
        self.assertEqual(sleeping.json()["state"], "dormant")
        self.assertIn("event: conversation", conversation_stream.text)
        self.assertIn(
            "event: conversation-terminal",
            conversation_stream.text,
        )
        self.assertNotIn("event: conversation\n", terminal_reconnect.text)
        self.assertIn(
            "event: conversation-terminal",
            terminal_reconnect.text,
        )
        self.assertEqual(
            follow_up.json()["operation"]["request"]["kind"],
            "judge_follow_up",
        )

    def test_validation_and_closed_session_behavior(self):
        session_id, token = self._session()
        path = f"/api/v1/sessions/{session_id}/operations"
        invalid = self.client.post(
            path,
            json=self._operation_body() | {"message": "   "},
            headers=self._headers(token),
        )
        closed = self.client.delete(
            f"/api/v1/sessions/{session_id}",
            headers=self._headers(token),
        )
        after_close = self.client.post(
            path,
            json=self._operation_body(),
            headers=self._headers(token),
        )

        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["state"], "closed")
        self.assertEqual(after_close.status_code, 401)


class JarvisHttpCancellationTests(unittest.TestCase):
    def test_running_operation_can_be_cancelled_over_http(self):
        registry = InMemoryBrowserOperationRegistry()
        handler = _ApiHandler(blocking=True)
        runner = AsyncBrowserOperationRunner(
            registry,
            handler,
            InMemoryBrowserOperationResultStore(),
            max_workers=1,
        )
        client = TestClient(
            create_jarvis_http_app(
                runner,
                session_id_factory=lambda: "session-cancel",
                operation_id_factory=lambda: "operation-cancel",
            )
        )
        try:
            session = client.post("/api/v1/sessions").json()
            token = session["access_token"]
            headers = {"X-Jarvis-Session-Token": token}
            path = "/api/v1/sessions/session-cancel/operations"
            client.post(
                path,
                json={
                    "idempotency_key": "request-cancel",
                    "kind": "swing_analysis",
                    "input_channel": "text",
                    "message": "Analyze Reliance",
                },
                headers=headers,
            )
            self.assertTrue(handler.started.wait(timeout=1))
            streamed = {}

            def read_stream():
                streamed["response"] = client.get(
                    f"{path}/operation-cancel/events/stream",
                    headers=headers,
                )

            stream_thread = threading.Thread(target=read_stream)
            stream_thread.start()
            time.sleep(0.05)
            self.assertTrue(stream_thread.is_alive())
            pending_result = client.get(
                f"{path}/operation-cancel/result",
                headers=headers,
            )
            active_close = client.delete(
                "/api/v1/sessions/session-cancel",
                headers=headers,
            )
            cancelled = client.post(
                f"{path}/operation-cancel/cancel",
                headers=headers,
            )
            handler.release.set()
            stream_thread.join(timeout=2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                snapshot = client.get(
                    f"{path}/operation-cancel",
                    headers=headers,
                ).json()
                if snapshot["status"] == "cancelled":
                    break
                time.sleep(0.005)
            result = client.get(
                f"{path}/operation-cancel/result",
                headers=headers,
            )
        finally:
            handler.release.set()
            runner.shutdown()

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(pending_result.status_code, 202)
        self.assertIsNone(pending_result.json()["output"])
        self.assertEqual(active_close.status_code, 409)
        self.assertEqual(
            cancelled.json()["status"],
            "cancellation_requested",
        )
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertEqual(result.status_code, 409)
        self.assertIsNone(result.json()["output"])
        self.assertFalse(stream_thread.is_alive())
        self.assertIn("event: workflow", streamed["response"].text)
        self.assertIn("event: terminal", streamed["response"].text)
        self.assertIn('"status":"cancelled"', streamed["response"].text)


class _NullOperationHandler:
    """Minimal operation handler for tests that only exercise /speech."""

    def execute(self, request, *, event_emitter, cancellation_token):
        raise AssertionError("operation execution is not used by speech tests")


class FakeSpeechSynthesisApplication:
    def __init__(self, synthesis=None, failure=None):
        self.synthesis = synthesis
        self.failure = failure
        self.calls = []

    def synthesize(self, *, text):
        self.calls.append(text)
        if self.failure is not None:
            raise self.failure
        return self.synthesis


def _speech_synthesis():
    return SpeechSynthesis(
        audio=b"fake-mp3-audio-bytes",
        provider="google",
        voice="en-GB-Neural2-B",
        audio_encoding="MP3",
        media_type="audio/mpeg",
        generated_at=datetime.now(IST),
    )


class SpeechHttpApiTests(unittest.TestCase):
    def _build_client(self, *, speech):
        registry = InMemoryBrowserOperationRegistry()
        results = InMemoryBrowserOperationResultStore()
        runner = AsyncBrowserOperationRunner(
            registry, _NullOperationHandler(), results, max_workers=1
        )
        self.addCleanup(runner.shutdown)
        client = TestClient(
            create_jarvis_http_app(
                runner,
                speech=speech,
                authorizer=InMemoryBrowserSessionAuthorizer(),
            )
        )
        response = client.post("/api/v1/sessions")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        return client, body["session"]["session_id"], body["access_token"]

    def test_returns_binary_audio_with_correct_media_type(self):
        speech = FakeSpeechSynthesisApplication(synthesis=_speech_synthesis())
        client, session_id, token = self._build_client(speech=speech)

        response = client.post(
            f"/api/v1/sessions/{session_id}/speech",
            json={"text": "Analysis complete."},
            headers={"X-Jarvis-Session-Token": token},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(response.content, b"fake-mp3-audio-bytes")
        self.assertEqual(speech.calls, ["Analysis complete."])

    def test_route_does_not_exist_when_speech_not_configured(self):
        registry = InMemoryBrowserOperationRegistry()
        results = InMemoryBrowserOperationResultStore()
        runner = AsyncBrowserOperationRunner(
            registry, _NullOperationHandler(), results, max_workers=1
        )
        self.addCleanup(runner.shutdown)
        client = TestClient(
            create_jarvis_http_app(
                runner,
                authorizer=InMemoryBrowserSessionAuthorizer(),
            )
        )
        session_response = client.post("/api/v1/sessions")
        session_id = session_response.json()["session"]["session_id"]
        token = session_response.json()["access_token"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/speech",
            json={"text": "Analysis complete."},
            headers={"X-Jarvis-Session-Token": token},
        )

        self.assertEqual(response.status_code, 404)

    def test_rejects_request_without_valid_token(self):
        speech = FakeSpeechSynthesisApplication(synthesis=_speech_synthesis())
        client, session_id, _token = self._build_client(speech=speech)

        response = client.post(
            f"/api/v1/sessions/{session_id}/speech",
            json={"text": "Analysis complete."},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(speech.calls, [])

    def test_configuration_error_maps_to_503(self):
        speech = FakeSpeechSynthesisApplication(
            failure=TTSConfigurationError("not configured", provider="google")
        )
        client, session_id, token = self._build_client(speech=speech)

        response = client.post(
            f"/api/v1/sessions/{session_id}/speech",
            json={"text": "Analysis complete."},
            headers={"X-Jarvis-Session-Token": token},
        )

        self.assertEqual(response.status_code, 503)

    def test_provider_unavailable_maps_to_502(self):
        speech = FakeSpeechSynthesisApplication(
            failure=TTSProviderUnavailableError("down", provider="google")
        )
        client, session_id, token = self._build_client(speech=speech)

        response = client.post(
            f"/api/v1/sessions/{session_id}/speech",
            json={"text": "Analysis complete."},
            headers={"X-Jarvis-Session-Token": token},
        )

        self.assertEqual(response.status_code, 502)

    def test_authentication_error_maps_to_502(self):
        speech = FakeSpeechSynthesisApplication(
            failure=TTSAuthenticationError("bad credential", provider="google")
        )
        client, session_id, token = self._build_client(speech=speech)

        response = client.post(
            f"/api/v1/sessions/{session_id}/speech",
            json={"text": "Analysis complete."},
            headers={"X-Jarvis-Session-Token": token},
        )

        self.assertEqual(response.status_code, 502)

    def test_rejects_blank_text(self):
        speech = FakeSpeechSynthesisApplication(synthesis=_speech_synthesis())
        client, session_id, token = self._build_client(speech=speech)

        response = client.post(
            f"/api/v1/sessions/{session_id}/speech",
            json={"text": "   "},
            headers={"X-Jarvis-Session-Token": token},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(speech.calls, [])


class FakeTranscriptionApplication:
    def __init__(self, transcription=None, failure=None):
        self.transcription = transcription
        self.failure = failure
        self.calls = []

    def transcribe(self, *, audio, media_type):
        self.calls.append((audio, media_type))
        if self.failure is not None:
            raise self.failure
        return self.transcription


def _transcription(**overrides):
    values = {
        "transcript": "Analyze Reliance for a swing trade",
        "provider": "google",
        "language_code": "en-IN",
        "confidence": 0.92,
        "generated_at": datetime.now(IST),
    }
    values.update(overrides)
    return Transcription(**values)


class TranscriptionHttpApiTests(unittest.TestCase):
    def _build_client(self, *, transcription):
        registry = InMemoryBrowserOperationRegistry()
        results = InMemoryBrowserOperationResultStore()
        runner = AsyncBrowserOperationRunner(
            registry, _NullOperationHandler(), results, max_workers=1
        )
        self.addCleanup(runner.shutdown)
        client = TestClient(
            create_jarvis_http_app(
                runner,
                transcription=transcription,
                authorizer=InMemoryBrowserSessionAuthorizer(),
            )
        )
        response = client.post("/api/v1/sessions")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        return client, body["session"]["session_id"], body["access_token"]

    def test_returns_transcript_and_confidence(self):
        transcription = FakeTranscriptionApplication(
            transcription=_transcription()
        )
        client, session_id, token = self._build_client(transcription=transcription)

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"fake-webm-audio-bytes",
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["transcript"], "Analyze Reliance for a swing trade")
        self.assertEqual(body["confidence"], 0.92)
        self.assertEqual(
            transcription.calls, [(b"fake-webm-audio-bytes", "audio/webm")]
        )

    def test_no_speech_detected_returns_200_with_null_transcript(self):
        transcription = FakeTranscriptionApplication(
            transcription=_transcription(transcript="", confidence=None)
        )
        client, session_id, token = self._build_client(transcription=transcription)

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"fake-silence-audio-bytes",
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["transcript"])
        self.assertIsNone(body["confidence"])

    def test_route_does_not_exist_when_transcription_not_configured(self):
        registry = InMemoryBrowserOperationRegistry()
        results = InMemoryBrowserOperationResultStore()
        runner = AsyncBrowserOperationRunner(
            registry, _NullOperationHandler(), results, max_workers=1
        )
        self.addCleanup(runner.shutdown)
        client = TestClient(
            create_jarvis_http_app(
                runner,
                authorizer=InMemoryBrowserSessionAuthorizer(),
            )
        )
        session_response = client.post("/api/v1/sessions")
        session_id = session_response.json()["session"]["session_id"]
        token = session_response.json()["access_token"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"fake-webm-audio-bytes",
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 404)

    def test_rejects_request_without_valid_token(self):
        transcription = FakeTranscriptionApplication(
            transcription=_transcription()
        )
        client, session_id, _token = self._build_client(transcription=transcription)

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"fake-webm-audio-bytes",
            headers={"Content-Type": "audio/webm"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(transcription.calls, [])

    def test_rejects_empty_body(self):
        transcription = FakeTranscriptionApplication(
            transcription=_transcription()
        )
        client, session_id, token = self._build_client(transcription=transcription)

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"",
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(transcription.calls, [])

    def test_rejects_oversized_body(self):
        transcription = FakeTranscriptionApplication(
            transcription=_transcription()
        )
        client, session_id, token = self._build_client(transcription=transcription)

        oversized = b"x" * (10 * 1024 * 1024 + 1)
        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=oversized,
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(transcription.calls, [])

    def test_configuration_error_maps_to_503(self):
        transcription = FakeTranscriptionApplication(
            failure=STTConfigurationError("not configured", provider="google")
        )
        client, session_id, token = self._build_client(transcription=transcription)

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"fake-webm-audio-bytes",
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 503)

    def test_provider_unavailable_maps_to_502(self):
        transcription = FakeTranscriptionApplication(
            failure=STTProviderUnavailableError("down", provider="google")
        )
        client, session_id, token = self._build_client(transcription=transcription)

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"fake-webm-audio-bytes",
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 502)

    def test_authentication_error_maps_to_502(self):
        transcription = FakeTranscriptionApplication(
            failure=STTAuthenticationError("bad credential", provider="google")
        )
        client, session_id, token = self._build_client(transcription=transcription)

        response = client.post(
            f"/api/v1/sessions/{session_id}/transcribe",
            content=b"fake-webm-audio-bytes",
            headers={
                "X-Jarvis-Session-Token": token,
                "Content-Type": "audio/webm",
            },
        )

        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
