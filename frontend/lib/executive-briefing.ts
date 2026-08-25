import type { Dashboard } from "@/lib/dashboard";

export type DecisionTone = "bullish" | "bearish" | "neutral" | "caution";

export type JudgeDecisionView = {
  tone: DecisionTone;
  verdictLabel: string;
  actionLabel: string;
  confidenceLabel: string;
  confidenceMeaning: string;
};

function normalizeWinner(value: string): "bullish" | "bearish" | "neutral" {
  const normalized = value.trim().toLowerCase();
  if (normalized === "bullish" || normalized === "bearish") return normalized;
  return "neutral";
}

export function judgeDecisionView(
  winner: string,
  confidencePercentage: number,
  disposition: Dashboard["trade_plan"]["disposition"],
): JudgeDecisionView {
  const direction = normalizeWinner(winner);
  const confidence = `${confidencePercentage.toFixed(0)}%`;
  if (direction === "bearish") {
    return {
      tone: "bearish",
      verdictLabel: "Bearish",
      actionLabel: "No Trade",
      confidenceLabel: `${confidence} confidence in bearish verdict`,
      confidenceMeaning: (
        "The Judge believes the supplied evidence supports a bearish market "
        + "condition. It is not a forecast that the price has the same chance of falling."
      ),
    };
  }
  if (direction === "bullish" && disposition === "actionable") {
    return {
      tone: "bullish",
      verdictLabel: "Bullish",
      actionLabel: "Buy Setup",
      confidenceLabel: `${confidence} confidence in bullish verdict`,
      confidenceMeaning: (
        "The Judge believes the supplied evidence supports the bullish case; "
        + "the separate risk rules have also approved a long setup."
      ),
    };
  }
  if (direction === "bullish") {
    return {
      tone: "caution",
      verdictLabel: "Bullish bias",
      actionLabel: "No Trade",
      confidenceLabel: `${confidence} confidence in bullish verdict`,
      confidenceMeaning: (
        "The evidence leans bullish, but Jarvis's risk and reward rules have "
        + "not approved an entry. Confidence is not permission to buy."
      ),
    };
  }
  return {
    tone: "neutral",
    verdictLabel: "Neutral",
    actionLabel: "No Trade",
    confidenceLabel: `${confidence} confidence in neutral verdict`,
    confidenceMeaning: (
      "The evidence does not support either side strongly enough. This is not "
      + "a probability of price direction."
    ),
  };
}

export function readableBriefingPoints(value?: string): string[] {
  if (!value?.trim()) return [];
  const withHeadingBreaks = value
    .replace(/\*\*([^*]+?):\*\*/g, "\n$1: ")
    .replace(/[`*_]/g, "")
    .trim();
  const sections = withHeadingBreaks
    .split(/\n+/)
    .map((item) => item.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  if (sections.length === 0) return [];
  if (sections.length > 1) return sections;
  return sections[0]
    .split(/(?<=[.!?])\s+(?=[A-Z])/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function beginnerTimeframeSummary(
  timeframe: "Weekly" | "Daily",
  stance: string,
): string {
  const meaning = timeframe === "Weekly"
    ? "the broader swing-trading trend"
    : "near-term entry timing";
  return `${timeframe}: ${stance.replaceAll("_", " ")} — this describes ${meaning}.`;
}
