import threading
import time
import unittest
import warnings
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=UserWarning,
)

from fastapi.testclient import TestClient

from app.api.http import create_jarvis_http_app
from app.api.session_auth import InMemoryBrowserSessionAuthorizer
from app.conversation.browser import BrowserConversationCoordinator
from app.conversation.config import JarvisConversationConfig
from app.models.browser_operations import (
    BrowserOperationOutput,
    BrowserOperationStatus,
)
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.debate import JudgeFollowUpAnswer
from app.models.workflow import WorkflowEventState, WorkflowStage
from app.workflow.browser_runner import (
    AsyncBrowserOperationRunner,
    InMemoryBrowserOperationResultStore,
)
from app.workflow.operations import InMemoryBrowserOperationRegistry
from tests.unit.test_jarvis_conversation import (
    RecordingPresenter,
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
    def __init__(self, *, blocking=False):
        self.blocking = blocking
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.result = _serializable_result()
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
        )


class JarvisHttpApiTests(unittest.TestCase):
    def setUp(self):
        self.registry = InMemoryBrowserOperationRegistry()
        self.results = InMemoryBrowserOperationResultStore()
        self.handler = _ApiHandler()
        self.runner = AsyncBrowserOperationRunner(
            self.registry,
            self.handler,
            self.results,
            max_workers=1,
        )
        self.authorizer = InMemoryBrowserSessionAuthorizer()
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
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json()["events"][0]["sequence"], 1)
        self.assertTrue(events.json()["has_more"])

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


if __name__ == "__main__":
    unittest.main()
