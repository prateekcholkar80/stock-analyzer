export type Evidence = {
  evidence_id: string;
  name: string;
  direction: string;
  strength: string;
  explanation: string;
  decisive: boolean;
};

export type DashboardCandle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type IndicatorSeries = {
  indicator_id: string;
  label: string;
  pane: "price" | "rsi";
  points: Array<{ timestamp: string; value: number }>;
};

export type ChartZone = {
  zone_id: string;
  original_type: "support" | "resistance";
  effective_type: "support" | "resistance" | null;
  lower_price: number;
  upper_price: number;
  touch_count: number;
  lifecycle_status: string;
  confirmed_at: string;
  broken_at: string | null;
  retested_at: string | null;
  reversal_confirmed_at: string | null;
  failed_at: string | null;
  immediate: boolean;
};

export type AccumulationZone = {
  zone_id: string;
  lower_price: number;
  upper_price: number;
  base_started_at: string;
  base_last_observed_at: string;
  ends_at: string;
  lifecycle_state: string;
  active: boolean;
  confidence_percentage: number;
  evidence_ids: string[];
};

export type LiquiditySweep = {
  sweep_id: string;
  zone_id: string;
  liquidity_side: "sell_side" | "buy_side";
  implication: "bullish" | "bearish";
  reference_price: number;
  extreme_price: number;
  reclaim_close_price: number;
  swept_at: string;
  reclaimed_at: string;
  available_at: string;
  volume_multiple: number | null;
  evidence_ids: string[];
};

export type TechnicalChartData = {
  indicators: IndicatorSeries[];
  pattern_catalog: Array<{ pattern: string; label: string }>;
  candlestick_patterns: Array<{
    pattern: string;
    label: string;
    direction: "bullish" | "bearish" | "neutral";
    timestamp: string;
    price: number;
    raw_value: number;
  }>;
  central_pivot_ranges: Array<{
    basis: "weekly" | "monthly";
    source_period_started_at: string;
    source_period_ended_at: string;
    valid_from: string;
    valid_to: string;
    pivot: number;
    bottom_central: number;
    top_central: number;
    width_percentage: number;
  }>;
  fair_value_gaps: Array<{
    direction: "bullish" | "bearish";
    detected_at: string;
    ends_at: string;
    lower_price: number;
    upper_price: number;
    status: string;
    fill_percentage: number;
  }>;
  zones: ChartZone[];
  pivots: Array<{
    pivot_type: "high" | "low";
    price: number;
    pivot_at: string;
    confirmed_at: string;
  }>;
  structure_points: Array<{
    classification: string;
    pivot_type: "high" | "low";
    price: number;
    pivot_at: string;
    confirmed_at: string;
  }>;
  structure_breaks: Array<{
    break_type: string;
    direction: "bullish" | "bearish";
    occurred_at: string;
    close_price: number;
    broken_pivot_price: number;
  }>;
  accumulation_zones: AccumulationZone[];
  liquidity_sweeps: LiquiditySweep[];
};

export type TimeframePanel = {
  timeframe: "daily" | "weekly";
  interval: string;
  evaluated_at: string;
  stance: string;
  score: number;
  confidence_percentage: number;
  current_close: number;
  candles: DashboardCandle[];
  chart: TechnicalChartData;
  evidence: Evidence[];
  nearest_support: {
    boundary_price: number;
    lower_price: number;
    upper_price: number;
    distance_percentage: number;
    lifecycle_status: string;
  } | null;
  nearest_resistance: {
    boundary_price: number;
    lower_price: number;
    upper_price: number;
    distance_percentage: number;
    lifecycle_status: string;
  } | null;
};

export type DashboardQuote = {
  price: number;
  open: number;
  high: number;
  low: number;
  previous_close: number;
  observed_at: string;
  source: string;
};

export type RefreshProvenance = {
  schema_version: "jarvis.dashboard_refresh.v1";
  mode: "initial" | "incremental" | "unchanged";
  dataset_id: string;
  adapter_name: string;
  source: string;
  source_retrieved_at: string;
  requested_from: string | null;
  requested_to: string | null;
  resumed_from: string | null;
  checked_at: string | null;
  stored_from: string;
  stored_to: string;
  existing_candle_count: number;
  final_candle_count: number;
  new_candle_count: number;
  corrected_candle_count: number;
  deduplicated_fetched_candle_count: number;
  chunk_request_count: number;
  reused_existing_dataset: boolean;
  intraday_gaps: Array<{
    interval: string;
    gap_after: string;
    resumes_at: string;
    cadence_minutes: number;
    missing_candle_count: number;
  }>;
};

