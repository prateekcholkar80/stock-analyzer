import assert from "node:assert/strict";
import test from "node:test";

import { isTechnicalResearchDashboard } from "../lib/dashboard.ts";


test("discriminates a retained technical dashboard without conclusion fields", () => {
  const researchOnly = {
    schema_version: "jarvis.technical_research_dashboard.v1",
    conclusion_available: false,
  };
  const completed = {
    schema_version: "jarvis.dashboard.v1",
    debate: {},
    trade_plan: {},
  };

  assert.equal(isTechnicalResearchDashboard(researchOnly), true);
  assert.equal(isTechnicalResearchDashboard(completed), false);
  assert.equal("debate" in researchOnly, false);
  assert.equal("trade_plan" in researchOnly, false);
});
