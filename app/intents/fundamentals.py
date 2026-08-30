"""Deterministic intent policy for explicit fundamental-data refreshes."""

import re
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.technical import TechnicalModel


_MAX_REQUEST_LENGTH = 2_000
_REFRESH_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\bi\s+need\s+(?:a\s+)?refresh\s+(?:on|of)\s+"
        r"(?:the\s+)?fundamentals\b",
        r"\b(?:please\s+)?refresh\s+(?:the\s+)?fundamentals"
        r"(?:\s+data)?\b",
        r"\b(?:please\s+)?update\s+(?:the\s+)?"
        r"(?:fundamentals|fundamental\s+data)\b",
        r"\b(?:please\s+)?re[-\s]?pull\s+(?:the\s+|company\s+)?"
        r"fundamentals\b",
    )
)
_FUNDAMENTAL_TERM = re.compile(
    r"\b(?:fundamentals?|financial\s+statements?|financials?|"
    r"shareholding(?:\s+history)?)\b",
    flags=re.IGNORECASE,
)
_FUNDAMENTAL_MODIFIER_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\s+(?:with|using)\s+(?:the\s+|company\s+)?"
        r"(?:fundamentals?|financial\s+statements?|financials?|"
        r"shareholding(?:\s+history)?)\b",
        r"\s+and\s+(?:also\s+)?(?:include|use|check|review)\s+"
        r"(?:the\s+|company\s+)?(?:fundamentals?|"
        r"financial\s+statements?|financials?|"
        r"shareholding(?:\s+history)?)\b",
    )
)
_NEGATION_BEFORE_DIRECTIVE = re.compile(
    r"(?:do\s+not|don't|dont|no\s+need\s+to|without)\s+$",
    flags=re.IGNORECASE,
)


class FundamentalRefreshDirective(TechnicalModel):
    """Fundamental routing plus text safe for downstream swing intent parsing."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.fundamental_refresh_directive.v2"] = (
        "jarvis.fundamental_refresh_directive.v2"
    )
    fundamentals_requested: bool
    refresh_requested: bool
    research_text: str | None = Field(default=None, max_length=_MAX_REQUEST_LENGTH)

    @field_validator("research_text")
    @classmethod
    def normalize_research_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split()).strip(" ,;.-")
        return normalized or None

    @model_validator(mode="after")
    def require_remaining_text_without_refresh(self) -> Self:
        if self.refresh_requested and not self.fundamentals_requested:
            raise ValueError("fundamental refresh requires fundamental research")
        if not self.refresh_requested and self.research_text is None:
            raise ValueError("non-refresh input must retain research text")
        return self


class FundamentalRefreshIntentInterpreter:
    """Recognize only explicit positive refresh instructions without an LLM."""

    def interpret(self, text: str) -> FundamentalRefreshDirective:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("fundamental refresh intent requires non-blank text")
        normalized = " ".join(text.strip().split())
        if len(normalized) > _MAX_REQUEST_LENGTH:
            raise ValueError("fundamental refresh intent exceeds maximum length")

        accepted_spans: list[tuple[int, int]] = []
        for pattern in _REFRESH_PATTERNS:
            for match in pattern.finditer(normalized):
                prefix = normalized[max(0, match.start() - 32) : match.start()]
                if _NEGATION_BEFORE_DIRECTIVE.search(prefix):
                    continue
                if any(
                    match.start() < end and start < match.end()
                    for start, end in accepted_spans
                ):
                    continue
                accepted_spans.append(match.span())

        fundamentals_requested = bool(accepted_spans) or bool(
            _FUNDAMENTAL_TERM.search(normalized)
        )
        if not accepted_spans:
            research_text = normalized
            if fundamentals_requested:
                research_text = _remove_fundamental_modifiers(research_text)
            return FundamentalRefreshDirective(
                fundamentals_requested=fundamentals_requested,
                refresh_requested=False,
                research_text=research_text,
            )

        remaining = normalized
        for start, end in sorted(accepted_spans, reverse=True):
            remaining = f"{remaining[:start]} {remaining[end:]}"
        remaining = _clean_remaining_request(remaining)
        return FundamentalRefreshDirective(
            fundamentals_requested=True,
            refresh_requested=True,
            research_text=remaining or None,
        )


def _clean_remaining_request(value: str) -> str:
    normalized = " ".join(value.split()).strip(" ,;.-")
    normalized = re.sub(
        r"^(?:and|then)\b[\s,;:-]*",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"[\s,;:-]*\b(?:and|then)$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    return " ".join(normalized.split()).strip(" ,;.-")


def _remove_fundamental_modifiers(value: str) -> str:
    remaining = value
    for pattern in _FUNDAMENTAL_MODIFIER_PATTERNS:
        remaining = pattern.sub(" ", remaining)
    return _clean_remaining_request(remaining)
