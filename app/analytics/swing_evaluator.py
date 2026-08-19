from datetime import datetime
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from app.analytics.fair_value_gaps import detect_fair_value_gaps
from app.analytics.historical_analysis import (
    analyze_swing_profiles_over_history,
)
from app.analytics.indicators import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_obv,
    calculate_rsi,
    calculate_sma,
    calculate_stochastic,
)
from app.analytics.momentum_signals import (
    generate_macd_momentum_signal,
    generate_rsi_mean_reversion_signal,
    generate_stochastic_momentum_signal,
)
from app.analytics.price_action_signals import (
    generate_fair_value_gap_signal,
    generate_market_structure_signal,
    generate_support_resistance_lifecycle_signal,
)
from app.analytics.signal_profiles import (
    build_swing_trading_signal_profile,
)
from app.analytics.support_resistance import (
    detect_support_resistance_zones,
)
from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.analytics.swing_pivots import detect_swing_pivots
from app.analytics.trend_signals import (
    generate_adx_trend_signal,
    generate_moving_average_alignment_signal,
)
from app.analytics.volatility_signals import (
    generate_atr_volatility_signal,
    generate_bollinger_band_signal,
)
from app.analytics.volume_signals import (
    generate_obv_confirmation_signal,
)
from app.exceptions import InsufficientDataError
from app.models.historical_analysis import HistoricalSwingProfileSeries
from app.models.market import HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SwingTradingSignalProfile,
    TechnicalSignalSnapshot,
)
from app.models.technical import PriceField, TechnicalModel


