import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createProfitAndLossSnapshotExtractor,
  normalizeEmbeddedProfitAndLossData,
  normalizeProfitAndLoss,
} from '../src/profit-and-loss.js';


const NOW = new Date('2026-08-31T06:00:00Z');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'EXAMPLE',
  legal_name: 'Example Industries Ltd.',
  provider_company_id: '1234',
  provider_slug: 'example-industries-limited',
});

function row(name, index, values, children = '', metadata = {}) {
  return {
    formula: metadata.formula ?? name.toLowerCase().replaceAll(' ', '_'),
    index,
    name,
    sub_section: children,
    value: values,
    value_yoy: metadata.value_yoy ?? values.map(() => null),
    value_perc: values.map(() => null),
    field: metadata.field ?? 'NA',
    source: metadata.source ?? 'provider',
    fs_name: metadata.fs_name ?? 'NA',
    op_id: metadata.op_id ?? 'NA',
  };
}

function statement(netProfit) {
  return {
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: ['Mar 2024'],
    data: [
      row('Sales', 1, [100, 120]),
      row('Operating Expenses', 1, [70, 80], [
        row('Company-specific input cost', 2, [10]),
      ]),
      row('OPM (%)', 1, [20, 25], '', {
        formula: '(operating_profit / sales) * 100',
        field: 'op_profit_margin',
        value_yoy: [null, 5.5],
      }),
      row('Net Profit', 1, [15, netProfit]),
      row('Number of shares(Crs)', 1, [0, 12], '', {
        formula: 'nsgrandtotal / 10000000',
        field: 'no_of_shares',
      }),
    ],
  };
}

function tables() {
  return {
    pl_c_s: statement(18),
    pl_s_s: statement(16),
  };
}

function request(reportingBasis = 'consolidated') {
  return { issuer: ISSUER, reporting_basis: reportingBasis };
}

test('selects consolidated and standalone Profit and Loss trees independently', () => {
  const consolidated = normalizeEmbeddedProfitAndLossData(tables(), 'consolidated');
  const standalone = normalizeEmbeddedProfitAndLossData(tables(), 'standalone');

  assert.equal(consolidated.rows[4].values[1].source_value, '18');
  assert.equal(standalone.rows[4].values[1].source_value, '16');
  assert.equal(consolidated.reporting_basis, 'consolidated');
  assert.equal(standalone.reporting_basis, 'standalone');
});

test('preserves arbitrary hierarchy, periods, skipped dates, zero and missing values', () => {
  const result = normalizeEmbeddedProfitAndLossData(tables(), 'consolidated');

  assert.deepEqual(
    result.periods.map(({ source_label }) => source_label),
    ['Mar 2025', 'Mar 2026'],
  );
  assert.deepEqual(result.skipped_report_dates, ['Mar 2024']);
  assert.equal(result.rows[2].original_label, 'Company-specific input cost');
  assert.equal(result.rows[2].parent_row_key, result.rows[1].row_key);
  assert.equal(result.rows[2].depth, 1);
  assert.equal(result.rows[2].values[1].source_value, null);
  assert.equal(result.rows[2].values[1].availability_status, 'unknown');
  assert.equal(result.rows[5].values[0].source_value, '0');
  assert.equal(result.rows[5].values[0].availability_status, 'available');
});

test('labels monetary, percentage and share-count rows explicitly', () => {
  const raw = normalizeEmbeddedProfitAndLossData(tables(), 'consolidated');
  const result = normalizeProfitAndLoss(raw, request(), NOW);

  assert.equal(result.document_type, 'profit_and_loss');
  assert.equal(result.rows[0].value_kind, 'monetary');
  assert.equal(result.rows[0].normalized_unit, 'INR crore');
  assert.equal(result.rows[3].value_kind, 'percentage');
  assert.equal(result.rows[3].normalized_unit, 'percent');
  assert.equal(result.rows[3].values[1].yoy_change, '5.5');
  assert.equal(result.rows[5].value_kind, 'count');
  assert.equal(result.rows[5].source_unit, 'crore shares');
  assert.equal(result.extraction.row_count, 6);
  assert.equal(result.extraction.cell_count, 12);
  assert.equal(result.extraction.maximum_depth, 1);
  assert.equal(result.extraction.status, 'complete');
});

test('extracts selected embedded basis without interacting with collapsed rows', async () => {
  const observed = {};
  const page = {
    async goto(url, options) {
      observed.options = options;
      return { url: () => url };
    },
    async waitForFunction(predicate, argument, options) {
      observed.wait = { predicate, argument, options };
    },
    async evaluate() { return tables(); },
  };
  const extractor = createProfitAndLossSnapshotExtractor({
    browserRunner: { async run(task) { return task(page); } },
    clock: () => NOW,
  });

  const result = await extractor(request('standalone'));

  assert.equal(result.reporting_basis, 'standalone');
  assert.equal(result.rows[4].values[1].source_value, '16');
  assert.equal(observed.options.waitUntil, 'commit');
  assert.equal(observed.wait.options.timeout, 20_000);
});

test('fails closed for missing basis, malformed hierarchy and misaligned auxiliary values', () => {
  const missing = tables();
  delete missing.pl_c_s;
  assert.throws(
    () => normalizeEmbeddedProfitAndLossData(missing, 'consolidated'),
    /must be an object/,
  );

  const badDepth = tables();
  badDepth.pl_c_s.data[1].sub_section[0].index = 3;
  assert.throws(
    () => normalizeEmbeddedProfitAndLossData(badDepth, 'consolidated'),
    /depth is inconsistent/,
  );

  const incomplete = tables();
  incomplete.pl_s_s.data[0].value_yoy.pop();
  assert.throws(
    () => normalizeEmbeddedProfitAndLossData(incomplete, 'standalone'),
    /must align with primary values/,
  );
});

test('rejects an unconfirmed issuer path and mismatched output basis', async () => {
  const extractor = createProfitAndLossSnapshotExtractor({
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

  const raw = normalizeEmbeddedProfitAndLossData(tables(), 'consolidated');
  assert.throws(
    () => normalizeProfitAndLoss(raw, request('standalone'), NOW),
    /does not match/,
  );
});
