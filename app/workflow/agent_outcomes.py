from dataclasses import dataclass

from app.models.browser_operations import (
    BrowserAgentExecutionOutcome,
    BrowserAgentExecutionStatus,
    BrowserOperationFailure,
    BrowserOperationKind,
    BrowserOperationOutput,
    BrowserOperationSnapshot,
)
from app.models.workflow import (
    JarvisWorkflowEvent,
    WorkflowActivityDescriptor,
    WorkflowEventState,
    WorkflowParticipantKind,
    WorkflowStage,
)


_AGENT_KINDS = frozenset(
    {
        WorkflowParticipantKind.ANALYST,
        WorkflowParticipantKind.ADVOCATE,
        WorkflowParticipantKind.JUDGE,
        WorkflowParticipantKind.PRESENTER,
    }
)


@dataclass(frozen=True, slots=True)
class _AgentInvocationPlan:
    activity: WorkflowActivityDescriptor
    stage: WorkflowStage
    round_number: int | None = None
    depends_on: tuple[str, ...] = ()

    @property
    def invocation_id(self) -> str:
        if self.round_number is None:
            return self.activity.participant_id
        return f"{self.activity.participant_id}:round:{self.round_number}"


_DAILY = WorkflowActivityDescriptor(
    activity_id="technical.daily.evaluate",
    participant_id="technical.daily_analyst",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Daily Technical Analyst",
    timeframe="ONE_DAY",
)
_WEEKLY = WorkflowActivityDescriptor(
    activity_id="technical.weekly.evaluate",
    participant_id="technical.weekly_analyst",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Weekly Technical Analyst",
    timeframe="ONE_WEEK",
)
_FUNDAMENTAL = WorkflowActivityDescriptor(
    activity_id="fundamentals.company.evaluate",
    participant_id="fundamentals.fundamental_analyst",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Fundamental Analyst",
)
_EVIDENCE_JUDGE = WorkflowActivityDescriptor(
    activity_id="evidence.release.review",
    participant_id="judge.evidence_release",
    participant_kind=WorkflowParticipantKind.JUDGE,
    participant_label="Jarvis Judge",
)
_BULL = WorkflowActivityDescriptor(
    activity_id="debate.bull.argue",
    participant_id="debate.bull",
    participant_kind=WorkflowParticipantKind.ADVOCATE,
    participant_label="Bull",
)
_BEAR = WorkflowActivityDescriptor(
    activity_id="debate.bear.argue",
    participant_id="debate.bear",
    participant_kind=WorkflowParticipantKind.ADVOCATE,
    participant_label="Bear",
)
_DEBATE_JUDGE = WorkflowActivityDescriptor(
    activity_id="debate.verdict.review",
    participant_id="judge.debate",
    participant_kind=WorkflowParticipantKind.JUDGE,
    participant_label="Jarvis Judge",
)
_TRADE_PLANNER = WorkflowActivityDescriptor(
    activity_id="trade.long_only.evaluate",
    participant_id="research.trade_planner",
    participant_kind=WorkflowParticipantKind.ANALYST,
    participant_label="Trade Planning Analyst",
    timeframe="ONE_DAY",
)
_PRESENTER = WorkflowActivityDescriptor(
    activity_id="jarvis.ceo_briefing.present",
    participant_id="jarvis.presenter",
    participant_kind=WorkflowParticipantKind.PRESENTER,
    participant_label="Jarvis",
)
_FOLLOW_UP_JUDGE = WorkflowActivityDescriptor(
    activity_id="judge.follow_up.answer",
    participant_id="judge.debate",
    participant_kind=WorkflowParticipantKind.JUDGE,
    participant_label="Jarvis Judge",
)


