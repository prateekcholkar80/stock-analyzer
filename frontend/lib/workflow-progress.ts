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

export const MATRIX_STEPS: ReadonlyArray<{
  key: string;
  label: string;
  matches: WorkflowMatcher;
}> = [
  { key: "market", label: "Market", matches: matchesMarket },
  { key: "daily", label: "Daily", matches: matchesDaily },
  { key: "weekly", label: "Weekly", matches: matchesWeekly },
  { key: "evidence", label: "Evidence", matches: matchesEvidence },
  { key: "bull", label: "Bull", matches: matchesBull },
  { key: "bear", label: "Bear", matches: matchesBear },
  { key: "judge", label: "Judge", matches: matchesJudge },
  { key: "briefing", label: "Briefing", matches: matchesBriefing },
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

export function timeframeLabel(value: string | null): string | null {
  if (value === "ONE_DAY") return "Daily";
  if (value === "ONE_WEEK") return "Weekly";
  if (value === "ONE_HOUR") return "Hourly";
  return value;
}
