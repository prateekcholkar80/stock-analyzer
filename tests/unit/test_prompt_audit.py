import json
import stat
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.audit.prompt_audit import (
    InMemoryPromptAuditSink,
    JsonlPromptAuditSink,
    NullPromptAuditSink,
    PromptAuditActor,
    PromptAuditConfig,
    PromptAuditEventType,
    PromptAuditRecorder,
    get_prompt_audit_session_id,
    prompt_audit_session_context,
    prompt_audit_sink_from_config,
)
from app.conversation.config import JarvisConversationConfig
from app.conversation.session import JarvisConversationSession
from app.composition.conversation import compose_jarvis_conversation
from app.instruments.in_memory import InMemoryInstrumentResolver
from app.llm.audited_gateway import PromptAuditedLLMGateway
from app.llm.config import LLMRole
from app.llm.gateway import StructuredGeneration
from app.logging_config import operation_context
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.instruments import ResolvedInstrument
from app.models.storage import EndToEndSwingAnalysisResult


IST = ZoneInfo("Asia/Kolkata")


class Draft(BaseModel):
    answer: str


class RecordingGateway:
    configuration_fingerprint = "f" * 64

    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def generate(self, *, system, messages, response_model):
        self.calls.append((system, messages, response_model))
        if self.failure is not None:
            raise self.failure
        return StructuredGeneration[response_model](
            value=response_model(answer="grounded response"),
            provider="provider",
            model="model",
            attempt_count=1,
        )


class RecordingResearchExecutor:
    def execute(self, text, *, to_date=None):
        result = EndToEndSwingAnalysisResult.model_construct(
            use_case_id="jarvis.run_end_to_end_swing_analysis.v1",
            market_dataset_id="market:test",
            fetch=object(),
            technical_result=object(),
            debate_result=object(),
        )
        return JarvisSwingAnalysisResponse.completed(
            operation_id="operation-1",
            result=result,
        )


class FailingSink:
    def publish(self, record):
        raise OSError("api_key=must-not-escape")


class PromptAuditConfigTests(unittest.TestCase):
    def test_is_disabled_by_default_and_supports_explicit_opt_in(self):
        disabled = PromptAuditConfig.from_environment({})
        enabled = PromptAuditConfig.from_environment(
            {
                "JARVIS_PROMPT_AUDIT_ENABLED": "yes",
                "JARVIS_PROMPT_AUDIT_PATH": "logs/review.jsonl",
            }
        )

        self.assertFalse(disabled.enabled)
        self.assertIsInstance(
            prompt_audit_sink_from_config(disabled),
            NullPromptAuditSink,
        )
        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.path, Path("logs/review.jsonl"))
        self.assertIsInstance(
            prompt_audit_sink_from_config(enabled),
            JsonlPromptAuditSink,
        )

    def test_rejects_invalid_boolean_or_non_jsonl_configuration(self):
        with self.assertRaisesRegex(ValueError, "true or false"):
            PromptAuditConfig.from_environment(
                {"JARVIS_PROMPT_AUDIT_ENABLED": "perhaps"}
            )
        with self.assertRaisesRegex(ValueError, r"\.jsonl"):
            PromptAuditConfig(
                enabled=True,
                path=Path("logs/prompts.txt"),
            )


class PromptAuditRecorderTests(unittest.TestCase):
    def test_redacts_nested_credentials_and_correlates_context(self):
        sink = InMemoryPromptAuditSink()
        recorder = PromptAuditRecorder(
            sink,
            clock=lambda: datetime(2026, 8, 22, 10, 0, tzinfo=IST),
        )

        with prompt_audit_session_context("session-1"):
            with operation_context("operation-1"):
                recorder.record(
                    PromptAuditEventType.LLM_REQUEST,
                    PromptAuditActor.BULL,
                    {
                        "system_prompt": (
                            "OPENAI_API_KEY=provider-secret "
                            "Bearer bearer-secret"
                        ),
                        "authorization": "Bearer hidden",
                    },
                )

        record = sink.records[0]
        rendered = json.dumps(record)
        self.assertEqual(record["session_id"], "session-1")
        self.assertEqual(record["operation_id"], "operation-1")
        self.assertEqual(record["actor"], "bull")
        self.assertIn("[REDACTED]", rendered)
        for secret in ("provider-secret", "bearer-secret", "hidden"):
            self.assertNotIn(secret, rendered)
        self.assertIsNone(get_prompt_audit_session_id())

    def test_sink_failure_is_safe_and_does_not_render_raw_message(self):
        recorder = PromptAuditRecorder(FailingSink())

        with self.assertLogs("jarvis.audit.prompt_audit", level="ERROR") as logs:
            recorder.record(
                PromptAuditEventType.CONVERSATION_INPUT,
                PromptAuditActor.USER,
                {"content": "safe request"},
            )

        self.assertNotIn("must-not-escape", " ".join(logs.output))

    def test_rejects_non_ist_clock(self):
        recorder = PromptAuditRecorder(
            InMemoryPromptAuditSink(),
            clock=lambda: datetime.fromisoformat("2026-08-22T10:00:00+00:00"),
        )

        with self.assertRaisesRegex(ValueError, "in IST"):
            recorder.record(
                PromptAuditEventType.CONVERSATION_INPUT,
                PromptAuditActor.USER,
                {"content": "request"},
            )


