import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createGrowthTableSnapshotExtractor,
  normalizeEmbeddedGrowthData,
  normalizeGrowthTable,
} from '../src/growth-table.js';


const NOW = new Date('2026-08-30T12:30:00Z');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'COFORGE',
  legal_name: 'Coforge Ltd.',
  provider_company_id: '4502',
  provider_slug: 'niit-technologies-limited',
});
const REQUEST = Object.freeze({ issuer: ISSUER, reporting_basis: 'consolidated' });

function raw(overrides = {}) {
  return {
    reporting_basis: 'not_applicable',
    unit: 'in %',
    all_sections_expanded: true,
    columns: [
      { column_key: 'column_0', source_label: '1Y', display_order: 0 },
      { column_key: 'column_1', source_label: '3Y', display_order: 1 },
    ],
    rows: [
      {
        row_key: 'growth', original_label: 'Growth', parent_row_key: null,
        depth: 0, row_kind: 'section', display_order: 0,
        values: [
          {
            column_key: 'column_0', source_value: null, yoy_change: null,
            percentage_of_parent: null, availability_status: 'unknown',
          },
          {
            column_key: 'column_1', source_value: null, yoy_change: null,
            percentage_of_parent: null, availability_status: 'unknown',
          },
        ],
      },
      {
        row_key: 'growth.sales', original_label: 'Sales Growth', parent_row_key: 'growth',
        depth: 1, row_kind: 'metric', display_order: 1,
        values: [
          {
            column_key: 'column_0', source_value: '0.00', yoy_change: null,
            percentage_of_parent: null, availability_status: 'available',
          },
          {
            column_key: 'column_1', source_value: '12.40', yoy_change: '24%',
            percentage_of_parent: '12.4%', availability_status: 'available',
          },
        ],
      },
    ],
    ...overrides,
  };
}

function embeddedGrowth(overrides = {}) {
  return {
    report_dates: ['1yr', '2yr'],
    data: [
      {
        formula: 'close', index: 1, name: 'Share Price CAGR',
        sub_section: [], value: [-5.1, 0], value_yoy: [null, '100%'],
        value_perc: [null, null], source: 'accord', field: 'NA',
      },
      {
        formula: 'Adj_eps_abs', index: 1, name: 'TTM EPS CAGR',
        sub_section: [
          {
            formula: 'Net_Sales', index: 2, name: 'Sales CAGR',
            sub_section: '', value: [14.74, null], value_yoy: [null, null],
            value_perc: [null, null], source: 'accord', field: 'NA',
          },
        ],
        value: [-9.52, 3.65], value_yoy: [null, '138%'],
        value_perc: [null, null], source: 'accord', field: 'NA',
      },
    ],
    ...overrides,
  };
}

test('retains the complete provider Growth Table shape without a line-item whitelist', () => {
  const result = normalizeGrowthTable(raw(), REQUEST, NOW);

  assert.equal(result.schema_version, 'tijori.financial_document.v1');
  assert.equal(result.document_type, 'growth_table');
  assert.equal(result.reporting_basis, 'not_applicable');
  assert.equal(result.extraction.row_count, 2);
  assert.equal(result.extraction.column_count, 2);
  assert.equal(result.rows[1].original_label, 'Sales Growth');
  assert.equal(result.rows[1].values[0].source_value, '0.00');
  assert.equal(result.rows[1].values[1].yoy_change, '24%');
  assert.equal(result.rows[1].values[1].percentage_of_parent, '12.4%');
  assert.equal(result.rows[0].values[0].availability_status, 'unknown');
  assert.equal(result.source.retrieved_at, NOW.toISOString());
});

test('reports the provider Growth Table basis as not applicable', () => {
  const request = { issuer: ISSUER, reporting_basis: 'standalone' };
  const result = normalizeGrowthTable(raw(), request, NOW);

  assert.equal(result.reporting_basis, 'not_applicable');
});

test('recursively expands the embedded provider hierarchy and preserves zero and null', () => {
  const result = normalizeEmbeddedGrowthData(embeddedGrowth());

  assert.deepEqual(result.columns.map(({ source_label }) => source_label), ['1yr', '2yr']);
  assert.equal(result.rows.length, 3);
  assert.equal(result.rows[1].row_kind, 'section');
  assert.equal(result.rows[2].parent_row_key, result.rows[1].row_key);
  assert.equal(result.rows[2].depth, 1);
  assert.equal(result.rows[0].values[1].source_value, '0');
  assert.equal(result.rows[0].values[1].availability_status, 'available');
  assert.equal(result.rows[0].values[1].yoy_change, '100%');
  assert.equal(result.rows[0].values[1].percentage_of_parent, null);
  assert.equal(result.rows[2].values[1].source_value, null);
  assert.equal(result.rows[2].values[1].availability_status, 'unknown');
  assert.equal(result.all_sections_expanded, true);
});

