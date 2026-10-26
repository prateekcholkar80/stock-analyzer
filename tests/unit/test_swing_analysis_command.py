import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.commands.swing_analysis import JarvisSwingAnalysisCommandHandler
from app.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMRateLimitError,
)
from app.logging_config import get_operation_id
from app.models.interaction import (
    JarvisCommandStatus,
    JarvisSwingAnalysisResponse,
    SwingAnalysisCommand,
)
from app.models.llm import LLMFailureCode
from app.models.storage import EndToEndSwingAnalysisResult
from tests.unit.test_run_end_to_end_multi_timeframe_swing_analysis import (
    _use_case as _multi_timeframe_use_case,
)


def _result():
    return EndToEndSwingAnalysisResult.model_construct(
        use_case_id="jarvis.run_end_to_end_swing_analysis.v1",
        market_dataset_id="market:test",
        fetch=object(),
        technical_result=object(),
        debate_result=object(),
    )


def _research_division_result():
    return _multi_timeframe_use_case().prepare_research_division(
        "NSE",
        "2885",
        "RELIANCE-EQ",
        "ONE_HOUR",
    )


class StubExecutor:
    def __init__(self, *, result=None, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append((kwargs, get_operation_id()))
        if self.failure is not None:
            raise self.failure
        return self.result


class IncrementalStubExecutor(StubExecutor):
    def __init__(self, *, research, result=None, failure=None):
        super().__init__(result=result, failure=failure)
        self.research = research

    def prepare_research_division(self, **kwargs):
        self.calls.append(("prepare", kwargs, get_operation_id()))
        return self.research

    def complete_research_division(self, research, **kwargs):
        self.calls.append(
            ("complete", research, kwargs, get_operation_id())
        )
        if self.failure is not None:
            raise self.failure
        return self.result


class JarvisSwingAnalysisCommandTests(unittest.TestCase):
    def setUp(self):
        self.command = SwingAnalysisCommand(
            exchange=" NSE ",
            symbol_token=" 2885 ",
            symbol=" RELIANCE-EQ ",
            interval=" ONE_HOUR ",
            to_date=datetime(2026, 8, 21, 15, 30, tzinfo=UTC),
        )

    def test_success_returns_complete_result_and_operation_id(self):
        expected = _result()
        executor = StubExecutor(result=expected)
        handler = JarvisSwingAnalysisCommandHandler(lambda: executor)

        response = handler.execute(self.command)

        self.assertEqual(response.status, JarvisCommandStatus.COMPLETED)
        self.assertIs(response.result, expected)
        self.assertIsNone(response.failure)
        self.assertEqual(len(response.operation_id), 32)
        kwargs, observed_operation_id = executor.calls[0]
        self.assertEqual(observed_operation_id, response.operation_id)
        event_emitter = kwargs.pop("event_emitter")
        self.assertEqual(event_emitter.operation_id, response.operation_id)
        self.assertEqual(
            kwargs,
            {
                "exchange": "NSE",
                "symbol_token": "2885",
                "symbol": "RELIANCE-EQ",
                "interval": "ONE_HOUR",
                "to_date": self.command.to_date,
            },
        )
        self.assertIsNone(get_operation_id())

    def test_uses_boundary_supplied_operation_id(self):
        executor = StubExecutor(result=_result())

        response = JarvisSwingAnalysisCommandHandler(
            lambda: executor
        ).execute(
            self.command,
            operation_id="facade-operation",
        )

        self.assertEqual(response.operation_id, "facade-operation")
        self.assertEqual(executor.calls[0][1], "facade-operation")
        self.assertIsNone(get_operation_id())

    def test_rejects_invalid_boundary_operation_id(self):
        with self.assertRaisesRegex(ValueError, "operation ID"):
            JarvisSwingAnalysisCommandHandler(
                lambda: StubExecutor(result=_result())
            ).execute(self.command, operation_id=" ")

    def test_composition_llm_failure_returns_ui_safe_envelope(self):
        secret = "configuration-secret"

        def failing_factory():
            raise LLMConfigurationError(secret)

        response = JarvisSwingAnalysisCommandHandler(
            failing_factory
        ).execute(self.command)

        self.assertEqual(response.status, JarvisCommandStatus.LLM_FAILURE)
        self.assertEqual(response.failure.code, LLMFailureCode.CONFIGURATION)
        self.assertEqual(
            response.failure.operation_id,
            response.operation_id,
        )
        self.assertFalse(response.failure.analysis_available)
        self.assertNotIn(secret, response.model_dump_json())
        self.assertIsNone(get_operation_id())

    def test_execution_llm_failure_preserves_classification(self):
        executor = StubExecutor(
            failure=LLMRateLimitError(
                "provider secret",
                role="bear",
                retryable=True,
            )
        )

        response = JarvisSwingAnalysisCommandHandler(
            lambda: executor
        ).execute(self.command)

        self.assertEqual(response.failure.code, LLMFailureCode.RATE_LIMITED)
        self.assertTrue(response.failure.retryable)
        self.assertEqual(response.failure.failed_role.value, "bear")
        self.assertNotIn("provider secret", response.model_dump_json())

    def test_llm_failure_can_retain_completed_research_without_conclusion(self):
        research = _research_division_result()
        executor = IncrementalStubExecutor(
            research=research,
            failure=LLMRateLimitError(
                "provider secret",
                role="bull",
                retryable=True,
            ),
        )

        response = JarvisSwingAnalysisCommandHandler(
            lambda: executor
        ).execute(self.command)

        self.assertEqual(
            [call[0] for call in executor.calls],
            ["prepare", "complete"],
        )
        self.assertIs(executor.calls[1][1], research)
        self.assertIs(response.research_division_result, research)
        self.assertEqual(response.failure.failed_role.value, "bull")
        self.assertIsNone(response.result)
        self.assertIsNone(response.multi_timeframe_review)
        self.assertIsNone(response.multi_timeframe_debate)

    def test_incremental_success_runs_dependencies_in_order(self):
        research = _research_division_result()
        result = _result()
        executor = IncrementalStubExecutor(
            research=research,
            result=result,
        )

        response = JarvisSwingAnalysisCommandHandler(
            lambda: executor
        ).execute(self.command)

        self.assertEqual(
            [call[0] for call in executor.calls],
            ["prepare", "complete"],
        )
        self.assertIs(response.result, result)
        self.assertIsNone(response.research_division_result)

    def test_response_contract_accepts_retained_research_artifact(self):
        research = _research_division_result()
        failure = JarvisSwingAnalysisCommandHandler(
            lambda: StubExecutor(
                failure=LLMRateLimitError(
                    "provider secret",
                    role="bull",
                    retryable=True,
                )
            )
        ).execute(self.command).failure

        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id=failure.operation_id,
            failure=failure,
            research_division_result=research,
        )

        self.assertIs(response.research_division_result, research)
        self.assertIsNone(response.result)
        self.assertIsNone(response.multi_timeframe_review)
        self.assertIsNone(response.multi_timeframe_debate)

    def test_boundary_operation_id_replaces_stale_failure_operation_id(self):
        executor = StubExecutor(
            failure=LLMAuthenticationError(
                "raw",
                operation_id="different-operation",
            )
        )

        response = JarvisSwingAnalysisCommandHandler(
            lambda: executor
        ).execute(self.command)

        self.assertEqual(response.failure.operation_id, response.operation_id)
        self.assertNotEqual(
            response.failure.operation_id,
            "different-operation",
        )

    def test_does_not_disguise_unrelated_failures(self):
        def broken_factory():
            raise RuntimeError("programming defect")

        with self.assertRaisesRegex(RuntimeError, "programming defect"):
            JarvisSwingAnalysisCommandHandler(
                broken_factory
            ).execute(self.command)

    def test_rejects_invalid_factory_executor_command_and_result(self):
        with self.assertRaises(ValueError):
            JarvisSwingAnalysisCommandHandler(None)
        with self.assertRaisesRegex(ValueError, "validated command"):
            JarvisSwingAnalysisCommandHandler(lambda: object()).execute(
                object()
            )
        with self.assertRaisesRegex(ValueError, "invalid executor"):
            JarvisSwingAnalysisCommandHandler(lambda: object()).execute(
                self.command
            )
        with self.assertRaisesRegex(ValueError, "invalid result"):
            JarvisSwingAnalysisCommandHandler(
                lambda: StubExecutor(result=object())
            ).execute(self.command)

    def test_command_rejects_blank_or_naive_inputs(self):
        for field in ("exchange", "symbol_token", "symbol", "interval"):
            values = {
                "exchange": "NSE",
                "symbol_token": "2885",
                "symbol": "RELIANCE-EQ",
                "interval": "ONE_HOUR",
            }
            values[field] = " "
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    SwingAnalysisCommand(**values)

        with self.assertRaisesRegex(ValidationError, "include timezone"):
            SwingAnalysisCommand(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                to_date=datetime(2026, 8, 21, 15, 30),
            )

    def test_response_rejects_mixed_or_missing_payloads(self):
        failure = JarvisSwingAnalysisCommandHandler(
            lambda: (_ for _ in ()).throw(LLMConfigurationError("raw"))
        ).execute(self.command).failure

        with self.assertRaises(ValidationError):
            JarvisSwingAnalysisResponse(
                operation_id="operation",
                status=JarvisCommandStatus.COMPLETED,
                failure=failure,
            )
        with self.assertRaises(ValidationError):
            JarvisSwingAnalysisResponse(
                operation_id="operation",
                status=JarvisCommandStatus.LLM_FAILURE,
                result=_result(),
            )


if __name__ == "__main__":
    unittest.main()
