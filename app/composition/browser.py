from collections.abc import Callable
from typing import Any, NamedTuple

from app.audit.prompt_audit import (
    PromptAuditConfig,
    PromptAuditSink,
    prompt_audit_sink_from_config,
)
from app.composition.conversation import LazyJarvisResearchPresenter
from app.composition.research import compose_jarvis_swing_research
from app.conversation.browser import BrowserConversationCoordinator
from app.conversation.config import JarvisConversationConfig
from app.models.fundamentals import ProviderConnectionScope
from app.workflow.browser_runner import (
    AsyncBrowserOperationRunner,
    BrowserOperationResultStore,
    BrowserResearchContextStore,
    InMemoryBrowserOperationResultStore,
    JarvisBrowserOperationHandler,
    BrowserFundamentalEvidenceExecutor,
)
from app.workflow.operations import (
    BrowserOperationRegistry,
    InMemoryBrowserOperationRegistry,
)
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries


class JarvisBrowserApplication(NamedTuple):
    """Bundle of the two composition roots examples/browser_api.py needs:
    the async operation runner (fed to create_jarvis_http_app as
    ``operations``) and the wake-aware conversation coordinator (fed as
    ``conversation``). Bundled together because the conversation
    coordinator's optional ticker-resolution fallback is built from the
    same research facade the operation runner already composes -- keeping
    them as two independently-constructed objects in the caller would
    require rebuilding (or threading through) that facade a second time.
    """

    runner: AsyncBrowserOperationRunner
    conversation: BrowserConversationCoordinator


def compose_jarvis_browser_operations(
    rolling_fetch: PullRollingMarketSeries,
    *,
    conversation_config: JarvisConversationConfig | None = None,
    registry: BrowserOperationRegistry | None = None,
    result_store: BrowserOperationResultStore | None = None,
    context_store: BrowserResearchContextStore | None = None,
    prompt_audit_config: PromptAuditConfig | None = None,
    prompt_audit_sink: PromptAuditSink | None = None,
    fundamental_evidence_executor: (
        BrowserFundamentalEvidenceExecutor | None
    ) = None,
    provider_scope_resolver: (
        Callable[[str], ProviderConnectionScope] | None
    ) = None,
    max_workers: int = 2,
    **research_dependencies: Any,
) -> JarvisBrowserApplication:
    """Compose the real research workflow behind the browser operation port.

    The conversational ticker-resolution fallback (partial/fuzzy company
    names, e.g. "Infosys" instead of the exact listed symbol) is opt-in,
    exactly mirroring compose_jarvis_swing_research: pass both
    ``amfi_catalog`` and ``nse_sector_catalog`` (as part of
    **research_dependencies) to have it composed and wired into the
    returned conversation coordinator. Leaving them unset preserves
    today's exact-match-only resolution behavior.
    """

    if prompt_audit_config is not None and prompt_audit_sink is not None:
        raise ValueError(
            "provide either prompt audit configuration or an audit sink"
        )
    if (fundamental_evidence_executor is None) != (
        provider_scope_resolver is None
    ):
        raise ValueError(
            "browser fundamental composition requires executor and resolver"
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
        fundamental_evidence_executor=fundamental_evidence_executor,
        provider_scope_resolver=provider_scope_resolver,
    )
    runner = AsyncBrowserOperationRunner(
        resolved_registry,
        handler,
        resolved_results,
        max_workers=max_workers,
    )
    conversation = BrowserConversationCoordinator(
        runner,
        resolved_config,
        ticker_resolution_executor=research.ticker_resolution_executor,
    )
    return JarvisBrowserApplication(runner=runner, conversation=conversation)
