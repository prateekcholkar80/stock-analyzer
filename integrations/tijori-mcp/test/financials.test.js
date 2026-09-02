import assert from 'node:assert/strict';
import test from 'node:test';

import { createFinancialsHandler } from '../src/financials.js';


const NOW = new Date('2026-08-28T04:00:00Z');
const ISSUER = {
  exchange: 'NSE', symbol: 'TCS', legal_name: 'TCS Limited',
  provider_company_id: '123', provider_slug: 'tcs',
};
const METADATA = { company_id: 123, exchange: 'NSE', symbol: 'TCS' };

function fixture({
  status = 200,
  metadata = METADATA,
  sections = [],
  embeddedFinancialTables = null,
  failure,
} = {}) {
  const observed = { calls: 0 };
  const uniqueLocator = {
    filter() { return this; },
    async count() { return 1; },
    async click() {},
  };
  const page = {
    async evaluate(callback) {
      if (failure) throw failure;
      if (embeddedFinancialTables !== null) {
        if (callback.name === 'readEmbeddedGrowthPayload') {
          return embeddedFinancialTables.growth ?? null;
        }
        if (callback.name === 'readEmbeddedFinancialTables') {
          return embeddedFinancialTables;
        }
      }
      return { metadata, sections };
    },
    async goto(url, options) {
      observed.calls += 1;
      observed.url = url;
      observed.gotoOptions = options;
      return { status: () => status, url: () => url };
    },
    getByText(label) {
      observed.labels ??= [];
      observed.labels.push(label);
      return uniqueLocator;
    },
    locator(selector) {
      observed.growthSelector = selector;
      return { async count() { return 0; } };
    },
    async waitForSelector(selector) { observed.selector = selector; },
    async waitForFunction() {},
  };
  return {
    observed,
    runner: { async run(task) { return task(page); } },
  };
}

function embeddedGrowthTable() {
  return {
    report_dates: ['1yr', '3yr'],
    data: [{
      formula: 'sales_growth', index: 1, name: 'Sales Growth', sub_section: '',
      value: [10.5, 12.4], value_yoy: [null, null],
      value_perc: [null, null], source: 'provider', field: 'NA',
    }],
  };
}

function embeddedBalanceSheet(value) {
  return {
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: [],
    data: [{
      formula: 'assets', index: 1, name: 'Assets',
      sub_section: [{
        formula: 'cash', index: 2, name: 'Cash and Bank Balances',
        sub_section: '', value: [20, value], value_yoy: [null, '25%'],
        value_perc: ['20%', '25%'], source: 'provider', field: 'NA',
      }],
      value: [100, value * 4], value_yoy: [null, '25%'],
      value_perc: ['100%', '100%'], source: 'provider', field: 'NA',
    }],
  };
}

function embeddedProfitAndLoss(value) {
  return {
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: [],
    data: [{
      formula: 'sales', index: 1, name: 'Sales', sub_section: '',
      value: [100, value], value_yoy: [null, '20%'],
      value_perc: [null, null], source: 'provider', field: 'NA',
    }, {
      formula: 'opm_per', index: 1, name: 'OPM (%)', sub_section: '',
      value: [18, 20], value_yoy: [null, 11.11],
      value_perc: [null, null], source: 'provider', field: 'NA',
    }],
  };
}

function embeddedCashFlow(value) {
  return {
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: [],
    data: [{
      formula: 'cash_from_operation', index: 1,
      name: 'Cash from Operating Activity',
      sub_section: [{
        formula: 'working_capital', index: 2,
        name: 'Working Capital Changes', sub_section: '',
        value: [0, -12], value_yoy: [null, null],
        value_perc: [null, null], source: 'provider', field: 'NA',
      }],
      value: [100, value], value_yoy: [null, '20%'],
      value_perc: [null, null], source: 'provider', field: 'NA',
    }],
  };
}

