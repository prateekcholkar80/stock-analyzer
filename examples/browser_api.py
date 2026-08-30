"""Live Jarvis HTTP API composition for the future browser dashboard.

Run from the repository root with:

    .venv/bin/uvicorn examples.browser_api:create_app --factory --reload

Creating the application authenticates with Angel One. A research operation
also invokes the configured Bull, Bear, Judge, and Jarvis LLM roles.
"""

import os
from collections.abc import Mapping

from app.composition.fundamentals import (
    compose_tijori_fundamental_coordinator,
    compose_tijori_session_service,
    load_tijori_stdio_settings,
)
from app.api.models import ProviderSessionTargetRequest
from app.exceptions import ConfigurationError
from app.fundamentals.provider_connections import (
    InMemoryProviderConnectionRegistry,
)
from app.models.fundamentals import ProviderConnectionScope
from app.storage.fundamental_repositories import FundamentalSnapshotRepository


def _provider_session_http_dependencies(
    environment: Mapping[str, str] | None = None,
    *,
    repository: FundamentalSnapshotRepository | None = None,
) -> dict[str, object]:
    """Compose optional provider sessions and cached fundamental evidence."""

    source = environment if environment is not None else os.environ
    enabled_value = source.get("JARVIS_TIJORI_ENABLED", "false")
    if not isinstance(enabled_value, str):
        raise ConfigurationError(
            "Tijori browser-session configuration is missing or invalid"
        )
    enabled = enabled_value.strip().casefold()
    if enabled == "false":
        return {}
    if enabled != "true":
        raise ConfigurationError(
            "Tijori browser-session configuration is missing or invalid"
        )
    try:
        if not isinstance(repository, FundamentalSnapshotRepository):
            raise TypeError("enabled Tijori runtime requires cache repository")
        tenant_id = _required_setting(source, "JARVIS_LOCAL_TENANT_ID")
        connection_id = _required_setting(
            source,
            "JARVIS_TIJORI_CONNECTION_ID",
        )
        account_hash = source.get(
            "JARVIS_TIJORI_ACCOUNT_REFERENCE_HASH"
        )
        if account_hash is not None:
            if not isinstance(account_hash, str):
                raise ValueError("account reference hash must be text")
            account_hash = account_hash.strip() or None

        settings = load_tijori_stdio_settings(source)
        service = compose_tijori_session_service(
            transport_settings=settings,
        )
        evidence = compose_tijori_fundamental_coordinator(
            transport_settings=settings,
            repository=repository,
        )
        registry = InMemoryProviderConnectionRegistry()
        registry.register_connection(
            ProviderConnectionScope(
                tenant_id=tenant_id,
                provider_connection_id=connection_id,
                provider="tijori",
                account_reference_hash=account_hash,
            )
        )
        provider_target = ProviderSessionTargetRequest(
            provider_connection_id=connection_id,
            provider="tijori",
            account_reference_hash=account_hash,
        )
    except (ConfigurationError, TypeError, ValueError) as exc:
        raise ConfigurationError(
            "Tijori browser-session configuration is missing or invalid"
        ) from exc

    return {
        "fundamental_evidence_coordinator": evidence,
        "fundamental_provider_scope_resolver": (
            lambda browser_session_id: registry.resolve(
                browser_session_id,
                provider_target,
            )
        ),
        "provider_sessions": service,
        "provider_connection_scope_resolver": registry,
        "browser_session_ownership": registry,
        "tenant_identity_resolver": lambda request: tenant_id,
    }


def _required_setting(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required provider-session setting is missing")
    return value.strip()


def create_app():
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

    market_service = MarketDataService(gateway=AngelOneClient())
    market_service.initialize()
    storage = DuckDBJarvisStorage(
        os.environ.get(
            "JARVIS_DATABASE_PATH",
            "data/jarvis_browser.duckdb",
        )
    )
    provider_session_dependencies = _provider_session_http_dependencies(
        repository=storage
    )
    fundamental_evidence = provider_session_dependencies.pop(
        "fundamental_evidence_coordinator",
        None,
    )
    fundamental_scope_resolver = provider_session_dependencies.pop(
        "fundamental_provider_scope_resolver",
        None,
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
        fundamental_evidence_executor=fundamental_evidence,
        provider_scope_resolver=fundamental_scope_resolver,
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
    application = create_jarvis_http_app(
        runner,
        conversation=conversation,
        speech=speech,
        transcription=transcription,
        shutdown=shutdown,
        allowed_origins=origins,
        **provider_session_dependencies,
    )
    application.state.fundamental_evidence_coordinator = fundamental_evidence
    return application
