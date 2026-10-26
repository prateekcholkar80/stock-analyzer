import assert from "node:assert/strict";
import test from "node:test";

import {
  fundamentalConfidenceTone,
  parseFundamentalAnalysis,
  parseFundamentalPackageReference,
} from "../lib/fundamental-analysis.ts";

const analysis = {
  schema_version: "jarvis.fundamental_analysis.v1",
  analysis_id: "fundamental-analysis-1",
  evidence_coverage_and_freshness: "Twelve current cached documents.",
  financial_strengths: ["Revenue growth is positive."],
  financial_risks_and_conflicts: [],
  preliminary_financial_health: "Preliminary health is stable.",
  confidence_percentage: 64,
  valuation_observations: [],
  missing_evidence_and_limitations: ["Annual report unavailable."],
  document_citations: ["financial_document:abc"],
  runtime_skill_id: "jarvis-fundamental-analyst",
  runtime_skill_version: "1.0.0",
  runtime_skill_prompt_sha256: "a".repeat(64),
  model_id: "provider/fundamental-model",
  generated_at: "2026-09-08T18:30:00+05:30",
};

test("accepts a complete versioned Fundamental Analyst result", () => {
  assert.deepEqual(parseFundamentalAnalysis(analysis), analysis);
});

test("rejects malformed or ungrounded Fundamental Analyst results", () => {
  assert.equal(parseFundamentalAnalysis(null), null);
  assert.equal(
    parseFundamentalAnalysis({ ...analysis, confidence_percentage: 101 }),
    null,
  );
  assert.equal(
    parseFundamentalAnalysis({ ...analysis, document_citations: [] }),
    null,
  );
  assert.equal(
    parseFundamentalAnalysis({ ...analysis, runtime_skill_prompt_sha256: "bad" }),
    null,
  );
});

test("maps confidence to stable presentation tones", () => {
  assert.equal(fundamentalConfidenceTone(80), "positive");
  assert.equal(fundamentalConfidenceTone(60), "caution");
  assert.equal(fundamentalConfidenceTone(30), "limited");
});

test("accepts a package reference bound to its manifest fingerprint", () => {
  const fingerprint = "b".repeat(64);
  const reference = {
    schema_version: "jarvis.browser_fundamental_package_ref.v1",
    package_id: `fundamental_package:${fingerprint}`,
    manifest_fingerprint: fingerprint,
    expires_at: "2026-09-18T18:30:00+05:30",
  };

  assert.deepEqual(parseFundamentalPackageReference(reference), reference);
});

test("rejects malformed or mismatched package references", () => {
  const fingerprint = "b".repeat(64);
  const reference = {
    schema_version: "jarvis.browser_fundamental_package_ref.v1",
    package_id: `fundamental_package:${fingerprint}`,
    manifest_fingerprint: fingerprint,
    expires_at: "2026-09-18T18:30:00+05:30",
  };

  assert.equal(
    parseFundamentalPackageReference({
      ...reference,
      package_id: `fundamental_package:${"c".repeat(64)}`,
    }),
    null,
  );
  assert.equal(
    parseFundamentalPackageReference({ ...reference, expires_at: "not-a-date" }),
    null,
  );
  assert.equal(parseFundamentalPackageReference(null), null);
});
