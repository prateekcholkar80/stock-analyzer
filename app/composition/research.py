from threading import RLock

from app.audit.prompt_audit import PromptAuditSink
from app.commands.swing_analysis import JarvisSwingAnalysisCommandHandler
from app.composition.debate import (
    PreflightBuilder,
    compose_end_to_end_multi_timeframe_swing_analysis,
)
from app.facades.swing_research import JarvisSwingResearchFacade
from app.gateways.instruments import InstrumentResolver
from app.instruments.amfi_market_cap import AmfiMarketCapCatalog
from app.instruments.angel_master import (
    AngelInstrumentMasterConfig,
    AngelInstrumentMasterResolver,
)
from app.instruments.nse_sector_master import NseSectorMasterCatalog
from app.intents.swing_analysis import (
    PatternSwingIntentInterpreter,
    SwingIntentInterpreter,
)
from app.llm.audited_gateway import PromptAuditedLLMGateway
from app.llm.config import LLMRole, LLMSettings, get_llm_settings
from app.llm.factory import GatewayBuilder, LLMGatewayFactory
from app.llm.preflight import LLMPreflightValidator
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import (
    DebateArchive,
    DebateOrchestratorConfig,
)
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries
from app.use_cases.resolve_swing_analysis_request import (
    ResolveSwingAnalysisRequest,
)
from app.use_cases.resolve_ticker_conversationally import (
    ResolveTickerConversationally,
)
from app.use_cases.run_end_to_end_multi_timeframe_swing_analysis import (
    RunEndToEndMultiTimeframeSwingAnalysis,
)
from app.use_cases.ask_jarvis_judge_follow_up import (
    AskJarvisJudgeFollowUp,
)
from app.workflow.events import WorkflowEventSink


class _LazySwingAnalysisExecutorFactory:
    """Cache only a successfully preflighted end-to-end executor."""

    def __init__(
        self,
        rolling_fetch: PullRollingMarketSeries,
        *,
        settings: LLMSettings | None,
        archive: DebateArchive | None,
        agent_orchestrator: AgentOrchestrator | None,
        orchestrator_config: DebateOrchestratorConfig | None,
        gateway_builder: GatewayBuilder | None,
        preflight_builder: PreflightBuilder,
        prompt_audit_sink: PromptAuditSink | None,
    ) -> None:
        self._rolling_fetch = rolling_fetch
        self._settings = settings
        self._archive = archive
        self._agent_orchestrator = agent_orchestrator
        self._orchestrator_config = orchestrator_config
        self._gateway_builder = gateway_builder
        self._preflight_builder = preflight_builder
        self._prompt_audit_sink = prompt_audit_sink
        self._executor: RunEndToEndMultiTimeframeSwingAnalysis | None = None
        self._lock = RLock()

    def __call__(self) -> RunEndToEndMultiTimeframeSwingAnalysis:
        with self._lock:
            if self._executor is None:
                self._executor = (
                    compose_end_to_end_multi_timeframe_swing_analysis(
                        self._rolling_fetch,
                        settings=self._settings,
                        archive=self._archive,
                        agent_orchestrator=self._agent_orchestrator,
                        orchestrator_config=self._orchestrator_config,
                        gateway_builder=self._gateway_builder,
                        preflight_builder=self._preflight_builder,
                        prompt_audit_sink=self._prompt_audit_sink,
                    )
                )
            return self._executor


class _LazyJarvisJudgeFollowUp:
    """Reuse the exact debate panel created for the initial analysis."""

    def __init__(self, executor_factory: _LazySwingAnalysisExecutorFactory):
        self._executor_factory = executor_factory

    def execute(
        self,
        question,
        *,
        technical_review,
        debate_result,
    ):
        executor = self._executor_factory()
        relay = AskJarvisJudgeFollowUp(executor.debate_orchestrator)
        return relay.execute(
            question,
            technical_review=technical_review,
            debate_result=debate_result,
        )


