from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.agentic import JarvisJudgeDecision
from app.models.signals import SignalDirection
from app.models.technical import TechnicalModel


class DebateSide(StrEnum):
    BULL = "bull"
    BEAR = "bear"


class DebateTerminationReason(StrEnum):
    MAX_ROUNDS_REACHED = "max_rounds_reached"
    STALL_DETECTED = "stall_detected"
    AGENT_FAILURE = "agent_failure"


class BullBearArgument(TechnicalModel):
    argument_id: str = Field(min_length=1)
    side: DebateSide
    round_number: int = Field(ge=1)
    thesis: str = Field(min_length=1)
    evidence_citations: tuple[str, ...] = Field(min_length=1)
    rebuts_argument_id: str | None = None
    model_id: str = Field(min_length=1)
    generated_at: datetime

    @field_validator("argument_id", "thesis", "model_id")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("debate argument text cannot be blank")
        return value

    @field_validator("rebuts_argument_id")
    @classmethod
    def reject_blank_rebuttal_reference(
        cls,
        value: str | None,
    ) -> str | None:
        if value is not None and not value.strip():
            raise ValueError(
                "debate argument rebuttal reference cannot be blank"
            )
        return value

    @field_validator("evidence_citations")
    @classmethod
    def validate_citations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("debate evidence citations cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("debate evidence citations must be unique")
        return values

    @field_validator("round_number", mode="before")
    @classmethod
    def require_integer_round(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("debate round number must be an integer")
        return value

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "debate argument timestamp must include timezone "
                "information"
            )
        return value


class DebateRound(TechnicalModel):
    round_number: int = Field(ge=1)
    bull_argument: BullBearArgument
    bear_argument: BullBearArgument

    @field_validator("round_number", mode="before")
    @classmethod
    def require_integer_round(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("debate round number must be an integer")
        return value

    @model_validator(mode="after")
    def validate_round_arguments(self) -> Self:
        if self.bull_argument.side is not DebateSide.BULL:
            raise ValueError("debate round bull argument has wrong side")
        if self.bear_argument.side is not DebateSide.BEAR:
            raise ValueError("debate round bear argument has wrong side")
        if (
            self.bull_argument.round_number != self.round_number
            or self.bear_argument.round_number != self.round_number
        ):
            raise ValueError(
                "debate round arguments must match the round number"
            )
        return self


class DebateTranscript(TechnicalModel):
    rounds: tuple[DebateRound, ...] = Field(min_length=1)
    termination_reason: DebateTerminationReason

    @model_validator(mode="after")
    def validate_round_sequence(self) -> Self:
        expected_numbers = list(range(1, len(self.rounds) + 1))
        actual_numbers = [
            debate_round.round_number for debate_round in self.rounds
        ]
        if actual_numbers != expected_numbers:
            raise ValueError(
                "debate transcript rounds must be numbered sequentially "
                "starting at 1"
            )
        return self

    @computed_field
    @property
    def round_count(self) -> int:
        return len(self.rounds)


class DebateVerdict(TechnicalModel):
    verdict_id: str = Field(min_length=1)
    judge_model_id: str = Field(min_length=1)
    winner: SignalDirection
    confidence_percentage: float = Field(ge=0, le=100)
    decisive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    bull_case_summary: str = Field(min_length=1)
    bear_case_summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    generated_at: datetime

    @field_validator(
        "verdict_id",
        "judge_model_id",
        "bull_case_summary",
        "bear_case_summary",
        "rationale",
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("debate verdict text cannot be blank")
        return value

    @field_validator("decisive_evidence_ids")
    @classmethod
    def validate_decisive_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("debate verdict evidence ids cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("debate verdict evidence ids must be unique")
        return values

    @field_validator("confidence_percentage", mode="before")
    @classmethod
    def require_finite_confidence(cls, value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("debate verdict confidence must be finite")
        return float(value)

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "debate verdict timestamp must include timezone "
                "information"
            )
        return value


class BullBearDebateSubmission(TechnicalModel):
    submission_id: str = Field(min_length=1)
    agent_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    technical_submission_id: str = Field(min_length=1)
    technical_decision_id: str = Field(min_length=1)
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    transcript: DebateTranscript
    verdict: DebateVerdict
    evaluated_at: datetime

    @field_validator(
        "submission_id",
        "technical_submission_id",
        "technical_decision_id",
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("debate submission text cannot be blank")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "debate submission timestamp must include timezone "
                "information"
            )
        return value

    @model_validator(mode="after")
    def validate_debate_consistency(self) -> Self:
        seen_ids: set[str] = set()
        for debate_round in self.transcript.rounds:
            for argument in (
                debate_round.bull_argument,
                debate_round.bear_argument,
            ):
                if argument.argument_id in seen_ids:
                    raise ValueError(
                        "debate argument identifiers must be unique"
                    )
                if (
                    argument.rebuts_argument_id is not None
                    and argument.rebuts_argument_id not in seen_ids
                ):
                    raise ValueError(
                        "debate argument rebuttal reference must match an "
                        "earlier argument in the transcript"
                    )
                seen_ids.add(argument.argument_id)

        cited_evidence = {
            citation
            for debate_round in self.transcript.rounds
            for argument in (
                debate_round.bull_argument,
                debate_round.bear_argument,
            )
            for citation in argument.evidence_citations
        }
        if not set(self.verdict.decisive_evidence_ids).issubset(
            cited_evidence
        ):
            raise ValueError(
                "debate verdict decisive evidence must have been cited "
                "during the debate"
            )

        last_argument_at = self.transcript.rounds[-1].bear_argument.generated_at
        if self.verdict.generated_at < last_argument_at:
            raise ValueError(
                "debate verdict cannot precede the final debate argument"
            )

        return self


class AgenticDebateResult(TechnicalModel):
    orchestrator_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    submission: BullBearDebateSubmission
    decision: JarvisJudgeDecision

    @model_validator(mode="after")
    def validate_review_chain(self) -> Self:
        if self.decision.submission_id != self.submission.submission_id:
            raise ValueError(
                "judge decision must reference the reviewed submission"
            )
        if self.decision.decided_at < self.submission.evaluated_at:
            raise ValueError(
                "judge decision cannot precede debate evaluation"
            )
        return self

    @computed_field
    @property
    def approved_verdict(self) -> DebateVerdict | None:
        if not self.decision.accepted:
            return None
        return self.submission.verdict
