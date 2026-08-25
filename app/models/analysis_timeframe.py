from enum import StrEnum


class SwingAnalysisTimeframe(StrEnum):
    """Stable daily/weekly identity shared by analysis contracts."""

    DAILY = "daily"
    WEEKLY = "weekly"


def timeframe_interval(timeframe: SwingAnalysisTimeframe) -> str:
    """Map a swing-analysis timeframe to its market-series interval."""
    if timeframe is SwingAnalysisTimeframe.DAILY:
        return "ONE_DAY"
    return "ONE_WEEK"
