import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Jarvis investment console", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Jarvis · Investment Intelligence<\/title>/i);
  assert.match(html, /JARVIS/);
  assert.match(html, /Investment intelligence/i);
  assert.match(html, /Research division/i);
  assert.match(html, /Debate chamber/i);
  assert.match(html, /Operation matrix/i);
  assert.match(html, /Live agent activity/i);
  assert.match(html, /Agent telemetry will appear/i);
  assert.match(html, /Wake Jarvis/i);
  assert.match(html, /IST · (?:<!-- -->)?--:--/i);
  assert.match(html, /Research, not guaranteed investment advice/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});

test("completed operations expose financial-document references to the Fundamental Analyst", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(page, /fetch\(`\$\{operationBase\}\/audit`/);
  assert.match(page, /structured_document_references/);
  assert.match(page, /setStructuredDocuments\(\[\]\)/);
  assert.match(page, /Completed financial documents/);
  assert.match(page, /FINANCIAL_DOCUMENT_LABELS\[document\.document_type\]/);
  assert.match(page, /benchmarking_financials_reference/);
  assert.match(page, /fetchBenchmarkingFinancials/);
  assert.match(page, /Peer benchmarking/);
  assert.match(page, /best-benchmark/);
});

test("completed operations render the deterministic evidence hierarchy", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const panel = await readFile(
    new URL("../components/EvidenceHierarchyPanel.tsx", import.meta.url),
    "utf8",
  );

  assert.match(page, /completedDashboard\?\.hierarchy_assessment/);
  assert.match(page, /<EvidenceHierarchyPanel assessment=/);
  assert.match(panel, /Counted once per group/);
  assert.match(panel, /Every applicable gate must pass/);
  assert.match(panel, /assessment\.blocking_families/);
  assert.match(panel, /assessment\.evidence_ids/);
});

test("completed operations render a grounded Fundamental Analyst briefing", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const panel = await readFile(
    new URL("../components/FundamentalAnalystBriefing.tsx", import.meta.url),
    "utf8",
  );

  assert.match(page, /parseFundamentalAnalysis/);
  assert.match(page, /parseFundamentalPackageReference/);
  assert.match(page, /operationResult\.fundamental_analysis/);
  assert.match(page, /fundamental_package_reference/);
  assert.match(page, /packageReference=\{fundamentalPackageReference\}/);
  assert.match(page, /<FundamentalAnalystBriefing/);
  assert.match(panel, /Financial health snapshot/);
  assert.match(panel, /Evidence confidence/);
  assert.match(panel, /Financial strengths/);
  assert.match(panel, /Risks &amp; conflicts/);
  assert.match(panel, /Evidence lineage/);
  assert.match(panel, /Committed package/);
  assert.match(panel, /manifest/);
  assert.match(panel, /valid until/);
});

test("debate failure retains Research Division artifacts and identifies the failed agent", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(page, /setActivities\(audit\.events\)/);
  assert.match(page, /parseAgentExecutionOutcomes\(audit\.agent_outcomes\)/);
  assert.match(page, /outcome\.status === "failed"/);
  assert.match(page, /as DashboardResponse/);
  assert.match(page, /setDashboard\(dashboardResult\)/);
  assert.match(page, /Research Division completed/);
  assert.match(page, /no trade conclusion was produced/);
  assert.match(page, /operationResult\.decision_failure/);
  assert.match(page, /isTechnicalResearchDashboard\(dashboard\)/);
  assert.match(page, />No trade conclusion</);
  assert.match(page, /Daily and weekly research is available; no decision was inferred/);
  assert.match(page, /panel=\{dashboard\.daily\}/);
  assert.match(page, /panel=\{dashboard\.weekly\}/);
  assert.match(page, /completedDashboard &&/);
  assert.match(page, /fundamentalProgress = outcomeAwareProgress\(/);
  assert.match(page, /matchesFundamentalOutcome/);
  assert.match(page, /fundamentalProgress\.status === "failed"/);
  assert.match(page, /Fundamental fault/);
});

test("agent cards and operation matrix prefer authoritative audit outcomes", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(page, /const \[agentOutcomes, setAgentOutcomes\]/);
  assert.match(page, /setAgentOutcomes\(verifiedOutcomes\)/);
  assert.match(page, /agentOutcomeProgress\(agentOutcomes, matchesOutcome\)/);
  assert.match(page, /authoritative: true/);
  assert.match(page, /setAgentOutcomes\(\[\]\)/);
  assert.match(page, /step\.matchesOutcome/);
  assert.match(page, /stepProgress\.statusLabel \?\? stepProgress\.status/);
  assert.match(page, /statusLabel=\{agentStates\.bull\.statusLabel/);
  assert.match(page, /statusLabel=\{agentStates\.judge\.statusLabel/);
  assert.match(page, /fetchOperationAudit\(operationId\)/);
});

test("Fundamental Analyst failure remains separate from technical output", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(page, /fundamental_failure/);
  assert.match(page, /setFundamentalFailure\(operationResult\.fundamental_failure \?\? null\)/);
  assert.match(page, /Fundamental Analyst explanation failed/);
  assert.match(page, /You can retry this research request/);
});

test("Research Division shows package commit and analyst failure as separate audit outcomes", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(page, /parseFundamentalPackagePublication\(/);
  assert.match(page, /setFundamentalPackagePublication\(publication\)/);
  assert.match(page, /package publication does not match its retained result/);
  assert.match(page, /fundamentalPackagePublication && \(/);
  assert.match(page, /Complete package committed · 16 datasets/);
  assert.match(page, /fundamentalFailure && \(/);
  assert.match(page, /Fundamental Analyst explanation failed/);
  assert.match(css, /\.fundamental-publication-outcome/);
  assert.match(css, /\.fundamental-analyst-outcome/);
});
