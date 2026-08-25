import assert from "node:assert/strict";
import test from "node:test";

import { marketRangeBreaks } from "../lib/chart-axis.ts";

function candle(date) {
  return {
    timestamp: `${date}T15:15:00+05:30`,
    open: 100,
    high: 102,
    low: 99,
    close: 101,
    volume: 1_000,
  };
}

test("compresses weekends and absent weekdays on daily trading charts", () => {
  const breaks = marketRangeBreaks("daily", [
    candle("2026-08-14"),
    candle("2026-08-17"),
    candle("2026-08-18"),
    candle("2026-08-20"),
  ]);

  assert.deepEqual(breaks[0].bounds, ["sat", "mon"]);
  assert.deepEqual(breaks[1].values, ["2026-08-19"]);
  assert.equal(breaks[1].values.includes("2026-08-15"), false);
  assert.equal(breaks[1].values.includes("2026-08-16"), false);
});

test("does not apply daily-market breaks to weekly candles", () => {
  assert.deepEqual(
    marketRangeBreaks("weekly", [
      candle("2026-08-14"),
      candle("2026-08-21"),
    ]),
    [],
  );
});