class JsonlPromptAuditSinkTests(unittest.TestCase):
    def test_writes_private_valid_jsonl_under_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "audit.jsonl"
            sink = JsonlPromptAuditSink(path)
            threads = [
                threading.Thread(
                    target=sink.publish,
                    args=({"index": index, "content": "complete"},),
                )
                for index in range(30)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 30)
            self.assertEqual(
                {record["index"] for record in records},
                set(range(30)),
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_restricts_permissions_on_an_existing_audit_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text("", encoding="utf-8")
            path.chmod(0o644)

            JsonlPromptAuditSink(path).publish({"content": "review"})

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class PromptAuditedLLMGatewayTests(unittest.TestCase):
    def test_records_exact_prompt_schema_and_structured_response(self):
        sink = InMemoryPromptAuditSink()
        underlying = RecordingGateway()
        gateway = PromptAuditedLLMGateway(
            underlying,
            LLMRole.BULL,
            sink,
        )

        with prompt_audit_session_context("session-1"):
            with operation_context("operation-1"):
                generation = gateway.generate(
                    system="# Role\nBull",
                    messages=[{"role": "user", "content": "# Context\ndata"}],
                    response_model=Draft,
                )

        self.assertEqual(generation.value.answer, "grounded response")
        self.assertEqual(
            [record["event_type"] for record in sink.records],
            ["llm_request", "llm_response"],
        )
        request = sink.records[0]
        response = sink.records[1]
        self.assertEqual(request["payload"]["system_prompt"], "# Role\nBull")
        self.assertEqual(
            request["payload"]["messages"][0]["content"],
            "# Context\ndata",
        )
        self.assertEqual(request["payload"]["response_model"], "Draft")
        self.assertIn("properties", request["payload"]["response_schema"])
        self.assertEqual(
            response["payload"]["structured_response"],
            {"answer": "grounded response"},
        )
        self.assertTrue(
            all(record["session_id"] == "session-1" for record in sink.records)
        )
        self.assertTrue(
            all(
                record["operation_id"] == "operation-1"
                for record in sink.records
            )
        )

    def test_records_only_failure_type_and_propagates_error(self):
        sink = InMemoryPromptAuditSink()
        gateway = PromptAuditedLLMGateway(
            RecordingGateway(RuntimeError("api_key=secret")),
            LLMRole.JUDGE,
            sink,
        )

        with self.assertRaises(RuntimeError):
            gateway.generate(
                system="judge",
                messages=[{"role": "user", "content": "context"}],
                response_model=Draft,
            )

        self.assertEqual(
            [record["event_type"] for record in sink.records],
            ["llm_request", "llm_failure"],
        )
        rendered = json.dumps(sink.records)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn("secret", rendered)


class ConversationPromptAuditTests(unittest.TestCase):
    def test_records_activated_conversation_but_not_dormant_noise(self):
        sink = InMemoryPromptAuditSink()
        session = JarvisConversationSession(
            RecordingResearchExecutor(),
            JarvisConversationConfig(user_name="Prateek"),
            prompt_audit_sink=sink,
            session_id_factory=lambda: "session-1",
        )

        session.handle_text("Analyze noise without activation")
        session.handle_text("Hey Jarvis")
        session.handle_text("Analyze Reliance for a swing trade")

        self.assertEqual(
            [record["event_type"] for record in sink.records],
            [
                "conversation_input",
                "conversation_output",
                "conversation_input",
                "conversation_output",
            ],
        )
        self.assertEqual(
            sink.records[0]["payload"]["content"],
            "Hey Jarvis",
        )
        self.assertEqual(
            sink.records[2]["payload"]["content"],
            "Analyze Reliance for a swing trade",
        )
        self.assertTrue(
            all(record["session_id"] == "session-1" for record in sink.records)
        )
        self.assertEqual(sink.records[-1]["operation_id"], "operation-1")

    def test_conversation_composition_creates_configured_jsonl_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jarvis.jsonl"
            session = compose_jarvis_conversation(
                object(),
                conversation_config=JarvisConversationConfig(
                    user_name="Prateek"
                ),
                prompt_audit_config=PromptAuditConfig(
                    enabled=True,
                    path=path,
                ),
                instrument_resolver=InMemoryInstrumentResolver(
                    (
                        ResolvedInstrument(
                            exchange="NSE",
                            symbol_token="2885",
                            symbol="RELIANCE-EQ",
                            display_name="RELIANCE",
                        ),
                    )
                ),
            )

            session.handle_text("Hey Jarvis")

            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [record["event_type"] for record in records],
                ["conversation_input", "conversation_output"],
            )
            self.assertEqual(records[0]["actor"], "user")
            self.assertEqual(records[1]["actor"], "jarvis")

    def test_conversation_composition_rejects_two_audit_sources(self):
        with self.assertRaisesRegex(ValueError, "either prompt audit"):
            compose_jarvis_conversation(
                object(),
                conversation_config=JarvisConversationConfig(
                    user_name="Prateek"
                ),
                prompt_audit_config=PromptAuditConfig(),
                prompt_audit_sink=InMemoryPromptAuditSink(),
            )


if __name__ == "__main__":
    unittest.main()
