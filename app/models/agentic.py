from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import (
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.execution import HistoricalTradeExecution
from app.models.signals import (
    SignalCategory,
    SwingTradingSignalProfile,
)
from app.models.technical import TechnicalModel
from app.models.trade_setup import SwingTradePlan, TradeSetupStatus


class JarvisJudgeVerdict(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TechnicalSwingAgentSubmission(TechnicalModel):
    submission_id: str = Field(min_length=1)
    agent_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    evaluator_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    evaluated_at: datetime
    input_candle_count: int = Field(ge=1)
    profile: SwingTradingSignalProfile

    @field_validator(
        "submission_id",
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agent submission text cannot be blank")
        return value

    @field_validator("source_retrieved_at", "evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "agent submission timestamps must include timezone "
                "information"
            )
        return value

    @field_validator("input_candle_count", mode="before")
    @classmethod
    def require_integer_count(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                "agent submission candle count must be an integer"
            )
        return value

    @model_validator(mode="after")
    def validate_profile_identity(self) -> Self:
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "agent submission evaluation cannot follow source "
                "retrieval"
            )
        snapshot = self.profile.snapshot
        declared_identity = (
            self.exchange,
            self.symbol_token,
            self.symbol,
            self.interval,
            self.source,
            self.source_retrieved_at,
            self.evaluated_at,
        )
        profile_identity = (
            snapshot.exchange,
            snapshot.symbol_token,
            snapshot.symbol,
            snapshot.interval,
            snapshot.source,
            snapshot.source_retrieved_at,
            snapshot.evaluated_at,
        )
        if profile_identity != declared_identity:
            raise ValueError(
                "agent submission profile must match its declared input"
            )
        return self

    @computed_field
    @property
    def evidence_count(self) -> int:
        return len(self.profile.snapshot.evidence)

    @computed_field
    @property
    def covered_categories(self) -> list[SignalCategory]:
        return list(self.profile.covered_categories)


class JarvisJudgeDecision(TechnicalModel):
    decision_id: str = Field(min_length=1)
    judge_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    submission_id: str = Field(min_length=1)
    verdict: JarvisJudgeVerdict
    decided_at: datetime
    passed_checks: tuple[str, ...] = Field(min_length=1)
    reasons: tuple[str, ...] = ()

    @field_validator("decision_id", "submission_id")
    @classmethod
    def reject_blank_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("judge decision identifiers cannot be blank")
        return value

    @field_validator("decided_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "judge decision time must include timezone information"
            )
        return value

    @field_validator("passed_checks", "reasons")
    @classmethod
    def validate_messages(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("judge decision messages cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("judge decision messages must be unique")
        return values

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        if (
            self.verdict is JarvisJudgeVerdict.ACCEPTED
            and self.reasons
        ):
            raise ValueError(
                "accepted judge decisions cannot contain rejection "
                "reasons"
            )
        if (
            self.verdict is JarvisJudgeVerdict.REJECTED
            and not self.reasons
        ):
            raise ValueError(
                "rejected judge decisions require at least one reason"
            )
        return self

    @computed_field
    @property
    def accepted(self) -> bool:
        return self.verdict is JarvisJudgeVerdict.ACCEPTED


class AgenticSwingAnalysisResult(TechnicalModel):
    orchestrator_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    submission: TechnicalSwingAgentSubmission
    decision: JarvisJudgeDecision

    @model_validator(mode="after")
    def validate_review_chain(self) -> Self:
        if self.decision.submission_id != self.submission.submission_id:
            raise ValueError(
                "judge decision must reference the reviewed submission"
            )
        if self.decision.decided_at < self.submission.evaluated_at:
            raise ValueError(
                "judge decision cannot precede agent evaluation"
            )
        return self

    @computed_field
    @property
    def accepted_profile(self) -> SwingTradingSignalProfile | None:
        if not self.decision.accepted:
            return None
        return self.submission.profile


class TradePlanningDisposition(StrEnum):
    ACTIONABLE = "actionable"
    NO_TRADE = "no_trade"


class TradePlanningReason(StrEnum):
    DIRECTIONAL_SETUP = "directional_setup"
    NON_DIRECTIONAL_PROFILE = "non_directional_profile"
    MINIMUM_TARGET_BLOCKED = "minimum_target_blocked"
    INSUFFICIENT_STOP_EVIDENCE = "insufficient_stop_evidence"


class TradePlanningAgentSubmission(TechnicalModel):
    submission_id: str = Field(min_length=1)
    agent_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    planner_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    technical_submission_id: str = Field(min_length=1)
    technical_decision_id: str = Field(min_length=1)
    evaluated_at: datetime
    disposition: TradePlanningDisposition
    reason: TradePlanningReason
    profile: SwingTradingSignalProfile
    plan: SwingTradePlan | None = None
    rationale: str = Field(min_length=1)

    @field_validator(
        "submission_id",
        "technical_submission_id",
        "technical_decision_id",
        "rationale",
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "trade-planning submission text cannot be blank"
            )
        return value

    @field_validator("evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "trade-planning evaluation must include timezone "
                "information"
            )
        return value

    @model_validator(mode="after")
    def validate_planning_outcome(self) -> Self:
        if self.profile.snapshot.evaluated_at != self.evaluated_at:
            raise ValueError(
                "trade-planning profile must match evaluation time"
            )
        if self.plan is not None and (
            self.plan.evaluation.profile != self.profile
        ):
            raise ValueError(
                "trade plan must use the submitted technical profile"
            )

        if self.disposition is TradePlanningDisposition.ACTIONABLE:
            if self.plan is None:
                raise ValueError(
                    "actionable trade-planning submissions require a plan"
                )
            if self.reason is not TradePlanningReason.DIRECTIONAL_SETUP:
                raise ValueError(
                    "actionable trade plans require directional setup "
                    "reasoning"
                )
            if self.plan.evaluation.status not in (
                TradeSetupStatus.VALID,
                TradeSetupStatus.MARGINAL,
            ):
                raise ValueError(
                    "actionable trade plan must be valid or marginal"
                )
            return self

        if self.reason is TradePlanningReason.DIRECTIONAL_SETUP:
            raise ValueError(
                "no-trade submissions require a no-trade reason"
            )
        if self.reason is TradePlanningReason.NON_DIRECTIONAL_PROFILE:
            if self.profile.direction.value != "neutral":
                raise ValueError(
                    "non-directional no-trade outcome requires a neutral "
                    "profile"
                )
            if self.plan is not None:
                raise ValueError(
                    "non-directional no-trade outcome cannot contain a "
                    "plan"
                )
        elif self.reason is TradePlanningReason.MINIMUM_TARGET_BLOCKED:
            if (
                self.plan is None
                or self.plan.evaluation.status
                is not TradeSetupStatus.REJECTED
            ):
                raise ValueError(
                    "blocked-target no-trade outcome requires a rejected "
                    "plan"
                )
        elif self.plan is not None:
            raise ValueError(
                "insufficient-evidence no-trade outcome cannot contain a "
                "plan"
            )
        return self


class AgenticTradePlanningResult(TechnicalModel):
    orchestrator_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    submission: TradePlanningAgentSubmission
    decision: JarvisJudgeDecision

    @model_validator(mode="after")
    def validate_review_chain(self) -> Self:
        if self.decision.submission_id != self.submission.submission_id:
            raise ValueError(
                "trade-plan decision must reference its submission"
            )
        if self.decision.decided_at < self.submission.evaluated_at:
            raise ValueError(
                "trade-plan decision cannot precede planning evaluation"
            )
        return self

    @computed_field
    @property
    def approved_trade_intent(self) -> SwingTradePlan | None:
        if (
            not self.decision.accepted
            or self.submission.disposition
            is not TradePlanningDisposition.ACTIONABLE
        ):
            return None
        return self.submission.plan


class HistoricalExecutionAgentSubmission(TechnicalModel):
    submission_id: str = Field(min_length=1)
    agent_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    execution_engine_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    planning_submission_id: str = Field(min_length=1)
    planning_decision_id: str = Field(min_length=1)
    simulated_at: datetime
    execution: HistoricalTradeExecution

    @field_validator(
        "submission_id",
        "planning_submission_id",
        "planning_decision_id",
    )
    @classmethod
    def reject_blank_execution_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "historical execution identifiers cannot be blank"
            )
        return value

    @field_validator("simulated_at")
    @classmethod
    def require_execution_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "historical simulation time must include timezone "
                "information"
            )
        return value

    @model_validator(mode="after")
    def validate_execution_submission(self) -> Self:
        if self.execution.market_series.retrieved_at != self.simulated_at:
            raise ValueError(
                "historical simulation time must equal source retrieval"
            )
        if self.execution.planned_at > self.simulated_at:
            raise ValueError(
                "historical simulation cannot precede trade planning"
            )
        return self


class AgenticHistoricalExecutionResult(TechnicalModel):
    orchestrator_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    submission: HistoricalExecutionAgentSubmission
    decision: JarvisJudgeDecision

    @model_validator(mode="after")
    def validate_execution_review_chain(self) -> Self:
        if self.decision.submission_id != self.submission.submission_id:
            raise ValueError(
                "execution decision must reference its submission"
            )
        if self.decision.decided_at < self.submission.simulated_at:
            raise ValueError(
                "execution decision cannot precede simulation"
            )
        return self

    @computed_field
    @property
    def approved_execution(self) -> HistoricalTradeExecution | None:
        if not self.decision.accepted:
            return None
        return self.submission.execution
