import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createCashFlowSnapshotExtractor,
  normalizeCashFlow,
  normalizeEmbeddedCashFlowData,
} from '../src/cash-flow.js';


const NOW = new Date('2026-08-31T08:30:00Z');
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
  const tree = (operatingCash) => ({
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: ['Mar 2024'],
    data: [
      providerRow('Cash from Operating Activity', 1, [100, operatingCash], [
        providerRow('Profit Before Tax', 2, [80, 90]),
        providerRow('Working Capital Changes', 2, [0, -12]),
      ]),
      providerRow('Cash from Investing Activity', 1, [-50, -60], [
        providerRow('Net Fixed Assets Purchased', 2, [-40, -45], [
          providerRow('Fixed Assets Purchased', 3, [-40]),
        ]),
      ]),
      providerRow('Net Cash Flow', 1, [50, null]),
    ],
  });
  return {
    cf_c: tree(120),
    cf_s: tree(105),
  };
}

function request(reportingBasis = 'consolidated') {
  return { issuer: ISSUER, reporting_basis: reportingBasis };
}

test('selects consolidated and standalone Cash Flow trees independently', () => {
  const consolidated = normalizeEmbeddedCashFlowData(
    providerTables(),
    'consolidated',
  );
  const standalone = normalizeEmbeddedCashFlowData(
    providerTables(),
    'standalone',
  );

  assert.equal(consolidated.reporting_basis, 'consolidated');
  assert.equal(standalone.reporting_basis, 'standalone');
  assert.equal(consolidated.rows[0].values[1].source_value, '120');
  assert.equal(standalone.rows[0].values[1].source_value, '105');
});

test('preserves hierarchy, outflows, zero, unknown values, and skipped periods', () => {
  const result = normalizeEmbeddedCashFlowData(
    providerTables(),
    'consolidated',
  );

  assert.deepEqual(
    result.periods.map(({ source_label }) => source_label),
    ['Mar 2025', 'Mar 2026'],
  );
  assert.deepEqual(result.skipped_report_dates, ['Mar 2024']);
  assert.equal(result.rows.length, 7);
  assert.equal(result.rows[2].values[0].source_value, '0');
  assert.equal(result.rows[2].values[1].source_value, '-12');
  assert.equal(result.rows[5].depth, 2);
  assert.equal(result.rows[5].parent_row_key, result.rows[4].row_key);
  assert.equal(result.rows[5].values[0].source_value, '-40');
  assert.equal(result.rows[5].values[1].source_value, null);
  assert.equal(result.rows[5].values[1].availability_status, 'unknown');
  assert.equal(result.rows[6].values[1].source_value, null);
  assert.equal(result.all_sections_expanded, true);
});

test('produces a complete labelled Cash Flow document', () => {
  const embedded = normalizeEmbeddedCashFlowData(
    providerTables(),
    'consolidated',
  );
  const result = normalizeCashFlow(embedded, request(), NOW);

  assert.equal(result.schema_version, 'tijori.financial_document.v1');
  assert.equal(result.document_type, 'cash_flow');
  assert.equal(result.reporting_basis, 'consolidated');
  assert.equal(result.source_unit, 'Rs. Cr.');
  assert.equal(result.normalized_unit, 'INR crore');
  assert.equal(result.extraction.period_count, 2);
  assert.equal(result.extraction.row_count, 7);
  assert.equal(result.extraction.cell_count, 14);
  assert.equal(result.extraction.maximum_depth, 2);
  assert.equal(result.extraction.status, 'complete');
  assert.equal(result.source.retrieved_at, NOW.toISOString());
});

test('extracts embedded JSON without clicking collapsed UI rows', async () => {
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
  const extractor = createCashFlowSnapshotExtractor({
    browserRunner: { async run(task) { return task(page); } },
    clock: () => NOW,
  });

  const result = await extractor(request('standalone'));

  assert.equal(result.reporting_basis, 'standalone');
  assert.equal(result.rows[0].values[1].source_value, '105');
  assert.equal(observed.options.waitUntil, 'commit');
  assert.equal(observed.wait.options.timeout, 20_000);
});

test('fails closed for missing basis, malformed hierarchy, and auxiliary drift', () => {
  const missing = providerTables();
  delete missing.cf_c;
  assert.throws(
    () => normalizeEmbeddedCashFlowData(missing, 'consolidated'),
    /must be an object/,
  );

  const badDepth = providerTables();
  badDepth.cf_c.data[0].sub_section[0].index = 3;
  assert.throws(
    () => normalizeEmbeddedCashFlowData(badDepth, 'consolidated'),
    /depth is inconsistent/,
  );

  const misaligned = providerTables();
  misaligned.cf_s.data[0].value_yoy.pop();
  assert.throws(
    () => normalizeEmbeddedCashFlowData(misaligned, 'standalone'),
    /must align with primary values/,
  );
});

test('rejects an unconfirmed issuer path and mismatched reporting basis', async () => {
  const extractor = createCashFlowSnapshotExtractor({
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

  const embedded = normalizeEmbeddedCashFlowData(
    providerTables(),
    'consolidated',
  );
  assert.throws(
    () => normalizeCashFlow(embedded, request('standalone'), NOW),
    /does not match/,
  );
});
