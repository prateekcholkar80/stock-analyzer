from datetime import datetime, timezone
from threading import RLock
from typing import Literal, Protocol, runtime_checkable

from pydantic import ConfigDict, Field

from app.agents.ticker_resolution_agent import resolve_via_llm
from app.exceptions import ApplicationError
from app.instruments.amfi_market_cap import AmfiMarketCapCatalog
from app.instruments.candidate_shortlist import shortlist_candidates
from app.instruments.catalog import NseInstrumentCatalog, build_nse_instrument_catalog
from app.instruments.nse_sector_master import NseSectorMasterCatalog
from app.intents.swing_analysis import SwingIntentInterpreter
from app.llm.gateway import StructuredLLMGateway
from app.models.instruments import ResolvedInstrument
from app.models.technical import TechnicalModel


class TickerConversationalResolutionResult(TechnicalModel):
    """Outcome of one deterministic-shortlist + LLM-proposal resolution
    attempt. Never a final resolution by itself -- ``resolved_needs_
    confirmation`` still requires the caller to obtain user confirmation
    before treating ``chosen_symbol`` as resolved.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    outcome: Literal[
        "resolved_needs_confirmation",
        "ambiguous",
        "not_found",
    ]
    chosen_symbol: str | None = Field(default=None, min_length=1)
    exchange: str | None = Field(default=None, min_length=1)
    candidate_display_names: tuple[str, ...] = ()


@runtime_checkable
class AngelIdentitySource(Protocol):
    """Structural contract for the identity source (matches
    AngelInstrumentMasterResolver's list_instruments/refresh shape)."""

    def list_instruments(self) -> tuple[ResolvedInstrument, ...]:
        ...

    def refresh(self) -> int:
        ...


class ResolveTickerConversationally:
    """Deterministic shortlist + constrained-LLM ticker resolution,
    composed with catalog refresh. Every non-`not_found` outcome still
    requires the caller to obtain user confirmation -- this use case
    never resolves a symbol on its own authority.
    """

    def __init__(
        self,
        *,
        interpreter: SwingIntentInterpreter,
        angel_identity_source: AngelIdentitySource,
        amfi_catalog: AmfiMarketCapCatalog,
        nse_sector_catalog: NseSectorMasterCatalog,
        resolver_gateway: StructuredLLMGateway,
        shortlist_limit: int = 8,
    ) -> None:
        if not isinstance(interpreter, SwingIntentInterpreter):
            raise ValueError("ticker resolution requires an intent interpreter")
        if not isinstance(angel_identity_source, AngelIdentitySource):
            raise ValueError(
                "ticker resolution requires an Angel identity source"
            )
        if not isinstance(amfi_catalog, AmfiMarketCapCatalog):
            raise ValueError("ticker resolution requires an AMFI catalog")
        if not isinstance(nse_sector_catalog, NseSectorMasterCatalog):
            raise ValueError(
                "ticker resolution requires an NSE sector-master catalog"
            )
        if not isinstance(resolver_gateway, StructuredLLMGateway):
            raise ValueError(
                "ticker resolution requires a structured LLM gateway"
            )
        if shortlist_limit <= 0:
            raise ValueError("ticker resolution shortlist limit must be positive")

        self._interpreter = interpreter
        self._angel_identity_source = angel_identity_source
        self._amfi_catalog = amfi_catalog
        self._nse_sector_catalog = nse_sector_catalog
        self._resolver_gateway = resolver_gateway
        self._shortlist_limit = shortlist_limit
        self._catalog: NseInstrumentCatalog | None = None
        self._lock = RLock()

    def attempt(self, command: str) -> TickerConversationalResolutionResult:
        intent = self._interpreter.interpret(command)
        catalog = self._catalog_snapshot()

        shortlist = shortlist_candidates(
            intent.instrument_query,
            catalog,
            limit=self._shortlist_limit,
        )
        if not shortlist:
            return TickerConversationalResolutionResult(outcome="not_found")
        if len(shortlist) == 1:
            candidate = shortlist[0].instrument
            return TickerConversationalResolutionResult(
                outcome="resolved_needs_confirmation",
                chosen_symbol=candidate.symbol,
                exchange=intent.exchange,
                candidate_display_names=(candidate.display_name,),
            )

        choice = resolve_via_llm(
            gateway=self._resolver_gateway,
            query=intent.instrument_query,
            shortlist=shortlist,
        )
        if choice.chosen_symbols:
            return TickerConversationalResolutionResult(
                outcome="resolved_needs_confirmation",
                chosen_symbol=choice.chosen_symbols[0],
                exchange=intent.exchange,
                candidate_display_names=tuple(
                    item.instrument.display_name for item in shortlist
                ),
            )
        return TickerConversationalResolutionResult(
            outcome="ambiguous" if choice.is_ambiguous else "not_found",
            candidate_display_names=tuple(
                item.instrument.display_name for item in shortlist
            ),
        )

    def refresh_catalog(self) -> int:
        """Force a fresh download of every underlying source and rebuild
        the composed classified catalog. Returns the resulting instrument
        count.
        """
        with self._lock:
            self._angel_identity_source.refresh()
            self._amfi_catalog.refresh()
            self._nse_sector_catalog.refresh()
            self._catalog = None
            catalog = self._catalog_snapshot()
            return len(catalog.all_instruments())

    def _catalog_snapshot(self) -> NseInstrumentCatalog:
        with self._lock:
            if self._catalog is not None:
                return self._catalog
            # Sector/industry is enrichment metadata, not required for
            # resolution -- ClassifiedInstrument.sector/industry are
            # already optional fields, and build_nse_instrument_catalog
            # already handles a symbol with no sector match. So an NSE
            # sector-master download failure must degrade to an empty
            # mapping rather than blocking ticker resolution entirely on
            # a data source it doesn't actually need.
            try:
                sector_industry_by_symbol = (
                    self._nse_sector_catalog.sector_industry_by_symbol()
                )
            except ApplicationError:
                sector_industry_by_symbol = {}
            self._catalog = build_nse_instrument_catalog(
                self._angel_identity_source.list_instruments(),
                market_cap_entries=self._amfi_catalog.entries(),
                sector_industry_by_symbol=sector_industry_by_symbol,
                as_of=datetime.now(timezone.utc),
            )
            return self._catalog
