from threading import RLock

from app.commands.swing_analysis import JarvisSwingAnalysisCommandHandler
from app.composition.debate import (
    PreflightBuilder,
    compose_end_to_end_swing_analysis,
)
from app.facades.swing_research import JarvisSwingResearchFacade
from app.gateways.instruments import InstrumentResolver
from app.instruments.angel_master import (
    AngelInstrumentMasterConfig,
    AngelInstrumentMasterResolver,
)
from app.intents.swing_analysis import (
    PatternSwingIntentInterpreter,
    SwingIntentInterpreter,
)
from app.llm.config import LLMSettings
from app.llm.factory import GatewayBuilder
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
from app.use_cases.run_end_to_end_swing_analysis import (
    RunEndToEndSwingAnalysis,
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
    ) -> None:
        self._rolling_fetch = rolling_fetch
        self._settings = settings
        self._archive = archive
        self._agent_orchestrator = agent_orchestrator
        self._orchestrator_config = orchestrator_config
        self._gateway_builder = gateway_builder
        self._preflight_builder = preflight_builder
        self._executor: RunEndToEndSwingAnalysis | None = None
        self._lock = RLock()

    def __call__(self) -> RunEndToEndSwingAnalysis:
        with self._lock:
            if self._executor is None:
                self._executor = compose_end_to_end_swing_analysis(
                    self._rolling_fetch,
                    settings=self._settings,
                    archive=self._archive,
                    agent_orchestrator=self._agent_orchestrator,
                    orchestrator_config=self._orchestrator_config,
                    gateway_builder=self._gateway_builder,
                    preflight_builder=self._preflight_builder,
                )
            return self._executor


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
) -> JarvisSwingResearchFacade:
    """Compose natural-language-to-debate research without eager LLM I/O."""
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
    request_resolver = ResolveSwingAnalysisRequest(
        (
            intent_interpreter
            if intent_interpreter is not None
            else PatternSwingIntentInterpreter()
        ),
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
    )
    command_handler = JarvisSwingAnalysisCommandHandler(executor_factory)
    return JarvisSwingResearchFacade(
        request_resolver,
        command_handler,
        event_sink,
    )
