import type { AgentExecutionOutcome } from "./operation-audit";

export type WorkflowEventState = "started" | "completed" | "failed";

export type WorkflowActivity = {
  event_id: string;
  operation_id: string;
  sequence: number;
  stage: string;
  state: WorkflowEventState;
  occurred_at: string;
  message: string;
  activity: {
    activity_id: string;
    participant_id: string;
    participant_label: string;
    participant_kind: string;
    timeframe: string | null;
  };
  exchange: string | null;
  symbol: string | null;
  round_number: number | null;
};

export type ProgressStatus = "standby" | "active" | "complete" | "failed";

export type WorkflowProgress = {
  status: ProgressStatus;
  latest: WorkflowActivity | null;
  eventCount: number;
  roundNumber: number | null;
};

export type WorkflowMatcher = (item: WorkflowActivity) => boolean;
export type AgentOutcomeMatcher = (item: AgentExecutionOutcome) => boolean;

export type AgentOutcomeProgress = {
  status: ProgressStatus;
  statusLabel: string;
  outcome: AgentExecutionOutcome;
  outcomeCount: number;
  roundNumber: number | null;
};

const DAILY_ACTIVITY_IDS = new Set([
  "market.aggregate.daily",
  "technical.daily.evaluate",
]);
const WEEKLY_ACTIVITY_IDS = new Set([
  "market.aggregate.weekly",
  "technical.weekly.evaluate",
]);

export const matchesMarket: WorkflowMatcher = (item) =>
  ["instrument_resolved", "market_data_loading"].includes(item.stage);

export const matchesDaily: WorkflowMatcher = (item) =>
  item.activity.timeframe === "ONE_DAY"
  && DAILY_ACTIVITY_IDS.has(item.activity.activity_id);

export const matchesWeekly: WorkflowMatcher = (item) =>
  item.activity.timeframe === "ONE_WEEK"
  && WEEKLY_ACTIVITY_IDS.has(item.activity.activity_id);

export const matchesFundamental: WorkflowMatcher = (item) =>
  item.stage === "analysis"
  && item.activity.participant_id === "fundamentals.fundamental_analyst";

export const matchesEvidence: WorkflowMatcher = (item) =>
  item.stage === "evidence_review";

export const matchesBull: WorkflowMatcher = (item) =>
  item.stage === "bull_debating"
  && item.activity.participant_id === "debate.bull";

export const matchesBear: WorkflowMatcher = (item) =>
  item.stage === "bear_debating"
  && item.activity.participant_id === "debate.bear";

export const matchesJudge: WorkflowMatcher = (item) =>
  ["judge_reviewing", "trade_planning"].includes(item.stage);

export const matchesBriefing: WorkflowMatcher = (item) =>
  ["presentation", "completed"].includes(item.stage);

export const matchesDailyOutcome: AgentOutcomeMatcher = (item) =>
  item.participant_id === "technical.daily_analyst";

export const matchesWeeklyOutcome: AgentOutcomeMatcher = (item) =>
  item.participant_id === "technical.weekly_analyst";

export const matchesFundamentalOutcome: AgentOutcomeMatcher = (item) =>
  item.participant_id === "fundamentals.fundamental_analyst";

export const matchesEvidenceOutcome: AgentOutcomeMatcher = (item) =>
  item.participant_id === "judge.evidence_release";

export const matchesBullOutcome: AgentOutcomeMatcher = (item) =>
  item.participant_id === "debate.bull";

export const matchesBearOutcome: AgentOutcomeMatcher = (item) =>
  item.participant_id === "debate.bear";

export const matchesJudgeOutcome: AgentOutcomeMatcher = (item) =>
  ["judge.debate", "research.trade_planner"].includes(item.participant_id);

export const matchesBriefingOutcome: AgentOutcomeMatcher = (item) =>
  item.participant_id === "jarvis.presenter";

export const MATRIX_STEPS: ReadonlyArray<{
  key: string;
  label: string;
  matches: WorkflowMatcher;
  matchesOutcome: AgentOutcomeMatcher | null;
}> = [
  { key: "market", label: "Market", matches: matchesMarket, matchesOutcome: null },
  { key: "daily", label: "Daily", matches: matchesDaily, matchesOutcome: matchesDailyOutcome },
  { key: "weekly", label: "Weekly", matches: matchesWeekly, matchesOutcome: matchesWeeklyOutcome },
  { key: "evidence", label: "Evidence", matches: matchesEvidence, matchesOutcome: matchesEvidenceOutcome },
  { key: "bull", label: "Bull", matches: matchesBull, matchesOutcome: matchesBullOutcome },
  { key: "bear", label: "Bear", matches: matchesBear, matchesOutcome: matchesBearOutcome },
  { key: "judge", label: "Judge", matches: matchesJudge, matchesOutcome: matchesJudgeOutcome },
  { key: "briefing", label: "Briefing", matches: matchesBriefing, matchesOutcome: matchesBriefingOutcome },
];

export function workflowProgress(
  activities: ReadonlyArray<WorkflowActivity>,
  matches: WorkflowMatcher,
): WorkflowProgress {
  const matched = activities.filter(matches);
  const latest = matched.at(-1) ?? null;
  const status: ProgressStatus = latest?.state === "started"
    ? "active"
    : latest?.state === "completed"
      ? "complete"
      : latest?.state === "failed"
        ? "failed"
        : "standby";
  return {
    status,
    latest,
    eventCount: matched.length,
    roundNumber: latest?.round_number ?? null,
  };
}

/** Return terminal display state from the immutable operation outcome ledger. */
export function agentOutcomeProgress(
  outcomes: ReadonlyArray<AgentExecutionOutcome>,
  matches: AgentOutcomeMatcher,
): AgentOutcomeProgress | null {
  const matched = outcomes.filter(matches);
  const outcome = matched.at(-1);
  if (!outcome) return null;
  const display = {
    completed: { status: "complete", statusLabel: "Complete" },
    failed: { status: "failed", statusLabel: "Failed" },
    skipped_dependency: {
      status: "standby",
      statusLabel: "Skipped — dependency",
    },
    not_started: { status: "standby", statusLabel: "Not started" },
  } as const;
  return {
    ...display[outcome.status],
    outcome,
    outcomeCount: matched.length,
    roundNumber: outcome.round_number,
  };
}

export function timeframeLabel(value: string | null): string | null {
  if (value === "ONE_DAY") return "Daily";
  if (value === "ONE_WEEK") return "Weekly";
  if (value === "ONE_HOUR") return "Hourly";
  return value;
}
