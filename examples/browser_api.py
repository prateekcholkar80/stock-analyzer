"""Live Jarvis HTTP API composition for the future browser dashboard.

Run from the repository root with:

    .venv/bin/uvicorn examples.browser_api:create_app --factory --reload

Creating the application authenticates with Angel One. A research operation
also invokes the configured Bull, Bear, Judge, and Jarvis LLM roles.
"""

import os

from app.angel.client import AngelOneClient
from app.api.http import create_jarvis_http_app
from app.composition.browser import compose_jarvis_browser_operations
from app.composition.speech import (
    compose_jarvis_speech_synthesis,
    compose_jarvis_speech_transcription,
)
from app.conversation.config import JarvisConversationConfig
from app.instruments.amfi_market_cap import AmfiMarketCapCatalog
from app.instruments.nse_sector_master import NseSectorMasterCatalog
from app.services.market_data import MarketDataService
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries


def create_app():
    market_service = MarketDataService(gateway=AngelOneClient())
    market_service.initialize()
    storage = DuckDBJarvisStorage(
        os.environ.get(
            "JARVIS_DATABASE_PATH",
            "data/jarvis_browser.duckdb",
        )
    )
    archive = ResearchArchiveService(storage)
    rolling_fetch = PullRollingMarketSeries(market_service, archive)
    conversation_config = JarvisConversationConfig.from_environment()
    # Both catalogs are lazily downloaded on first use (a company name the
    # exact-match resolver can't place, e.g. "Infosys" rather than the
    # exact listed symbol) -- composing them here does no eager I/O and
    # adds no startup latency.
    runner, conversation = compose_jarvis_browser_operations(
        rolling_fetch,
        conversation_config=conversation_config,
        archive=archive,
        amfi_catalog=AmfiMarketCapCatalog(),
        nse_sector_catalog=NseSectorMasterCatalog(),
        max_workers=int(os.environ.get("JARVIS_BROWSER_WORKERS", "2")),
    )
    # TTS/STT are composed independently, not threaded through the
    # research/conversation dependency graph -- neither eagerly fires and
    # each is only built (and only requires Google credentials) on first
    # real use of the /speech or /transcribe route.
    speech = compose_jarvis_speech_synthesis()
    transcription = compose_jarvis_speech_transcription()

    def shutdown() -> None:
        runner.shutdown()
        storage.close()

    origins = tuple(
        origin.strip()
        for origin in os.environ.get(
            "JARVIS_BROWSER_ORIGINS",
            (
                "http://127.0.0.1:3000,http://localhost:3000,"
                "http://127.0.0.1:3001,http://localhost:3001,"
                "http://127.0.0.1:5173,http://localhost:5173"
            ),
        ).split(",")
        if origin.strip()
    )
    return create_jarvis_http_app(
        runner,
        conversation=conversation,
        speech=speech,
        transcription=transcription,
        shutdown=shutdown,
        allowed_origins=origins,
    )