export type SetupStepState =
  | "confirmed"
  | "developing"
  | "pending"
  | "contradicted"
  | "invalidated"
  | "unavailable";

export type DashboardSetupStep = {
  step_id: string;
  label: string;
  sequence: number;
  state: SetupStepState;
  evidence_ids: string[];
  observed_at: string | null;
  available_at: string | null;
  confirmed_at: string | null;
  invalidated_at: string | null;
  explanation: string;
  observed_values: Record<string, boolean | number | string>;
  thresholds: Record<string, boolean | number | string>;
};

export type DashboardSwingSetup = {
  setup_id: string;
  side: "bullish" | "bearish";
  steps: DashboardSetupStep[];
};

export type DashboardTimeframeInterpretation = {
  timeframe: "daily" | "weekly";
  interval: string;
  evaluated_at: string;
  market_condition:
    | "bullish"
    | "neutral"
    | "bearish"
    | "conflicted"
    | "insufficient";
  bullish_setup: DashboardSwingSetup;
  bearish_setup: DashboardSwingSetup;
  decisive_evidence_ids: string[];
  rationale: string;
};

export type RewardRiskTargetInterpretation = {
  reward_to_risk: 2 | 3;
  feasibility:
    | "feasible"
    | "blocked_by_structure"
    | "insufficient_data"
    | "not_evaluated";
  target_price: number | null;
  blocking_evidence_ids: string[];
  rationale: string;
};

export type DashboardMultiTimeframeInterpretation = {
  schema_version: "jarvis.dashboard_interpretation.v1";
  daily: DashboardTimeframeInterpretation;
  weekly: DashboardTimeframeInterpretation;
  alignment:
    | "aligned_bullish"
    | "aligned_bearish"
    | "aligned_neutral"
    | "mixed"
    | "conflicted"
    | "insufficient";
  tactical_readiness:
    | "ready"
    | "developing"
    | "blocked"
    | "not_applicable"
    | "insufficient";
  structural_risk: "low" | "moderate" | "high" | "prohibitive" | "unknown";
  risk_reward: {
    reference_entry: number | null;
    stop_loss: number | null;
    risk_per_unit: number | null;
    target_2r: RewardRiskTargetInterpretation;
    target_3r: RewardRiskTargetInterpretation;
  };
  trade_decision: {
    market_condition:
      | "bullish"
      | "neutral"
      | "bearish"
      | "conflicted"
      | "insufficient";
    decision: "buy" | "no_trade";
    no_trade_reasons: string[];
    rationale: string;
  };
  decisive_evidence_ids: string[];
  decision_change_conditions: string[];
  rationale: string;
  interpreted_at: string;
};

export type Dashboard = {
  symbol: string;
  exchange: string;
  completed_at: string;
  source_retrieved_at: string;
  latest_quote: DashboardQuote | null;
  refresh: RefreshProvenance;
  daily: TimeframePanel;
  weekly: TimeframePanel;
  debate: {
    winner: string;
    confidence_percentage: number;
    bull_case_summary: string;
    bear_case_summary: string;
    judge_rationale: string;
    rounds: Array<{
      round_number: number;
      bull: { thesis: string; evidence_citations: string[] };
      bear: { thesis: string; evidence_citations: string[] };
    }>;
  };
  trade_plan: {
    disposition: "actionable" | "no_trade";
    rationale: string;
    entry_price: number | null;
    stop_loss_price: number | null;
    target_2r_price: number | null;
    target_3r_price: number | null;
  };
  /** Additive field: absent/null when reading a pre-interpretation result. */
  interpretation?: DashboardMultiTimeframeInterpretation | null;
  presentation: {
    executive_briefing?: string;
    weekly_analysis?: string;
    daily_analysis?: string;
    judge_conclusion_explanation?: string;
    limitations?: string[];
    disclaimer?: string;
    summary?: string;
  } | null;
};
