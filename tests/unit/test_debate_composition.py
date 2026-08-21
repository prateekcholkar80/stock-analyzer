import unittest
from datetime import UTC, datetime

from app.composition.debate import (
    compose_end_to_end_swing_analysis,
    compose_full_debate,
)
from app.exceptions import LLMConfigurationError
from app.llm.config import LLMRole, LLMSettings
from app.llm.preflight import LLMPreflightValidator
from app.models.llm import LLMPreflightResult, LLMRolePreflight
from app.use_cases.run_end_to_end_swing_analysis import (
    RunEndToEndSwingAnalysis,
)


class FakeGateway:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configuration_fingerprint(self):
        return self.settings.model_dump_json().encode("utf-8").hex()[:64].ljust(
            64,
            "0",
        )

    def generate(self, *, system, messages, response_model):
        raise AssertionError("composition does not generate LLM output")


class RecordingGatewayBuilder:
    def __init__(self):
        self.gateways = {}

    def __call__(self, settings):
        gateway = FakeGateway(settings)
        self.gateways[settings.role] = gateway
        return gateway


def _provider_resolver(*, model):
    provider = model.split("/", 1)[0]
    return model, provider, None, None


def _preflight_builder(*, credential="configured-key"):
    def build(factory):
        return LLMPreflightValidator(
            factory,
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: credential,
        )

    return build


def _ready_result(*, fingerprint):
    return LLMPreflightResult(
        configuration_fingerprint=fingerprint,
        roles=tuple(
            LLMRolePreflight(
                role=role,
                provider="provider",
                model="provider/model",
                credential_required=True,
                credential_ready=True,
                structured_gateway_ready=True,
            )
            for role in LLMRole
        ),
        checked_at=datetime.now(UTC),
        ready=True,
    )


class StubPreflightValidator(LLMPreflightValidator):
    def __init__(self, factory, result=None, failure=None):
        super().__init__(
            factory,
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: "configured-key",
        )
        self.result = result
        self.failure = failure

    def validate_full_debate(self):
        if self.failure is not None:
            raise self.failure
        return self.result


class FullDebateCompositionTests(unittest.TestCase):
    def test_builds_preflighted_role_bound_panel(self):
        builder = RecordingGatewayBuilder()

        composition = compose_full_debate(
            settings=LLMSettings.from_environment(
                {
                    "JARVIS_LLM_MODEL": "provider/shared-model",
                    "JARVIS_JUDGE_LLM_MODEL": "provider/judge-model",
                }
            ),
            gateway_builder=builder,
            preflight_builder=_preflight_builder(),
        )

        self.assertTrue(composition.preflight_result.ready)
        self.assertEqual(set(builder.gateways), set(LLMRole))
        self.assertIs(
            composition.orchestrator.bull_agent._gateway,
            builder.gateways[LLMRole.BULL],
        )
        self.assertIs(
            composition.orchestrator.bear_agent._gateway,
            builder.gateways[LLMRole.BEAR],
        )
        self.assertIs(
            composition.orchestrator.judge_agent._gateway,
            builder.gateways[LLMRole.JUDGE],
        )
        self.assertEqual(
            composition.orchestrator.judge_agent._gateway.settings.model,
            "provider/judge-model",
        )

    def test_preflight_reuses_the_same_cached_gateways_as_agents(self):
        builder = RecordingGatewayBuilder()

        composition = compose_full_debate(
            settings=LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            gateway_builder=builder,
            preflight_builder=_preflight_builder(),
        )

        self.assertEqual(len(builder.gateways), 3)
        for role in LLMRole:
            self.assertIs(
                composition.gateway_factory.for_role(role),
                builder.gateways[role],
            )

    def test_missing_credential_fails_before_panel_is_constructed(self):
        builder = RecordingGatewayBuilder()

        with self.assertRaises(LLMConfigurationError):
            compose_full_debate(
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/model"}
                ),
                gateway_builder=builder,
                preflight_builder=_preflight_builder(credential=None),
            )

        self.assertEqual(builder.gateways, {})

    def test_composes_end_to_end_use_case_with_preflight_receipt(self):
        rolling_fetch = object()

        use_case = compose_end_to_end_swing_analysis(
            rolling_fetch,
            settings=LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            gateway_builder=RecordingGatewayBuilder(),
            preflight_builder=_preflight_builder(),
        )

        self.assertIsInstance(use_case, RunEndToEndSwingAnalysis)
        self.assertIs(use_case.rolling_fetch, rolling_fetch)
        self.assertTrue(use_case.llm_preflight.ready)

    def test_rejects_invalid_settings_and_preflight_builder(self):
        with self.assertRaises(ValueError):
            compose_full_debate(settings="invalid-settings")
        with self.assertRaises(ValueError):
            compose_full_debate(
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/model"}
                ),
                preflight_builder=None,
            )

    def test_rejects_invalid_preflight_result_provider(self):
        def invalid_preflight_builder(factory):
            return object()

        with self.assertRaises(LLMConfigurationError):
            compose_full_debate(
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/model"}
                ),
                gateway_builder=RecordingGatewayBuilder(),
                preflight_builder=invalid_preflight_builder,
            )

    def test_sanitizes_unexpected_preflight_construction_failure(self):
        secret = "preflight-builder-secret"

        def failing_preflight_builder(factory):
            raise RuntimeError(secret)

        with self.assertRaises(LLMConfigurationError) as context:
            compose_full_debate(
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/model"}
                ),
                gateway_builder=RecordingGatewayBuilder(),
                preflight_builder=failing_preflight_builder,
            )

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))

    def test_rejects_invalid_or_mismatched_preflight_receipt(self):
        settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "provider/model"}
        )

        with self.assertRaisesRegex(
            LLMConfigurationError,
            "invalid result",
        ):
            compose_full_debate(
                settings=settings,
                gateway_builder=RecordingGatewayBuilder(),
                preflight_builder=lambda factory: StubPreflightValidator(
                    factory,
                    result=object(),
                ),
            )

        with self.assertRaisesRegex(
            LLMConfigurationError,
            "does not match",
        ):
            compose_full_debate(
                settings=settings,
                gateway_builder=RecordingGatewayBuilder(),
                preflight_builder=lambda factory: StubPreflightValidator(
                    factory,
                    result=_ready_result(fingerprint="b" * 64),
                ),
            )

    def test_sanitizes_unexpected_preflight_execution_failure(self):
        secret = "preflight-execution-secret"

        with self.assertRaises(LLMConfigurationError) as context:
            compose_full_debate(
                settings=LLMSettings.from_environment(
                    {"JARVIS_LLM_MODEL": "provider/model"}
                ),
                gateway_builder=RecordingGatewayBuilder(),
                preflight_builder=lambda factory: StubPreflightValidator(
                    factory,
                    failure=RuntimeError(secret),
                ),
            )

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))


if __name__ == "__main__":
    unittest.main()
