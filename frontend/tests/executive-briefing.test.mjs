import assert from "node:assert/strict";
import test from "node:test";

import {
  beginnerTimeframeSummary,
  judgeDecisionView,
  readableBriefingPoints,
} from "../lib/executive-briefing.ts";

test("labels bearish confidence as confidence in a bearish no-trade verdict", () => {
  const view = judgeDecisionView("bearish", 62, "no_trade");

  assert.equal(view.tone, "bearish");
  assert.equal(view.verdictLabel, "Bearish");
  assert.equal(view.actionLabel, "No Trade");
  assert.equal(view.confidenceLabel, "62% confidence in bearish verdict");
  assert.match(view.confidenceMeaning, /not a forecast/i);
});

test("does not colour a bullish verdict as an approved buy when risk blocks it", () => {
  const blocked = judgeDecisionView("bullish", 71, "no_trade");
  const approved = judgeDecisionView("bullish", 71, "actionable");

  assert.equal(blocked.tone, "caution");
  assert.equal(blocked.actionLabel, "No Trade");
  assert.equal(approved.tone, "bullish");
  assert.equal(approved.actionLabel, "Buy Setup");
});

test("turns legacy markdown briefing headings into readable points", () => {
  const points = readableBriefingPoints(
    "Opening summary. **Weekly structure:** Bearish regime. "
    + "**Daily structure:** Oversold but bearish.",
  );

  assert.deepEqual(points, [
    "Opening summary.",
    "Weekly structure: Bearish regime.",
    "Daily structure: Oversold but bearish.",
  ]);
  assert.equal(points.some((item) => item.includes("**")), false);
});

test("explains weekly and daily roles in beginner language", () => {
  assert.match(beginnerTimeframeSummary("Weekly", "bearish",), /broader/);
  assert.match(beginnerTimeframeSummary("Daily", "neutral",), /entry timing/);
});
