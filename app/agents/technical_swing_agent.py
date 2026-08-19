from hashlib import sha256

from app.analytics.swing_evaluator import (
    UnifiedSwingEvaluator,
    UnifiedSwingEvaluatorConfig,
)
from app.models.agentic import TechnicalSwingAgentSubmission
from app.models.market import HistoricalCandleSeries


class TechnicalSwingAgent:
    """Own the deterministic technical-analysis execution task."""

    agent_id = "jarvis.technical_swing_agent.v1"

    def __init__(
        self,
        config: UnifiedSwingEvaluatorConfig | None = None,
    ) -> None:
        self._evaluator = UnifiedSwingEvaluator(config)

    @property
    def evaluator_id(self) -> str:
        return self._evaluator.config.evaluator_id

    @property
    def config(self) -> UnifiedSwingEvaluatorConfig:
        return self._evaluator.config

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self._evaluator.config.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    @property
    def minimum_candles(self) -> int:
        return self._evaluator.minimum_candles

    def execute(
        self,
        market_series: HistoricalCandleSeries,
    ) -> TechnicalSwingAgentSubmission:
        """Complete the assigned prefix analysis and submit evidence."""
        profile = self._evaluator(market_series)
        evaluated_at = profile.snapshot.evaluated_at
        submission_id = (
            f"{self.agent_id}:{market_series.exchange}:"
            f"{market_series.symbol_token}:{market_series.interval}:"
            f"{evaluated_at.isoformat()}:"
            f"{market_series.retrieved_at.isoformat()}"
        )
        return TechnicalSwingAgentSubmission(
            submission_id=submission_id,
            agent_id=self.agent_id,
            evaluator_id=self.evaluator_id,
            input_fingerprint=market_series_fingerprint(market_series),
            configuration_fingerprint=self.configuration_fingerprint,
            exchange=market_series.exchange,
            symbol_token=market_series.symbol_token,
            symbol=market_series.symbol,
            interval=market_series.interval,
            source=market_series.source,
            source_retrieved_at=market_series.retrieved_at,
            evaluated_at=evaluated_at,
            input_candle_count=len(market_series.candles),
            profile=profile,
        )


def market_series_fingerprint(
    market_series: HistoricalCandleSeries,
) -> str:
    """Create a stable digest for the exact assigned candle prefix."""
    serialized = market_series.model_dump_json()
    return sha256(serialized.encode("utf-8")).hexdigest()
