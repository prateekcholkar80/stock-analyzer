import type { ProgressStatus } from "./workflow-progress";

export type AgentVisualKey =
  | "jarvis"
  | "research"
  | "daily"
  | "weekly"
  | "bull"
  | "bear"
  | "judge"
  | "financial"
  | "news";

export type AgentVisualTone = "cyan" | "amber" | "rose" | "violet" | "emerald";
export type AgentVisualMotif =
  | "reactor"
  | "market-radar"
  | "daily-orbit"
  | "weekly-orbit"
  | "bull-charge"
  | "bear-prowl"
  | "balance"
  | "document-radar"
  | "signal-array";
export type AgentVisualState = "idle" | "acquiring" | "linked" | "fault";

export type AgentVisualSpec = Readonly<{
  key: AgentVisualKey;
  label: string;
  specialty: string;
  tone: AgentVisualTone;
  motif: AgentVisualMotif;
  ariaLabel: string;
  available: boolean;
}>;

export const AGENT_VISUALS = Object.freeze({
  jarvis: agent("jarvis", "Jarvis", "Chief research interface", "cyan", "reactor", true),
  research: agent("research", "Market Data Analyst", "Instrument and broker data", "cyan", "market-radar", true),
  daily: agent("daily", "Daily Analyst", "Tactical structure", "violet", "daily-orbit", true),
  weekly: agent("weekly", "Weekly Analyst", "Strategic structure", "amber", "weekly-orbit", true),
  bull: agent("bull", "Bull", "Constructive advocate", "emerald", "bull-charge", true),
  bear: agent("bear", "Bear", "Risk advocate", "rose", "bear-prowl", true),
  judge: agent("judge", "Senior Judge", "Evidence synthesis", "cyan", "balance", true),
  financial: agent("financial", "Fundamental Analyst", "Document-grounded company research", "emerald", "document-radar", true),
  news: agent("news", "News Analyst", "Event and catalyst intelligence", "cyan", "signal-array", false),
} satisfies Readonly<Record<AgentVisualKey, AgentVisualSpec>>);

export const ACTIVE_AGENT_VISUAL_KEYS = Object.freeze([
  "research",
  "daily",
  "weekly",
  "financial",
  "bull",
  "bear",
  "judge",
] satisfies AgentVisualKey[]);

export const PARKED_AGENT_VISUAL_KEYS = Object.freeze([
  "news",
] satisfies AgentVisualKey[]);

export function agentVisualState(status: ProgressStatus): AgentVisualState {
  return {
    standby: "idle",
    active: "acquiring",
    complete: "linked",
    failed: "fault",
  }[status];
}

function agent(
  key: AgentVisualKey,
  label: string,
  specialty: string,
  tone: AgentVisualTone,
  motif: AgentVisualMotif,
  available: boolean,
): AgentVisualSpec {
  return Object.freeze({
    key,
    label,
    specialty,
    tone,
    motif,
    ariaLabel: `${label} holographic status`,
    available,
  });
}
