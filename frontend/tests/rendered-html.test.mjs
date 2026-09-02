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

  assert.match(page, /fetch\(`\$\{operationBase\}\/result`/);
  assert.match(page, /structured_document_references/);
  assert.match(page, /setStructuredDocuments\(\[\]\)/);
  assert.match(page, /Completed financial documents/);
  assert.match(page, /FINANCIAL_DOCUMENT_LABELS\[document\.document_type\]/);
  assert.match(page, /benchmarking_financials_reference/);
  assert.match(page, /fetchBenchmarkingFinancials/);
  assert.match(page, /Peer benchmarking/);
  assert.match(page, /best-benchmark/);
});
