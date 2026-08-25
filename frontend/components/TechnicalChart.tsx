"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Annotations, Config, Data, Layout, Shape } from "plotly.js";

import type {
  DashboardQuote,
  TechnicalChartData,
  TimeframePanel,
} from "@/lib/dashboard";
import {
  DEFAULT_CHART_OVERLAY_SELECTION,
  PRICE_ACTION_OVERLAY_SELECTION,
  accumulationOverlayCounts,
} from "@/lib/chart-overlays";
import { marketRangeBreaks } from "@/lib/chart-axis";

type OverlayOption = { id: string; label: string; count: number };

type RuntimeTimeframePanel = Omit<TimeframePanel, "chart"> & {
  chart?: Partial<TechnicalChartData>;
};

const EMPTY_CHART: TechnicalChartData = {
  indicators: [],
  pattern_catalog: [],
  candlestick_patterns: [],
  central_pivot_ranges: [],
  fair_value_gaps: [],
  zones: [],
  pivots: [],
  structure_points: [],
  structure_breaks: [],
  accumulation_zones: [],
  liquidity_sweeps: [],
};

function normalizeChartPanel(source: TimeframePanel): {
  panel: TimeframePanel;
  projectionComplete: boolean;
} {
  const runtimeChart = (source as RuntimeTimeframePanel).chart;
  const projectionComplete = Boolean(
    runtimeChart
      && Object.keys(EMPTY_CHART).every((key) =>
        Array.isArray(runtimeChart[key as keyof TechnicalChartData]),
      ),
  );

  return {
    panel: {
      ...source,
      chart: {
        ...EMPTY_CHART,
        ...runtimeChart,
        indicators: runtimeChart?.indicators ?? [],
        pattern_catalog: runtimeChart?.pattern_catalog ?? [],
        candlestick_patterns: runtimeChart?.candlestick_patterns ?? [],
        central_pivot_ranges: runtimeChart?.central_pivot_ranges ?? [],
        fair_value_gaps: runtimeChart?.fair_value_gaps ?? [],
        zones: runtimeChart?.zones ?? [],
        pivots: runtimeChart?.pivots ?? [],
        structure_points: runtimeChart?.structure_points ?? [],
        structure_breaks: runtimeChart?.structure_breaks ?? [],
        accumulation_zones: runtimeChart?.accumulation_zones ?? [],
        liquidity_sweeps: runtimeChart?.liquidity_sweeps ?? [],
      },
    },
    projectionComplete,
  };
}

const CORE_OVERLAYS = {
  ema_20: "EMA 20",
  ema_50: "EMA 50",
  bollinger: "Bollinger Bands",
  rsi: "RSI 14",
  volume: "Volume",
  cpr: "CPR",
  fvg: "Fair Value Gaps",
  pivots: "Confirmed pivots",
  zones: "S/R lifecycle",
  structure: "HH / HL / LH / LL",
  breaks: "BOS / CHOCH",
  accumulation: "Accumulation zones",
  liquidity_sweeps: "Liquidity sweeps",
} as const;

const STRUCTURE_LABELS: Record<string, string> = {
  higher_high: "HH",
  higher_low: "HL",
  lower_high: "LH",
  lower_low: "LL",
  equal_high: "EH",
  equal_low: "EL",
};

const COLORS = {
  cyan: "#53e5ff",
  cyanSoft: "#a6f5ff",
  green: "#58dca8",
  red: "#f45e7e",
  amber: "#ffb43f",
  violet: "#9787ff",
  muted: "#73909d",
  grid: "rgba(83,229,255,.08)",
};

function formatIst(value: string) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
    timeZoneName: "short",
  }).format(new Date(value));
}

function indicatorColour(id: string) {
  if (id.startsWith("ema")) return COLORS.cyan;
  if (id.startsWith("sma")) return COLORS.amber;
  if (id.includes("upper") || id.includes("lower")) return "rgba(151,135,255,.66)";
  if (id.includes("middle")) return "rgba(151,135,255,.35)";
  return COLORS.violet;
}

