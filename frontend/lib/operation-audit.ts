import type { WorkflowActivity } from "./workflow-progress";
import type { FundamentalPackageReference } from "./fundamental-analysis";

export type OperationDecisionFailure = {
  code: string;
  message: string;
  retryable: boolean;
};

export type AgentExecutionStatus =
  | "completed"
  | "failed"
  | "skipped_dependency"
  | "not_started";

export type AgentParticipantKind =
  | "analyst"
  | "advocate"
  | "judge"
  | "presenter";

export type AgentExecutionOutcome = {
  schema_version: "jarvis.agent_execution_outcome.v1";
  invocation_id: string;
  operation_id: string;
  activity_id: string;
  participant_id: string;
  participant_kind: AgentParticipantKind;
  participant_label: string;
  stage: string;
  status: AgentExecutionStatus;
  recorded_at: string;
  started_at: string | null;
  completed_at: string | null;
  round_number: number | null;
  depends_on: string[];
  failure: OperationDecisionFailure | null;
  result_reference: string | null;
};

export type FundamentalPackagePublication = {
  schema_version: "jarvis.browser_fundamental_package_publication.v1";
  status: "committed";
  reference: FundamentalPackageReference;
};

export type OperationAudit<TOutput> = {
  schema_version: "jarvis.browser_operation_audit.v1";
  operation: {
    request: { operation_id: string };
    status: "queued" | "running" | "cancellation_requested" | "completed" | "failed" | "cancelled";
    last_event_sequence: number;
    result_available: boolean;
    failure: OperationDecisionFailure | null;
  };
  events: WorkflowActivity[];
  agent_outcomes: AgentExecutionOutcome[];
  fundamental_package_publication: FundamentalPackagePublication | null;
  result: TOutput | null;
};

export type FailedAgent = {
  participantId: string;
  label: string;
  activityId: string;
  stage: string;
  roundNumber: number | null;
};

const AGENT_KINDS = new Set(["analyst", "advocate", "judge", "presenter"]);
const AGENT_EXECUTION_STATUSES = new Set<AgentExecutionStatus>([
  "completed",
  "failed",
  "skipped_dependency",
  "not_started",
]);
const PACKAGE_ID_PATTERN = /^fundamental_package:([a-f0-9]{64})$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isDecisionFailure(value: unknown): value is OperationDecisionFailure {
  return isRecord(value)
    && typeof value.code === "string"
    && typeof value.message === "string"
    && typeof value.retryable === "boolean";
}

function isAgentExecutionOutcome(value: unknown): value is AgentExecutionOutcome {
  if (!isRecord(value)) return false;
  const dependsOn = value.depends_on;
  const failure = value.failure;
  const roundNumber = value.round_number;
  return value.schema_version === "jarvis.agent_execution_outcome.v1"
    && typeof value.invocation_id === "string"
    && typeof value.operation_id === "string"
    && typeof value.activity_id === "string"
    && typeof value.participant_id === "string"
    && typeof value.participant_kind === "string"
    && AGENT_KINDS.has(value.participant_kind)
    && typeof value.participant_label === "string"
    && typeof value.stage === "string"
    && typeof value.status === "string"
    && AGENT_EXECUTION_STATUSES.has(value.status as AgentExecutionStatus)
    && typeof value.recorded_at === "string"
    && isNullableString(value.started_at)
    && isNullableString(value.completed_at)
    && (roundNumber === null
      || (typeof roundNumber === "number" && Number.isInteger(roundNumber) && roundNumber >= 1))
    && Array.isArray(dependsOn)
    && dependsOn.every((item) => typeof item === "string")
    && (failure === null || isDecisionFailure(failure))
    && isNullableString(value.result_reference);
}

/**
 * Validate the immutable backend ledger at the HTTP boundary. Terminal agent
 * state must never be reconstructed from generic workflow messages when this
 * authoritative contract is malformed.
 */
export function parseAgentExecutionOutcomes(value: unknown): AgentExecutionOutcome[] {
  if (!Array.isArray(value) || !value.every(isAgentExecutionOutcome)) {
    throw new Error("Operation audit contains an invalid agent outcome ledger.");
  }
  const invocationIds = value.map((outcome) => outcome.invocation_id);
  if (new Set(invocationIds).size !== invocationIds.length) {
    throw new Error("Operation audit contains duplicate agent outcomes.");
  }
  return value;
}

/** A package commit is an artifact outcome, never an inferred agent success. */
export function parseFundamentalPackagePublication(
  value: unknown,
): FundamentalPackagePublication | null {
  if (value === null) return null;
  if (!isRecord(value) || !isRecord(value.reference)) {
    throw new Error("Operation audit contains an invalid package publication.");
  }
  const reference = value.reference;
  const packageId = typeof reference.package_id === "string"
    ? PACKAGE_ID_PATTERN.exec(reference.package_id)
    : null;
  if (
    value.schema_version !== "jarvis.browser_fundamental_package_publication.v1"
    || value.status !== "committed"
    || reference.schema_version !== "jarvis.browser_fundamental_package_ref.v1"
    || packageId === null
    || reference.manifest_fingerprint !== packageId[1]
    || typeof reference.expires_at !== "string"
    || !Number.isFinite(Date.parse(reference.expires_at))
  ) {
    throw new Error("Operation audit contains an invalid package publication.");
  }
  return value as FundamentalPackagePublication;
}

export function exactFailedAgent(
  events: ReadonlyArray<WorkflowActivity>,
): FailedAgent | null {
  const failed = [...events].reverse().find((event) =>
    event.state === "failed"
    && AGENT_KINDS.has(event.activity.participant_kind));
  if (!failed) return null;
  return {
    participantId: failed.activity.participant_id,
    label: failed.activity.participant_label,
    activityId: failed.activity.activity_id,
    stage: failed.stage,
    roundNumber: failed.round_number,
  };
}
