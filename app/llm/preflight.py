from collections.abc import Callable, Iterable
from datetime import UTC, datetime

import litellm

from app.exceptions import LLMConfigurationError
from app.llm.config import DEBATE_LLM_ROLES, LLMRole
from app.llm.factory import LLMGatewayFactory
from app.logging_config import get_operation_id
from app.models.llm import LLMPreflightResult, LLMRolePreflight


ProviderResolver = Callable[..., tuple[str, str, str | None, str | None]]
APIKeyResolver = Callable[[str, str | None], str | None]

DEFAULT_KEYLESS_PROVIDERS = frozenset(
    {
        "ollama",
        "ollama_chat",
        "lm_studio",
        "vllm",
    }
)


class LLMPreflightValidator:
    """Validate mandatory full-debate readiness without a network call."""

    def __init__(
        self,
        factory: LLMGatewayFactory,
        *,
        provider_resolver: ProviderResolver = litellm.get_llm_provider,
        api_key_resolver: APIKeyResolver = litellm.get_api_key,
        keyless_providers: Iterable[str] = DEFAULT_KEYLESS_PROVIDERS,
    ) -> None:
        if not isinstance(factory, LLMGatewayFactory):
            raise ValueError(
                "LLM preflight requires a validated gateway factory"
            )
        if not callable(provider_resolver):
            raise ValueError("LLM provider resolver must be callable")
        if not callable(api_key_resolver):
            raise ValueError("LLM API-key resolver must be callable")

        self._factory = factory
        self._provider_resolver = provider_resolver
        self._api_key_resolver = api_key_resolver
        self._keyless_providers = _normalize_keyless_providers(
            keyless_providers
        )

    def validate_full_debate(self) -> LLMPreflightResult:
        resolved: list[tuple[LLMRole, str, str]] = []
        for role in DEBATE_LLM_ROLES:
            role_settings = self._factory.settings.for_role(role)
            try:
                _, provider, _, _ = self._provider_resolver(
                    model=role_settings.model
                )
            except Exception as exc:
                raise LLMConfigurationError(
                    "The configured LLM model could not be resolved "
                    "during preflight",
                    role=role.value,
                    model=role_settings.model,
                    operation_id=get_operation_id(),
                ) from exc
            if not isinstance(provider, str) or not provider.strip():
                raise LLMConfigurationError(
                    "The configured LLM provider could not be resolved "
                    "during preflight",
                    role=role.value,
                    model=role_settings.model,
                    operation_id=get_operation_id(),
                )
            resolved.append(
                (role, provider.strip().lower(), role_settings.model)
            )

        credential_ready: dict[str, bool] = {}
        for role, provider, model in resolved:
            if provider in credential_ready:
                continue
            if provider in self._keyless_providers:
                credential_ready[provider] = True
                continue
            try:
                credential = self._api_key_resolver(provider, None)
            except Exception as exc:
                raise LLMConfigurationError(
                    "The LLM provider credential could not be inspected",
                    role=role.value,
                    provider=provider,
                    model=model,
                    operation_id=get_operation_id(),
                ) from exc
            if not isinstance(credential, str) or not credential.strip():
                raise LLMConfigurationError(
                    "A required LLM provider credential is not configured",
                    role=role.value,
                    provider=provider,
                    model=model,
                    operation_id=get_operation_id(),
                )
            credential_ready[provider] = True

        role_results = []
        for role, provider, model in resolved:
            self._factory.for_role(role)
            role_results.append(
                LLMRolePreflight(
                    role=role,
                    provider=provider,
                    model=model,
                    credential_required=(
                        provider not in self._keyless_providers
                    ),
                    credential_ready=credential_ready[provider],
                    structured_gateway_ready=True,
                )
            )

        return LLMPreflightResult(
            configuration_fingerprint=(
                self._factory.configuration_fingerprint
            ),
            roles=tuple(role_results),
            checked_at=datetime.now(UTC),
            ready=True,
        )


def _normalize_keyless_providers(
    providers: Iterable[str],
) -> frozenset[str]:
    if isinstance(providers, str):
        raise ValueError("keyless LLM providers must be an iterable of names")
    normalized = set()
    try:
        values = tuple(providers)
    except TypeError as exc:
        raise ValueError(
            "keyless LLM providers must be an iterable of names"
        ) from exc
    for provider in values:
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError(
                "keyless LLM provider names must be non-blank strings"
            )
        normalized.add(provider.strip().lower())
    return frozenset(normalized)