function candlestickTrace(panel: TimeframePanel): Data {
  return {
    type: "candlestick",
    name: `${panel.timeframe} OHLC`,
    x: panel.candles.map((item) => item.timestamp),
    open: panel.candles.map((item) => item.open),
    high: panel.candles.map((item) => item.high),
    low: panel.candles.map((item) => item.low),
    close: panel.candles.map((item) => item.close),
    increasing: { line: { color: COLORS.green, width: 1 }, fillcolor: COLORS.green },
    decreasing: { line: { color: COLORS.red, width: 1 }, fillcolor: COLORS.red },
    xaxis: "x",
    yaxis: "y",
    hoverlabel: { bgcolor: "#07151d", bordercolor: COLORS.cyan },
  };
}

function lineTrace(
  series: TimeframePanel["chart"]["indicators"][number],
): Data {
  const isRsi = series.pane === "rsi";
  return {
    type: "scatter",
    mode: "lines",
    name: series.label,
    x: series.points.map((point) => point.timestamp),
    y: series.points.map((point) => point.value),
    xaxis: isRsi ? "x2" : "x",
    yaxis: isRsi ? "y2" : "y",
    line: {
      color: indicatorColour(series.indicator_id),
      width: series.indicator_id.includes("middle") ? 1 : 1.5,
      dash: series.indicator_id.includes("bollinger") ? "dot" : "solid",
    },
    hovertemplate: `${series.label}: %{y:.2f}<extra></extra>`,
  };
}

function markerTrace(
  panel: TimeframePanel,
  direction: "bullish" | "bearish" | "neutral",
  selected: Set<string>,
): Data | null {
  const points = panel.chart.candlestick_patterns.filter(
    (item) => item.direction === direction && selected.has(`pattern:${item.pattern}`),
  );
  if (!points.length) return null;
  const colour = direction === "bullish" ? COLORS.green : direction === "bearish" ? COLORS.red : COLORS.amber;
  return {
    type: "scatter",
    mode: "markers",
    name: `${direction} candle pattern`,
    x: points.map((item) => item.timestamp),
    y: points.map((item) => item.price),
    text: points.map((item) => item.label),
    customdata: points.map((item) => item.raw_value),
    marker: {
      color: colour,
      symbol: direction === "bullish" ? "triangle-up" : direction === "bearish" ? "triangle-down" : "diamond",
      size: 9,
      line: { color: "#02070b", width: 1 },
    },
    hovertemplate: "%{text}<br>%{x|%d %b %Y}<br>Price %{y:.2f}<br>TA-Lib %{customdata}<extra></extra>",
    xaxis: "x",
    yaxis: "y",
  };
}

function structureTraces(panel: TimeframePanel): Data[] {
  const pivotTrace: Data = {
    type: "scatter",
    mode: "markers",
    name: "Confirmed swing pivots",
    x: panel.chart.pivots.map((item) => item.pivot_at),
    y: panel.chart.pivots.map((item) => item.price),
    text: panel.chart.pivots.map((item) => `${item.pivot_type} · confirmed ${formatIst(item.confirmed_at)}`),
    marker: {
      color: panel.chart.pivots.map((item) => item.pivot_type === "high" ? COLORS.red : COLORS.green),
      symbol: panel.chart.pivots.map((item) => item.pivot_type === "high" ? "triangle-down-open" : "triangle-up-open"),
      size: 9,
      line: { width: 1.4 },
    },
    hovertemplate: "%{text}<br>Pivot %{y:.2f}<extra></extra>",
    xaxis: "x",
    yaxis: "y",
  };
  const pointTrace: Data = {
    type: "scatter",
    mode: "text",
    name: "HH / HL / LH / LL",
    x: panel.chart.structure_points.map((item) => item.pivot_at),
    y: panel.chart.structure_points.map((item) => item.price),
    text: panel.chart.structure_points.map((item) => STRUCTURE_LABELS[item.classification] ?? item.classification),
    textposition: panel.chart.structure_points.map((item) => item.pivot_type === "high" ? "top center" : "bottom center"),
    textfont: { color: COLORS.cyanSoft, size: 10, family: "ui-monospace, monospace" },
    hovertemplate: "%{text}<br>%{x|%d %b %Y}<br>%{y:.2f}<extra></extra>",
    xaxis: "x",
    yaxis: "y",
  };
  const breakTrace: Data = {
    type: "scatter",
    mode: "markers+text",
    name: "BOS / CHOCH",
    x: panel.chart.structure_breaks.map((item) => item.occurred_at),
    y: panel.chart.structure_breaks.map((item) => item.close_price),
    text: panel.chart.structure_breaks.map((item) => item.break_type === "break_of_structure" ? "BOS" : item.break_type === "change_of_character" ? "CHOCH" : "BREAK"),
    customdata: panel.chart.structure_breaks.map((item) => item.broken_pivot_price),
    textposition: "top center",
    marker: {
      color: panel.chart.structure_breaks.map((item) => item.direction === "bullish" ? COLORS.green : COLORS.red),
      symbol: "x",
      size: 9,
    },
    textfont: { color: COLORS.amber, size: 10 },
    hovertemplate: "%{text}<br>Close %{y:.2f}<br>Broken pivot %{customdata:.2f}<extra></extra>",
    xaxis: "x",
    yaxis: "y",
  };
  return [pivotTrace, pointTrace, breakTrace];
}

