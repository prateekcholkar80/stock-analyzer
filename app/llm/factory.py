import json
from collections.abc import Callable
from hashlib import sha256
from threading import RLock

from app.exceptions import LLMConfigurationError
from app.llm.adapters.litellm_gateway import LiteLLMStructuredGateway
from app.llm.config import LLMRole, LLMRoleSettings, LLMSettings
from app.llm.gateway import StructuredLLMGateway
from app.logging_config import get_operation_id


GatewayBuilder = Callable[[LLMRoleSettings], StructuredLLMGateway]


class LLMGatewayFactory:
    """Build and cache provider-neutral gateways bound to debate roles."""

    def __init__(
        self,
        settings: LLMSettings,
        gateway_builder: GatewayBuilder = LiteLLMStructuredGateway,
    ) -> None:
        if not isinstance(settings, LLMSettings):
            raise ValueError("LLM gateway factory requires validated settings")
        if not callable(gateway_builder):
            raise ValueError("LLM gateway builder must be callable")

        self.settings = settings
        self._gateway_builder = gateway_builder
        self._gateways: dict[LLMRole, StructuredLLMGateway] = {}
        self._lock = RLock()

    @property
    def configuration_fingerprint(self) -> str:
        payload = {
            role.value: self.settings.for_role(role).model_dump(mode="json")
            for role in LLMRole
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(serialized.encode("utf-8")).hexdigest()

    def role_configuration_fingerprint(self, role: LLMRole) -> str:
        role_settings = self._validated_role_settings(role)
        serialized = role_settings.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def for_role(self, role: LLMRole) -> StructuredLLMGateway:
        role_settings = self._validated_role_settings(role)
        with self._lock:
            existing = self._gateways.get(role)
            if existing is not None:
                return existing

            try:
                gateway = self._gateway_builder(role_settings)
            except LLMConfigurationError:
                raise
            except Exception as exc:
                raise LLMConfigurationError(
                    "The LLM gateway could not be constructed",
                    role=role.value,
                    model=role_settings.model,
                    operation_id=get_operation_id(),
                ) from exc
            if not isinstance(gateway, StructuredLLMGateway):
                raise LLMConfigurationError(
                    "The configured LLM adapter does not implement the "
                    "structured gateway contract",
                    role=role.value,
                    model=role_settings.model,
                    operation_id=get_operation_id(),
                )

            self._gateways[role] = gateway
            return gateway

    def _validated_role_settings(
        self,
        role: LLMRole,
    ) -> LLMRoleSettings:
        if not isinstance(role, LLMRole):
            raise ValueError("LLM gateway role must be a validated LLMRole")
        return self.settings.for_role(role)
