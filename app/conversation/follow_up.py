_FOLLOW_UP_TERMS = frozenset(
    {
        "support",
        "resistance",
        "pivot",
        "daily",
        "weekly",
        "evidence",
        "judge",
        "verdict",
        "conclusion",
        "explain",
        "why",
        "tell me more",
    }
)
_NEW_ANALYSIS_TERMS = (
    "analyze ",
    "analyse ",
    "analysis of ",
    "research ",
    "swing trade",
    "how is ",
    "how's ",
    "how does ",
    "look at ",
)


def looks_like_analysis_follow_up(command: str) -> bool:
    """Conservatively distinguish a grounded follow-up from new research."""

    normalized = " ".join(command.casefold().split())
    if any(term in normalized for term in _NEW_ANALYSIS_TERMS):
        return False
    return any(term in normalized for term in _FOLLOW_UP_TERMS)
