import type { RangeBreak } from "plotly.js";

import type { DashboardCandle, TimeframePanel } from "@/lib/dashboard";

const ONE_DAY_MS = 24 * 60 * 60 * 1000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function istDateKey(timestamp: string): string | null {
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).formatToParts(parsed);
  const values = new Map(parts.map((part) => [part.type, part.value]));
  const date = `${values.get("year")}-${values.get("month")}-${values.get("day")}`;
  return ISO_DATE.test(date) ? date : null;
}

function missingWeekdays(candles: DashboardCandle[]): string[] {
  const dates = candles
    .map((candle) => istDateKey(candle.timestamp))
    .filter((value): value is string => value !== null)
    .sort();
  if (dates.length < 2) return [];

  const present = new Set(dates);
  const first = new Date(`${dates[0]}T00:00:00Z`);
  const last = new Date(`${dates[dates.length - 1]}T00:00:00Z`);
  const missing: string[] = [];
  for (
    let cursor = first.getTime();
    cursor <= last.getTime();
    cursor += ONE_DAY_MS
  ) {
    const candidate = new Date(cursor);
    const weekday = candidate.getUTCDay();
    const date = candidate.toISOString().slice(0, 10);
    if (weekday !== 0 && weekday !== 6 && !present.has(date)) {
      missing.push(date);
    }
  }
  return missing;
}

export function marketRangeBreaks(
  timeframe: TimeframePanel["timeframe"],
  candles: DashboardCandle[],
): Array<Partial<RangeBreak>> {
  if (timeframe !== "daily") return [];
  const holidaysOrClosures = missingWeekdays(candles);
  return [
    { bounds: ["sat", "mon"], pattern: "day of week" },
    ...(holidaysOrClosures.length > 0
      ? [{ values: holidaysOrClosures, dvalue: ONE_DAY_MS }]
      : []),
  ];
}
