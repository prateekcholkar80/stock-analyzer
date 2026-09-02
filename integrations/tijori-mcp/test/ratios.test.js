import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createRatiosSnapshotExtractor,
  normalizeEmbeddedRatiosData,
  normalizeRatios,
} from '../src/ratios.js';


const NOW = new Date('2026-09-01T08:30:00Z');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'EXAMPLE',
  legal_name: 'Example Industries Ltd.',
  provider_company_id: '1234',
  provider_slug: 'example-industries-limited',
});

function providerRow(name, index, values, children = '') {
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
  };
}

function providerTables() {
  const tree = (currentRatio) => ({
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: ['Mar 2024'],
    data: [
      providerRow('Operational Ratios', 1, [0, 0], [
        providerRow('Current Ratio', 2, [1.1, currentRatio], [
          providerRow('Current Assets (Crs)', 3, [100, 120]),
        ]),
        providerRow('Cash Conversion Cycle', 2, [-30, -20]),
      ]),
      providerRow('Profitability Ratios', 1, [0, 0], [
        providerRow('Gross Margin (%)', 2, [35, 36]),
        providerRow('Adjusted EPS', 2, [10, null]),
      ]),
      providerRow('Valuation Ratios', 1, [0, 0], [
        providerRow('P/E', 2, [20, 18]),
      ]),
    ],
  });
  return {
    fr_c: tree(1.2),
    fr_s: tree(0.9),
  };
}

function request(reportingBasis = 'consolidated') {
  return { issuer: ISSUER, reporting_basis: reportingBasis };
}

test('selects consolidated and standalone Ratios trees independently', () => {
  const consolidated = normalizeEmbeddedRatiosData(
    providerTables(),
    'consolidated',
  );
  const standalone = normalizeEmbeddedRatiosData(
    providerTables(),
    'standalone',
  );

  assert.equal(consolidated.reporting_basis, 'consolidated');
  assert.equal(standalone.reporting_basis, 'standalone');
  assert.equal(consolidated.rows[1].values[1].source_value, '1.2');
  assert.equal(standalone.rows[1].values[1].source_value, '0.9');
});

test('preserves hierarchy, periods, negative values, zero, and unknown values', () => {
  const result = normalizeEmbeddedRatiosData(
    providerTables(),
    'consolidated',
  );

  assert.deepEqual(
    result.periods.map(({ source_label }) => source_label),
    ['Mar 2025', 'Mar 2026'],
  );
  assert.deepEqual(result.skipped_report_dates, ['Mar 2024']);
  assert.equal(result.rows.length, 9);
  assert.equal(result.rows[0].values[0].source_value, '0');
  assert.equal(result.rows[2].depth, 2);
  assert.equal(result.rows[2].parent_row_key, result.rows[1].row_key);
  assert.equal(result.rows[3].values[0].source_value, '-30');
  assert.equal(result.rows[6].values[1].source_value, null);
  assert.equal(result.rows[6].values[1].availability_status, 'unknown');
  assert.equal(result.all_sections_expanded, true);
});

test('labels every mixed-unit ratio row explicitly', () => {
  const result = normalizeEmbeddedRatiosData(
    providerTables(),
    'consolidated',
  );

  assert.deepEqual(
    [
      result.rows[0].value_kind,
      result.rows[1].value_kind,
      result.rows[2].value_kind,
      result.rows[3].source_unit,
      result.rows[5].value_kind,
      result.rows[6].value_kind,
      result.rows[8].value_kind,
    ],
    ['other', 'ratio', 'monetary', 'days', 'percentage', 'per_share', 'ratio'],
  );
  assert.equal(result.rows[2].normalized_unit, 'INR crore');
  assert.equal(result.rows[5].normalized_unit, 'percent');
});

test('produces a complete labelled Ratios document', () => {
  const embedded = normalizeEmbeddedRatiosData(
    providerTables(),
    'consolidated',
  );
  const result = normalizeRatios(embedded, request(), NOW);

  assert.equal(result.schema_version, 'tijori.financial_document.v1');
  assert.equal(result.document_type, 'ratios');
  assert.equal(result.reporting_basis, 'consolidated');
  assert.equal(result.source_unit, 'mixed');
  assert.equal(result.normalized_unit, 'mixed');
  assert.equal(result.extraction.period_count, 2);
  assert.equal(result.extraction.row_count, 9);
  assert.equal(result.extraction.cell_count, 18);
  assert.equal(result.extraction.maximum_depth, 2);
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
  const extractor = createRatiosSnapshotExtractor({
    browserRunner: { async run(task) { return task(page); } },
    clock: () => NOW,
  });

  const result = await extractor(request('standalone'));

  assert.equal(result.reporting_basis, 'standalone');
  assert.equal(result.rows[1].values[1].source_value, '0.9');
  assert.equal(observed.options.waitUntil, 'commit');
  assert.equal(observed.wait.options.timeout, 20_000);
});

test('fails closed for missing basis, malformed hierarchy, and auxiliary drift', () => {
  const missing = providerTables();
  delete missing.fr_c;
  assert.throws(
    () => normalizeEmbeddedRatiosData(missing, 'consolidated'),
    /must be an object/,
  );

  const badDepth = providerTables();
  badDepth.fr_c.data[0].sub_section[0].index = 3;
  assert.throws(
    () => normalizeEmbeddedRatiosData(badDepth, 'consolidated'),
    /depth is inconsistent/,
  );

  const misaligned = providerTables();
  misaligned.fr_s.data[0].value_yoy.pop();
  assert.throws(
    () => normalizeEmbeddedRatiosData(misaligned, 'standalone'),
    /must align with primary values/,
  );
});

test('rejects an unconfirmed issuer path and mismatched reporting basis', async () => {
  const extractor = createRatiosSnapshotExtractor({
    browserRunner: {
      async run(task) {
        return task({
          async goto() {
            return { url: () => 'https://www.tijorifinance.com/login/' };
          },
        });
      },
    },
    clock: () => NOW,
  });
  await assert.rejects(() => extractor(request()), /resolved issuer/);

  const embedded = normalizeEmbeddedRatiosData(
    providerTables(),
    'consolidated',
  );
  assert.throws(
    () => normalizeRatios(embedded, request('standalone'), NOW),
    /does not match/,
  );
});
