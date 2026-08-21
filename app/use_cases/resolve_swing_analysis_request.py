from datetime import datetime

from app.gateways.instruments import InstrumentResolver
from app.intents.swing_analysis import SwingIntentInterpreter
from app.models.instruments import ResolvedInstrument
from app.models.interaction import SwingAnalysisCommand, SwingAnalysisIntent


class ResolveSwingAnalysisRequest:
    """Convert one natural-language request into an executable command."""

    def __init__(
        self,
        interpreter: SwingIntentInterpreter,
        instrument_resolver: InstrumentResolver,
    ) -> None:
        if not isinstance(interpreter, SwingIntentInterpreter):
            raise ValueError(
                "swing request resolver requires an intent interpreter"
            )
        if not isinstance(instrument_resolver, InstrumentResolver):
            raise ValueError(
                "swing request resolver requires an instrument resolver"
            )
        self._interpreter = interpreter
        self._instrument_resolver = instrument_resolver

    def execute(
        self,
        text: str,
        *,
        to_date: datetime | None = None,
    ) -> SwingAnalysisCommand:
        intent = self._interpreter.interpret(text)
        if not isinstance(intent, SwingAnalysisIntent):
            raise ValueError("intent interpreter returned an invalid result")
        instrument = self._instrument_resolver.resolve(
            intent.instrument_query,
            exchange=intent.exchange,
        )
        if not isinstance(instrument, ResolvedInstrument):
            raise ValueError("instrument resolver returned an invalid result")
        if instrument.exchange.casefold() != intent.exchange.casefold():
            raise ValueError(
                "resolved instrument exchange does not match the intent"
            )
        return SwingAnalysisCommand(
            exchange=instrument.exchange,
            symbol_token=instrument.symbol_token,
            symbol=instrument.symbol,
            interval=intent.interval,
            to_date=to_date,
        )