function embeddedRatios(value) {
  return {
    report_dates: ['Mar 2025', 'Mar 2026'],
    skip_report_dates: [],
    data: [{
      formula: 'operational_ratios', index: 1,
      name: 'Operational Ratios',
      sub_section: [{
        formula: 'current_ratio', index: 2,
        name: 'Current Ratio',
        sub_section: [{
          formula: 'current_assets', index: 3,
          name: 'Current Assets (Crs)', sub_section: '',
          value: [100, 120], value_yoy: [null, '20%'],
          value_perc: [null, null], source: 'provider', field: 'NA',
        }],
        value: [1.1, value], value_yoy: [null, '5%'],
        value_perc: [null, null], source: 'provider', field: 'NA',
      }, {
        formula: 'cash_conversion_cycle', index: 2,
        name: 'Cash Conversion Cycle', sub_section: '',
        value: [-30, -20], value_yoy: [null, '33%'],
        value_perc: [null, null], source: 'provider', field: 'NA',
      }],
      value: [0, 0], value_yoy: [null, null],
      value_perc: [null, null], source: 'provider', field: 'NA',
    }, {
      formula: 'profitability_ratios', index: 1,
      name: 'Profitability Ratios',
      sub_section: [{
        formula: 'gross_margin', index: 2,
        name: 'Gross Margin (%)', sub_section: '',
        value: [35, 36], value_yoy: [null, '3%'],
        value_perc: [null, null], source: 'provider', field: 'NA',
      }],
      value: [0, 0], value_yoy: [null, null],
      value_perc: [null, null], source: 'provider', field: 'NA',
    }],
  };
}

function embeddedQuarterlyResults(value) {
  return {
    report_dates: ['Sep 2025', 'Dec 2025', 'Mar 2026', 'Jun 2026'],
    skip_report_dates: [],
    data: [{
      formula: 'net_sales', index: 1, name: 'Net Sales', sub_section: '',
      value: [100, 110, 120, value], value_yoy: ['20%', '25%'],
      value_perc: [null, null, null, null], source: 'provider', field: 'NA',
    }, {
      formula: 'quarterly_ratios', index: 1,
      name: 'Quarterly Ratios',
      sub_section: [{
        formula: 'adj_eps_abs', index: 2, name: 'EPS', sub_section: '',
        value: [2, 2.1, 2.2, 2.3], value_yoy: ['10%', '15%'],
        value_perc: [null, null, null, null], source: 'provider', field: 'NA',
      }, {
        formula: 'operating_profit_margin', index: 1,
        name: 'Operating Profit Margin', sub_section: '',
        value: [17, 18, 19, 20], value_yoy: ['5%', '6%'],
        value_perc: [null, null, null, null], source: 'derived',
        field: 'operating_profit_margin',
      }],
      value: [0, 0, 0, 0], value_yoy: [null, null, null, null],
      value_perc: [null, null, null, null], source: 'provider', field: 'NA',
    }],
  };
}

function structuredTables() {
  return {
    growth: embeddedGrowthTable(),
    bs_c_s: embeddedBalanceSheet(25),
    bs_s_s: embeddedBalanceSheet(22),
    pl_c_s: embeddedProfitAndLoss(120),
    pl_s_s: embeddedProfitAndLoss(115),
    cf_c: embeddedCashFlow(120),
    cf_s: embeddedCashFlow(105),
    fr_c: embeddedRatios(1.2),
    fr_s: embeddedRatios(0.9),
    qt_c: embeddedQuarterlyResults(130),
    qt_s: embeddedQuarterlyResults(125),
  };
}

function handler(provider) {
  return createFinancialsHandler({ browserRunner: provider.runner, clock: () => NOW });
}

function annualIncome() {
  return {
    statement: 'income_statement', period_type: 'annual', unit: '₹ in Cr.',
    headers: ["Mar'24", "Mar'25"],
    rows: [
      { metric: 'Revenue', values: ['100,000', '120,000'] },
      { metric: 'Net Profit', values: ['10,000', '12,500'] },
      { metric: 'EPS', values: ['50.25', null] },
      { metric: 'Unmapped raw field', values: ['999', '999'] },
    ],
  };
}

