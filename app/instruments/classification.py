import re
from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import ConfigDict, Field, field_validator

from app.models.instruments import ResolvedInstrument
from app.models.technical import TechnicalModel


class MarketCapClass(StrEnum):
    """AMFI's SEBI-mandated large/mid/small-cap classification."""

    LARGE_CAP = "LARGE_CAP"
    MID_CAP = "MID_CAP"
    SMALL_CAP = "SMALL_CAP"
    NONE = "NONE"


class ClassifiedInstrument(TechnicalModel):
    """An NSE cash-equity instrument annotated with cap-class and sector."""

    model_config = ConfigDict(frozen=True, strict=True)

    instrument: ResolvedInstrument
    market_cap_class: MarketCapClass = MarketCapClass.NONE
    sector: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)

    @field_validator("sector", "industry")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UnmatchedMarketCapEntry(TechnicalModel):
    """An AMFI company name that could not be joined to an NSE symbol."""

    model_config = ConfigDict(frozen=True, strict=True)

    company_name: str = Field(min_length=1)
    market_cap_class: MarketCapClass


class MatchReport(TechnicalModel):
    """Audit trail of the AMFI name-to-symbol join, gaps included."""

    model_config = ConfigDict(frozen=True, strict=True)

    matched_count: int = Field(ge=0)
    unmatched: tuple[UnmatchedMarketCapEntry, ...] = ()


def _normalize_company_key(value: str) -> str:
    without_suffix = re.sub(
        r"\b(limited|ltd|the)\b\.?",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"[^a-z0-9]+", "", without_suffix.casefold())


def match_company_name_to_symbol(
    company_name: str,
    candidates: Iterable[ResolvedInstrument],
) -> str | None:
    """Exact normalized-name match from an AMFI company name to a known
    NSE symbol. Returns None rather than guessing when no candidate's
    display name normalizes to the same key.

    Deliberately exact-only, not substring/containment matching: an
    earlier containment-based fallback was found (via a real live-data
    run against AMFI's actual release) to produce false-positive joins
    whenever a candidate's normalized display name was short -- e.g. an
    instrument literally named "IT" or "TECH" matched as a substring of
    unrelated companies like "Bagmane Prime Office REIT" or "TechNVision
    Ventures". A missed match (None) is always safer here than a wrong
    one, since a wrong join silently mislabels a company's cap class.
    """
    if not isinstance(company_name, str) or not company_name.strip():
        raise ValueError("company name must be a non-blank string")

    target_key = _normalize_company_key(company_name)
    if not target_key:
        return None

    exact_matches = [
        candidate
        for candidate in candidates
        if _normalize_company_key(candidate.display_name) == target_key
    ]

    if len(exact_matches) == 1:
        return exact_matches[0].symbol
    return None


def build_market_cap_match_report(
    market_cap_by_company: Mapping[str, MarketCapClass],
    candidates: Iterable[ResolvedInstrument],
) -> tuple[dict[str, MarketCapClass], MatchReport]:
    """Join AMFI's company-name-keyed cap classification onto NSE symbols.

    Returns the resolved {symbol: MarketCapClass} mapping plus a
    MatchReport recording any company name that failed to join, so gaps
    are visible rather than silently dropped.
    """
    resolved_candidates = tuple(candidates)
    by_symbol: dict[str, MarketCapClass] = {}
    unmatched: list[UnmatchedMarketCapEntry] = []
    matched_count = 0

    for company_name, market_cap_class in market_cap_by_company.items():
        symbol = match_company_name_to_symbol(
            company_name,
            resolved_candidates,
        )
        if symbol is None:
            unmatched.append(
                UnmatchedMarketCapEntry(
                    company_name=company_name,
                    market_cap_class=market_cap_class,
                )
            )
            continue
        matched_count += 1
        by_symbol[symbol] = market_cap_class

    return by_symbol, MatchReport(
        matched_count=matched_count,
        unmatched=tuple(unmatched),
    )
