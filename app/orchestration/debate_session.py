from datetime import UTC, datetime
from enum import StrEnum

from app.models.debate import (
    DebateRound,
    DebateTerminationReason,
    DebateTranscript,
    DebateVerdict,
)


class DebateSessionState(StrEnum):
    IN_PROGRESS = "in_progress"
    STALLED = "stalled"
    MAX_ROUNDS_REACHED = "max_rounds_reached"
    AGENT_FAILURE = "agent_failure"
    JUDGED = "judged"


_TERMINAL_STATES = frozenset(
    {
        DebateSessionState.STALLED,
        DebateSessionState.MAX_ROUNDS_REACHED,
        DebateSessionState.AGENT_FAILURE,
    }
)

_TERMINATION_REASON_BY_STATE = {
    DebateSessionState.STALLED: DebateTerminationReason.STALL_DETECTED,
    DebateSessionState.MAX_ROUNDS_REACHED: (
        DebateTerminationReason.MAX_ROUNDS_REACHED
    ),
    DebateSessionState.AGENT_FAILURE: DebateTerminationReason.AGENT_FAILURE,
}


class DebateSession:
    """Working memory for one in-progress debate.

    Mutable and process-local by design -- unlike the rest of this
    codebase's frozen domain models, this tracks a debate as it unfolds
    round by round. Nothing here is persisted; it exists only for the
    lifetime of one DebateOrchestrator.run_debate() call. States:

        IN_PROGRESS -> STALLED         -> JUDGED
                    -> MAX_ROUNDS_REACHED -> JUDGED
                    -> AGENT_FAILURE      -> JUDGED
    """

    def __init__(self) -> None:
        self.state = DebateSessionState.IN_PROGRESS
        self.rounds: list[DebateRound] = []
        self.verdict: DebateVerdict | None = None
        self.history: list[tuple[DebateSessionState, datetime]] = [
            (DebateSessionState.IN_PROGRESS, datetime.now(UTC)),
        ]

    def record_round(self, round_: DebateRound) -> None:
        if self.state is not DebateSessionState.IN_PROGRESS:
            raise ValueError(
                "cannot record a round while session state is "
                f"{self.state.value}"
            )
        self.rounds.append(round_)

    def mark_stalled(self) -> None:
        self._transition(DebateSessionState.STALLED)

    def mark_max_rounds_reached(self) -> None:
        self._transition(DebateSessionState.MAX_ROUNDS_REACHED)

    def mark_agent_failure(self) -> None:
        self._transition(DebateSessionState.AGENT_FAILURE)

    def attach_verdict(self, verdict: DebateVerdict) -> None:
        if self.state not in _TERMINAL_STATES:
            raise ValueError(
                "cannot attach a verdict before the debate has reached a "
                "terminal state"
            )
        self.verdict = verdict
        self.state = DebateSessionState.JUDGED
        self.history.append((DebateSessionState.JUDGED, datetime.now(UTC)))

    def to_transcript(self) -> DebateTranscript:
        termination_state = next(
            (
                state
                for state, _ in self.history
                if state in _TERMINAL_STATES
            ),
            None,
        )
        if termination_state is None:
            raise ValueError(
                "cannot build a transcript before the debate has reached "
                "a terminal state"
            )
        if not self.rounds:
            raise ValueError(
                "cannot build a transcript with zero completed rounds"
            )
        return DebateTranscript(
            rounds=tuple(self.rounds),
            termination_reason=(
                _TERMINATION_REASON_BY_STATE[termination_state]
            ),
        )

    def _transition(self, new_state: DebateSessionState) -> None:
        if self.state is not DebateSessionState.IN_PROGRESS:
            raise ValueError(
                f"cannot transition from {self.state.value} to "
                f"{new_state.value}"
            )
        self.state = new_state
        self.history.append((new_state, datetime.now(UTC)))
