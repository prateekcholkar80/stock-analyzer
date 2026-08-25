import assert from "node:assert/strict";
import test from "node:test";

import {
  MATRIX_STEPS,
  matchesBear,
  matchesBriefing,
  matchesBull,
  matchesDaily,
  matchesEvidence,
  matchesJudge,
  matchesMarket,
  matchesWeekly,
  workflowProgress,
} from "../lib/workflow-progress.ts";

function event(sequence, stage, state, activityId, timeframe = null, participantId = "research.service", round = null) {
  return {
    event_id: `operation-1:${sequence}`,
    operation_id: "operation-1",
    sequence,
    stage,
    state,
    occurred_at: "2026-08-23T20:00:00+05:30",
    message: `${stage} ${state}`,
    activity: {
      activity_id: activityId,
      participant_id: participantId,
      participant_label: "Test participant",
      participant_kind: "analyst",
      timeframe,
    },
    exchange: "NSE",
    symbol: "TCS-EQ",
    round_number: round,
  };
}

test("binds every operation-matrix cell to a backend workflow contract", () => {
  assert.deepEqual(
    MATRIX_STEPS.map((step) => step.key),
    ["market", "daily", "weekly", "evidence", "bull", "bear", "judge", "briefing"],
  );
  assert.equal(matchesMarket(event(1, "instrument_resolved", "completed", "instrument.resolve")), true);
  assert.equal(matchesDaily(event(2, "analysis", "started", "technical.daily.evaluate", "ONE_DAY")), true);
  assert.equal(matchesWeekly(event(3, "analysis", "started", "technical.weekly.evaluate", "ONE_WEEK")), true);
  assert.equal(matchesEvidence(event(4, "evidence_review", "started", "evidence.release.review")), true);
  assert.equal(matchesBull(event(5, "bull_debating", "started", "debate.bull.argue", null, "debate.bull", 1)), true);
  assert.equal(matchesBear(event(6, "bear_debating", "started", "debate.bear.argue", null, "debate.bear", 1)), true);
  assert.equal(matchesJudge(event(7, "judge_reviewing", "started", "debate.verdict.review")), true);
  assert.equal(matchesJudge(event(8, "trade_planning", "started", "trade.long_only.evaluate", "ONE_DAY")), true);
  assert.equal(matchesBriefing(event(9, "presentation", "started", "jarvis.ceo_briefing.present")), true);
  assert.equal(matchesBriefing(event(10, "completed", "completed", "workflow.complete")), true);
});

test("does not mistake the daily trade planner for the daily technical analyst", () => {
  const tradePlanner = event(1, "trade_planning", "started", "trade.long_only.evaluate", "ONE_DAY");
  assert.equal(matchesDaily(tradePlanner), false);
});

test("derives active, complete and failed states from the latest matching event", () => {
  const started = event(1, "bull_debating", "started", "debate.bull.argue", null, "debate.bull", 1);
  const completed = event(2, "bull_debating", "completed", "debate.bull.argue", null, "debate.bull", 1);
  const failed = event(3, "bull_debating", "failed", "debate.bull.argue", null, "debate.bull", 2);

  assert.equal(workflowProgress([started], matchesBull).status, "active");
  assert.equal(workflowProgress([started, completed], matchesBull).status, "complete");
  assert.equal(workflowProgress([started, completed, failed], matchesBull).status, "failed");
  assert.equal(workflowProgress([started, completed, failed], matchesBull).roundNumber, 2);
  assert.equal(workflowProgress([], matchesBull).status, "standby");
});
