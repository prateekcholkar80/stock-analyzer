import assert from 'node:assert/strict';
import test from 'node:test';

import { createFinancialsHandler } from '../src/financials.js';


const NOW = new Date('2026-08-28T04:00:00Z');
const ISSUER = {
  exchange: 'NSE', symbol: 'TCS', legal_name: 'TCS Limited',
  provider_company_id: '123', provider_slug: 'tcs',
};
const METADATA = { company_id: 123, exchange: 'NSE', symbol: 'TCS' };

function fixture({ status = 200, metadata = METADATA, sections = [], failure } = {}) {
  const observed = { calls: 0 };
  const page = {
    async evaluate() {
      if (failure) throw failure;
      return { metadata, sections };
    },
    async goto(url) {
      observed.calls += 1;
      observed.url = url;
      return { status: () => status };
    },
    async waitForSelector() {},
  };
  return {
    observed,
    runner: { async run(task) { return task(page); } },
  };
}

function handler(provider) {
  return createFinancialsHandler({ browserRunner: provider.runner, clock: () => NOW });
}

function annualIncome() {
  return {
    statement: 'income_statement', period_type: 'annual', unit: '₹ in Cr.',
    headers: ['Mar 24', 'Mar 25'],
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
