from collections.abc import Callable
from dataclasses import dataclass

from app.agents.bear_agent import BearDebateAgent
from app.agents.bull_agent import BullDebateAgent
from app.agents.debate_judge_agent import DebateJudgeAgent
from app.exceptions import LLMConfigurationError
from app.llm.config import LLMRole, LLMSettings, get_llm_settings
from app.llm.factory import GatewayBuilder, LLMGatewayFactory
from app.llm.preflight import LLMPreflightValidator
from app.models.llm import LLMPreflightResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import (
    DebateArchive,
    DebateOrchestrator,
    DebateOrchestratorConfig,
)
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries
from app.use_cases.run_end_to_end_swing_analysis import (
    RunEndToEndSwingAnalysis,
)


PreflightBuilder = Callable[[LLMGatewayFactory], LLMPreflightValidator]


@dataclass(frozen=True, slots=True)
class FullDebateComposition:
    """Fully validated dependencies for the mandatory debate workflow."""

    gateway_factory: LLMGatewayFactory
    preflight_validator: LLMPreflightValidator
    preflight_result: LLMPreflightResult
    orchestrator: DebateOrchestrator


def compose_full_debate(
    *,
    settings: LLMSettings | None = None,
    archive: DebateArchive | None = None,
    orchestrator_config: DebateOrchestratorConfig | None = None,
    gateway_builder: GatewayBuilder | None = None,
    preflight_builder: PreflightBuilder = LLMPreflightValidator,
) -> FullDebateComposition:
    """Build the complete provider-neutral panel after local preflight."""
    resolved_settings = settings or get_llm_settings()
    if not isinstance(resolved_settings, LLMSettings):
        raise ValueError("full debate requires validated LLM settings")
    if not callable(preflight_builder):
        raise ValueError("full debate preflight builder must be callable")

    gateway_factory = (
        LLMGatewayFactory(resolved_settings)
        if gateway_builder is None
        else LLMGatewayFactory(resolved_settings, gateway_builder)
    )
    try:
        preflight_validator = preflight_builder(gateway_factory)
    except LLMConfigurationError:
        raise
    except Exception as exc:
        raise LLMConfigurationError(
            "The full-debate preflight could not be constructed"
        ) from exc
    if not isinstance(preflight_validator, LLMPreflightValidator):
        raise LLMConfigurationError(
            "The configured full-debate preflight is invalid"
        )

    try:
        preflight_result = preflight_validator.validate_full_debate()
    except LLMConfigurationError:
        raise
    except Exception as exc:
        raise LLMConfigurationError(
            "The full-debate preflight could not be completed"
        ) from exc
    if not isinstance(preflight_result, LLMPreflightResult):
        raise LLMConfigurationError(
            "The full-debate preflight returned an invalid result"
        )
    if (
        preflight_result.configuration_fingerprint
        != gateway_factory.configuration_fingerprint
    ):
        raise LLMConfigurationError(
            "The full-debate preflight result does not match the active "
            "LLM configuration"
        )

    bull_agent = BullDebateAgent(
        gateway_factory.for_role(LLMRole.BULL)
    )
    bear_agent = BearDebateAgent(
        gateway_factory.for_role(LLMRole.BEAR)
    )
    judge_agent = DebateJudgeAgent(
        gateway_factory.for_role(LLMRole.JUDGE)
    )
    orchestrator = DebateOrchestrator(
        bull_agent=bull_agent,
        bear_agent=bear_agent,
        judge_agent=judge_agent,
        config=orchestrator_config,
        archive=archive,
    )
    return FullDebateComposition(
        gateway_factory=gateway_factory,
        preflight_validator=preflight_validator,
        preflight_result=preflight_result,
        orchestrator=orchestrator,
    )


def compose_end_to_end_swing_analysis(
    rolling_fetch: PullRollingMarketSeries,
    *,
    settings: LLMSettings | None = None,
    archive: DebateArchive | None = None,
    agent_orchestrator: AgentOrchestrator | None = None,
    orchestrator_config: DebateOrchestratorConfig | None = None,
    gateway_builder: GatewayBuilder | None = None,
    preflight_builder: PreflightBuilder = LLMPreflightValidator,
) -> RunEndToEndSwingAnalysis:
    """Compose a market-to-debate use case only after LLM readiness."""
    debate = compose_full_debate(
        settings=settings,
        archive=archive,
        orchestrator_config=orchestrator_config,
        gateway_builder=gateway_builder,
        preflight_builder=preflight_builder,
    )
    return RunEndToEndSwingAnalysis(
        rolling_fetch,
        agent_orchestrator=agent_orchestrator,
        debate_orchestrator=debate.orchestrator,
        llm_preflight=debate.preflight_result,
    )
