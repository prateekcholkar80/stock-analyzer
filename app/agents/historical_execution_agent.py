from hashlib import sha256

from pydantic import ConfigDict, Field

from app.agents.technical_swing_agent import market_series_fingerprint
from app.analytics.trade_execution import simulate_historical_trade
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticTradePlanningResult,
    HistoricalExecutionAgentSubmission,
)
from app.models.execution import ExecutionSimulationConfig
from app.models.market import HistoricalCandleSeries
from app.models.technical import TechnicalModel


class HistoricalExecutionAgentConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    execution_engine_id: str = Field(
        default="jarvis.historical_execution.v1",
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    simulation: ExecutionSimulationConfig = Field(
        default_factory=ExecutionSimulationConfig
    )


class HistoricalExecutionAgent:
    """Execute approved historical intents without future-data leakage."""

    agent_id = "jarvis.historical_execution_agent.v1"

    def __init__(
        self,
        config: HistoricalExecutionAgentConfig | None = None,
    ) -> None:
        if config is not None and not isinstance(
            config,
            HistoricalExecutionAgentConfig,
        ):
            raise ValueError(
                "execution agent config must be validated configuration"
            )
        self.config = HistoricalExecutionAgentConfig.model_validate(
            (config or HistoricalExecutionAgentConfig()).model_dump()
        )

    @property
    def execution_engine_id(self) -> str:
        return self.config.execution_engine_id

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.config.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def execute(
        self,
        planning_result: AgenticTradePlanningResult,
        market_series: HistoricalCandleSeries,
    ) -> HistoricalExecutionAgentSubmission:
        planning_result = _validate_planning_result(planning_result)
        market_series = _validate_execution_market(market_series)
        profile = planning_result.submission.profile
        planned_at = profile.snapshot.evaluated_at
        _validate_planning_prefix(planning_result, market_series)

        execution = simulate_historical_trade(
            planning_result.approved_trade_intent,
            market_series,
            planned_at=planned_at,
            config=self.config.simulation,
        )
        market_fingerprint = market_series_fingerprint(market_series)
        submission_id = (
            f"{self.agent_id}:{planning_result.submission.submission_id}:"
            f"{market_fingerprint}:{self.configuration_fingerprint}"
        )
        return HistoricalExecutionAgentSubmission(
            submission_id=submission_id,
            agent_id=self.agent_id,
            execution_engine_id=self.execution_engine_id,
            configuration_fingerprint=self.configuration_fingerprint,
            market_fingerprint=market_fingerprint,
            planning_submission_id=(
                planning_result.submission.submission_id
            ),
            planning_decision_id=planning_result.decision.decision_id,
            simulated_at=market_series.retrieved_at,
            execution=execution,
        )


def _validate_planning_result(
    result: AgenticTradePlanningResult,
) -> AgenticTradePlanningResult:
    if not isinstance(result, AgenticTradePlanningResult):
        raise ValueError(
            "execution agent requires an agentic trade-planning result"
        )
    result = AgenticTradePlanningResult.model_validate(
        result.model_dump(exclude_computed_fields=True)
    )
    if not result.decision.accepted:
        raise AgentSubmissionRejectedError(
            "execution agent requires a Jarvis-approved trade plan"
        )
    return result


def _validate_execution_market(
    series: HistoricalCandleSeries,
) -> HistoricalCandleSeries:
    if not isinstance(series, HistoricalCandleSeries):
        raise ValueError(
            "execution agent requires historical candle series"
        )
    return HistoricalCandleSeries.model_validate(series.model_dump())


def _validate_planning_prefix(
    planning_result: AgenticTradePlanningResult,
    full_series: HistoricalCandleSeries,
) -> None:
    submission = planning_result.submission
    snapshot = submission.profile.snapshot
    identity = (
        full_series.exchange,
        full_series.symbol_token,
        full_series.symbol,
        full_series.interval,
        full_series.source,
    )
    expected_identity = (
        snapshot.exchange,
        snapshot.symbol_token,
        snapshot.symbol,
        snapshot.interval,
        snapshot.source,
    )
    if identity != expected_identity:
        raise AgentSubmissionRejectedError(
            "execution history does not match the planned instrument"
        )
    if full_series.retrieved_at < snapshot.source_retrieved_at:
        raise AgentSubmissionRejectedError(
            "execution history predates the planning source retrieval"
        )

    prefix = [
        candle
        for candle in full_series.candles
        if candle.timestamp <= snapshot.evaluated_at
    ]
    prefix_series = HistoricalCandleSeries(
        exchange=snapshot.exchange,
        symbol_token=snapshot.symbol_token,
        symbol=snapshot.symbol,
        interval=snapshot.interval,
        candles=prefix,
        retrieved_at=snapshot.source_retrieved_at,
        source=snapshot.source,
    )
    if market_series_fingerprint(prefix_series) != (
        submission.market_fingerprint
    ):
        raise AgentSubmissionRejectedError(
            "execution history does not preserve the approved planning "
            "prefix"
        )
