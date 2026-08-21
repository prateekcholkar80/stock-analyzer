"""Shared fixtures for debate-layer tests: a real, judge-approved technical
result built the same way test_agent_orchestrator.py builds one, so debate
tests exercise real evidence ids rather than hand-rolled ones.
"""

from datetime import UTC, datetime, timedelta
from math import sin

from app.agents.technical_swing_agent import TechnicalSwingAgent
from app.models.agentic import AgenticSwingAnalysisResult
from app.models.debate import DebateTerminationReason
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import SignalDirection
from app.models.storage import DebateRunSummary
from app.orchestration.agent_orchestrator import JarvisSwingJudge


BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
RETRIEVED_AT = BASE_TIME + timedelta(days=100)


def build_market_series(count=60, **overrides):
    candles = []
    for index in range(count):
        close = 100 + index * 0.3 + sin(index / 2)
        candles.append(
            Candle(
                timestamp=BASE_TIME + timedelta(days=index),
                open=close - 0.2,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=100_000 + index * 1_000,
            )
        )
    values = {
        "exchange": "NSE",
        "symbol_token": "2885",
        "symbol": "RELIANCE-EQ",
        "interval": "ONE_DAY",
        "candles": candles,
        "retrieved_at": RETRIEVED_AT,
        "source": "test_market",
    }
    values.update(overrides)
    return HistoricalCandleSeries(**values)


def build_approved_technical_result(
    market_series: HistoricalCandleSeries | None = None,
) -> AgenticSwingAnalysisResult:
    series = market_series or build_market_series()
    agent = TechnicalSwingAgent()
    judge = JarvisSwingJudge(
        expected_agent_id=agent.agent_id,
        expected_evaluator_id=agent.evaluator_id,
        expected_configuration_fingerprint=agent.configuration_fingerprint,
    )
    submission = agent.execute(series)
    decision = judge.review(submission, series)
    return AgenticSwingAnalysisResult(
        orchestrator_id="jarvis.agent_orchestrator.v1",
        submission=submission,
        decision=decision,
    )


def build_precedent_summary(**overrides) -> DebateRunSummary:
    values = {
        "run_id": "debate:precedent-1",
        "result_fingerprint": "a" * 64,
        "technical_fingerprint": "b" * 64,
        "stored_at": BASE_TIME,
        "exchange": "NSE",
        "symbol_token": "2885",
        "symbol": "RELIANCE-EQ",
        "interval": "ONE_DAY",
        "winner": SignalDirection.BULLISH,
        "confidence_percentage": 62.0,
        "termination_reason": DebateTerminationReason.MAX_ROUNDS_REACHED,
        "round_count": 2,
        "signature": ("trend:bullish", "momentum:bullish"),
    }
    values.update(overrides)
    return DebateRunSummary(**values)
