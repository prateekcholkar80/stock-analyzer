import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createBenchmarkingFinancialsExtractor,
  normalizeBenchmarkingFinancials,
} from '../src/benchmarking-financials.js';


const NOW = new Date('2026-09-01T10:00:00+05:30');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'COFORGE',
  legal_name: 'Coforge Ltd.',
  provider_company_id: '4502',
  provider_slug: 'niit-technologies-limited',
});

function snapshot() {
  return {
    companies: [
      { legal_name: 'Coforge', href: '/company/niit-technologies-limited/' },
      { legal_name: 'Birlasoft', href: '/company/kpit-technologies-limited/' },
      { legal_name: 'Persistent Systems', href: '/company/persistent-systems-limited/' },
    ],
    rows: [
      row('financial', null, 0, 'Financials', ['', '', ''], {
        has_children: true,
      }),
      row('284', 'financial', 1, '5 yr Average ROE', ['19.61 %', '15.89 %', '22.35 %'], {
        has_children: true,
        best: 2,
      }),
      row('362', '284', 2, '5yr average Equity Multiplier', ['2.04', '—', '2.13']),
      row('303', 'financial', 1, '5yr Avg ROCE', ['19.46 %', '22.15 %', '28.79 %'], {
        provider_hidden: true,
        best: 2,
      }),
    ],
  };
}

function row(
  providerRowId,
  parentProviderRowId,
  depth,
  label,
  values,
  { has_children = false, provider_hidden = false, best = -1 } = {},
) {
  return {
    provider_row_id: providerRowId,
    parent_provider_row_id: parentProviderRowId,
    depth,
    provider_section: 'bch_financial',
    provider_hidden,
    has_children,
    label,
    values: values.map((sourceValue, index) => ({
      source_value: sourceValue,
      is_best: index === best,
    })),
  };
}

test('normalizes the complete hierarchical Financial benchmarking matrix', () => {
  const result = normalizeBenchmarkingFinancials(snapshot(), { issuer: ISSUER }, NOW);

  assert.equal(result.schema_version, 'tijori.benchmarking_financials.v1');
  assert.equal(result.document_type, 'benchmarking_financials');
  assert.equal(result.reporting_basis, 'not_applicable');
  assert.equal(result.companies[0].is_subject, true);
  assert.equal(result.rows[0].row_kind, 'section');
  assert.equal(result.rows[2].parent_row_key, '284');
  assert.equal(result.rows[2].values[1].source_value, null);
  assert.equal(result.rows[2].values[1].availability_status, 'unknown');
  assert.equal(result.rows[3].provider_hidden, true);
  assert.equal(result.rows[3].values[2].is_best, true);
  assert.equal(result.all_rows_captured, true);
  assert.deepEqual(result.extraction, {
    company_count: 3,
    row_count: 4,
    cell_count: 12,
    maximum_depth: 2,
    provider_hidden_row_count: 1,
    status: 'complete',
  });
});

test('extracts all DOM rows without clicking provider presentation toggles', async () => {
  const observed = {};
  const page = {
    async goto(url, options) {
      observed.url = url;
      observed.options = options;
      return { url: () => url };
    },
    async waitForSelector(selector) {
      observed.selector = selector;
    },
    async evaluate(callback) {
      observed.callback = callback;
      return snapshot();
    },
  };
  const extractor = createBenchmarkingFinancialsExtractor({
    browserRunner: { run: async (task) => task(page) },
    clock: () => NOW,
  });

  const result = await extractor({ issuer: ISSUER });

  assert.equal(
    observed.url,
    'https://www.tijorifinance.com/company/niit-technologies-limited/benchmarking/',
  );
  assert.equal(observed.options.waitUntil, 'domcontentloaded');
  assert.match(observed.selector, /#benchmarking-financial/);
  assert.equal(result.extraction.provider_hidden_row_count, 1);
});

test('rejects inconsistent hierarchy, duplicate companies and missing subject', () => {
  const hierarchy = snapshot();
  hierarchy.rows[2].depth = 1;
  assert.throws(
    () => normalizeBenchmarkingFinancials(hierarchy, { issuer: ISSUER }, NOW),
    /hierarchy is inconsistent/,
  );

  const duplicate = snapshot();
  duplicate.companies[1].href = duplicate.companies[0].href;
  assert.throws(
    () => normalizeBenchmarkingFinancials(duplicate, { issuer: ISSUER }, NOW),
    /companies must be unique/,
  );

  const missingSubject = snapshot();
  missingSubject.companies[0].href = '/company/another-company/';
  assert.throws(
    () => normalizeBenchmarkingFinancials(missingSubject, { issuer: ISSUER }, NOW),
    /identify the subject exactly once/,
  );
});

test('preserves zero, distinguishes missing values and rejects uneven rows', () => {
  const raw = snapshot();
  raw.rows[1].values[0].source_value = '0';
  const result = normalizeBenchmarkingFinancials(raw, { issuer: ISSUER }, NOW);
  assert.equal(result.rows[1].values[0].source_value, '0');
  assert.equal(result.rows[1].values[0].availability_status, 'available');

  const uneven = snapshot();
  uneven.rows[1].values.pop();
  assert.throws(
    () => normalizeBenchmarkingFinancials(uneven, { issuer: ISSUER }, NOW),
    /cover every company/,
  );
});

test('fails closed for redirects, unsafe links, extra fields and invalid clocks', async () => {
  const redirected = createBenchmarkingFinancialsExtractor({
    browserRunner: {
      run: async (task) => task({
        goto: async () => ({ url: () => 'https://www.tijorifinance.com/dashboard/' }),
      }),
    },
    clock: () => NOW,
  });
  await assert.rejects(
    () => redirected({ issuer: ISSUER }),
    /did not match the resolved issuer/,
  );

  const unsafe = snapshot();
  unsafe.companies[1].href = 'https://example.com/company/kpit-technologies-limited/';
  assert.throws(
    () => normalizeBenchmarkingFinancials(unsafe, { issuer: ISSUER }, NOW),
    /outside the provider/,
  );
  assert.throws(
    () => normalizeBenchmarkingFinancials(snapshot(), { issuer: ISSUER, raw: true }, NOW),
    /unsupported fields/,
  );
  assert.throws(
    () => normalizeBenchmarkingFinancials(snapshot(), { issuer: ISSUER }, new Date('invalid')),
    /retrieval time is invalid/,
  );
});
