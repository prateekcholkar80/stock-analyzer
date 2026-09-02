import assert from "node:assert/strict";
import test from "node:test";

import {
  FinancialDocumentClientError,
  fetchBenchmarkingFinancials,
  fetchStructuredFinancialDocument,
} from "../lib/financial-documents.ts";

const cacheEntryId = `financial_document:${"a".repeat(64)}`;
const operationId = "operation-1";
const documentId = "provider.NSE.COFORGE.growth_table.not_applicable";
const fingerprint = "b".repeat(64);

function responseBody() {
  return {
    schema_version: "jarvis.http_financial_document.v1",
    operation_id: operationId,
    reference: { cache_entry_id: cacheEntryId, document_id: documentId, document_fingerprint: fingerprint },
    document: {
      schema_version: "jarvis.http_financial_document_payload.v1",
      document_id: documentId,
      document_fingerprint: fingerprint,
      all_sections_expanded: true,
      periods: [{ period_key: "fy2026", source_label: "MAR'26", display_order: 0 }],
      rows: [{ row_key: "revenue", original_label: "Revenue", display_order: 0, cells: [] }],
    },
  };
}

test("resolves an operation-owned document with browser authorization", async () => {
  const calls = [];
  const result = await fetchStructuredFinancialDocument({
    apiBase: "http://127.0.0.1:8000/",
    sessionId: "session/1",
    operationId,
    cacheEntryId,
    accessToken: "browser-token",
    fetcher: async (input, init) => {
      calls.push({ input, init });
      return new Response(JSON.stringify(responseBody()));
    },
  });

  assert.equal(result.document.rows[0].original_label, "Revenue");
  assert.equal(calls[0].init.headers["X-Jarvis-Session-Token"], "browser-token");
  assert.match(calls[0].input, /sessions\/session%2F1\/operations\/operation-1/);
  assert.match(calls[0].input, /financial_document%3A/);
});

test("rejects a document that does not match its released reference", async () => {
  const invalid = responseBody();
  invalid.document.document_fingerprint = "c".repeat(64);

  await assert.rejects(
    fetchStructuredFinancialDocument({
      apiBase: "http://127.0.0.1:8000",
      sessionId: "session-1",
      operationId,
      cacheEntryId,
      accessToken: "browser-token",
      fetcher: async () => new Response(JSON.stringify(invalid)),
    }),
    (error) => error instanceof FinancialDocumentClientError && error.status === 502,
  );
});

test("explains automatic cache expiry without reflecting server content", async () => {
  await assert.rejects(
    fetchStructuredFinancialDocument({
      apiBase: "http://127.0.0.1:8000",
      sessionId: "session-1",
      operationId,
      cacheEntryId,
      accessToken: "browser-token",
      fetcher: async () => new Response("secret detail", { status: 410 }),
    }),
    (error) => error instanceof FinancialDocumentClientError
      && error.status === 410
      && error.message.includes("expired")
      && !error.message.includes("secret"),
  );
});

test("resolves an operation-owned benchmarking matrix", async () => {
  const benchmarkCacheId = `benchmarking_financials:${"c".repeat(64)}`;
  const benchmarkDocumentId = "provider.NSE.COFORGE.benchmarking_financials.not_applicable";
  const body = {
    schema_version: "jarvis.http_benchmarking_financials.v1",
    operation_id: operationId,
    reference: {
      cache_entry_id: benchmarkCacheId,
      document_id: benchmarkDocumentId,
      document_fingerprint: fingerprint,
      observation_date: "2026-09-01",
      company_count: 2,
      row_count: 1,
    },
    document: {
      schema_version: "jarvis.http_benchmarking_financials_payload.v1",
      document_id: benchmarkDocumentId,
      document_fingerprint: fingerprint,
      observation_date: "2026-09-01",
      all_rows_captured: true,
      companies: [
        { company_key: "coforge", legal_name: "Coforge", display_order: 0 },
        { company_key: "birlasoft", legal_name: "Birlasoft", display_order: 1 },
      ],
      rows: [{ row_key: "roe", original_label: "5 yr Average ROE", cells: [] }],
    },
  };
  const calls = [];

  const result = await fetchBenchmarkingFinancials({
    apiBase: "http://127.0.0.1:8000/",
    sessionId: "session/1",
    operationId,
    cacheEntryId: benchmarkCacheId,
    accessToken: "browser-token",
    fetcher: async (input, init) => {
      calls.push({ input, init });
      return new Response(JSON.stringify(body));
    },
  });

  assert.equal(result.document.companies[0].legal_name, "Coforge");
  assert.equal(calls[0].init.headers["X-Jarvis-Session-Token"], "browser-token");
  assert.match(calls[0].input, /benchmarking-financials/);
  assert.match(calls[0].input, /benchmarking_financials%3A/);
});

test("rejects a benchmarking matrix that does not match its reference", async () => {
  const benchmarkCacheId = `benchmarking_financials:${"c".repeat(64)}`;
  const body = {
    schema_version: "jarvis.http_benchmarking_financials.v1",
    operation_id: operationId,
    reference: {
      cache_entry_id: benchmarkCacheId,
      document_id: "benchmark-one",
      document_fingerprint: fingerprint,
      observation_date: "2026-09-01",
      company_count: 2,
      row_count: 1,
    },
    document: {
      schema_version: "jarvis.http_benchmarking_financials_payload.v1",
      document_id: "benchmark-two",
      document_fingerprint: fingerprint,
      observation_date: "2026-09-01",
      all_rows_captured: true,
      companies: [{}, {}],
      rows: [{}],
    },
  };

  await assert.rejects(
    fetchBenchmarkingFinancials({
      apiBase: "http://127.0.0.1:8000",
      sessionId: "session-1",
      operationId,
      cacheEntryId: benchmarkCacheId,
      accessToken: "browser-token",
      fetcher: async () => new Response(JSON.stringify(body)),
    }),
    (error) => error instanceof FinancialDocumentClientError && error.status === 502,
  );
});
