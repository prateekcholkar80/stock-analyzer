import unittest
from types import SimpleNamespace

import litellm
from pydantic import BaseModel, Field

from app.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
)
from app.llm.adapters.litellm_gateway import LiteLLMStructuredGateway
from app.llm.config import LLMRole, LLMRoleSettings
from app.llm.gateway import StructuredLLMGateway


class SampleResponse(BaseModel):
    conclusion: str = Field(min_length=1)


def _response(arguments: str, *, tool_name="emit_sampleresponse", model=None):
    return SimpleNamespace(
        model=model,
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
        ],
    )


def _response_without_tool_call():
    return SimpleNamespace(
        model=None,
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))],
    )


def _provider_resolver(*, model):
    return model, "test-provider", None, None


class LiteLLMStructuredGatewayTests(unittest.TestCase):
    def setUp(self):
        self.settings = LLMRoleSettings(
            role=LLMRole.BULL,
            model="test-provider/requested-model",
            temperature=0.4,
            max_tokens=800,
        )

    def _gateway(self, completion_fn, provider_resolver=_provider_resolver):
        return LiteLLMStructuredGateway(
            self.settings,
            completion_fn=completion_fn,
            provider_resolver=provider_resolver,
        )

    def test_satisfies_provider_neutral_gateway_contract(self):
        gateway = self._gateway(
            lambda **kwargs: _response('{"conclusion": "Validated"}')
        )

        self.assertIsInstance(gateway, StructuredLLMGateway)

    def test_configuration_fingerprint_tracks_resolved_role_settings(self):
        first = self._gateway(
            lambda **kwargs: _response('{"conclusion": "Validated"}')
        )
        second_settings = self.settings.model_copy(
            update={"max_tokens": 801}
        )
        second = LiteLLMStructuredGateway(
            second_settings,
            completion_fn=lambda **kwargs: _response(
                '{"conclusion": "Validated"}'
            ),
            provider_resolver=_provider_resolver,
        )

        self.assertEqual(len(first.configuration_fingerprint), 64)
        self.assertNotEqual(
            first.configuration_fingerprint,
            second.configuration_fingerprint,
        )

    def test_returns_validated_response_and_actual_execution_metadata(self):
        calls = []

        def completion_fn(**kwargs):
            calls.append(kwargs)
            return _response(
                '{"conclusion": "Grounded"}',
                model="actual-provider-model",
            )

        generation = self._gateway(completion_fn).generate(
            system="System prompt",
            messages=[{"role": "user", "content": "Analyze evidence"}],
            response_model=SampleResponse,
        )

        self.assertEqual(generation.value.conclusion, "Grounded")
        self.assertEqual(generation.provider, "test-provider")
        self.assertEqual(generation.model, "actual-provider-model")
        self.assertEqual(generation.attempt_count, 1)
        self.assertEqual(calls[0]["model"], self.settings.model)
        self.assertEqual(calls[0]["temperature"], 0.4)
        self.assertEqual(calls[0]["max_tokens"], 800)
        self.assertEqual(
            calls[0]["tool_choice"]["function"]["name"],
            "emit_sampleresponse",
        )

    def test_falls_back_to_configured_model_when_response_omits_model(self):
        gateway = self._gateway(
            lambda **kwargs: _response('{"conclusion": "Validated"}')
        )

        generation = gateway.generate(
            system="System prompt",
            messages=[],
            response_model=SampleResponse,
        )

        self.assertEqual(generation.model, self.settings.model)

    def test_retries_malformed_schema_once_and_reports_attempt_count(self):
        responses = [
            _response('{"conclusion": ""}'),
            _response('{"conclusion": "Corrected"}'),
        ]
        calls = []

        def completion_fn(**kwargs):
            calls.append(kwargs)
            return responses.pop(0)

        generation = self._gateway(completion_fn).generate(
            system="System prompt",
            messages=[{"role": "user", "content": "Analyze"}],
            response_model=SampleResponse,
        )

        self.assertEqual(generation.value.conclusion, "Corrected")
        self.assertEqual(generation.attempt_count, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[1]["messages"]), 3)
        self.assertNotIn("conclusion", calls[1]["messages"][-1]["content"])

    def test_does_not_mutate_caller_messages_during_retry(self):
        messages = [{"role": "user", "content": "Original"}]
        original = [dict(message) for message in messages]
        responses = [
            _response_without_tool_call(),
            _response('{"conclusion": "Corrected"}'),
        ]

        self._gateway(lambda **kwargs: responses.pop(0)).generate(
            system="System prompt",
            messages=messages,
            response_model=SampleResponse,
        )

        self.assertEqual(messages, original)

    def test_rejects_missing_wrong_or_invalid_tool_output_after_retry(self):
        invalid_responses = (
            _response_without_tool_call(),
            _response("{}", tool_name="unexpected_tool"),
            _response("not-json"),
        )

        for invalid_response in invalid_responses:
            with self.subTest(response=invalid_response):
                gateway = self._gateway(
                    lambda **kwargs: invalid_response
                )
                with self.assertRaises(LLMResponseValidationError) as context:
                    gateway.generate(
                        system="System prompt",
                        messages=[],
                        response_model=SampleResponse,
                    )
                self.assertEqual(context.exception.context.role, "bull")
                self.assertEqual(
                    context.exception.context.provider,
                    "test-provider",
                )

    def test_translates_authentication_failure_without_leaking_secret(self):
        secret = "provider-secret-must-not-leak"

        def completion_fn(**kwargs):
            raise litellm.AuthenticationError(
                f"invalid key {secret}",
                "test-provider",
                self.settings.model,
            )

        with self.assertRaises(LLMAuthenticationError) as context:
            self._gateway(completion_fn).generate(
                system="System prompt",
                messages=[],
                response_model=SampleResponse,
            )

        self.assertIsInstance(
            context.exception.__cause__,
            litellm.AuthenticationError,
        )
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception.context))
        self.assertFalse(context.exception.context.retryable)

    def test_translates_rate_limit_failure(self):
        def completion_fn(**kwargs):
            raise litellm.RateLimitError(
                "rate limited",
                "test-provider",
                self.settings.model,
            )

        with self.assertRaises(LLMRateLimitError) as context:
            self._gateway(completion_fn).generate(
                system="System prompt",
                messages=[],
                response_model=SampleResponse,
            )

        self.assertTrue(context.exception.context.retryable)

    def test_translates_timeout_connection_and_service_failures(self):
        failures = (
            litellm.Timeout(
                "timeout",
                self.settings.model,
                "test-provider",
            ),
            litellm.APIConnectionError(
                "connection failed",
                "test-provider",
                self.settings.model,
            ),
            litellm.ServiceUnavailableError(
                "service unavailable",
                "test-provider",
                self.settings.model,
            ),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                def completion_fn(**kwargs):
                    raise failure

                with self.assertRaises(
                    LLMProviderUnavailableError
                ) as context:
                    self._gateway(completion_fn).generate(
                        system="System prompt",
                        messages=[],
                        response_model=SampleResponse,
                    )
                self.assertTrue(context.exception.context.retryable)

    def test_translates_rejected_model_configuration(self):
        failures = (
            litellm.BadRequestError(
                "bad model",
                self.settings.model,
                "test-provider",
            ),
            litellm.NotFoundError(
                "missing model",
                self.settings.model,
                "test-provider",
            ),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                def completion_fn(**kwargs):
                    raise failure

                with self.assertRaises(LLMConfigurationError):
                    self._gateway(completion_fn).generate(
                        system="System prompt",
                        messages=[],
                        response_model=SampleResponse,
                    )

    def test_translates_unknown_provider_failure_safely(self):
        secret = "unknown-provider-secret"

        def completion_fn(**kwargs):
            raise RuntimeError(secret)

        with self.assertRaises(LLMProviderUnavailableError) as context:
            self._gateway(completion_fn).generate(
                system="System prompt",
                messages=[],
                response_model=SampleResponse,
            )

        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception.context))

    def test_provider_resolution_failure_is_safe_configuration_error(self):
        secret = "resolver-secret"

        def resolver(*, model):
            raise RuntimeError(secret)

        with self.assertRaises(LLMConfigurationError) as context:
            self._gateway(lambda **kwargs: None, resolver)

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception.context))

    def test_rejects_invalid_constructor_dependencies(self):
        with self.assertRaises(ValueError):
            LiteLLMStructuredGateway(
                "invalid-settings",
                completion_fn=lambda **kwargs: None,
                provider_resolver=_provider_resolver,
            )
        with self.assertRaises(ValueError):
            LiteLLMStructuredGateway(
                self.settings,
                completion_fn=None,
                provider_resolver=_provider_resolver,
            )


if __name__ == "__main__":
    unittest.main()
