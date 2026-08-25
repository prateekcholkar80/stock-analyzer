from typing import Any

from app.audit.prompt_audit import (
    PromptAuditConfig,
    PromptAuditSink,
    prompt_audit_sink_from_config,
)
from app.composition.conversation import LazyJarvisResearchPresenter
from app.composition.research import compose_jarvis_swing_research
from app.conversation.config import JarvisConversationConfig
from app.workflow.browser_runner import (
    AsyncBrowserOperationRunner,
    BrowserOperationResultStore,
    BrowserResearchContextStore,
    InMemoryBrowserOperationResultStore,
    JarvisBrowserOperationHandler,
)
from app.workflow.operations import (
    BrowserOperationRegistry,
    InMemoryBrowserOperationRegistry,
)
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries


def compose_jarvis_browser_operations(
    rolling_fetch: PullRollingMarketSeries,
    *,
    conversation_config: JarvisConversationConfig | None = None,
    registry: BrowserOperationRegistry | None = None,
    result_store: BrowserOperationResultStore | None = None,
    context_store: BrowserResearchContextStore | None = None,
    prompt_audit_config: PromptAuditConfig | None = None,
    prompt_audit_sink: PromptAuditSink | None = None,
    max_workers: int = 2,
    **research_dependencies: Any,
) -> AsyncBrowserOperationRunner:
    """Compose the real research workflow behind the browser operation port."""

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
    resolved_registry = (
        registry if registry is not None else InMemoryBrowserOperationRegistry()
    )
    resolved_results = (
        result_store
        if result_store is not None
        else InMemoryBrowserOperationResultStore()
    )
    resolved_config = (
        conversation_config or JarvisConversationConfig.from_environment()
    )
    research = compose_jarvis_swing_research(
        rolling_fetch,
        prompt_audit_sink=resolved_audit_sink,
        **research_dependencies,
    )
    presenter = LazyJarvisResearchPresenter(
        settings=research_dependencies.get("settings"),
        gateway_builder=research_dependencies.get("gateway_builder"),
        prompt_audit_sink=resolved_audit_sink,
    )
    handler = JarvisBrowserOperationHandler(
        research,
        presenter,
        research.judge_follow_up_executor,
        user_name=resolved_config.user_name,
        context_store=context_store,
    )
    return AsyncBrowserOperationRunner(
        resolved_registry,
        handler,
        resolved_results,
        max_workers=max_workers,
    )
