"""Live end-to-end demo: wake word -> natural language -> Bull/Bear/Judge.

This authenticates with Angel One, downloads/uses the cached instrument
master, pulls real historical candles, and makes real LLM API calls for
the Bull, Bear, and Judge roles. It is not part of the offline test suite.

Requires, in .env: ANGEL_API_KEY/ANGEL_CLIENT_CODE/ANGEL_PIN/
ANGEL_TOTP_SECRET, JARVIS_USER_NAME, JARVIS_LLM_MODEL (a "provider/model"
string), and the provider credential it implies (e.g. ANTHROPIC_API_KEY).
"""

from app.angel.client import AngelOneClient
from app.composition.conversation import compose_jarvis_conversation
from app.runtime import run_entrypoint
from app.services.market_data import MarketDataService
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries


def main():
    market_service = MarketDataService(gateway=AngelOneClient())
    market_service.initialize()

    archive = ResearchArchiveService(
        DuckDBJarvisStorage("data/jarvis_conversation_demo.duckdb")
    )
    rolling_fetch = PullRollingMarketSeries(market_service, archive)

    session = compose_jarvis_conversation(rolling_fetch, archive=archive)

    wake_turn = session.handle_text("Hey Jarvis")
    print(f"[{wake_turn.outcome.value}] {wake_turn.display_message}")

    turn = session.handle_text(
        "How is Reliance looking for a swing trade?"
    )
    print(f"[{turn.outcome.value}] {turn.display_message}")

    response = turn.research_response
    if response is not None and response.result is not None:
        verdict = response.result.debate_result.submission.verdict
        print(f"\nWinner: {verdict.winner.value}")
        print(f"Confidence: {verdict.confidence_percentage:.0f}%")
        print(f"\nBull case: {verdict.bull_case_summary}")
        print(f"Bear case: {verdict.bear_case_summary}")
        print(f"\nRationale: {verdict.rationale}")
    elif response is not None and response.failure is not None:
        print(f"\nLLM failure: {response.failure.display_message}")


if __name__ == "__main__":
    raise SystemExit(
        run_entrypoint(
            main,
            logger_name="examples.conversation_demo",
        )
    )