function liquiditySweepTrace(panel: TimeframePanel): Data | null {
  const sweeps = panel.chart.liquidity_sweeps;
  if (!sweeps.length) return null;
  return {
    type: "scatter",
    mode: "markers",
    name: "Liquidity sweeps",
    x: sweeps.map((item) => item.swept_at),
    y: sweeps.map((item) => item.extreme_price),
    text: sweeps.map((item) => {
      const side = item.liquidity_side === "sell_side" ? "SELL-SIDE" : "BUY-SIDE";
      const volume = item.volume_multiple == null
        ? "volume unavailable"
        : `${item.volume_multiple.toFixed(2)}× volume`;
      return [
        `${side} LIQUIDITY SWEEP · ${item.implication.toUpperCase()}`,
        `Reference ₹${item.reference_price.toFixed(2)}`,
        `Extreme ₹${item.extreme_price.toFixed(2)}`,
        `Reclaim close ₹${item.reclaim_close_price.toFixed(2)}`,
        volume,
        `Confirmed ${formatIst(item.available_at)}`,
      ].join("<br>");
    }),
    marker: {
      color: sweeps.map((item) => item.implication === "bullish" ? COLORS.green : COLORS.red),
      symbol: sweeps.map((item) => item.liquidity_side === "sell_side" ? "triangle-up" : "triangle-down"),
      size: 13,
      line: { color: "#02070b", width: 1.5 },
    },
    hovertemplate: "%{text}<extra></extra>",
    xaxis: "x",
    yaxis: "y",
  };
}

function accumulationColour(state: string, active: boolean) {
  if (["failed_breakout", "invalidated", "expired"].includes(state)) {
    return { fill: "rgba(244,94,126,.09)", line: "rgba(244,94,126,.6)", text: COLORS.red };
  }
  if (["breakout", "retesting", "holding_as_support"].includes(state)) {
    return { fill: "rgba(88,220,168,.10)", line: "rgba(88,220,168,.62)", text: COLORS.green };
  }
  return active
    ? { fill: "rgba(255,180,63,.10)", line: "rgba(255,180,63,.62)", text: COLORS.amber }
    : { fill: "rgba(115,144,157,.07)", line: "rgba(115,144,157,.45)", text: COLORS.muted };
}

