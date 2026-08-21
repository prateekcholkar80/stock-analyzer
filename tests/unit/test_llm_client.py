import unittest
from types import SimpleNamespace

from pydantic import BaseModel, Field

from app.exceptions import LLMResponseValidationError
from app.llm.client import LLMClient, LLMGenerationConfig


class SampleModel(BaseModel):
    value: str = Field(min_length=1)


def _response(tool_name: str, arguments: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=tool_name,
                                arguments=arguments,
                            )
                        )
                    ]
                )
            )
        ]
    )


def _response_without_tool_call():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))]
    )


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        self.config = LLMGenerationConfig(
            model="anthropic/claude-sonnet-4-5",
            temperature=0.0,
            max_tokens=100,
        )

    def test_successful_structured_completion(self):
        calls = []

        def completion_fn(**kwargs):
            calls.append(kwargs)
            return _response(
                "emit_samplemodel",
                '{"value": "ok"}',
            )

        client = LLMClient(completion_fn=completion_fn)
        result = client.complete_structured(
            config=self.config,
            system="system prompt",
            messages=[{"role": "user", "content": "hi"}],
            response_model=SampleModel,
        )

        self.assertEqual(result.value, "ok")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["tool_choice"]["function"]["name"],
            "emit_samplemodel",
        )

    def test_retries_once_then_succeeds(self):
        responses = [
            _response("emit_samplemodel", '{"value": ""}'),
            _response("emit_samplemodel", '{"value": "fixed"}'),
        ]

        def completion_fn(**kwargs):
            return responses.pop(0)

        client = LLMClient(completion_fn=completion_fn)
        result = client.complete_structured(
            config=self.config,
            system="system prompt",
            messages=[{"role": "user", "content": "hi"}],
            response_model=SampleModel,
        )

        self.assertEqual(result.value, "fixed")
        self.assertEqual(responses, [])

    def test_raises_after_two_failed_attempts(self):
        def completion_fn(**kwargs):
            return _response("emit_samplemodel", '{"value": ""}')

        client = LLMClient(completion_fn=completion_fn)
        with self.assertRaises(LLMResponseValidationError):
            client.complete_structured(
                config=self.config,
                system="system prompt",
                messages=[{"role": "user", "content": "hi"}],
                response_model=SampleModel,
            )

    def test_missing_tool_call_triggers_retry_then_raises(self):
        def completion_fn(**kwargs):
            return _response_without_tool_call()

        client = LLMClient(completion_fn=completion_fn)
        with self.assertRaises(LLMResponseValidationError):
            client.complete_structured(
                config=self.config,
                system="system prompt",
                messages=[{"role": "user", "content": "hi"}],
                response_model=SampleModel,
            )

    def test_unexpected_tool_name_triggers_retry_then_raises(self):
        def completion_fn(**kwargs):
            return _response("some_other_tool", '{"value": "ok"}')

        client = LLMClient(completion_fn=completion_fn)
        with self.assertRaises(LLMResponseValidationError):
            client.complete_structured(
                config=self.config,
                system="system prompt",
                messages=[{"role": "user", "content": "hi"}],
                response_model=SampleModel,
            )


if __name__ == "__main__":
    unittest.main()
