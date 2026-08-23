from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.debate import AgenticDebateResult
from app.models.llm import JarvisLLMFailureResponse
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
from app.models.storage import (
    EndToEndSwingAnalysisResult,
    MultiTimeframeEndToEndSwingAnalysisResult,
)
from app.models.technical import TechnicalModel


class JarvisCommandStatus(StrEnum):
    """Stable command states consumed by voice and dashboard clients."""

    COMPLETED = "completed"
    LLM_FAILURE = "llm_failure"


class SwingAnalysisIntent(TechnicalModel):
    """Natural-language routing result; it contains no market conclusion."""

    model_config = ConfigDict(frozen=True, strict=True)

    original_text: str = Field(min_length=1, max_length=2_000)
    instrument_query: str = Field(min_length=1, max_length=200)
    exchange: str = Field(default="NSE", min_length=1, max_length=32)
    interval: str = Field(default="ONE_HOUR", min_length=1, max_length=50)

    @field_validator(
        "original_text",
        "instrument_query",
        "exchange",
        "interval",
    )
    @classmethod
    def normalize_intent_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("swing-analysis intent values must not be blank")
        return normalized


class SwingAnalysisCommand(TechnicalModel):
    """Resolved instrument command entering the Jarvis application layer."""

    model_config = ConfigDict(frozen=True, strict=True)

    exchange: str = Field(min_length=1, max_length=32)
    symbol_token: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=100)
    interval: str = Field(default="ONE_HOUR", min_length=1, max_length=50)
    to_date: datetime | None = None

    @field_validator("exchange", "symbol_token", "symbol", "interval")
    @classmethod
    def normalize_instrument_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("swing-analysis command values must not be blank")
        return normalized

    @field_validator("to_date")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("swing-analysis command time must include timezone")
        return value


class JarvisSwingAnalysisResponse(TechnicalModel):
    """Exclusive success-or-LLM-failure application response envelope."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.swing_analysis.v1"] = (
        "jarvis.swing_analysis.v1"
    )
    operation_id: str = Field(min_length=1)
    status: JarvisCommandStatus
    result: (
        EndToEndSwingAnalysisResult
        | MultiTimeframeEndToEndSwingAnalysisResult
        | None
    ) = None
    failure: JarvisLLMFailureResponse | None = None
    multi_timeframe_review: MultiTimeframeEvidenceReview | None = None
    multi_timeframe_debate: AgenticDebateResult | None = None

    @field_validator("operation_id")
    @classmethod
    def normalize_operation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Jarvis response operation ID must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_exclusive_payload(self) -> Self:
        has_review = self.multi_timeframe_review is not None
        has_debate = self.multi_timeframe_debate is not None
        if has_review != has_debate:
            raise ValueError(
                "Jarvis multi-timeframe conversation context requires both "
                "technical review and debate"
            )
        if has_review and has_debate:
            package = self.multi_timeframe_review.released_evidence
            debate = self.multi_timeframe_debate
            if package is None or not debate.decision.accepted:
                raise ValueError(
                    "Jarvis conversation can retain only approved "
                    "multi-timeframe context"
                )
            if (
                debate.submission.technical_submission_id
                != package.package_fingerprint
                or debate.submission.technical_decision_id
                != self.multi_timeframe_review.decision.decision_id
            ):
                raise ValueError(
                    "Jarvis conversation context must reference one "
                    "technical review chain"
                )
        if self.status is JarvisCommandStatus.COMPLETED:
            if self.result is None or self.failure is not None:
                raise ValueError(
                    "completed Jarvis response requires only a result"
                )
        elif (
            self.result is not None
            or self.failure is None
            or has_review
            or has_debate
        ):
            raise ValueError(
                "failed Jarvis response requires only an LLM failure"
            )
        if (
            self.failure is not None
            and self.failure.operation_id is not None
            and self.failure.operation_id != self.operation_id
        ):
            raise ValueError(
                "Jarvis response and failure operation IDs must match"
            )
        return self

    @classmethod
    def completed(
        cls,
        *,
        operation_id: str,
        result: (
            EndToEndSwingAnalysisResult
            | MultiTimeframeEndToEndSwingAnalysisResult
        ),
        multi_timeframe_review: MultiTimeframeEvidenceReview | None = None,
        multi_timeframe_debate: AgenticDebateResult | None = None,
    ) -> "JarvisSwingAnalysisResponse":
        return cls(
            operation_id=operation_id,
            status=JarvisCommandStatus.COMPLETED,
            result=result,
            multi_timeframe_review=multi_timeframe_review,
            multi_timeframe_debate=multi_timeframe_debate,
        )

    @classmethod
    def llm_failure(
        cls,
        *,
        operation_id: str,
        failure: JarvisLLMFailureResponse,
    ) -> "JarvisSwingAnalysisResponse":
        return cls(
            operation_id=operation_id,
            status=JarvisCommandStatus.LLM_FAILURE,
            failure=failure,
        )