class UnifiedSwingEvaluatorConfig(TechnicalModel):
    """Versioned calculation settings for the unified swing evaluator."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    evaluator_id: str = Field(
        default="jarvis.unified_swing.v1",
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    fast_ema_period: int = Field(default=20, ge=1)
    slow_sma_period: int = Field(default=50, ge=1)
    rsi_period: int = Field(default=14, ge=1)
    macd_fast_period: int = Field(default=12, ge=1)
    macd_slow_period: int = Field(default=26, ge=1)
    macd_signal_period: int = Field(default=9, ge=1)
    bollinger_period: int = Field(default=20, ge=1)
    bollinger_deviation: float = Field(default=2.0, gt=0)
    atr_period: int = Field(default=14, ge=1)
    adx_period: int = Field(default=14, ge=1)
    stochastic_fast_k_period: int = Field(default=14, ge=1)
    stochastic_slow_k_period: int = Field(default=3, ge=1)
    stochastic_slow_d_period: int = Field(default=3, ge=1)

    pivot_left_strength: int = Field(default=2, ge=1)
    pivot_right_strength: int = Field(default=2, ge=1)
    structure_equality_tolerance_percentage: float = Field(
        default=0.1,
        ge=0,
    )
    support_resistance_tolerance_percentage: float = Field(
        default=0.5,
        ge=0,
    )
    support_resistance_minimum_touches: int = Field(
        default=2,
        ge=2,
    )
    support_resistance_strong_touch_count: int = Field(
        default=3,
        ge=2,
    )
    fvg_minimum_gap_percentage: float = Field(default=0.0, ge=0)
    fvg_minimum_atr_multiple: float | None = Field(
        default=None,
        ge=0,
    )
    fvg_max_age_candles: int | None = Field(default=None, ge=1)

    trend_weight: float = Field(default=1.25, gt=0)
    momentum_weight: float = Field(default=1.0, gt=0)
    volatility_weight: float = Field(default=0.75, gt=0)
    volume_weight: float = Field(default=1.0, gt=0)
    price_action_weight: float = Field(default=1.5, gt=0)
    minimum_coverage_percentage: float = Field(default=60.0, ge=0, le=100)
    directional_threshold: float = Field(default=20.0, gt=0, le=100)
    strong_threshold: float = Field(default=60.0, gt=0, le=100)

    @model_validator(mode="after")
    def validate_period_relationships(self) -> Self:
        if self.fast_ema_period >= self.slow_sma_period:
            raise ValueError(
                "unified swing fast EMA period must be below slow SMA "
                "period"
            )
        if self.macd_fast_period >= self.macd_slow_period:
            raise ValueError(
                "unified swing MACD fast period must be below slow "
                "period"
            )
        if self.strong_threshold <= self.directional_threshold:
            raise ValueError(
                "unified swing strong threshold must exceed directional "
                "threshold"
            )
        if (
            self.support_resistance_strong_touch_count
            < self.support_resistance_minimum_touches
        ):
            raise ValueError(
                "unified swing strong zone touch count cannot be below "
                "minimum zone touches"
            )
        return self

    @property
    def minimum_candles(self) -> int:
        """Minimum prefix length needed by every configured indicator."""
        return max(
            self.fast_ema_period,
            self.slow_sma_period,
            self.rsi_period + 1,
            self.macd_slow_period + self.macd_signal_period - 1,
            self.bollinger_period,
            self.atr_period + 1,
            2 * self.adx_period,
            (
                self.stochastic_fast_k_period
                + self.stochastic_slow_k_period
                + self.stochastic_slow_d_period
                - 2
            ),
            self.pivot_left_strength + self.pivot_right_strength + 1,
            3,
        )

    @property
    def category_weights(self) -> dict[SignalCategory, float]:
        return {
            SignalCategory.TREND: self.trend_weight,
            SignalCategory.MOMENTUM: self.momentum_weight,
            SignalCategory.VOLATILITY: self.volatility_weight,
            SignalCategory.VOLUME: self.volume_weight,
            SignalCategory.PRICE_ACTION: self.price_action_weight,
        }


class UnifiedSwingEvaluator:
    """Calculate a complete swing profile from one candle prefix."""

    def __init__(
        self,
        config: UnifiedSwingEvaluatorConfig | None = None,
    ) -> None:
        if config is not None and not isinstance(
            config,
            UnifiedSwingEvaluatorConfig,
        ):
            raise ValueError(
                "unified swing config must be a validated evaluator "
                "configuration"
            )
        self.config = config or UnifiedSwingEvaluatorConfig()

    @property
    def minimum_candles(self) -> int:
        return self.config.minimum_candles

    def __call__(
        self,
        market_series: HistoricalCandleSeries,
    ) -> SwingTradingSignalProfile:
        _validate_market_prefix(market_series, self.minimum_candles)
        config = self.config
        evaluated_at = market_series.candles[-1].timestamp

        fast_ema = calculate_ema(
            market_series,
            config.fast_ema_period,
            PriceField.CLOSE,
        )
        slow_sma = calculate_sma(
            market_series,
            config.slow_sma_period,
            PriceField.CLOSE,
        )
        rsi = calculate_rsi(
            market_series,
            config.rsi_period,
            PriceField.CLOSE,
        )
        macd = calculate_macd(
            market_series,
            config.macd_fast_period,
            config.macd_slow_period,
            config.macd_signal_period,
            PriceField.CLOSE,
        )
        bollinger = calculate_bollinger_bands(
            market_series,
            config.bollinger_period,
            config.bollinger_deviation,
            config.bollinger_deviation,
            PriceField.CLOSE,
        )
        atr = calculate_atr(market_series, config.atr_period)
        adx = calculate_adx(market_series, config.adx_period)
        stochastic = calculate_stochastic(
            market_series,
            config.stochastic_fast_k_period,
            config.stochastic_slow_k_period,
            config.stochastic_slow_d_period,
        )
        obv = calculate_obv(market_series, PriceField.CLOSE)

        pivots = detect_swing_pivots(
            market_series,
            left_strength=config.pivot_left_strength,
            right_strength=config.pivot_right_strength,
        )
        zones = detect_support_resistance_zones(
            pivots,
            tolerance_percentage=(
                config.support_resistance_tolerance_percentage
            ),
            minimum_touches=(
                config.support_resistance_minimum_touches
            ),
            as_of=evaluated_at,
        )
        zone_lifecycle = track_support_resistance_lifecycle(
            market_series,
            zones,
            as_of=evaluated_at,
        )
        fair_value_gaps = detect_fair_value_gaps(
            market_series,
            minimum_gap_percentage=config.fvg_minimum_gap_percentage,
            atr_series=atr,
            minimum_atr_multiple=config.fvg_minimum_atr_multiple,
        )

        evidence = [
            generate_moving_average_alignment_signal(
                fast_ema,
                slow_sma,
                as_of=evaluated_at,
            ),
            generate_adx_trend_signal(adx, as_of=evaluated_at),
            generate_rsi_mean_reversion_signal(
                rsi,
                as_of=evaluated_at,
            ),
            generate_macd_momentum_signal(
                macd,
                as_of=evaluated_at,
            ),
            generate_stochastic_momentum_signal(
                stochastic,
                as_of=evaluated_at,
            ),
            generate_bollinger_band_signal(
                bollinger,
                market_series,
                as_of=evaluated_at,
            ),
            generate_atr_volatility_signal(
                atr,
                market_series,
                as_of=evaluated_at,
            ),
            generate_obv_confirmation_signal(
                obv,
                market_series,
                as_of=evaluated_at,
            ),
            generate_fair_value_gap_signal(
                fair_value_gaps,
                market_series,
                max_age_candles=config.fvg_max_age_candles,
                as_of=evaluated_at,
            ),
            generate_support_resistance_lifecycle_signal(
                zone_lifecycle,
                market_series,
                strong_touch_count=(
                    config.support_resistance_strong_touch_count
                ),
                as_of=evaluated_at,
            ),
            generate_market_structure_signal(
                pivots,
                market_series,
                equality_tolerance_percentage=(
                    config.structure_equality_tolerance_percentage
                ),
                as_of=evaluated_at,
            ),
        ]
        evidence.sort(
            key=lambda item: (
                item.available_at,
                item.category.value,
                item.evidence_id,
            )
        )

        snapshot = TechnicalSignalSnapshot(
            exchange=market_series.exchange,
            symbol_token=market_series.symbol_token,
            symbol=market_series.symbol,
            interval=market_series.interval,
            source=market_series.source,
            source_retrieved_at=market_series.retrieved_at,
            evaluated_at=evaluated_at,
            evidence=evidence,
        )
        return build_swing_trading_signal_profile(
            snapshot,
            category_weights=config.category_weights,
            minimum_coverage_percentage=(
                config.minimum_coverage_percentage
            ),
            directional_threshold=config.directional_threshold,
            strong_threshold=config.strong_threshold,
            require_synchronized_evidence=True,
        )


def evaluate_unified_swing_profile(
    market_series: HistoricalCandleSeries,
    *,
    config: UnifiedSwingEvaluatorConfig | None = None,
) -> SwingTradingSignalProfile:
    """Run agent execution and return only the judge-approved profile."""
    from app.agents.technical_swing_agent import TechnicalSwingAgent
    from app.orchestration.agent_orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator(
        technical_agent=TechnicalSwingAgent(config)
    )
    return orchestrator.evaluate_swing_prefix(market_series)


def analyze_unified_swing_profiles_over_history(
    market_series: HistoricalCandleSeries,
    *,
    config: UnifiedSwingEvaluatorConfig | None = None,
    warmup_candles: int | None = None,
    evaluation_stride: int = 1,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> HistoricalSwingProfileSeries:
    """Run agent-reviewed analysis over chronological candle prefixes."""
    from app.agents.technical_swing_agent import TechnicalSwingAgent
    from app.orchestration.agent_orchestrator import AgentOrchestrator

    technical_agent = TechnicalSwingAgent(config)
    orchestrator = AgentOrchestrator(technical_agent=technical_agent)
    effective_warmup = (
        technical_agent.minimum_candles - 1
        if warmup_candles is None
        else warmup_candles
    )
    return analyze_swing_profiles_over_history(
        market_series,
        orchestrator.evaluate_swing_prefix,
        warmup_candles=effective_warmup,
        evaluation_stride=evaluation_stride,
        start_at=start_at,
        end_at=end_at,
        analysis_version=technical_agent.evaluator_id,
        evaluator_name=orchestrator.orchestrator_id,
    )


def _validate_market_prefix(
    market_series: HistoricalCandleSeries,
    minimum_candles: int,
) -> None:
    candle_count = len(market_series.candles)
    if candle_count < minimum_candles:
        raise InsufficientDataError(
            "unified swing evaluation requires at least "
            f"{minimum_candles} candles; received {candle_count}"
        )

    previous_timestamp = None
    for candle in market_series.candles:
        if (
            previous_timestamp is not None
            and candle.timestamp <= previous_timestamp
        ):
            raise ValueError(
                "unified swing candles must have unique timestamps in "
                "ascending order"
            )
        if candle.timestamp > market_series.retrieved_at:
            raise ValueError(
                "unified swing evaluation cannot use candles after "
                "source retrieval"
            )
        previous_timestamp = candle.timestamp
