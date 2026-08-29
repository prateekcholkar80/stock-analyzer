import assert from 'node:assert/strict';
import test from 'node:test';

import { createCompanyOverviewHandler } from '../src/company-overview.js';


const NOW = new Date('2026-08-28T03:30:00.000Z');
const ISSUER = {
  exchange: 'NSE',
  symbol: 'TCS',
  legal_name: 'Tata Consultancy Services Limited',
  provider_company_id: '123',
  provider_slug: 'tata-consultancy-services',
};

function fixture({ status = 200, metadata, ratios, failure } = {}) {
  const observed = { calls: 0, selectors: [] };
  const page = {
    async evaluate() {
      if (failure) throw failure;
      return {
        metadata: metadata ?? {
          company_id: 123,
          exchange: 'nse',
          symbol: 'tcs',
        },
        ratios: ratios ?? [],
      };
    },
    async goto(url, options) {
      observed.calls += 1;
      observed.url = url;
      observed.options = options;
      return { status: () => status };
    },
    async waitForSelector(selector, options) {
      observed.selectors.push({ selector, options });
    },
  };
  return {
    observed,
    runner: {
      async run(task) {
        if (failure?.stage === 'runner') throw failure;
        return task(page);
      },
    },
  };
}

function handlerFor(provider) {
  return createCompanyOverviewHandler({
    browserRunner: provider.runner,
    clock: () => NOW,
  });
}

test('normalizes only approved overview facts with research-grade provenance', async () => {
  const provider = fixture({
    ratios: [
      { label: 'Market Cap', value: '₹52,028 Cr.' },
      { label: 'P/E', value: '30.45' },
      { label: 'ROE', value: '18.25%' },
      { label: 'ROCE', value: '22.10 %' },
      { label: 'Unmapped secret ratio', value: '999' },
    ],
  });
  const result = await handlerFor(provider)({ issuer: ISSUER });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.company_id, '123');
  assert.equal(result.payload.exchange, 'NSE');
  assert.equal(result.payload.symbol, 'TCS');
  assert.deepEqual(
    result.payload.facts.map(({ provider_fact_id }) => provider_fact_id),
    [
      'overview.market_cap',
      'overview.pe_ratio',
      'overview.return_on_equity',
      'overview.return_on_capital_employed',
    ],
  );
  assert.deepEqual(
    result.payload.facts.map(({ normalized_value }) => normalized_value),
    ['52028', '30.45', '18.25', '22.10'],
  );
  assert.equal(result.payload.sources[0].as_of_date, '2026-08-28');
  assert.equal(result.payload.sources[0].location.includes('?'), false);
  assert.equal(result.payload.limitations.length, 2);
  assert.equal(JSON.stringify(result).includes('Unmapped secret ratio'), false);
  assert.equal(provider.observed.calls, 1);
});

test('accepts only today as an explicit as-of date', async () => {
  const provider = fixture({ ratios: [{ label: 'P/E', value: '30' }] });
  const handler = handlerFor(provider);

  assert.equal((await handler({ issuer: ISSUER, as_of_date: '2026-08-28' })).status, 'success');
  assert.equal((await handler({ issuer: ISSUER, as_of_date: '2026-08-27' })).status, 'unavailable');
  assert.equal(provider.observed.calls, 1);
});

test('rejects incomplete or conflicting provider identity', async () => {
  for (const metadata of [
    { company_id: 123, symbol: 'TCS' },
    { company_id: 123, exchange: 'BSE', symbol: 'TCS' },
    { company_id: 999, exchange: 'NSE', symbol: 'TCS' },
    { company_id: 123, exchange: 'NSE', symbol: 'INFY' },
  ]) {
    const provider = fixture({ metadata, ratios: [{ label: 'P/E', value: '30' }] });
    const result = await handlerFor(provider)({ issuer: ISSUER });
    assert.equal(result.status, 'unavailable');
  }
});

test('fails closed when supported values are malformed or duplicated inconsistently', async () => {
  for (const ratios of [
    [{ label: 'Unknown', value: '10' }],
    [{ label: 'Market Cap', value: '₹1.2 Lakh Cr.' }],
    [{ label: 'ROE', value: 'not available' }],
    [{ label: 'P/E', value: '10' }, { label: 'PE', value: '11' }],
    Array.from({ length: 101 }, () => ({ label: 'P/E', value: '10' })),
  ]) {
    const provider = fixture({ ratios });
    const result = await handlerFor(provider)({ issuer: ISSUER });
    assert.equal(result.status, 'unavailable');
  }
});

test('maps provider response failures to canonical statuses', async () => {
  const expectations = new Map([
    [401, 'authentication_required'],
    [403, 'authentication_required'],
    [402, 'paywalled'],
    [404, 'not_found'],
    [429, 'rate_limited'],
    [503, 'unavailable'],
  ]);
  for (const [status, expected] of expectations) {
    const result = await handlerFor(fixture({ status }))({ issuer: ISSUER });
    assert.equal(result.status, expected);
    assert.equal(result.payload, null);
  }
});

test('requires a provider slug and sanitizes browser or parser failures', async () => {
  const noSlug = await handlerFor(fixture())({
    issuer: { ...ISSUER, provider_slug: null },
  });
  assert.equal(noSlug.status, 'unavailable');

  const secret = 'Bearer secret-provider-value';
  const failedProvider = fixture({ failure: Object.assign(new Error(secret), { stage: 'runner' }) });
  const failed = await handlerFor(failedProvider)({ issuer: ISSUER });
  assert.equal(failed.status, 'unavailable');
  assert.equal(JSON.stringify(failed).includes(secret), false);
});

test('validates dependencies, arguments, and clock output', async () => {
  assert.throws(
    () => createCompanyOverviewHandler({ browserRunner: null }),
    /browser runner/,
  );
  assert.throws(
    () => createCompanyOverviewHandler({ browserRunner: fixture().runner, clock: null }),
    /clock must be callable/,
  );

  const invalidClock = createCompanyOverviewHandler({
    browserRunner: fixture().runner,
    clock: () => new Date('invalid'),
  });
  assert.equal((await invalidClock({ issuer: ISSUER })).status, 'unavailable');

  const invalidArguments = await handlerFor(fixture())({
    issuer: ISSUER,
    authorization: 'Bearer forbidden',
  });
  assert.equal(invalidArguments.status, 'unavailable');
});
