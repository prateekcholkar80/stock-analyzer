import re
from collections.abc import Iterable, Mapping
from datetime import datetime

from pydantic import ConfigDict, Field, field_validator

from app.instruments.amfi_market_cap import AmfiMarketCapEntry
from app.instruments.classification import (
    ClassifiedInstrument,
    MarketCapClass,
    MatchReport,
    UnmatchedMarketCapEntry,
    match_company_name_to_symbol,
)
from app.instruments.nse_sector_master import SectorIndustry
from app.models.instruments import ResolvedInstrument
from app.models.technical import TechnicalModel


class NseInstrumentCatalog(TechnicalModel):
    """The composed NSE cash-equity universe: identity + cap-class + sector.

    Both AMFI's cap-class list and NSE's sector list are periodic
    snapshots, not live data, so provenance (which release each came
    from) is carried on the catalog itself rather than hidden.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    instruments: tuple[ClassifiedInstrument, ...] = Field(min_length=1)
    market_cap_match_report: MatchReport
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("catalog as_of must include timezone information")
        return value

    @field_validator("instruments")
    @classmethod
    def require_unique_symbols(
        cls,
        value: tuple[ClassifiedInstrument, ...],
    ) -> tuple[ClassifiedInstrument, ...]:
        symbols = [item.instrument.symbol.casefold() for item in value]
        if len(symbols) != len(set(symbols)):
            raise ValueError("catalog instruments must have unique symbols")
        return value

    def all_instruments(self) -> tuple[ClassifiedInstrument, ...]:
        return self.instruments

    def filter(
        self,
        *,
        market_cap_class: MarketCapClass | None = None,
        sector: str | None = None,
    ) -> tuple[ClassifiedInstrument, ...]:
        normalized_sector = sector.strip().casefold() if sector else None
        return tuple(
            item
            for item in self.instruments
            if (
                market_cap_class is None
                or item.market_cap_class == market_cap_class
            )
            and (
                normalized_sector is None
                or (
                    item.sector is not None
                    and item.sector.casefold() == normalized_sector
                )
            )
        )

    def to_records(self) -> tuple[dict, ...]:
        """Flat, JSON-serializable rows for downstream consumers (the
        frontend symbol resolver, an LLM-facing dataset export, etc.).
        """
        return tuple(
            {
                "symbol": item.instrument.symbol,
                "exchange": item.instrument.exchange,
                "symbol_token": item.instrument.symbol_token,
                "display_name": item.instrument.display_name,
                "market_cap_class": item.market_cap_class.value,
                "sector": item.sector,
                "industry": item.industry,
                "as_of": self.as_of.isoformat(),
            }
            for item in self.instruments
        )


def _without_equity_suffix(symbol: str) -> str:
    return re.sub(r"-(?:EQ|BE|BZ)$", "", symbol, flags=re.IGNORECASE)


def _match_market_cap_entries(
    entries: Iterable[AmfiMarketCapEntry],
    candidates: tuple[ResolvedInstrument, ...],
) -> tuple[dict[str, MarketCapClass], dict[str, str], MatchReport]:
    """Join AMFI entries onto NSE symbols, preferring AMFI's own "NSE
    Symbol" column (a direct, reliable key) and falling back to fuzzy
    company-name matching only for entries with no NSE symbol (typically
    BSE-only listings, which have nothing to join to in an NSE-only
    candidate set anyway).

    Also returns the matched company name per symbol -- Angel's own
    identity feed only carries the bare trading symbol as display_name
    (e.g. "INFY", not "Infosys Limited"), so this is the only source of
    a real company name available to enrich the catalog for
    natural-language ticker-resolution queries.
    """
    by_suffixless_symbol = {
        _without_equity_suffix(candidate.symbol).upper(): candidate.symbol
        for candidate in candidates
    }

    by_symbol: dict[str, MarketCapClass] = {}
    company_name_by_symbol: dict[str, str] = {}
    unmatched: list[UnmatchedMarketCapEntry] = []
    matched_count = 0

    for entry in entries:
        symbol: str | None = None
        if entry.nse_symbol is not None:
            symbol = by_suffixless_symbol.get(entry.nse_symbol)
        if symbol is None:
            symbol = match_company_name_to_symbol(
                entry.company_name,
                candidates,
            )
        if symbol is None:
            unmatched.append(
                UnmatchedMarketCapEntry(
                    company_name=entry.company_name,
                    market_cap_class=entry.market_cap_class,
                )
            )
            continue
        matched_count += 1
        by_symbol[symbol] = entry.market_cap_class
        company_name_by_symbol[symbol] = entry.company_name

    return by_symbol, company_name_by_symbol, MatchReport(
        matched_count=matched_count,
        unmatched=tuple(unmatched),
    )


def build_nse_instrument_catalog(
    instruments: Iterable[ResolvedInstrument],
    *,
    market_cap_entries: Iterable[AmfiMarketCapEntry],
    sector_industry_by_symbol: Mapping[str, SectorIndustry],
    as_of: datetime,
) -> NseInstrumentCatalog:
    """Compose Angel identity + AMFI cap-class + NSE sector into one
    classified catalog. A symbol with no cap-class or sector match keeps
    NONE/None rather than being dropped or fabricated.
    """
    resolved_instruments = tuple(instruments)
    market_cap_by_symbol, company_name_by_symbol, match_report = (
        _match_market_cap_entries(
            market_cap_entries,
            resolved_instruments,
        )
    )

    classified: list[ClassifiedInstrument] = []
    for instrument in resolved_instruments:
        market_cap_class = market_cap_by_symbol.get(
            instrument.symbol,
            MarketCapClass.NONE,
        )
        sector_industry = sector_industry_by_symbol.get(
            _without_equity_suffix(instrument.symbol).upper()
        )
        company_name = company_name_by_symbol.get(instrument.symbol)
        if company_name is not None:
            primary_identities = {
                instrument.symbol.casefold(),
                instrument.display_name.casefold(),
                instrument.symbol_token.casefold(),
            }
            existing_aliases = {
                alias.casefold() for alias in instrument.aliases
            }
            if company_name.casefold() not in (
                primary_identities | existing_aliases
            ):
                # Angel's identity feed only carries the bare trading
                # symbol as display_name; without this, a
                # natural-language query like "Infosys" can never match
                # the catalog at all.
                instrument = instrument.model_copy(
                    update={"aliases": instrument.aliases + (company_name,)}
                )
        classified.append(
            ClassifiedInstrument(
                instrument=instrument,
                market_cap_class=market_cap_class,
                sector=sector_industry.sector if sector_industry else None,
                industry=(
                    sector_industry.industry if sector_industry else None
                ),
            )
        )

    return NseInstrumentCatalog(
        instruments=tuple(classified),
        market_cap_match_report=match_report,
        as_of=as_of,
    )
