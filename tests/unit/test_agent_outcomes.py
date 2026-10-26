import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.browser_operations import (
    BrowserAgentExecutionStatus,
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationRequest,
    BrowserOperationSnapshot,
    BrowserOperationStatus,
)
from app.models.conversation import InputChannel
from app.models.interaction import JarvisSwingAnalysisResponse
from app.models.llm import JarvisLLMFailureResponse, LLMFailureCode
from app.models.workflow import (
    WorkflowActivityDescriptor,
    WorkflowEventState,
    WorkflowParticipantKind,
    WorkflowStage,
)
from app.workflow.agent_outcomes import build_agent_execution_outcomes
from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter


IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2026, 9, 12, 10, 0, tzinfo=IST)
DAILY = WorkflowActivityDescriptor(
    activity_id="technical.daily.evaluate",
    participant_id="technical.daily_analyst",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Daily Technical Analyst",
    timeframe="ONE_DAY",
)
WEEKLY = WorkflowActivityDescriptor(
    activity_id="technical.weekly.evaluate",
    participant_id="technical.weekly_analyst",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Weekly Technical Analyst",
    timeframe="ONE_WEEK",
)


def _request(*, fundamentals=False):
    return BrowserOperationRequest(
        operation_id="operation-1",
        session_id="session-1",
        idempotency_key="request-1",
        kind=BrowserOperationKind.SWING_ANALYSIS,
        input_channel=InputChannel.TEXT,
        message="Analyze Coforge",
        fundamentals_requested=fundamentals,
        requested_at=T0,
    )


class AgentOutcomeProjectionTests(unittest.TestCase):
    def test_bull_failure_skips_only_its_dependent_agents(self):
        times = iter(T0 + timedelta(seconds=value) for value in range(1, 9))
        sink = InMemoryWorkflowEventSink()
        emitter = WorkflowEventEmitter(
            "operation-1",
            sink,
            clock=lambda: next(times),
        )
        for activity in (DAILY, WEEKLY):
            emitter.emit(
                WorkflowStage.ANALYSIS,
                WorkflowEventState.STARTED,
                activity=activity,
            )
            emitter.emit(
                WorkflowStage.ANALYSIS,
                WorkflowEventState.COMPLETED,
                activity=activity,
            )
        emitter.emit(
            WorkflowStage.EVIDENCE_REVIEW,
            WorkflowEventState.STARTED,
        )
        emitter.emit(
            WorkflowStage.EVIDENCE_REVIEW,
            WorkflowEventState.COMPLETED,
        )
        emitter.emit(
            WorkflowStage.BULL_DEBATING,
            WorkflowEventState.STARTED,
            round_number=1,
        )
        emitter.emit(
            WorkflowStage.BULL_DEBATING,
            WorkflowEventState.FAILED,
            round_number=1,
        )
        failure = BrowserOperationFailure(
            code="llm.authentication",
            message="The configured debate credential was rejected.",
            retryable=False,
        )
        response = JarvisSwingAnalysisResponse.llm_failure(
            operation_id="operation-1",
            failure=JarvisLLMFailureResponse(
                code=LLMFailureCode.AUTHENTICATION,
                title="The Bull agent could not complete its case",
                display_message=failure.message,
                spoken_message="The Bull agent could not complete its case.",
                recovery_action="Update the configured debate credential.",
                retryable=False,
                operation_id="operation-1",
            ),
        )
        result = BrowserOperationOutput(
            operation_id="operation-1",
            session_id="session-1",
            kind=BrowserOperationKind.SWING_ANALYSIS,
            completed_at=T0 + timedelta(seconds=9),
            research_response=response,
            decision_failure=failure,
        )
        operation = BrowserOperationSnapshot(
            request=_request(),
            status=BrowserOperationStatus.COMPLETED,
            updated_at=T0 + timedelta(seconds=9),
            last_event_sequence=len(sink.events),
            result_available=True,
        )

        outcomes = build_agent_execution_outcomes(
            operation,
            sink.events,
            result,
        )
        by_id = {outcome.invocation_id: outcome for outcome in outcomes}

        self.assertIs(
            by_id["technical.daily_analyst"].status,
            BrowserAgentExecutionStatus.COMPLETED,
        )
        self.assertIs(
            by_id["technical.weekly_analyst"].status,
            BrowserAgentExecutionStatus.COMPLETED,
        )
        self.assertIs(
            by_id["judge.evidence_release"].status,
            BrowserAgentExecutionStatus.COMPLETED,
        )
        self.assertIs(
            by_id["debate.bull:round:1"].status,
            BrowserAgentExecutionStatus.FAILED,
        )
        self.assertEqual(
            by_id["debate.bull:round:1"].failure,
            failure,
        )
        for invocation_id in (
            "debate.bear:round:1",
            "judge.debate",
            "research.trade_planner",
            "jarvis.presenter",
        ):
            with self.subTest(invocation_id=invocation_id):
                self.assertIs(
                    by_id[invocation_id].status,
                    BrowserAgentExecutionStatus.SKIPPED_DEPENDENCY,
                )

    def test_optional_fundamental_agent_is_planned_only_when_requested(self):
        operation = BrowserOperationSnapshot(
            request=_request(),
            status=BrowserOperationStatus.FAILED,
            updated_at=T0 + timedelta(seconds=1),
            failure=BrowserOperationFailure(
                code="market.unavailable",
                message="Historical market data could not be prepared.",
                retryable=True,
            ),
        )
        without_fundamentals = build_agent_execution_outcomes(
            operation,
            (),
            None,
        )
        with_fundamentals = build_agent_execution_outcomes(
            operation.model_copy(
                update={"request": _request(fundamentals=True)}
            ),
            (),
            None,
        )

        self.assertNotIn(
            "fundamentals.fundamental_analyst",
            {item.invocation_id for item in without_fundamentals},
        )
        fundamental = {
            item.invocation_id: item for item in with_fundamentals
        }["fundamentals.fundamental_analyst"]
        self.assertIs(
            fundamental.status,
            BrowserAgentExecutionStatus.NOT_STARTED,
        )


if __name__ == "__main__":
    unittest.main()
