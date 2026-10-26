import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../lib/operation-audit.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  exactFailedAgent,
  parseAgentExecutionOutcomes,
  parseFundamentalPackagePublication,
} = await import(moduleUrl);

const activity = (overrides = {}) => ({
  event_id: "operation-1:1",
  operation_id: "operation-1",
  sequence: 1,
  stage: "bull_debating",
  state: "failed",
  occurred_at: "2026-09-12T09:00:00+05:30",
  message: "The Bull agent could not complete its case.",
  activity: {
    activity_id: "debate.bull.argue",
    participant_id: "debate.bull",
    participant_label: "Bull",
    participant_kind: "advocate",
    timeframe: null,
  },
  exchange: "NSE",
  symbol: "COFORGE-EQ",
  round_number: 1,
  ...overrides,
});

test("identifies the exact failed agent and debate round", () => {
  assert.deepEqual(exactFailedAgent([activity()]), {
    participantId: "debate.bull",
    label: "Bull",
    activityId: "debate.bull.argue",
    stage: "bull_debating",
    roundNumber: 1,
  });
});

test("ignores terminal workflow failures that are not agent outcomes", () => {
  assert.equal(exactFailedAgent([activity({
    stage: "failed",
    activity: {
      activity_id: "workflow.fail",
      participant_id: "jarvis.orchestrator",
      participant_label: "Jarvis",
      participant_kind: "orchestrator",
      timeframe: null,
    },
    round_number: null,
  })]), null);
});

const outcome = (overrides = {}) => ({
  schema_version: "jarvis.agent_execution_outcome.v1",
  invocation_id: "debate.bull:round:1",
  operation_id: "operation-1",
  activity_id: "debate.bull.argue",
  participant_id: "debate.bull",
  participant_kind: "advocate",
  participant_label: "Bull",
  stage: "bull_debating",
  status: "failed",
  recorded_at: "2026-09-12T09:00:01+05:30",
  started_at: "2026-09-12T09:00:00+05:30",
  completed_at: "2026-09-12T09:00:01+05:30",
  round_number: 1,
  depends_on: ["judge.evidence_release"],
  failure: {
    code: "llm_authentication_failed",
    message: "The Bull agent could not complete its case.",
    retryable: false,
  },
  result_reference: null,
  ...overrides,
});

test("accepts the immutable backend agent outcome ledger", () => {
  const ledger = [outcome()];
  assert.deepEqual(parseAgentExecutionOutcomes(ledger), ledger);
});

test("rejects malformed or duplicate agent outcomes instead of inferring terminal state", () => {
  assert.throws(
    () => parseAgentExecutionOutcomes([outcome({ status: "started" })]),
    /invalid agent outcome ledger/,
  );
  assert.throws(
    () => parseAgentExecutionOutcomes([outcome(), outcome()]),
    /duplicate agent outcomes/,
  );
});

test("parses a committed package separately from failed agent outcomes", () => {
  const fingerprint = "a".repeat(64);
  const publication = {
    schema_version: "jarvis.browser_fundamental_package_publication.v1",
    status: "committed",
    reference: {
      schema_version: "jarvis.browser_fundamental_package_ref.v1",
      package_id: `fundamental_package:${fingerprint}`,
      manifest_fingerprint: fingerprint,
      expires_at: "2026-09-25T09:00:00+05:30",
    },
  };
  assert.deepEqual(parseFundamentalPackagePublication(publication), publication);
  assert.equal(parseFundamentalPackagePublication(null), null);
  assert.throws(
    () => parseFundamentalPackagePublication({
      ...publication,
      reference: { ...publication.reference, manifest_fingerprint: "b".repeat(64) },
    }),
    /invalid package publication/,
  );
  assert.equal(parseAgentExecutionOutcomes([outcome()])[0].status, "failed");
});