class _LazyTickerResolutionExecutor:
    """Build the TICKER_RESOLVER gateway only on first use, mirroring
    LazyJarvisResearchPresenter's lazy-build pattern for the persona role.
    """

    def __init__(
        self,
        *,
        interpreter: SwingIntentInterpreter,
        angel_identity_source: AngelInstrumentMasterResolver,
        amfi_catalog: AmfiMarketCapCatalog,
        nse_sector_catalog: NseSectorMasterCatalog,
        settings: LLMSettings | None,
        gateway_builder: GatewayBuilder | None,
        prompt_audit_sink: PromptAuditSink | None,
    ) -> None:
        self._interpreter = interpreter
        self._angel_identity_source = angel_identity_source
        self._amfi_catalog = amfi_catalog
        self._nse_sector_catalog = nse_sector_catalog
        self._settings = settings
        self._gateway_builder = gateway_builder
        self._prompt_audit_sink = prompt_audit_sink
        self._use_case: ResolveTickerConversationally | None = None
        self._lock = RLock()

    def attempt(self, command: str):
        return self._get_use_case().attempt(command)

    def refresh_catalog(self) -> int:
        return self._get_use_case().refresh_catalog()

    def _get_use_case(self) -> ResolveTickerConversationally:
        with self._lock:
            if self._use_case is None:
                settings = self._settings or get_llm_settings()
                factory = (
                    LLMGatewayFactory(settings)
                    if self._gateway_builder is None
                    else LLMGatewayFactory(settings, self._gateway_builder)
                )
                gateway = PromptAuditedLLMGateway(
                    factory.for_role(LLMRole.TICKER_RESOLVER),
                    LLMRole.TICKER_RESOLVER,
                    self._prompt_audit_sink,
                )
                self._use_case = ResolveTickerConversationally(
                    interpreter=self._interpreter,
                    angel_identity_source=self._angel_identity_source,
                    amfi_catalog=self._amfi_catalog,
                    nse_sector_catalog=self._nse_sector_catalog,
                    resolver_gateway=gateway,
                )
            return self._use_case


def compose_jarvis_swing_research(
    rolling_fetch: PullRollingMarketSeries,
    *,
    instrument_config: AngelInstrumentMasterConfig | None = None,
    instrument_resolver: InstrumentResolver | None = None,
    intent_interpreter: SwingIntentInterpreter | None = None,
    settings: LLMSettings | None = None,
    archive: DebateArchive | None = None,
    agent_orchestrator: AgentOrchestrator | None = None,
    orchestrator_config: DebateOrchestratorConfig | None = None,
    gateway_builder: GatewayBuilder | None = None,
    preflight_builder: PreflightBuilder = LLMPreflightValidator,
    event_sink: WorkflowEventSink | None = None,
    prompt_audit_sink: PromptAuditSink | None = None,
    ticker_resolution_executor=None,
    amfi_catalog: AmfiMarketCapCatalog | None = None,
    nse_sector_catalog: NseSectorMasterCatalog | None = None,
) -> JarvisSwingResearchFacade:
    """Compose natural-language-to-debate research without eager LLM I/O.

    The conversational ticker-resolution fallback (partial/fuzzy company
    names) is opt-in: pass a pre-built ``ticker_resolution_executor``, or
    both ``amfi_catalog`` and ``nse_sector_catalog`` to have one lazily
    composed (built only on first use, mirroring the persona presenter's
    lazy pattern). Leaving all three unset disables the feature entirely
    and preserves today's exact-match-only resolution behavior.
    """
    if instrument_resolver is not None and instrument_config is not None:
        raise ValueError(
            "provide either an instrument resolver or its configuration"
        )
    resolved_instrument_resolver = (
        instrument_resolver
        if instrument_resolver is not None
        else AngelInstrumentMasterResolver(
            instrument_config
            if instrument_config is not None
            else AngelInstrumentMasterConfig.from_environment()
        )
    )
    resolved_intent_interpreter = (
        intent_interpreter
        if intent_interpreter is not None
        else PatternSwingIntentInterpreter()
    )
    request_resolver = ResolveSwingAnalysisRequest(
        resolved_intent_interpreter,
        resolved_instrument_resolver,
    )
    executor_factory = _LazySwingAnalysisExecutorFactory(
        rolling_fetch,
        settings=settings,
        archive=archive,
        agent_orchestrator=agent_orchestrator,
        orchestrator_config=orchestrator_config,
        gateway_builder=gateway_builder,
        preflight_builder=preflight_builder,
        prompt_audit_sink=prompt_audit_sink,
    )
    command_handler = JarvisSwingAnalysisCommandHandler(executor_factory)
    resolved_ticker_resolution_executor = ticker_resolution_executor
    if (
        resolved_ticker_resolution_executor is None
        and amfi_catalog is not None
        and nse_sector_catalog is not None
    ):
        resolved_ticker_resolution_executor = _LazyTickerResolutionExecutor(
            interpreter=resolved_intent_interpreter,
            angel_identity_source=resolved_instrument_resolver,
            amfi_catalog=amfi_catalog,
            nse_sector_catalog=nse_sector_catalog,
            settings=settings,
            gateway_builder=gateway_builder,
            prompt_audit_sink=prompt_audit_sink,
        )
    return JarvisSwingResearchFacade(
        request_resolver,
        command_handler,
        event_sink,
        judge_follow_up_executor=(
            _LazyJarvisJudgeFollowUp(executor_factory)
        ),
        ticker_resolution_executor=resolved_ticker_resolution_executor,
    )
