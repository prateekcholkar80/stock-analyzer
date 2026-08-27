import re
from enum import IntEnum

from app.instruments.catalog import NseInstrumentCatalog
from app.instruments.classification import ClassifiedInstrument


class _MatchRank(IntEnum):
    """Lower is better; used only to order the shortlist, never to
    auto-select a single winner."""

    SYMBOL_PREFIX = 0
    DISPLAY_NAME_PREFIX = 1
    DISPLAY_NAME_SUBSTRING = 2


def _without_equity_suffix(symbol: str) -> str:
    return re.sub(r"-(?:EQ|BE|BZ)$", "", symbol, flags=re.IGNORECASE)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def shortlist_candidates(
    query: str,
    catalog: NseInstrumentCatalog,
    *,
    limit: int = 8,
) -> tuple[ClassifiedInstrument, ...]:
    """Deterministic, non-LLM shortlist of real candidates for a partial
    or fuzzy company/symbol query. Always returns a tuple, never a single
    confirmed match, even when exactly one candidate is found -- ranking
    is only ever a hint for downstream disambiguation (deterministic or
    LLM-assisted), never an auto-accept decision. Every candidate returned
    is a real catalog entry; nothing is invented.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("shortlist query must be a non-blank string")
    if limit <= 0:
        raise ValueError("shortlist limit must be a positive integer")

    normalized_query = _normalize(query)
    if not normalized_query:
        return ()

    ranked: list[tuple[_MatchRank, str, ClassifiedInstrument]] = []
    for item in catalog.all_instruments():
        instrument = item.instrument
        symbol_key = _normalize(_without_equity_suffix(instrument.symbol))
        display_key = _normalize(instrument.display_name)
        alias_keys = tuple(_normalize(alias) for alias in instrument.aliases)

        if symbol_key.startswith(normalized_query):
            rank = _MatchRank.SYMBOL_PREFIX
        elif display_key.startswith(normalized_query) or any(
            alias_key.startswith(normalized_query) for alias_key in alias_keys
        ):
            rank = _MatchRank.DISPLAY_NAME_PREFIX
        elif normalized_query in display_key or any(
            normalized_query in alias_key for alias_key in alias_keys
        ):
            rank = _MatchRank.DISPLAY_NAME_SUBSTRING
        else:
            continue

        ranked.append((rank, instrument.symbol, item))

    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    return tuple(item for _, _, item in ranked[:limit])
