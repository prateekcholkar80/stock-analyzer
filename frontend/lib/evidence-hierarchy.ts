import type {
  EvidenceCorrelationGroup,
  EvidenceFamily,
  EvidenceFamilyState,
  HierarchicalEvidenceAssessment,
  MarketCondition,
} from "./dashboard";

export type HierarchyTone = "positive" | "caution" | "negative" | "muted";

export const EVIDENCE_FAMILY_LABELS: Readonly<Record<EvidenceFamily, string>> =
  Object.freeze({
    market_structure: "Market structure",
    price_location: "Price location",
    participation: "Participation",
    trend_confirmation: "Trend confirmation",
    momentum_confirmation: "Momentum confirmation",
    cpr_context: "CPR context",
    candlestick_context: "Candlestick context",
    volatility_context: "Volatility context",
    fundamental_health: "Fundamental health",
    entry_trigger: "Entry trigger",
    risk_target_feasibility: "1:2 target space",
  });

export const CORRELATION_GROUP_LABELS: Readonly<
  Record<EvidenceCorrelationGroup, string>
> = Object.freeze({
  market_structure: "Market structure",
  price_context: "Price context",
  participation: "Participation",
  ohlc_derived_confirmation: "Technical confirmation",
  fundamental: "Fundamental eligibility",
  entry: "Entry confirmation",
  risk: "Risk and target feasibility",
});

type HierarchyState = EvidenceFamilyState | MarketCondition;

export const HIERARCHY_STATE_META: Readonly<
  Record<HierarchyState, { label: string; tone: HierarchyTone }>
> = Object.freeze({
  bullish: { label: "Bullish", tone: "positive" },
  neutral: { label: "Neutral", tone: "caution" },
  bearish: { label: "Bearish", tone: "negative" },
  conflicted: { label: "Conflicted", tone: "caution" },
  insufficient: { label: "Insufficient", tone: "muted" },
  pass: { label: "Pass", tone: "positive" },
  fail: { label: "Blocked", tone: "negative" },
  not_applicable: { label: "Outside scope", tone: "muted" },
});

export function hierarchyReleaseLabel(
  assessment: HierarchicalEvidenceAssessment,
): string {
  if (assessment.release_eligible) return "Release qualified";
  const count = assessment.blocking_families.length;
  return count === 1 ? "1 release gate blocked" : `${count} release gates blocked`;
}