test('normalizes requested annual facts and preserves missing values explicitly', async () => {
  const provider = fixture({ sections: [annualIncome()] });
  const result = await handler(provider)({
    issuer: ISSUER,
    statements: ['income_statement'],
    period_types: ['annual'],
    max_periods: 1,
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.company_id, '123');
  assert.equal(result.payload.facts.length, 3);
  assert.deepEqual(
    result.payload.facts.map(({ provider_fact_id }) => provider_fact_id),
    [
      'income_statement.revenue.annual.2025-03-31',
      'income_statement.net_income.annual.2025-03-31',
      'income_statement.earnings_per_share.annual.2025-03-31',
    ],
  );
  assert.equal(result.payload.facts[0].normalized_value, '120000');
  assert.equal(result.payload.facts[0].normalized_unit, 'INR crore');
  assert.equal(result.payload.facts[2].availability_status, 'unknown');
  assert.equal(result.payload.facts[2].provider_source_id, null);
  assert.equal(JSON.stringify(result).includes('Unmapped raw field'), false);
});

test('routes a structured Growth Table request to its scenario extractor', async () => {
  const provider = fixture({ embeddedFinancialTables: structuredTables() });

  const result = await handler(provider)({
    issuer: ISSUER,
    document_type: 'growth_table',
    reporting_basis: 'not_applicable',
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.document.document_type, 'growth_table');
  assert.equal(result.payload.document.reporting_basis, 'not_applicable');
  assert.equal(result.payload.document.rows[0].original_label, 'Sales Growth');
  assert.equal(provider.observed.calls, 1);
  assert.equal(provider.observed.gotoOptions.waitUntil, 'commit');
  assert.equal(Object.hasOwn(result.payload, 'facts'), false);
});

test('fails closed when the structured Growth Table extraction is incomplete', async () => {
  const incomplete = structuredTables();
  incomplete.growth.data[0].value.pop();

  const result = await handler(fixture({ embeddedFinancialTables: incomplete }))({
    issuer: ISSUER,
    document_type: 'growth_table',
    reporting_basis: 'consolidated',
  });

  assert.equal(result.status, 'unavailable');
  assert.equal(result.payload, null);
});

test('routes Balance Sheet requests to the independently selected basis tree', async () => {
  for (const [reportingBasis, expectedCash] of [
    ['consolidated', '25'],
    ['standalone', '22'],
  ]) {
    const provider = fixture({ embeddedFinancialTables: structuredTables() });
    const result = await handler(provider)({
      issuer: ISSUER,
      document_type: 'balance_sheet',
      reporting_basis: reportingBasis,
    });

    assert.equal(result.status, 'success');
    assert.equal(result.payload.document.document_type, 'balance_sheet');
    assert.equal(result.payload.document.reporting_basis, reportingBasis);
    assert.equal(result.payload.document.rows[1].original_label, 'Cash and Bank Balances');
    assert.equal(result.payload.document.rows[1].values[1].source_value, expectedCash);
    assert.equal(result.payload.document.extraction.status, 'complete');
    assert.equal(provider.observed.calls, 1);
    assert.equal(provider.observed.gotoOptions.waitUntil, 'commit');
  }
});

test('routes Profit and Loss requests to the independently selected basis tree', async () => {
  for (const [reportingBasis, expectedSales] of [
    ['consolidated', '120'],
    ['standalone', '115'],
  ]) {
    const provider = fixture({ embeddedFinancialTables: structuredTables() });
    const result = await handler(provider)({
      issuer: ISSUER,
      document_type: 'profit_and_loss',
      reporting_basis: reportingBasis,
    });

    assert.equal(result.status, 'success');
    assert.equal(result.payload.document.document_type, 'profit_and_loss');
    assert.equal(result.payload.document.reporting_basis, reportingBasis);
    assert.equal(result.payload.document.rows[0].original_label, 'Sales');
    assert.equal(result.payload.document.rows[0].values[1].source_value, expectedSales);
    assert.equal(result.payload.document.rows[1].value_kind, 'percentage');
    assert.equal(result.payload.document.extraction.status, 'complete');
    assert.equal(provider.observed.calls, 1);
    assert.equal(provider.observed.gotoOptions.waitUntil, 'commit');
  }
});

test('routes Cash Flow requests to the independently selected basis tree', async () => {
  for (const [reportingBasis, expectedOperatingCash] of [
    ['consolidated', '120'],
    ['standalone', '105'],
  ]) {
    const provider = fixture({ embeddedFinancialTables: structuredTables() });
    const result = await handler(provider)({
      issuer: ISSUER,
      document_type: 'cash_flow',
      reporting_basis: reportingBasis,
    });

    assert.equal(result.status, 'success');
    assert.equal(result.payload.document.document_type, 'cash_flow');
    assert.equal(result.payload.document.reporting_basis, reportingBasis);
    assert.equal(
      result.payload.document.rows[0].values[1].source_value,
      expectedOperatingCash,
    );
    assert.equal(result.payload.document.rows[1].parent_row_key, 'cash_from_operating_activity');
    assert.equal(result.payload.document.rows[1].values[1].source_value, '-12');
    assert.equal(result.payload.document.extraction.status, 'complete');
    assert.equal(provider.observed.calls, 1);
    assert.equal(provider.observed.gotoOptions.waitUntil, 'commit');
  }
});

test('routes Ratios requests with independently selected mixed-unit trees', async () => {
  for (const [reportingBasis, expectedCurrentRatio] of [
    ['consolidated', '1.2'],
    ['standalone', '0.9'],
  ]) {
    const provider = fixture({ embeddedFinancialTables: structuredTables() });
    const result = await handler(provider)({
      issuer: ISSUER,
      document_type: 'ratios',
      reporting_basis: reportingBasis,
    });

    assert.equal(result.status, 'success');
    assert.equal(result.payload.document.document_type, 'ratios');
    assert.equal(result.payload.document.reporting_basis, reportingBasis);
    assert.equal(
      result.payload.document.rows[1].values[1].source_value,
      expectedCurrentRatio,
    );
    assert.equal(result.payload.document.rows[1].value_kind, 'ratio');
    assert.equal(result.payload.document.rows[2].value_kind, 'monetary');
    assert.equal(result.payload.document.rows[3].source_unit, 'days');
    assert.equal(result.payload.document.rows[5].value_kind, 'percentage');
    assert.equal(result.payload.document.extraction.status, 'complete');
    assert.equal(provider.observed.calls, 1);
    assert.equal(provider.observed.gotoOptions.waitUntil, 'commit');
  }
});

test('routes Quarterly Results requests to the independently selected basis tree', async () => {
  for (const [reportingBasis, expectedSales] of [
    ['consolidated', '130'],
    ['standalone', '125'],
  ]) {
    const provider = fixture({ embeddedFinancialTables: structuredTables() });
    const result = await handler(provider)({
      issuer: ISSUER,
      document_type: 'quarterly_results',
      reporting_basis: reportingBasis,
    });

    assert.equal(result.status, 'success');
    assert.equal(result.payload.document.document_type, 'quarterly_results');
    assert.equal(result.payload.document.reporting_basis, reportingBasis);
    assert.equal(result.payload.document.rows[0].values[3].source_value, expectedSales);
    assert.deepEqual(
      result.payload.document.rows[0].values.map(({ yoy_change }) => yoy_change),
      [null, null, '20%', '25%'],
    );
    assert.equal(result.payload.document.rows[2].value_kind, 'per_share');
    assert.equal(result.payload.document.rows[3].value_kind, 'percentage');
    assert.equal(result.payload.document.extraction.status, 'complete');
    assert.equal(provider.observed.calls, 1);
    assert.equal(provider.observed.gotoOptions.waitUntil, 'commit');
  }
});

test('waits for current direct financial table IDs before extraction', async () => {
  const provider = fixture({ sections: [annualIncome()] });

  const result = await handler(provider)({ issuer: ISSUER });

  assert.equal(result.status, 'success');
  assert.match(provider.observed.selector, /#profit_and_loss_table/);
  assert.match(provider.observed.selector, /#balance_sheet_table/);
  assert.match(provider.observed.selector, /#cash_flow_table/);
  assert.match(provider.observed.selector, /#quarterly_results_table/);
});

test('supports quarterly income periods and sorts the latest periods first', async () => {
  const section = {
    statement: 'income_statement', period_type: 'quarterly', unit: 'INR Crore',
    headers: ['Q4 FY25', 'Q1 FY26', 'Q2 FY26'],
    rows: [{ metric: 'Sales', values: ['25', '30', '35'] }],
  };
  const result = await handler(fixture({ sections: [section] }))({
    issuer: ISSUER,
    statements: ['income_statement'],
    period_types: ['quarterly'],
    max_periods: 2,
  });

  assert.deepEqual(
    result.payload.facts.map(({ period_end }) => period_end),
    ['2025-09-30', '2025-06-30'],
  );
  assert.deepEqual(
    result.payload.facts.map(({ normalized_value }) => normalized_value),
    ['35', '30'],
  );
});

test('omits ambiguous duplicate line-item aliases without selecting a row arbitrarily', async () => {
  const section = annualIncome();
  section.rows.push({ metric: 'Sales', values: ['101,000', '121,000'] });

  const result = await handler(fixture({ sections: [section] }))({ issuer: ISSUER });

  assert.equal(result.status, 'success');
  assert.equal(
    result.payload.facts.some(({ line_item_id }) => line_item_id === 'income_statement.revenue'),
    false,
  );
  assert.match(result.payload.limitations.at(-1), /ambiguous duplicated financial line item/i);
});

test('omits unsupported request dimensions and records bounded limitations', async () => {
  const result = await handler(fixture({ sections: [annualIncome()] }))({
    issuer: ISSUER,
    statements: ['income_statement', 'segment'],
    period_types: ['annual', 'ltm'],
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.limitations.some((item) => item.includes('segment')), true);
  assert.equal(result.payload.limitations.some((item) => item.includes('ltm')), true);

  const unsupportedOnly = await handler(fixture())({
    issuer: ISSUER,
    statements: ['segment'],
    period_types: ['ltm'],
  });
  assert.equal(unsupportedOnly.status, 'unavailable');
});

test('retains a resolved issuer exchange when financial metadata omits it', async () => {
  const result = await handler(fixture({
    metadata: { company_id: 123, symbol: 'TCS' },
    sections: [annualIncome()],
  }))({ issuer: ISSUER });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.exchange, 'NSE');
  assert.match(result.payload.limitations.at(-1), /omitted its exchange field/i);
});

test('retains the resolved issuer ID when the financial page uses another internal ID', async () => {
  const result = await handler(fixture({
    metadata: { company_id: 999, exchange: 'NSE', symbol: 'TCS' },
    sections: [annualIncome()],
  }))({ issuer: ISSUER });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.company_id, ISSUER.provider_company_id);
  assert.match(result.payload.limitations.at(-1), /provider-internal company ID/i);
});

test('rejects historical cutoffs, missing slugs, and conflicting identities', async () => {
  const provider = fixture({ sections: [annualIncome()] });
  const getFinancials = handler(provider);
  assert.equal((await getFinancials({
    issuer: ISSUER, as_of_date: '2026-08-27',
  })).status, 'unavailable');
  assert.equal((await getFinancials({
    issuer: { ...ISSUER, provider_slug: null },
  })).status, 'unavailable');
  assert.equal(provider.observed.calls, 0);

  const mismatch = await handler(fixture({
    metadata: { ...METADATA, symbol: 'INFY' }, sections: [annualIncome()],
  }))({ issuer: ISSUER });
  assert.equal(mismatch.status, 'unavailable');

  const exchangeMismatch = await handler(fixture({
    metadata: { ...METADATA, exchange: 'BSE' }, sections: [annualIncome()],
  }))({ issuer: ISSUER });
  assert.equal(exchangeMismatch.status, 'unavailable');
});

test('fails closed for missing units, malformed periods, rows, and values', async () => {
  const invalidSections = [
    { ...annualIncome(), unit: null },
    { ...annualIncome(), headers: ['Unknown period'] },
    { ...annualIncome(), rows: Array.from({ length: 301 }, () => ({ metric: 'Revenue', values: ['1', '2'] })) },
    { ...annualIncome(), rows: [{ metric: 'Revenue', values: ['1', 'not numeric'] }] },
  ];
  for (const section of invalidSections) {
    const result = await handler(fixture({ sections: [section] }))({ issuer: ISSUER });
    assert.equal(result.status, 'unavailable');
  }
});

test('maps provider statuses and sanitizes browser errors', async () => {
  const expectations = new Map([
    [401, 'authentication_required'], [403, 'authentication_required'],
    [402, 'paywalled'], [404, 'not_found'], [429, 'rate_limited'],
    [503, 'unavailable'],
  ]);
  for (const [status, expected] of expectations) {
    assert.equal((await handler(fixture({ status }))({ issuer: ISSUER })).status, expected);
  }
  const secret = 'Bearer financial-secret';
  const failed = await handler(fixture({ failure: new Error(secret) }))({ issuer: ISSUER });
  assert.equal(failed.status, 'unavailable');
  assert.equal(JSON.stringify(failed).includes(secret), false);
});

test('validates dependencies, arguments, and clock output', async () => {
  assert.throws(() => createFinancialsHandler({ browserRunner: null }), /browser runner/);
  assert.throws(
    () => createFinancialsHandler({ browserRunner: fixture().runner, clock: null }),
    /clock must be callable/,
  );
  const badClock = createFinancialsHandler({
    browserRunner: fixture().runner, clock: () => new Date('invalid'),
  });
  assert.equal((await badClock({ issuer: ISSUER })).status, 'unavailable');
  assert.equal((await handler(fixture())({
    issuer: ISSUER, authorization: 'Bearer forbidden',
  })).status, 'unavailable');
});
