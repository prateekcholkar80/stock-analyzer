import assert from "node:assert/strict";
import test from "node:test";

import {
  SETUP_STATE_META,
  confirmedStepCount,
  timeframeSetupSummary,
} from "../lib/setup-view.ts";

function setup(side, states) {
  return {
    setup_id: `${side}-setup`,
    side,
    steps: states.map((state, index) => ({
      step_id: `${side}-${index + 1}`,
      label: `Step ${index + 1}`,
      sequence: index + 1,
      state,
      evidence_ids: [],
      observed_at: null,
      available_at: null,
      confirmed_at: null,
      invalidated_at: null,
      explanation: `${state} setup step`,
      observed_values: {},
      thresholds: {},
    })),
  };
}

test("maps every setup state to a compact visual marker", () => {
  assert.deepEqual(Object.keys(SETUP_STATE_META).sort(), [
    "confirmed",
    "contradicted",
    "developing",
    "invalidated",
    "pending",
    "unavailable",
  ]);
  assert.equal(SETUP_STATE_META.confirmed.symbol, "✓");
  assert.equal(SETUP_STATE_META.contradicted.label, "Contradicted");
});

test("summarises bullish and bearish progression without changing evidence", () => {
  const bullish = setup("bullish", ["confirmed", "developing", "confirmed", "pending"]);
  const bearish = setup("bearish", ["confirmed", "contradicted", "pending"]);
  const interpretation = {
    timeframe: "daily",
    interval: "ONE_DAY",
    evaluated_at: "2026-08-24T15:30:00+05:30",
    market_condition: "conflicted",
    bullish_setup: bullish,
    bearish_setup: bearish,
    decisive_evidence_ids: [],
    rationale: "Mixed deterministic evidence.",
  };

  assert.equal(confirmedStepCount(bullish), 2);
  assert.equal(confirmedStepCount(bearish), 1);
  assert.equal(
    timeframeSetupSummary(interpretation),
    "2/4 bullish · 1/3 bearish checks confirmed",
  );
  assert.equal(bullish.steps[1].state, "developing");
});