function chartShapes(
  panel: TimeframePanel,
  quote: DashboardQuote | null,
  selected: Set<string>,
): { shapes: Partial<Shape>[]; annotations: Partial<Annotations>[] } {
  const shapes: Partial<Shape>[] = [];
  const annotations: Partial<Annotations>[] = [];
  const x0 = panel.candles[0].timestamp;
  const x1 = panel.candles.at(-1)?.timestamp ?? x0;

  const addImmediateLevel = (
    kind: "support" | "resistance",
    zone: TimeframePanel["nearest_support"],
  ) => {
    if (!zone) return;
    const support = kind === "support";
    const colour = support ? COLORS.green : COLORS.red;
    const level = zone.boundary_price;
    shapes.push({
      type: "line",
      xref: "paper",
      yref: "y",
      x0: 0,
      x1: 1,
      y0: level,
      y1: level,
      line: { color: colour, width: 2.25, dash: "solid" },
      layer: "above",
    });
    annotations.push({
      x: support ? 0.01 : 0.99,
      xref: "paper",
      y: level,
      yref: "y",
      text: `${panel.timeframe.toUpperCase()} ${kind.toUpperCase()} ₹${zone.lower_price.toFixed(2)}–₹${zone.upper_price.toFixed(2)}`,
      showarrow: false,
      xanchor: support ? "left" : "right",
      yanchor: support ? "top" : "bottom",
      font: { color: colour, size: 10 },
      bgcolor: "rgba(2,7,11,.9)",
      bordercolor: support ? "rgba(88,220,168,.4)" : "rgba(244,94,126,.4)",
      borderwidth: 1,
      borderpad: 4,
    });
  };

  addImmediateLevel("support", panel.nearest_support);
  addImmediateLevel("resistance", panel.nearest_resistance);

  if (selected.has("fvg")) {
    for (const gap of panel.chart.fair_value_gaps) {
      const bullish = gap.direction === "bullish";
      shapes.push({
        type: "rect", xref: "x", yref: "y",
        x0: gap.detected_at, x1: gap.ends_at,
        y0: gap.lower_price, y1: gap.upper_price,
        fillcolor: bullish ? "rgba(88,220,168,.08)" : "rgba(244,94,126,.08)",
        line: { color: bullish ? "rgba(88,220,168,.28)" : "rgba(244,94,126,.28)", width: 1, dash: "dot" },
        layer: "below",
      });
    }
  }

  if (selected.has("zones")) {
    for (const zone of panel.chart.zones) {
      const kind = zone.effective_type ?? zone.original_type;
      const support = kind === "support";
      const opacity = zone.immediate ? 0.18 : 0.045;
      const colour = support ? `rgba(88,220,168,${opacity})` : `rgba(244,94,126,${opacity})`;
      shapes.push({
        type: "rect", xref: "x", yref: "y", x0, x1,
        y0: zone.lower_price, y1: zone.upper_price,
        fillcolor: colour,
        line: { color: support ? "rgba(88,220,168,.5)" : "rgba(244,94,126,.5)", width: zone.immediate ? 2 : 1, dash: zone.immediate ? "solid" : "dot" },
        layer: "below",
      });
      if (zone.immediate) {
        annotations.push({
          x: x1, xref: "x", y: support ? zone.upper_price : zone.lower_price, yref: "y",
          text: `${support ? "SUPPORT" : "RESISTANCE"} ${zone.lower_price.toFixed(2)}–${zone.upper_price.toFixed(2)} · ${zone.lifecycle_status.replaceAll("_", " ")} · ${zone.touch_count} touches`,
          showarrow: false, xanchor: "right", yanchor: support ? "top" : "bottom",
          font: { color: support ? COLORS.green : COLORS.red, size: 9 },
          bgcolor: "rgba(2,7,11,.82)", borderpad: 4,
        });
      }
    }
  }

  if (selected.has("accumulation")) {
    for (const zone of panel.chart.accumulation_zones) {
      const colour = accumulationColour(zone.lifecycle_state, zone.active);
      shapes.push({
        type: "rect", xref: "x", yref: "y",
        x0: zone.base_started_at, x1: zone.ends_at,
        y0: zone.lower_price, y1: zone.upper_price,
        fillcolor: colour.fill,
        line: { color: colour.line, width: zone.active ? 2 : 1, dash: zone.active ? "solid" : "dot" },
        layer: "below",
      });
      annotations.push({
        x: zone.ends_at, xref: "x", y: zone.upper_price, yref: "y",
        text: `ACCUMULATION ₹${zone.lower_price.toFixed(2)}–₹${zone.upper_price.toFixed(2)} · ${zone.lifecycle_state.replaceAll("_", " ").toUpperCase()} · ${zone.confidence_percentage.toFixed(0)}%`,
        showarrow: false, xanchor: "right", yanchor: "bottom",
        font: { color: colour.text, size: 9 },
        bgcolor: "rgba(2,7,11,.86)", bordercolor: colour.line, borderwidth: 1, borderpad: 4,
      });
    }
  }

  if (selected.has("liquidity_sweeps")) {
    for (const sweep of panel.chart.liquidity_sweeps) {
      const colour = sweep.implication === "bullish" ? COLORS.green : COLORS.red;
      shapes.push({
        type: "line", xref: "x", yref: "y",
        x0: sweep.swept_at, x1: sweep.reclaimed_at,
        y0: sweep.reference_price, y1: sweep.reference_price,
        line: { color: colour, width: 1.4, dash: "dot" },
        layer: "above",
      });
    }
  }

  if (selected.has("cpr")) {
    const latest = panel.chart.central_pivot_ranges.at(-1);
    for (const range of panel.chart.central_pivot_ranges) {
      shapes.push({
        type: "rect", xref: "x", yref: "y",
        x0: range.valid_from, x1: range.valid_to,
        y0: range.bottom_central, y1: range.top_central,
        fillcolor: "rgba(83,229,255,.09)",
        line: { color: "rgba(83,229,255,.38)", width: 1 },
        layer: "below",
      });
      shapes.push({
        type: "line", xref: "x", yref: "y",
        x0: range.valid_from, x1: range.valid_to,
        y0: range.pivot, y1: range.pivot,
        line: { color: "rgba(83,229,255,.75)", width: 1, dash: "dot" },
      });
    }
    if (latest) {
      annotations.push({
        x: latest.valid_to, xref: "x", y: latest.top_central, yref: "y",
        text: `${latest.basis.toUpperCase()} CPR ${latest.bottom_central.toFixed(2)}–${latest.top_central.toFixed(2)} · P ${latest.pivot.toFixed(2)} · width ${latest.width_percentage.toFixed(2)}%`,
        showarrow: false, xanchor: "right", yanchor: "bottom",
        font: { color: COLORS.cyan, size: 9 },
        bgcolor: "rgba(2,7,11,.84)", borderpad: 4,
      });
    }
  }

  shapes.push({
    type: "line", xref: "paper", yref: "y", x0: 0, x1: 1,
    y0: panel.current_close, y1: panel.current_close,
    line: { color: "rgba(255,180,63,.72)", width: 1, dash: "dash" },
  });
  annotations.push({
    x: 0, xref: "paper", y: panel.current_close, yref: "y",
    text: `ANALYSIS CLOSE ${panel.current_close.toFixed(2)}`,
    showarrow: false, xanchor: "left", yanchor: "bottom",
    font: { color: COLORS.amber, size: 9 }, bgcolor: "rgba(2,7,11,.82)", borderpad: 3,
  });
  if (quote) {
    shapes.push({
      type: "line", xref: "paper", yref: "y", x0: 0, x1: 1,
      y0: quote.price, y1: quote.price,
      line: { color: COLORS.cyan, width: 1.5, dash: "dot" },
    });
    annotations.push({
      x: 1, xref: "paper", y: quote.price, yref: "y",
      text: `BROKER LTP ${quote.price.toFixed(2)}`,
      showarrow: false, xanchor: "right", yanchor: "bottom",
      font: { color: COLORS.cyan, size: 9 }, bgcolor: "rgba(2,7,11,.86)", borderpad: 3,
    });
  }
  for (const threshold of [30, 70]) {
    shapes.push({
      type: "line", xref: "paper", yref: "y2", x0: 0, x1: 1,
      y0: threshold, y1: threshold,
      line: { color: "rgba(115,144,157,.35)", width: 1, dash: "dot" },
    });
  }
  return { shapes, annotations };
}