def build_agent_execution_outcomes(
    operation: BrowserOperationSnapshot,
    events: tuple[JarvisWorkflowEvent, ...],
    result: BrowserOperationOutput | None,
) -> tuple[BrowserAgentExecutionOutcome, ...]:
    """Project immutable agent outcomes from one operation's event history."""

    operation_id = operation.request.operation_id
    if any(event.operation_id != operation_id for event in events):
        raise ValueError("agent outcome projection cannot mix operation events")
    if result is not None and result.operation_id != operation_id:
        raise ValueError("agent outcome projection result must match operation")

    plans = _invocation_plan(operation, events)
    plan_by_id = {plan.invocation_id: plan for plan in plans}
    grouped: dict[str, list[JarvisWorkflowEvent]] = {}
    first_sequence: dict[str, int] = {}
    for event in events:
        if event.activity.participant_kind not in _AGENT_KINDS:
            continue
        invocation_id = _invocation_id(event)
        grouped.setdefault(invocation_id, []).append(event)
        first_sequence.setdefault(invocation_id, event.sequence)

    outcomes: dict[str, BrowserAgentExecutionOutcome] = {}
    for invocation_id, invocation_events in grouped.items():
        started = tuple(
            event
            for event in invocation_events
            if event.state is WorkflowEventState.STARTED
        )
        terminal = tuple(
            event
            for event in invocation_events
            if event.state
            in {WorkflowEventState.COMPLETED, WorkflowEventState.FAILED}
        )
        if len(started) != 1 or len(terminal) != 1:
            continue
        start_event = started[0]
        terminal_event = terminal[0]
        status = (
            BrowserAgentExecutionStatus.COMPLETED
            if terminal_event.state is WorkflowEventState.COMPLETED
            else BrowserAgentExecutionStatus.FAILED
        )
        plan = plan_by_id.get(invocation_id)
        outcomes[invocation_id] = BrowserAgentExecutionOutcome(
            invocation_id=invocation_id,
            operation_id=operation_id,
            activity_id=terminal_event.activity.activity_id,
            participant_id=terminal_event.activity.participant_id,
            participant_kind=terminal_event.activity.participant_kind,
            participant_label=terminal_event.activity.participant_label,
            stage=terminal_event.stage,
            status=status,
            recorded_at=terminal_event.occurred_at,
            started_at=start_event.occurred_at,
            completed_at=terminal_event.occurred_at,
            round_number=terminal_event.round_number,
            depends_on=plan.depends_on if plan is not None else (),
            failure=(
                _safe_failure(
                    terminal_event,
                    result,
                    operation.failure,
                )
                if status is BrowserAgentExecutionStatus.FAILED
                else None
            ),
        )

    if operation.status.terminal:
        for plan in plans:
            if plan.invocation_id in outcomes:
                continue
            dependency_states = tuple(
                outcomes[dependency].status
                for dependency in plan.depends_on
                if dependency in outcomes
            )
            dependency_unavailable = len(dependency_states) != len(
                plan.depends_on
            ) or any(
                state is not BrowserAgentExecutionStatus.COMPLETED
                for state in dependency_states
            )
            status = (
                BrowserAgentExecutionStatus.SKIPPED_DEPENDENCY
                if plan.depends_on and dependency_unavailable
                else BrowserAgentExecutionStatus.NOT_STARTED
            )
            outcomes[plan.invocation_id] = BrowserAgentExecutionOutcome(
                invocation_id=plan.invocation_id,
                operation_id=operation_id,
                activity_id=plan.activity.activity_id,
                participant_id=plan.activity.participant_id,
                participant_kind=plan.activity.participant_kind,
                participant_label=plan.activity.participant_label,
                stage=plan.stage,
                status=status,
                recorded_at=operation.updated_at,
                completed_at=(
                    operation.updated_at
                    if status
                    is BrowserAgentExecutionStatus.SKIPPED_DEPENDENCY
                    else None
                ),
                round_number=plan.round_number,
                depends_on=plan.depends_on,
            )

    plan_order = {
        plan.invocation_id: index for index, plan in enumerate(plans)
    }
    return tuple(
        sorted(
            outcomes.values(),
            key=lambda outcome: (
                plan_order.get(outcome.invocation_id, len(plan_order)),
                first_sequence.get(outcome.invocation_id, 2**31),
                outcome.invocation_id,
            ),
        )
    )


