import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createFinancialDocumentPackageExtractor,
  normalizeEmbeddedFinancialDocumentPackage,
} from '../src/financial-document-package.js';


const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'COFORGE',
  legal_name: 'Coforge Limited',
  provider_company_id: '242',
  provider_slug: 'niit-technologies-limited',
});
const RETRIEVED_AT = new Date('2026-09-11T17:50:00.000Z');


test('extracts all required financial documents from one browser snapshot', async () => {
  const tables = completeTables();
  let runs = 0;
  let navigations = 0;
  const readinessStates = [];
  const browserRunner = {
    async run(task) {
      runs += 1;
      return task({
        async goto(url, options) {
          navigations += 1;
          assert.equal(
            url,
            'https://www.tijorifinance.com/company/niit-technologies-limited/financials/',
          );
          assert.deepEqual(options, {
            timeout: 20_000,
            waitUntil: 'domcontentloaded',
          });
          return { url: () => url };
        },
        async waitForFunction(predicate) {
          let source = '{"growth":';
          globalThis.document = {
            getElementById: () => ({ textContent: source }),
          };
          try {
            readinessStates.push(predicate());
            source = JSON.stringify(tables);
            readinessStates.push(predicate());
          } finally {
            delete globalThis.document;
          }
        },
        async evaluate() {
          return tables;
        },
      });
    },
  };

  const extract = createFinancialDocumentPackageExtractor({
    browserRunner,
    clock: () => RETRIEVED_AT,
  });
  const result = await extract({ issuer: ISSUER });

  assert.equal(runs, 1);
  assert.equal(navigations, 1);
  assert.deepEqual(readinessStates, [false, true]);
  assert.equal(result.extraction.status, 'complete');
  assert.equal(result.extraction.document_count, 11);
  assert.equal(result.documents.length, 11);
  assert.equal(new Set(result.documents.map(
    ({ source }) => source.retrieved_at,
  )).size, 1);
  assert.deepEqual(
    result.documents.map(({ document_type, reporting_basis }) => (
      `${document_type}:${reporting_basis}`
    )),
    [
      'growth_table:not_applicable',
      'balance_sheet:consolidated',
      'profit_and_loss:consolidated',
      'cash_flow:consolidated',
      'ratios:consolidated',
      'quarterly_results:consolidated',
      'balance_sheet:standalone',
      'profit_and_loss:standalone',
      'cash_flow:standalone',
      'ratios:standalone',
      'quarterly_results:standalone',
    ],
  );
});

test('fails closed when either reporting basis is absent', () => {
  const tables = completeTables();
  delete tables.bs_s_s;

  assert.throws(
    () => normalizeEmbeddedFinancialDocumentPackage(
      tables,
      { issuer: ISSUER },
      RETRIEVED_AT,
    ),
    /Embedded standalone Balance Sheet/,
  );
});

function completeTables() {
  const periods = ['MAR\'25', 'MAR\'26'];
  const quarterlyPeriods = ['JUN\'25', 'SEP\'25'];
  return {
    growth: statement(periods, 'Sales Growth', [12, 14]),
    bs_c_s: statement(periods, 'Assets', [100, 120]),
    bs_s_s: statement(periods, 'Assets', [90, 105]),
    pl_c_s: mixedStatement(periods, 'Sales', [80, 95]),
    pl_s_s: mixedStatement(periods, 'Sales', [75, 88]),
    cf_c: statement(periods, 'Cash From Operations', [20, 24]),
    cf_s: statement(periods, 'Cash From Operations', [18, 21]),
    fr_c: mixedStatement(periods, 'Return on Equity %', [14, 15]),
    fr_s: mixedStatement(periods, 'Return on Equity %', [13, 14]),
    qt_c: mixedStatement(quarterlyPeriods, 'Sales', [22, 25]),
    qt_s: mixedStatement(quarterlyPeriods, 'Sales', [20, 23]),
  };
}

function statement(reportDates, name, values) {
  return {
    report_dates: reportDates,
    skip_report_dates: [],
    data: [{
      index: 1,
      name,
      value: values,
      value_yoy: values.map(() => null),
      value_perc: values.map(() => null),
      sub_section: '',
    }],
  };
}

function mixedStatement(reportDates, name, values) {
  return {
    ...statement(reportDates, name, values),
    currency_unit: 'Rs. Cr.',
  };
}
