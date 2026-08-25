import type {
  DashboardSwingSetup,
  DashboardTimeframeInterpretation,
  SetupStepState,
} from "./dashboard";

export const SETUP_STATE_META: Record<
  SetupStepState,
  { symbol: string; label: string }
> = {
  confirmed: { symbol: "✓", label: "Confirmed" },
  developing: { symbol: "◌", label: "Developing" },
  pending: { symbol: "·", label: "Pending" },
  contradicted: { symbol: "×", label: "Contradicted" },
  invalidated: { symbol: "!", label: "Invalidated" },
  unavailable: { symbol: "—", label: "Unavailable" },
};

export function confirmedStepCount(setup: DashboardSwingSetup): number {
  return setup.steps.filter((step) => step.state === "confirmed").length;
}

export function timeframeSetupSummary(
  interpretation: DashboardTimeframeInterpretation,
): string {
  const bullish = confirmedStepCount(interpretation.bullish_setup);
  const bearish = confirmedStepCount(interpretation.bearish_setup);
  return `${bullish}/${interpretation.bullish_setup.steps.length} bullish · ${bearish}/${interpretation.bearish_setup.steps.length} bearish checks confirmed`;
}