test('rejects malformed embedded periods, hierarchy, and value coverage', () => {
  const badDepth = embeddedGrowth();
  badDepth.data[1].sub_section[0].index = 3;
  assert.throws(() => normalizeEmbeddedGrowthData(badDepth), /depth is inconsistent/);

  const missingValue = embeddedGrowth();
  missingValue.data[0].value.pop();
  assert.throws(() => normalizeEmbeddedGrowthData(missingValue), /cover every period/);

  assert.throws(
    () => normalizeEmbeddedGrowthData({ report_dates: [], data: [] }),
    /periods must be/,
  );

  const malformedAuxiliary = embeddedGrowth();
  malformedAuxiliary.data[0].value_yoy = [null];
  assert.throws(
    () => normalizeEmbeddedGrowthData(malformedAuxiliary),
    /year-over-year values must cover every period/,
  );
});

test('rejects unbounded or non-string auxiliary cell values', () => {
  const malformed = raw();
  malformed.rows[1].values[0].yoy_change = 12;
  assert.throws(() => normalizeGrowthTable(malformed, REQUEST, NOW));

  const oversized = raw();
  oversized.rows[1].values[0].percentage_of_parent = 'x'.repeat(81);
  assert.throws(() => normalizeGrowthTable(oversized, REQUEST, NOW));
});

test('rejects collapsed, wrong-basis, incomplete, and contradictory snapshots', () => {
  const invalid = [
    raw({ all_sections_expanded: false }),
    raw({ reporting_basis: 'standalone' }),
    raw({ rows: [{ ...raw().rows[0], values: raw().rows[0].values.slice(0, 1) }] }),
    raw({ rows: [{
      ...raw().rows[0],
      values: [
        { column_key: 'column_0', source_value: '5', availability_status: 'unknown' },
        raw().rows[0].values[1],
      ],
    }] }),
  ];
  for (const value of invalid) {
    assert.throws(() => normalizeGrowthTable(value, REQUEST, NOW));
  }
});

test('rejects malformed hierarchy and duplicate row keys', () => {
  const missingParent = raw();
  missingParent.rows[1] = { ...missingParent.rows[1], parent_row_key: 'missing' };
  assert.throws(
    () => normalizeGrowthTable(missingParent, REQUEST, NOW),
    /hierarchy is invalid/,
  );

  const duplicate = raw();
  duplicate.rows[1] = { ...duplicate.rows[1], row_key: 'growth' };
  assert.throws(
    () => normalizeGrowthTable(duplicate, REQUEST, NOW),
    /row keys must be unique/,
  );
});

test('extracts the allowlisted embedded Growth Table without UI interaction', async () => {
  const observed = { labels: [], selectors: [] };
  const page = {
    async goto(url, options) {
      observed.url = url;
      observed.options = options;
      return { url: () => url };
    },
    async waitForFunction(predicate, argument, options) {
      observed.waits ??= [];
      observed.waits.push({ predicate, argument, options });
    },
    async evaluate(callback) {
      observed.evaluateCallback = callback;
      return embeddedGrowth();
    },
  };
  const extractor = createGrowthTableSnapshotExtractor({
    browserRunner: { async run(task) { return task(page); } },
    clock: () => NOW,
  });

  const result = await extractor(REQUEST);

  assert.equal(result.document_type, 'growth_table');
  assert.equal(result.reporting_basis, 'not_applicable');
  assert.equal(result.rows.length, 3);
  assert.deepEqual(observed.labels, []);
  assert.equal(observed.options.waitUntil, 'commit');
  assert.equal(observed.waits.length, 1);
  assert.deepEqual(observed.selectors, []);
});

test('fails closed for an unconfirmed issuer path or ambiguous UI control', async () => {
  const wrongPage = {
    async goto() { return { url: () => 'https://www.tijorifinance.com/login/' }; },
  };
  const wrongPath = createGrowthTableSnapshotExtractor({
    browserRunner: { async run(task) { return task(wrongPage); } },
    clock: () => NOW,
  });
  await assert.rejects(() => wrongPath(REQUEST), /resolved issuer/);

  const malformedEmbeddedPage = {
    async goto(url) { return { url: () => url }; },
    async waitForFunction() {},
    async evaluate() { return { report_dates: ['1yr'], data: [] }; },
  };
  const malformed = createGrowthTableSnapshotExtractor({
    browserRunner: { async run(task) { return task(malformedEmbeddedPage); } },
    clock: () => NOW,
  });
  await assert.rejects(() => malformed(REQUEST), /rows must be/);
});
