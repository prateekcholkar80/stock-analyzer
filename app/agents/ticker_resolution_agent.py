from app.agents._debate_support import (
    build_system_prompt,
    build_user_message,
    generate_grounded,
)
from app.instruments.classification import ClassifiedInstrument
from app.llm.gateway import StructuredLLMGateway
from app.models.ticker_resolution import TickerResolutionChoice


_ROLE = (
    "You are Jarvis's ticker-resolution assistant. Your only job is to "
    "match an ambiguous or partial company/symbol reference the user "
    "typed to one candidate from a fixed shortlist of real NSE "
    "instruments, or to say the reference is ambiguous / not present."
)

_RULES = (
    "You must choose at most one symbol, and it must be copied exactly "
    "from the candidate list you are given -- never invent a symbol that "
    "is not on the list. If more than one candidate could plausibly be "
    "what the user meant and you cannot tell which, leave chosen_symbols "
    "empty and set is_ambiguous to true. If none of the candidates match "
    "the user's reference at all, leave chosen_symbols empty and set "
    "is_ambiguous to false. Your choice is only a proposal -- the user "
    "will always be asked to confirm it before it is used."
)


def _serialize_shortlist(shortlist: tuple[ClassifiedInstrument, ...]) -> str:
    lines = [
        (
            f"- symbol={item.instrument.symbol} "
            f"name={item.instrument.display_name} "
            f"sector={item.sector or 'unknown'} "
            f"industry={item.industry or 'unknown'} "
            f"market_cap_class={item.market_cap_class.value}"
        )
        for item in shortlist
    ]
    return "\n".join(lines)


def resolve_via_llm(
    *,
    gateway: StructuredLLMGateway,
    query: str,
    shortlist: tuple[ClassifiedInstrument, ...],
) -> TickerResolutionChoice:
    """Propose a resolution for `query` from `shortlist`, structurally
    incapable of returning a symbol outside it. Never calls the LLM for
    an empty shortlist -- an empty shortlist is an immediate "not found"
    outcome for the caller. The result is always a proposal: the caller
    must still obtain user confirmation before treating it as resolved.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("ticker resolution query must be a non-blank string")

    if not shortlist:
        return TickerResolutionChoice()

    valid_symbols = frozenset(item.instrument.symbol for item in shortlist)
    system = build_system_prompt(role=_ROLE, rules=_RULES)
    context = (
        f'User reference: "{query.strip()}"\n\n'
        f"Candidates:\n{_serialize_shortlist(shortlist)}"
    )

    generation = generate_grounded(
        gateway=gateway,
        system=system,
        context=context,
        draft_model=TickerResolutionChoice,
        valid_ids=valid_symbols,
        citation_field="chosen_symbols",
    )
    return generation.value
