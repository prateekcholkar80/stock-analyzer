import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class WakePhraseDetector(Protocol):
    def command_after_wake_phrase(self, text: str) -> str | None:
        """Return the trailing command, or None when text did not activate."""
        ...


class NormalizedWakePhraseDetector:
    """Detect an anchored wake phrase in typed text or a voice transcript."""

    def __init__(self, wake_phrase: str = "Hey Jarvis") -> None:
        if not isinstance(wake_phrase, str) or not wake_phrase.strip():
            raise ValueError("wake phrase must not be blank")
        words = wake_phrase.split()
        flexible_phrase = r"[\s,;:!?.\-]+".join(
            re.escape(word) for word in words
        )
        self._pattern = re.compile(
            rf"^\s*{flexible_phrase}(?=$|[\s,;:!?.\-])"
            rf"[\s,;:!?.\-]*",
            re.IGNORECASE,
        )

    def command_after_wake_phrase(self, text: str) -> str | None:
        if not isinstance(text, str):
            raise TypeError("wake phrase input must be text")
        match = self._pattern.match(text)
        if match is None:
            return None
        return text[match.end():].strip()

