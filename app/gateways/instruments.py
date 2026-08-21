from typing import Protocol, runtime_checkable

from app.models.instruments import ResolvedInstrument


@runtime_checkable
class InstrumentResolver(Protocol):
    """Resolve a user-facing company identity to a market instrument."""

    def resolve(
        self,
        query: str,
        *,
        exchange: str | None = None,
    ) -> ResolvedInstrument:
        ...
