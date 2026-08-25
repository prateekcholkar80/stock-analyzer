import type { TechnicalChartData } from "./dashboard";

export const DEFAULT_CHART_OVERLAY_SELECTION = ["ema_20", "ema_50"] as const;

export const PRICE_ACTION_OVERLAY_SELECTION = [
  "volume",
  "cpr",
  "fvg",
  "pivots",
  "zones",
  "structure",
  "breaks",
  "accumulation",
  "liquidity_sweeps",
] as const;

export function accumulationOverlayCounts(chart: TechnicalChartData) {
  return {
    accumulation: chart.accumulation_zones.length,
    liquidity_sweeps: chart.liquidity_sweeps.length,
  };
}
