from typing import Protocol, runtime_checkable

from app.models.debate import AgenticDebateResult, JudgeFollowUpAnswer
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview


@runtime_checkable
class JudgeFollowUpCoordinator(Protocol):
    def answer_multi_timeframe_follow_up(
        self,
        question: str,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> JudgeFollowUpAnswer:
        ...


class AskJarvisJudgeFollowUp:
    """Relay one conversational follow-up to the original debate Judge."""

    use_case_id = "jarvis.ask_judge_follow_up.v1"

    def __init__(self, coordinator: JudgeFollowUpCoordinator) -> None:
        if not isinstance(coordinator, JudgeFollowUpCoordinator):
            raise ValueError(
                "Jarvis follow-up requires a Judge follow-up coordinator"
            )
        self._coordinator = coordinator

    def execute(
        self,
        question: str,
        *,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> JudgeFollowUpAnswer:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Jarvis follow-up question cannot be blank")
        return self._coordinator.answer_multi_timeframe_follow_up(
            question.strip(),
            technical_review,
            debate_result,
        )
