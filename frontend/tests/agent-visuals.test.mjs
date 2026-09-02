import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIVE_AGENT_VISUAL_KEYS,
  AGENT_VISUALS,
  PARKED_AGENT_VISUAL_KEYS,
  agentVisualState,
} from "../lib/agent-visuals.ts";

test("defines one immutable visual identity for every planned agent", () => {
  assert.deepEqual(Object.keys(AGENT_VISUALS), [
    "jarvis",
    "research",
    "daily",
    "weekly",
    "bull",
    "bear",
    "judge",
    "financial",
    "news",
  ]);
  assert.equal(AGENT_VISUALS.bull.motif, "bull-charge");
  assert.equal(AGENT_VISUALS.bear.motif, "bear-prowl");
  assert.equal(AGENT_VISUALS.judge.motif, "balance");
  assert.equal(AGENT_VISUALS.financial.motif, "document-radar");
  assert.equal(Object.isFrozen(AGENT_VISUALS), true);
  assert.equal(Object.values(AGENT_VISUALS).every(Object.isFrozen), true);
});

test("keeps implemented agents active and future agents parked", () => {
  assert.deepEqual(ACTIVE_AGENT_VISUAL_KEYS, [
    "research",
    "daily",
    "weekly",
    "financial",
    "bull",
    "bear",
    "judge",
  ]);
  assert.deepEqual(PARKED_AGENT_VISUAL_KEYS, ["news"]);
  assert.equal(AGENT_VISUALS.financial.label, "Fundamental Analyst");
  assert.equal(AGENT_VISUALS.financial.available, true);
  assert.equal(AGENT_VISUALS.news.available, false);
});

test("maps backend progress into presentation-only hologram states", () => {
  assert.equal(agentVisualState("standby"), "idle");
  assert.equal(agentVisualState("active"), "acquiring");
  assert.equal(agentVisualState("complete"), "linked");
  assert.equal(agentVisualState("failed"), "fault");
});
