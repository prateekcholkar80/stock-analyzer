import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_CHART_OVERLAY_SELECTION,
  PRICE_ACTION_OVERLAY_SELECTION,
  accumulationOverlayCounts,
} from "../lib/chart-overlays.ts";

test("keeps accumulation evidence hidden in the clean decision view", () => {
  assert.deepEqual([...DEFAULT_CHART_OVERLAY_SELECTION], ["ema_20", "ema_50"]);
  assert.equal(DEFAULT_CHART_OVERLAY_SELECTION.includes("accumulation"), false);
  assert.equal(DEFAULT_CHART_OVERLAY_SELECTION.includes("liquidity_sweeps"), false);
});

test("makes accumulation and liquidity sweeps selectable price-action overlays", () => {
  assert.equal(PRICE_ACTION_OVERLAY_SELECTION.includes("accumulation"), true);
  assert.equal(PRICE_ACTION_OVERLAY_SELECTION.includes("liquidity_sweeps"), true);

  const counts = accumulationOverlayCounts({
    accumulation_zones: [{ zone_id: "daily:accumulation:one" }],
    liquidity_sweeps: [
      { sweep_id: "daily:liquidity_sweep:one" },
      { sweep_id: "daily:liquidity_sweep:two" },
    ],
  });
  assert.deepEqual(counts, { accumulation: 1, liquidity_sweeps: 2 });
});