def _invocation_plan(
    operation: BrowserOperationSnapshot,
    events: tuple[JarvisWorkflowEvent, ...],
) -> tuple[_AgentInvocationPlan, ...]:
    if operation.request.kind is BrowserOperationKind.JUDGE_FOLLOW_UP:
        return (
            _AgentInvocationPlan(
                activity=_FOLLOW_UP_JUDGE,
                stage=WorkflowStage.FOLLOW_UP,
            ),
        )

    plans = [
        _AgentInvocationPlan(activity=_DAILY, stage=WorkflowStage.ANALYSIS),
        _AgentInvocationPlan(activity=_WEEKLY, stage=WorkflowStage.ANALYSIS),
    ]
    if operation.request.fundamentals_requested:
        plans.append(
            _AgentInvocationPlan(
                activity=_FUNDAMENTAL,
                stage=WorkflowStage.ANALYSIS,
            )
        )
    plans.append(
        _AgentInvocationPlan(
            activity=_EVIDENCE_JUDGE,
            stage=WorkflowStage.EVIDENCE_REVIEW,
            depends_on=(
                _DAILY.participant_id,
                _WEEKLY.participant_id,
            ),
        )
    )
    rounds = sorted(
        {
            event.round_number
            for event in events
            if event.stage
            in {WorkflowStage.BULL_DEBATING, WorkflowStage.BEAR_DEBATING}
            and event.round_number is not None
        }
    ) or [1]
    previous_bear: str | None = None
    for round_number in rounds:
        bull_id = f"{_BULL.participant_id}:round:{round_number}"
        bear_id = f"{_BEAR.participant_id}:round:{round_number}"
        plans.append(
            _AgentInvocationPlan(
                activity=_BULL,
                stage=WorkflowStage.BULL_DEBATING,
                round_number=round_number,
                depends_on=(
                    previous_bear or _EVIDENCE_JUDGE.participant_id,
                ),
            )
        )
        plans.append(
            _AgentInvocationPlan(
                activity=_BEAR,
                stage=WorkflowStage.BEAR_DEBATING,
                round_number=round_number,
                depends_on=(bull_id,),
            )
        )
        previous_bear = bear_id
    final_bull = f"{_BULL.participant_id}:round:{rounds[-1]}"
    final_bear = f"{_BEAR.participant_id}:round:{rounds[-1]}"
    plans.extend(
        (
            _AgentInvocationPlan(
                activity=_DEBATE_JUDGE,
                stage=WorkflowStage.JUDGE_REVIEWING,
                depends_on=(final_bull, final_bear),
            ),
            _AgentInvocationPlan(
                activity=_TRADE_PLANNER,
                stage=WorkflowStage.TRADE_PLANNING,
                depends_on=(_DEBATE_JUDGE.participant_id,),
            ),
            _AgentInvocationPlan(
                activity=_PRESENTER,
                stage=WorkflowStage.PRESENTATION,
                depends_on=(_TRADE_PLANNER.participant_id,),
            ),
        )
    )
    return tuple(plans)


def _invocation_id(event: JarvisWorkflowEvent) -> str:
    if event.round_number is None:
        return event.activity.participant_id
    return f"{event.activity.participant_id}:round:{event.round_number}"


def _safe_failure(
    event: JarvisWorkflowEvent,
    result: BrowserOperationOutput | None,
    operation_failure: BrowserOperationFailure | None,
) -> BrowserOperationFailure:
    if result is not None:
        if (
            event.activity.participant_id
            == _FUNDAMENTAL.participant_id
            and result.fundamental_failure is not None
        ):
            return result.fundamental_failure
        if (
            event.stage is WorkflowStage.PRESENTATION
            and result.presentation_failure is not None
        ):
            return result.presentation_failure
        if (
            event.stage
            in {
                WorkflowStage.BULL_DEBATING,
                WorkflowStage.BEAR_DEBATING,
                WorkflowStage.JUDGE_REVIEWING,
            }
            and result.decision_failure is not None
        ):
            return result.decision_failure
    if operation_failure is not None:
        return operation_failure
    return BrowserOperationFailure(
        code="agent.execution_failed",
        message=event.message,
        retryable=False,
    )
