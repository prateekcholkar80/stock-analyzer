from typing import TypeVar

from pydantic import BaseModel

from app.audit.prompt_audit import (
    PromptAuditActor,
    PromptAuditEventType,
    PromptAuditRecorder,
    PromptAuditSink,
)
from app.llm.config import LLMRole
from app.llm.gateway import (
    StructuredGeneration,
    StructuredLLMGateway,
)


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


_ACTOR_BY_ROLE = {
    LLMRole.BULL: PromptAuditActor.BULL,
    LLMRole.BEAR: PromptAuditActor.BEAR,
    LLMRole.JUDGE: PromptAuditActor.JUDGE,
    LLMRole.JARVIS: PromptAuditActor.JARVIS,
}


class PromptAuditedLLMGateway:
    """Audit exact agent-level prompts and validated structured responses."""

    def __init__(
        self,
        gateway: StructuredLLMGateway,
        role: LLMRole,
        sink: PromptAuditSink | None = None,
    ) -> None:
        if not isinstance(gateway, StructuredLLMGateway):
            raise ValueError("audited LLM gateway requires a gateway")
        if not isinstance(role, LLMRole):
            raise ValueError("audited LLM gateway requires a validated role")
        self._gateway = gateway
        self._role = role
        self._actor = _ACTOR_BY_ROLE[role]
        self._recorder = PromptAuditRecorder(sink)

    @property
    def configuration_fingerprint(self) -> str:
        return self._gateway.configuration_fingerprint

    def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        response_model: type[ResponseModelT],
    ) -> StructuredGeneration[ResponseModelT]:
        self._recorder.record(
            PromptAuditEventType.LLM_REQUEST,
            self._actor,
            {
                "role": self._role.value,
                "system_prompt": system,
                "messages": messages,
                "response_model": response_model.__name__,
                "response_schema": response_model.model_json_schema(),
            },
        )
        try:
            generation = self._gateway.generate(
                system=system,
                messages=messages,
                response_model=response_model,
            )
        except Exception as exc:
            self._recorder.record(
                PromptAuditEventType.LLM_FAILURE,
                self._actor,
                {
                    "role": self._role.value,
                    "response_model": response_model.__name__,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        self._recorder.record(
            PromptAuditEventType.LLM_RESPONSE,
            self._actor,
            {
                "role": self._role.value,
                "provider": generation.provider,
                "model": generation.model,
                "attempt_count": generation.attempt_count,
                "response_model": response_model.__name__,
                "structured_response": generation.value.model_dump(
                    mode="json"
                ),
            },
        )
        return generation
