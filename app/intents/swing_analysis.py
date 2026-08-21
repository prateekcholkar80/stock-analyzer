import re
from typing import Protocol, runtime_checkable

from app.exceptions import IntentRecognitionError
from app.models.interaction import SwingAnalysisIntent


@runtime_checkable
class SwingIntentInterpreter(Protocol):
    """Interpret text as a swing-analysis routing intent."""

    def interpret(self, text: str) -> SwingAnalysisIntent:
        ...


class PatternSwingIntentInterpreter:
    """Conservative local interpreter for explicit swing/company requests."""

    _PATTERNS = tuple(
        re.compile(pattern, flags=re.IGNORECASE)
        for pattern in (
            r"^how(?:\s+is|'s|\s+does)\s+(?P<instrument>.+?)\s+"
            r"(?:look|looking|doing|performing)(?:\s+today)?"
            r"(?:\s+for\s+(?:a\s+)?swing(?:\s+trade)?)?$",
            r"^(?:analy[sz]e|review|check)\s+(?P<instrument>.+?)"
            r"(?:\s+for\s+(?:a\s+)?swing(?:\s+trade)?)$",
            r"^(?:give\s+me\s+)?(?:a\s+)?swing(?:\s+trade)?\s+"
            r"analysis\s+(?:for|on)\s+(?P<instrument>.+)$",
        )
    )

    def __init__(
        self,
        *,
        default_exchange: str = "NSE",
        default_interval: str = "ONE_HOUR",
    ) -> None:
        self._default_exchange = _validated_default(
            default_exchange,
            "exchange",
        )
        self._default_interval = _validated_default(
            default_interval,
            "interval",
        )

    def interpret(self, text: str) -> SwingAnalysisIntent:
        if not isinstance(text, str) or not text.strip():
            raise IntentRecognitionError(
                "Jarvis requires a non-blank research request"
            )
        normalized = _normalize_request(text)
        for pattern in self._PATTERNS:
            match = pattern.fullmatch(normalized)
            if match is None:
                continue
            instrument_query = match.group("instrument").strip(" ,.-")
            if instrument_query and not _contains_multiple_requests(
                instrument_query
            ):
                return SwingAnalysisIntent(
                    original_text=text,
                    instrument_query=instrument_query,
                    exchange=self._default_exchange,
                    interval=self._default_interval,
                )
        raise IntentRecognitionError(
            "Jarvis could not identify one swing-analysis instrument"
        )


def _normalize_request(text: str) -> str:
    normalized = " ".join(text.strip().split()).rstrip(".!?")
    return re.sub(
        r"^(?:hey\s+)?jarvis\s*[,;:]?\s*",
        "",
        normalized,
        flags=re.IGNORECASE,
    )


def _contains_multiple_requests(instrument_query: str) -> bool:
    return bool(re.search(r"\s+(?:versus|vs\.?)\s+", instrument_query))


def _validated_default(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"default swing {field_name} must not be blank")
    return value.strip()
