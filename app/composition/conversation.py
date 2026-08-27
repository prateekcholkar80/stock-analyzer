from threading import RLock
from typing import Any

from app.agents.jarvis_presentation_agent import JarvisPresentationAgent
from app.audit.prompt_audit import (
    PromptAuditConfig,
    PromptAuditSink,
    prompt_audit_sink_from_config,
)
from app.composition.research import compose_jarvis_swing_research
from app.conversation.config import JarvisConversationConfig
from app.conversation.events import ConversationEventSink
from app.conversation.session import (
    JarvisConversationSession,
    ResearchPresentationExecutor,
    JudgeFollowUpExecutor,
)
from app.llm.audited_gateway import PromptAuditedLLMGateway
from app.llm.config import LLMRole, LLMSettings, get_llm_settings
from app.llm.factory import GatewayBuilder, LLMGatewayFactory
from app.models.presentation import (
    JarvisMultiTimeframeResearchExplanation,
    JarvisResearchExplanation,
)
from app.models.storage import (
    EndToEndSwingAnalysisResult,
    MultiTimeframeEndToEndSwingAnalysisResult,
)
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries


class LazyJarvisResearchPresenter:
    """Build the persona gateway only after validated research exists."""

    def __init__(
        self,
        *,
        settings: LLMSettings | None,
        gateway_builder: GatewayBuilder | None,
        prompt_audit_sink: PromptAuditSink | None,
    ) -> None:
        self._settings = settings
        self._gateway_builder = gateway_builder
        self._prompt_audit_sink = prompt_audit_sink
        self._presenter: JarvisPresentationAgent | None = None
        self._lock = RLock()

    def explain(
        self,
        result: (
            EndToEndSwingAnalysisResult
            | MultiTimeframeEndToEndSwingAnalysisResult
        ),
        *,
        user_name: str,
    ) -> (
        JarvisResearchExplanation
        | JarvisMultiTimeframeResearchExplanation
    ):
        return self._get_presenter().explain(result, user_name=user_name)

    def _get_presenter(self) -> JarvisPresentationAgent:
        with self._lock:
            if self._presenter is None:
                settings = self._settings or get_llm_settings()
                factory = (
                    LLMGatewayFactory(settings)
                    if self._gateway_builder is None
                    else LLMGatewayFactory(settings, self._gateway_builder)
                )
                gateway = PromptAuditedLLMGateway(
                    factory.for_role(LLMRole.JARVIS),
                    LLMRole.JARVIS,
                    self._prompt_audit_sink,
                )
                self._presenter = JarvisPresentationAgent(gateway)
            return self._presenter


def compose_jarvis_conversation(
    rolling_fetch: PullRollingMarketSeries,
    *,
    conversation_config: JarvisConversationConfig | None = None,
    conversation_event_sink: ConversationEventSink | None = None,
    prompt_audit_config: PromptAuditConfig | None = None,
    prompt_audit_sink: PromptAuditSink | None = None,
    research_presenter: ResearchPresentationExecutor | None = None,
    judge_follow_up_executor: JudgeFollowUpExecutor | None = None,
    **research_dependencies: Any,
) -> JarvisConversationSession:
    """Compose text/voice activation around the lazy research workflow."""
    if prompt_audit_config is not None and prompt_audit_sink is not None:
        raise ValueError(
            "provide either prompt audit configuration or an audit sink"
        )
    if "prompt_audit_sink" in research_dependencies:
        raise ValueError(
            "provide the prompt audit sink through its named argument"
        )
    resolved_audit_sink = (
        prompt_audit_sink
        if prompt_audit_sink is not None
        else prompt_audit_sink_from_config(prompt_audit_config)
    )
    research = compose_jarvis_swing_research(
        rolling_fetch,
        prompt_audit_sink=resolved_audit_sink,
        **research_dependencies,
    )
    presenter = (
        research_presenter
        if research_presenter is not None
        else LazyJarvisResearchPresenter(
            settings=research_dependencies.get("settings"),
            gateway_builder=research_dependencies.get("gateway_builder"),
            prompt_audit_sink=resolved_audit_sink,
        )
    )
    return JarvisConversationSession(
        research,
        conversation_config or JarvisConversationConfig.from_environment(),
        event_sink=conversation_event_sink,
        prompt_audit_sink=resolved_audit_sink,
        research_presenter=presenter,
        judge_follow_up_executor=(
            judge_follow_up_executor
            if judge_follow_up_executor is not None
            else research.judge_follow_up_executor
        ),
        ticker_resolution_executor=research.ticker_resolution_executor,
    )
