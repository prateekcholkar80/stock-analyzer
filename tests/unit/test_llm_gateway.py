import unittest

from pydantic import BaseModel, Field, ValidationError

from app.llm.gateway import StructuredGeneration, StructuredLLMGateway


class SampleResponse(BaseModel):
    conclusion: str = Field(min_length=1)


class FakeStructuredGateway:
    def __init__(self):
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def generate(self, *, system, messages, response_model):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "response_model": response_model,
            }
        )
        return StructuredGeneration[response_model](
            value=response_model(conclusion="Grounded conclusion"),
            provider="fake-provider",
            model="fake-model",
            attempt_count=1,
        )


class IncompleteGateway:
    pass


class StructuredGenerationTests(unittest.TestCase):
    def test_preserves_validated_generic_response_and_metadata(self):
        response = SampleResponse(conclusion="Bull case is supported")

        generation = StructuredGeneration[SampleResponse](
            value=response,
            provider="openai",
            model="gpt-test",
            attempt_count=2,
        )

        self.assertIsInstance(generation.value, SampleResponse)
        self.assertEqual(generation.value.conclusion, response.conclusion)
        self.assertEqual(generation.provider, "openai")
        self.assertEqual(generation.model, "gpt-test")
        self.assertEqual(generation.attempt_count, 2)

    def test_normalizes_provider_and_model_identity(self):
        generation = StructuredGeneration[SampleResponse](
            value=SampleResponse(conclusion="Validated"),
            provider="  local  ",
            model="  qwen-test  ",
            attempt_count=1,
        )

        self.assertEqual(generation.provider, "local")
        self.assertEqual(generation.model, "qwen-test")

    def test_rejects_blank_provider(self):
        with self.assertRaises(ValidationError):
            StructuredGeneration[SampleResponse](
                value=SampleResponse(conclusion="Validated"),
                provider="   ",
                model="test-model",
                attempt_count=1,
            )

    def test_rejects_blank_model(self):
        with self.assertRaises(ValidationError):
            StructuredGeneration[SampleResponse](
                value=SampleResponse(conclusion="Validated"),
                provider="test-provider",
                model="   ",
                attempt_count=1,
            )

    def test_rejects_non_positive_attempt_count(self):
        with self.assertRaises(ValidationError):
            StructuredGeneration[SampleResponse](
                value=SampleResponse(conclusion="Validated"),
                provider="test-provider",
                model="test-model",
                attempt_count=0,
            )

    def test_rejects_response_that_does_not_match_generic_model(self):
        with self.assertRaises(ValidationError):
            StructuredGeneration[SampleResponse](
                value={"unexpected": "field"},
                provider="test-provider",
                model="test-model",
                attempt_count=1,
            )


class StructuredLLMGatewayTests(unittest.TestCase):
    def test_plain_fake_satisfies_runtime_gateway_contract(self):
        gateway = FakeStructuredGateway()

        self.assertIsInstance(gateway, StructuredLLMGateway)

    def test_object_without_generate_does_not_satisfy_contract(self):
        self.assertNotIsInstance(IncompleteGateway(), StructuredLLMGateway)

    def test_fake_gateway_can_generate_typed_response(self):
        gateway = FakeStructuredGateway()

        generation = gateway.generate(
            system="Ground every claim in evidence.",
            messages=[{"role": "user", "content": "Assess the setup."}],
            response_model=SampleResponse,
        )

        self.assertIsInstance(generation.value, SampleResponse)
        self.assertEqual(generation.value.conclusion, "Grounded conclusion")
        self.assertEqual(generation.provider, "fake-provider")
        self.assertEqual(len(gateway.calls), 1)
        self.assertIs(gateway.calls[0]["response_model"], SampleResponse)


if __name__ == "__main__":
    unittest.main()
