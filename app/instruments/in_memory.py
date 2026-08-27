import re
from collections.abc import Iterable

from app.exceptions import AmbiguousInstrumentError, InstrumentNotFoundError
from app.models.instruments import ResolvedInstrument


class InMemoryInstrumentResolver:
    """Exact normalized alias resolver for tests and small local catalogs."""

    def __init__(self, instruments: Iterable[ResolvedInstrument]) -> None:
        if isinstance(instruments, (str, bytes)):
            raise ValueError("instrument catalog must contain instruments")
        try:
            catalog = tuple(instruments)
        except TypeError as exc:
            raise ValueError(
                "instrument catalog must be an iterable"
            ) from exc
        if not catalog:
            raise ValueError("instrument catalog must not be empty")
        if any(not isinstance(item, ResolvedInstrument) for item in catalog):
            raise ValueError(
                "instrument catalog requires resolved instrument records"
            )
        identities = [
            (item.exchange.casefold(), item.symbol_token.casefold())
            for item in catalog
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("instrument catalog identities must be unique")
        self._catalog = catalog

    def list_instruments(self) -> tuple[ResolvedInstrument, ...]:
        return self._catalog

    def resolve(
        self,
        query: str,
        *,
        exchange: str | None = None,
    ) -> ResolvedInstrument:
        normalized_query = _search_key(query, field_name="query")
        normalized_exchange = (
            _search_key(exchange, field_name="exchange")
            if exchange is not None
            else None
        )
        matches = tuple(
            instrument
            for instrument in self._catalog
            if (
                normalized_exchange is None
                or _search_key(
                    instrument.exchange,
                    field_name="exchange",
                )
                == normalized_exchange
            )
            and normalized_query in _instrument_keys(instrument)
        )
        if not matches:
            raise InstrumentNotFoundError(
                "No configured instrument matches the requested company"
            )
        if len(matches) > 1:
            raise AmbiguousInstrumentError(
                "The requested company matches multiple instruments"
            )
        return matches[0]


def _instrument_keys(instrument: ResolvedInstrument) -> frozenset[str]:
    values = (
        instrument.symbol_token,
        instrument.symbol,
        _without_equity_suffix(instrument.symbol),
        instrument.display_name,
        *instrument.aliases,
    )
    return frozenset(
        _search_key(value, field_name="instrument identity")
        for value in values
    )


def _without_equity_suffix(symbol: str) -> str:
    return re.sub(r"-(?:EQ|BE|BZ)$", "", symbol, flags=re.IGNORECASE)


def _search_key(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"instrument {field_name} must be a non-blank string")
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if not normalized:
        raise ValueError(
            f"instrument {field_name} must include letters or numbers"
        )
    return normalized
