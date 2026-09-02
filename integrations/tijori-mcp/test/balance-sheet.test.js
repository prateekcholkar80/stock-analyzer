import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createBalanceSheetSnapshotExtractor,
  normalizeBalanceSheet,
  normalizeEmbeddedBalanceSheetData,
} from '../src/balance-sheet.js';
import {
  normalizeEmbeddedFinancialStatementData,
  normalizeFinancialStatement,
} from '../src/financial-statement-document.js';


const NOW = new Date('2026-08-30T12:30:00Z');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'EXAMPLE',
  legal_name: 'Example Industries Ltd.',
  provider_company_id: '1234',
  provider_slug: 'example-industries-limited',
});

function providerRow(name, index, values, children = '', extras = {}) {
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
    ...extras,
  };
}

function providerTables() {
  const tree = (assetValue) => ({
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: ['Mar 2024'],
    data: [
      providerRow('Assets', 1, [100, assetValue], [
        providerRow('Current Assets', 2, [40, 50], [
          providerRow('Company-specific receivable', 3, [0, null]),
        ]),
      ]),
      providerRow('Liabilities', 1, [100, assetValue], [
        providerRow('Shareholders Funds', 2, [70, 80]),
      ]),
    ],
  });
  return {
    bs_c_s: tree(120),
    bs_s_s: tree(110),
  };
}

function request(reportingBasis = 'consolidated') {
  return { issuer: ISSUER, reporting_basis: reportingBasis };
}

test('selects consolidated and standalone trees independently', () => {
  const consolidated = normalizeEmbeddedBalanceSheetData(
    providerTables(),
    'consolidated',
  );
  const standalone = normalizeEmbeddedBalanceSheetData(
    providerTables(),
    'standalone',
  );

  assert.equal(consolidated.reporting_basis, 'consolidated');
  assert.equal(standalone.reporting_basis, 'standalone');
  assert.equal(consolidated.rows[0].values[1].source_value, '120');
  assert.equal(standalone.rows[0].values[1].source_value, '110');
});

test('recursively preserves arbitrary hierarchy, periods, zero, null, and skipped dates', () => {
  const result = normalizeEmbeddedBalanceSheetData(
    providerTables(),
    'consolidated',
  );

  assert.deepEqual(
    result.periods.map(({ source_label }) => source_label),
    ['Mar 2025', 'Mar 2026'],
  );
  assert.deepEqual(result.skipped_report_dates, ['Mar 2024']);
  assert.equal(result.rows.length, 5);
  assert.equal(result.rows[2].original_label, 'Company-specific receivable');
  assert.equal(result.rows[2].depth, 2);
  assert.equal(result.rows[2].parent_row_key, result.rows[1].row_key);
  assert.equal(result.rows[2].values[0].source_value, '0');
  assert.equal(result.rows[2].values[0].availability_status, 'available');
  assert.equal(result.rows[2].values[1].source_value, null);
  assert.equal(result.rows[2].values[1].availability_status, 'unknown');
  assert.equal(result.all_sections_expanded, true);
});

test('produces a complete labelled Balance Sheet document', () => {
  const embedded = normalizeEmbeddedBalanceSheetData(
    providerTables(),
    'consolidated',
  );
  const result = normalizeBalanceSheet(embedded, request(), NOW);

  assert.equal(result.schema_version, 'tijori.financial_document.v1');
  assert.equal(result.document_type, 'balance_sheet');
  assert.equal(result.reporting_basis, 'consolidated');
  assert.equal(result.source_unit, 'Rs. Cr.');
  assert.equal(result.normalized_unit, 'INR crore');
  assert.equal(result.extraction.period_count, 2);
  assert.equal(result.extraction.row_count, 5);
  assert.equal(result.extraction.cell_count, 10);
  assert.equal(result.extraction.maximum_depth, 2);
  assert.equal(result.extraction.status, 'complete');
  assert.equal(result.source.retrieved_at, NOW.toISOString());
});

test('extracts the selected embedded basis without clicking collapsed UI rows', async () => {
  const observed = {};
  const page = {
    async goto(url, options) {
      observed.url = url;
      observed.options = options;
      return { url: () => url };
    },
    async waitForFunction(predicate, argument, options) {
      observed.wait = { predicate, argument, options };
    },
    async evaluate(callback) {
      observed.callback = callback;
      return providerTables();
    },
  };
  const extractor = createBalanceSheetSnapshotExtractor({
    browserRunner: { async run(task) { return task(page); } },
    clock: () => NOW,
  });

  const result = await extractor(request('standalone'));

  assert.equal(result.reporting_basis, 'standalone');
  assert.equal(result.rows[0].values[1].source_value, '110');
  assert.equal(observed.options.waitUntil, 'commit');
  assert.equal(observed.wait.options.timeout, 20_000);
});

test('fails closed for missing basis, malformed hierarchy, and misaligned auxiliary values', () => {
  const missing = providerTables();
  delete missing.bs_c_s;
  assert.throws(
    () => normalizeEmbeddedBalanceSheetData(missing, 'consolidated'),
    /must be an object/,
  );

  const badDepth = providerTables();
  badDepth.bs_c_s.data[0].sub_section[0].index = 3;
  assert.throws(
    () => normalizeEmbeddedBalanceSheetData(badDepth, 'consolidated'),
    /depth is inconsistent/,
  );

  const incomplete = providerTables();
  incomplete.bs_s_s.data[0].value_yoy.pop();
  assert.throws(
    () => normalizeEmbeddedBalanceSheetData(incomplete, 'standalone'),
    /must align with primary values/,
  );
});

test('rejects an unconfirmed issuer path and mismatched output basis', async () => {
  const extractor = createBalanceSheetSnapshotExtractor({
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

  const embedded = normalizeEmbeddedBalanceSheetData(
    providerTables(),
    'consolidated',
  );
  assert.throws(
    () => normalizeBalanceSheet(embedded, request('standalone'), NOW),
    /does not match/,
  );
});

test('shared statement parser validates optional mixed-unit row metadata', () => {
  const definition = {
    documentType: 'profit_and_loss',
    displayName: 'Profit and Loss',
    basisKeys: { consolidated: 'pl_c_s', standalone: 'pl_s_s' },
    sourceUnit: 'mixed',
    normalizedUnit: 'mixed',
    classifyRow(row) {
      return row.field === 'margin'
        ? { value_kind: 'percentage', source_unit: 'percent', normalized_unit: 'percent' }
        : { value_kind: 'monetary', source_unit: 'Rs. Cr.', normalized_unit: 'INR crore' };
    },
  };
  const table = {
    report_dates: ['Mar 2026'],
    skip_report_dates: [],
    data: [providerRow('Operating Margin', 1, [22], '', { field: 'margin' })],
  };
  const raw = normalizeEmbeddedFinancialStatementData(
    { pl_c_s: table, pl_s_s: table },
    'consolidated',
    definition,
  );
  const result = normalizeFinancialStatement(
    raw,
    request(),
    NOW,
    definition,
  );

  assert.equal(result.rows[0].value_kind, 'percentage');
  assert.equal(result.rows[0].source_unit, 'percent');
  assert.equal(result.rows[0].normalized_unit, 'percent');

  const invalidDefinition = { ...definition, classifyRow: () => ({}) };
  assert.throws(
    () => normalizeEmbeddedFinancialStatementData(
      { pl_c_s: table, pl_s_s: table },
      'consolidated',
      invalidDefinition,
    ),
    /value kind is invalid/,
  );
});
