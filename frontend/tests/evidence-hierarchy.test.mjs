import assert from "node:assert/strict";
import test from "node:test";

import {
  CORRELATION_GROUP_LABELS,
  EVIDENCE_FAMILY_LABELS,
  HIERARCHY_STATE_META,
  hierarchyReleaseLabel,
} from "../lib/evidence-hierarchy.ts";

test("labels every deterministic evidence family and correlation group", () => {
  assert.equal(Object.keys(EVIDENCE_FAMILY_LABELS).length, 11);
  assert.equal(EVIDENCE_FAMILY_LABELS.market_structure, "Market structure");
  assert.equal(EVIDENCE_FAMILY_LABELS.risk_target_feasibility, "1:2 target space");
  assert.equal(Object.keys(CORRELATION_GROUP_LABELS).length, 7);
  assert.equal(
    CORRELATION_GROUP_LABELS.ohlc_derived_confirmation,
    "Technical confirmation",
  );
});

test("maps authoritative hierarchy states to stable presentation tones", () => {
  assert.deepEqual(HIERARCHY_STATE_META.bullish, {
    label: "Bullish",
    tone: "positive",
  });
  assert.equal(HIERARCHY_STATE_META.fail.tone, "negative");
  assert.equal(HIERARCHY_STATE_META.not_applicable.label, "Outside scope");
  assert.equal(Object.keys(HIERARCHY_STATE_META).length, 8);
});

test("summarises backend release eligibility without recalculating it", () => {
  const assessment = {
    release_eligible: false,
    blocking_families: ["entry_trigger", "risk_target_feasibility"],
  };
  assert.equal(hierarchyReleaseLabel(assessment), "2 release gates blocked");
  assert.equal(
    hierarchyReleaseLabel({ ...assessment, blocking_families: ["entry_trigger"] }),
    "1 release gate blocked",
  );
  assert.equal(
    hierarchyReleaseLabel({ ...assessment, release_eligible: true }),
    "Release qualified",
  );
});
