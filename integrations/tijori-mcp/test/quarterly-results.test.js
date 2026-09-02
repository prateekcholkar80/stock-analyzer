import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createQuarterlyResultsSnapshotExtractor,
  normalizeEmbeddedQuarterlyResultsData,
  normalizeQuarterlyResults,
} from '../src/quarterly-results.js';


const NOW = new Date('2026-09-01T08:30:00Z');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'EXAMPLE',
  legal_name: 'Example Industries Ltd.',
  provider_company_id: '1234',
  provider_slug: 'example-industries-limited',
});

function providerRow(name, index, values, children = '', overrides = {}) {
  return {
    formula: name.toLowerCase().replaceAll(' ', '_'),
    index,
    name,
    name_span: name,
    sub_section: children,
    value: values,
    value_yoy: values.map(() => null),
    value_perc: values.map(() => null),
    source: 'provider',
    field: 'NA',
    ...overrides,
  };
}

function providerTables() {
  const tree = (latestSales) => ({
    report_dates: ['Sep 2025', 'Dec 2025', 'Mar 2026', 'Jun 2026'],
    skip_report_dates: ['Jun 2025'],
    data: [
      providerRow('Net Sales', 1, [100, 110, 120, latestSales], '', {
        value_yoy: ['20%', '25%'],
      }),
      providerRow('Operating Expenses', 1, [70, 75, 80, 90], [
        providerRow('Employee Cost', 2, [20, 21, 22, 23]),
      ]),
      providerRow('Net Profit', 1, [10, 11, 12, null]),
      providerRow('Quarterly Ratios', 1, [0, 0, 0, 0], [
        providerRow('EPS', 2, [2, 2.1, 2.2, 2.3]),
        providerRow('Operating Profit Margin', 1, [17, 18, 19, 20], '', {
          field: 'operating_profit_margin',
          value_yoy: ['5%', '6%'],
        }),
        providerRow('Net Profit Margin', 2, [8, 9, 10, 11], '', {
          field: 'net_profit_margin',
        }),
      ]),
    ],
  });
  return {
    qt_c: tree(130),
    qt_s: tree(125),
  };
}

function request(reportingBasis = 'consolidated') {
  return { issuer: ISSUER, reporting_basis: reportingBasis };
}

test('selects consolidated and standalone Quarterly Results independently', () => {
  const consolidated = normalizeEmbeddedQuarterlyResultsData(
    providerTables(),
    'consolidated',
  );
  const standalone = normalizeEmbeddedQuarterlyResultsData(
    providerTables(),
    'standalone',
  );

  assert.equal(consolidated.rows[0].values[3].source_value, '130');
  assert.equal(standalone.rows[0].values[3].source_value, '125');
});

test('preserves nested hierarchy and aligns shorter YoY arrays to trailing quarters', () => {
  const result = normalizeEmbeddedQuarterlyResultsData(
    providerTables(),
    'consolidated',
  );

  assert.deepEqual(
    result.periods.map(({ source_label }) => source_label),
    ['Sep 2025', 'Dec 2025', 'Mar 2026', 'Jun 2026'],
  );
  assert.deepEqual(result.skipped_report_dates, ['Jun 2025']);
  assert.deepEqual(
    result.rows[0].values.map(({ yoy_change }) => yoy_change),
    [null, null, '20%', '25%'],
  );
  assert.equal(result.rows[2].parent_row_key, result.rows[1].row_key);
  assert.equal(result.rows[6].depth, 1);
  assert.equal(result.rows[6].parent_row_key, result.rows[4].row_key);
  assert.equal(result.rows[3].values[3].source_value, null);
  assert.equal(result.rows[3].values[3].availability_status, 'unknown');
  assert.equal(result.all_sections_expanded, true);
});

test('labels monetary, per-share, percentage, and section rows explicitly', () => {
  const result = normalizeEmbeddedQuarterlyResultsData(
    providerTables(),
    'consolidated',
  );

  assert.equal(result.rows[0].value_kind, 'monetary');
  assert.equal(result.rows[0].normalized_unit, 'INR crore');
  assert.equal(result.rows[4].value_kind, 'other');
  assert.equal(result.rows[5].value_kind, 'per_share');
  assert.equal(result.rows[6].value_kind, 'percentage');
  assert.equal(result.rows[7].normalized_unit, 'percent');
});

test('produces a complete labelled Quarterly Results document', () => {
  const embedded = normalizeEmbeddedQuarterlyResultsData(
    providerTables(),
    'consolidated',
  );
  const result = normalizeQuarterlyResults(embedded, request(), NOW);

  assert.equal(result.schema_version, 'tijori.financial_document.v1');
  assert.equal(result.document_type, 'quarterly_results');
  assert.equal(result.reporting_basis, 'consolidated');
  assert.equal(result.source_unit, 'mixed');
  assert.equal(result.normalized_unit, 'mixed');
  assert.equal(result.extraction.period_count, 4);
  assert.equal(result.extraction.row_count, 8);
  assert.equal(result.extraction.cell_count, 32);
  assert.equal(result.extraction.maximum_depth, 1);
  assert.equal(result.extraction.status, 'complete');
});

test('extracts embedded JSON without interacting with collapsed rows', async () => {
  const observed = {};
  const page = {
    async goto(url, options) {
      observed.options = options;
      return { url: () => url };
    },
    async waitForFunction(predicate, argument, options) {
      observed.wait = { predicate, argument, options };
    },
    async evaluate() {
      return providerTables();
    },
  };
  const extractor = createQuarterlyResultsSnapshotExtractor({
    browserRunner: { async run(task) { return task(page); } },
    clock: () => NOW,
  });

  const result = await extractor(request('standalone'));

  assert.equal(result.reporting_basis, 'standalone');
  assert.equal(result.rows[0].values[3].source_value, '125');
  assert.equal(observed.options.waitUntil, 'commit');
  assert.equal(observed.wait.options.timeout, 20_000);
});

test('fails closed for missing basis, excess auxiliary values, and bad input', () => {
  const missing = providerTables();
  delete missing.qt_c;
  assert.throws(
    () => normalizeEmbeddedQuarterlyResultsData(missing, 'consolidated'),
    /must be an object/,
  );

  const excess = providerTables();
  excess.qt_s.data[0].value_yoy.push('1%', '2%', '3%');
  assert.throws(
    () => normalizeEmbeddedQuarterlyResultsData(excess, 'standalone'),
    /must align with primary values/,
  );

  assert.throws(
    () => normalizeEmbeddedQuarterlyResultsData(providerTables(), 'annual'),
    /reporting basis is invalid/,
  );
});