export function TechnicalChart({ panel: sourcePanel, quote }: { panel: TimeframePanel; quote: DashboardQuote | null }) {
  const root = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "failed">("loading");
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(DEFAULT_CHART_OVERLAY_SELECTION),
  );
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);
  const { panel, projectionComplete } = useMemo(
    () => normalizeChartPanel(sourcePanel),
    [sourcePanel],
  );
  const selectionKey = useMemo(() => [...selected].sort().join("|"), [selected]);
  const storageKey = `jarvis-chart-overlays:${panel.timeframe}`;

  const indicatorOptions = useMemo<OverlayOption[]>(() => [
    { id: "ema_20", label: CORE_OVERLAYS.ema_20, count: panel.chart.indicators.find((item) => item.indicator_id.startsWith("ema"))?.points.length ?? 0 },
    { id: "ema_50", label: CORE_OVERLAYS.ema_50, count: panel.chart.indicators.find((item) => item.indicator_id === "ema_50")?.points.length ?? 0 },
    { id: "bollinger", label: CORE_OVERLAYS.bollinger, count: panel.chart.indicators.find((item) => item.indicator_id === "bollinger_middle")?.points.length ?? 0 },
    { id: "rsi", label: CORE_OVERLAYS.rsi, count: panel.chart.indicators.find((item) => item.pane === "rsi")?.points.length ?? 0 },
    { id: "volume", label: CORE_OVERLAYS.volume, count: panel.candles.length },
    { id: "cpr", label: `${panel.timeframe === "daily" ? "Weekly" : "Monthly"} ${CORE_OVERLAYS.cpr}`, count: panel.chart.central_pivot_ranges.length },
  ], [panel]);
  const priceActionOptions = useMemo<OverlayOption[]>(() => {
    const accumulationCounts = accumulationOverlayCounts(panel.chart);
    return [
      { id: "fvg", label: CORE_OVERLAYS.fvg, count: panel.chart.fair_value_gaps.length },
      { id: "pivots", label: CORE_OVERLAYS.pivots, count: panel.chart.pivots.length },
      { id: "zones", label: CORE_OVERLAYS.zones, count: panel.chart.zones.length },
      { id: "structure", label: CORE_OVERLAYS.structure, count: panel.chart.structure_points.length },
      { id: "breaks", label: CORE_OVERLAYS.breaks, count: panel.chart.structure_breaks.length },
      { id: "accumulation", label: CORE_OVERLAYS.accumulation, count: accumulationCounts.accumulation },
      { id: "liquidity_sweeps", label: CORE_OVERLAYS.liquidity_sweeps, count: accumulationCounts.liquidity_sweeps },
    ];
  }, [panel]);
  const patternOptions = useMemo<OverlayOption[]>(() => {
    const counts = new Map<string, number>();
    for (const hit of panel.chart.candlestick_patterns) {
      counts.set(hit.pattern, (counts.get(hit.pattern) ?? 0) + 1);
    }
    return panel.chart.pattern_catalog.map((item) => ({
      id: `pattern:${item.pattern}`,
      label: item.label,
      count: counts.get(item.pattern) ?? 0,
    }));
  }, [panel]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      try {
        const stored = window.localStorage.getItem(storageKey);
        if (stored) {
          const values = JSON.parse(stored);
          if (Array.isArray(values) && values.every((item) => typeof item === "string")) {
            setSelected(new Set(values));
          }
        }
      } catch {
        // Corrupt device-local preferences must not block evidence rendering.
      } finally {
        setPreferencesLoaded(true);
      }
    });
    return () => { cancelled = true; };
  }, [storageKey]);

  useEffect(() => {
    if (!preferencesLoaded) return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify([...selected].sort()));
    } catch {
      // Storage can be unavailable in privacy mode; selection still works.
    }
  }, [preferencesLoaded, selected, storageKey]);

  useEffect(() => {
    let disposed = false;
    const element = root.current;
    if (!element) return;
    setStatus("loading");

    void import("plotly.js-dist-min").then(async ({ default: Plotly }) => {
      if (disposed) return;
      const traces: Data[] = [candlestickTrace(panel)];
      for (const indicator of panel.chart.indicators) {
        const isBand = indicator.indicator_id.startsWith("bollinger");
        const optionId = indicator.indicator_id === "ema_20"
          ? "ema_20"
          : indicator.indicator_id === "ema_50"
            ? "ema_50"
            : indicator.pane === "rsi"
              ? "rsi"
              : "bollinger";
        if ((!isBand || selected.has("bollinger")) && selected.has(optionId)) {
          traces.push(lineTrace(indicator));
        }
      }
      for (const direction of ["bullish", "bearish", "neutral"] as const) {
        const trace = markerTrace(panel, direction, selected);
        if (trace) traces.push(trace);
      }
      const structure = structureTraces(panel);
      if (selected.has("pivots")) traces.push(structure[0]);
      if (selected.has("structure")) traces.push(structure[1]);
      if (selected.has("breaks")) traces.push(structure[2]);
      if (selected.has("liquidity_sweeps")) {
        const sweepTrace = liquiditySweepTrace(panel);
        if (sweepTrace) traces.push(sweepTrace);
      }
      if (selected.has("volume")) {
        traces.push({
          type: "bar", name: "Volume",
          x: panel.candles.map((item) => item.timestamp),
          y: panel.candles.map((item) => item.volume),
          marker: { color: panel.candles.map((item) => item.close >= item.open ? "rgba(88,220,168,.45)" : "rgba(244,94,126,.45)") },
          xaxis: "x3", yaxis: "y3", hovertemplate: "Volume %{y:,}<extra></extra>",
        });
      }
      const overlays = chartShapes(panel, quote, selected);
      const showRsi = selected.has("rsi");
      const showVolume = selected.has("volume");
      const priceDomain: [number, number] = showRsi && showVolume
        ? [0.40, 1]
        : showRsi
          ? [0.25, 1]
          : showVolume
            ? [0.20, 1]
            : [0, 1];
      const rsiDomain: [number, number] = showVolume ? [0.20, 0.34] : [0, 0.18];
      const volumeDomain: [number, number] = [0, 0.14];
      const rangeValues = [
        ...panel.candles.flatMap((item) => [item.low, item.high]),
        panel.current_close,
        ...(quote ? [quote.price] : []),
        ...(panel.nearest_support ? [panel.nearest_support.boundary_price] : []),
        ...(panel.nearest_resistance ? [panel.nearest_resistance.boundary_price] : []),
        ...(selected.has("accumulation")
          ? panel.chart.accumulation_zones.flatMap((item) => [item.lower_price, item.upper_price])
          : []),
        ...(selected.has("liquidity_sweeps")
          ? panel.chart.liquidity_sweeps.flatMap((item) => [item.reference_price, item.extreme_price, item.reclaim_close_price])
          : []),
      ];
      const rangeLow = Math.min(...rangeValues);
      const rangeHigh = Math.max(...rangeValues);
      const rangePadding = Math.max((rangeHigh - rangeLow) * 0.06, rangeHigh * 0.002);
      const rangeBreaks = marketRangeBreaks(panel.timeframe, panel.candles);
      const layout: Partial<Layout> = {
        autosize: true,
        height: 720,
        margin: { l: 58, r: 72, t: 34, b: 42 },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(2,12,18,.2)",
        font: { color: "#8facb7", family: "Inter, ui-sans-serif, system-ui", size: 10 },
        hovermode: "x unified",
        dragmode: "pan",
        showlegend: true,
        legend: { orientation: "h", x: 0, y: 1.08, font: { size: 9 }, bgcolor: "rgba(0,0,0,0)" },
        xaxis: { domain: [0, 1], anchor: "y", showticklabels: !showRsi && !showVolume, showgrid: true, gridcolor: COLORS.grid, rangeslider: { visible: false }, rangebreaks: rangeBreaks, tickformat: "%d %b\n%Y" },
        yaxis: { domain: priceDomain, title: { text: "PRICE (₹)" }, side: "right", range: [rangeLow - rangePadding, rangeHigh + rangePadding], showgrid: true, gridcolor: COLORS.grid, zeroline: false, fixedrange: false },
        xaxis2: { domain: [0, 1], anchor: "y2", matches: "x", showticklabels: showRsi && !showVolume, visible: showRsi, showgrid: true, gridcolor: COLORS.grid, rangebreaks: rangeBreaks, tickformat: "%d %b\n%Y" },
        yaxis2: { domain: rsiDomain, title: { text: "RSI" }, side: "right", range: [0, 100], visible: showRsi, showgrid: true, gridcolor: COLORS.grid, zeroline: false },
        xaxis3: { domain: [0, 1], anchor: "y3", matches: "x", visible: showVolume, showgrid: true, gridcolor: COLORS.grid, rangebreaks: rangeBreaks, tickformat: "%d %b\n%Y" },
        yaxis3: { domain: volumeDomain, title: { text: "VOL" }, side: "right", visible: showVolume, showgrid: true, gridcolor: COLORS.grid, zeroline: false },
        shapes: overlays.shapes,
        annotations: overlays.annotations,
      };
      const config: Partial<Config> = {
        responsive: true,
        displaylogo: false,
        scrollZoom: true,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
        toImageButtonOptions: { format: "png", filename: `jarvis-${panel.timeframe}-technical-chart`, scale: 2 },
      };
      await Plotly.react(element, traces, layout, config);
      if (!disposed) setStatus("ready");
    }).catch(() => {
      if (!disposed) setStatus("failed");
    });

    return () => {
      disposed = true;
      void import("plotly.js-dist-min").then(({ default: Plotly }) => {
        if (element) Plotly.purge(element);
      });
    };
  }, [panel, quote, selected, selectionKey]);

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const applyPreset = (preset: "clean" | "trend" | "price" | "patterns" | "decision" | "all") => {
    const patternIds = patternOptions.map((item) => item.id);
    const values = {
      clean: [],
      trend: ["ema_20", "ema_50", "bollinger", "rsi", "volume", "cpr"],
      price: [...PRICE_ACTION_OVERLAY_SELECTION],
      patterns: patternIds,
      decision: [...DEFAULT_CHART_OVERLAY_SELECTION],
      all: [...Object.keys(CORE_OVERLAYS), ...patternIds],
    }[preset];
    setSelected(new Set(values));
  };

  const optionButton = (option: OverlayOption) => (
    <button
      type="button"
      className={selected.has(option.id) ? "active" : ""}
      aria-pressed={selected.has(option.id)}
      onClick={() => toggle(option.id)}
      key={option.id}
    >
      <span>{option.label}</span><em>{option.count}</em>
    </button>
  );

  return (
    <div className="technical-chart-shell">
      {!projectionComplete && (
        <p className="chart-compatibility-notice" role="status">
          This result predates the technical-chart projection. The price candles remain available,
          but a fresh analysis is required for indicators, patterns, CPR and price-action overlays.
        </p>
      )}
      <details className="indicator-drawer">
        <summary>
          <span>Indicators &amp; evidence</span>
          <b>{selected.size} selected</b>
        </summary>
        <div className="preset-toolbar" aria-label={`${panel.timeframe} chart presets`}>
          <span>Presets</span>
          <button type="button" onClick={() => applyPreset("clean")}>Clean</button>
          <button type="button" onClick={() => applyPreset("trend")}>Trend</button>
          <button type="button" onClick={() => applyPreset("price")}>Price action</button>
          <button type="button" onClick={() => applyPreset("patterns")}>Patterns</button>
          <button type="button" onClick={() => applyPreset("decision")}>Decision</button>
          <button type="button" onClick={() => applyPreset("all")}>Everything</button>
        </div>
        <div className="indicator-groups">
          <section>
            <h3>Trend &amp; momentum</h3>
            <div className="chart-toolbar">{indicatorOptions.map(optionButton)}</div>
          </section>
          <section>
            <h3>Price action</h3>
            <div className="chart-toolbar">{priceActionOptions.map(optionButton)}</div>
          </section>
          <section className="pattern-selector">
            <h3>Candlestick patterns</h3>
            <div className="chart-toolbar">{patternOptions.map(optionButton)}</div>
          </section>
        </div>
      </details>
      <div className="plotly-chart" ref={root} aria-label={`${panel.timeframe} interactive candlestick chart`} />
      {status === "loading" && <p className="chart-status">Calibrating technical overlays…</p>}
      {status === "failed" && <p className="chart-status failed">The chart renderer could not initialise.</p>}
      <div className="chart-provenance">
        <span>{panel.candles.length} completed {panel.timeframe} candles</span>
        <span>Analysis candle · {formatIst(panel.evaluated_at)}</span>
        <span>{panel.chart.candlestick_patterns.length} named pattern hits</span>
        <span>{panel.chart.fair_value_gaps.length} visible FVG lifecycles</span>
        <span>{panel.chart.central_pivot_ranges.length} look-ahead-safe CPR ranges</span>
        <span>{panel.chart.accumulation_zones.length} accumulation zones</span>
        <span>{panel.chart.liquidity_sweeps.length} confirmed liquidity sweeps</span>
      </div>
    </div>
  );
}
