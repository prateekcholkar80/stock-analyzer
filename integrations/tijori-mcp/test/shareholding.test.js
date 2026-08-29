import assert from 'node:assert/strict';
import test from 'node:test';

import { createShareholdingHandler } from '../src/shareholding.js';


const NOW = new Date('2026-08-28T04:00:00Z');
const ISSUER = {
  exchange: 'NSE', symbol: 'TCS', legal_name: 'Tata Consultancy Services Limited',
  provider_company_id: '123', provider_slug: 'tata-consultancy-services',
};
const METADATA = { company_id: 123, exchange: 'NSE', symbol: 'TCS' };

function fixture({ status = 200, metadata = METADATA, headers = [], rows = [], failure } = {}) {
  const observed = { calls: 0 };
  const page = {
    async evaluate() {
      if (failure) throw failure;
      return { metadata, headers, rows };
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
  return createShareholdingHandler({ browserRunner: provider.runner, clock: () => NOW });
}

function history() {
  return {
    headers: ['Category', 'Mar 25', 'Jun 25', 'Sep 25'],
    rows: [
      { category: 'Promoter and Promoter Group', values: ['71.74%', '71.74%', '71.74%'] },
      { category: 'FII', values: ['12.70', '12.50', '12.20'] },
      { category: 'DII', values: ['10.20', '10.50', '10.80'] },
      { category: 'Public', values: ['5.36', '5.26', '5.26'] },
      { category: 'Promoter Pledge', values: ['0', null, '0'] },
      { category: 'Unmapped category', values: ['99', '99', '99'] },
    ],
  };
}

test('normalizes latest quarterly ownership facts without inferring categories', async () => {
  const provider = fixture(history());
  const result = await handler(provider)({ issuer: ISSUER, quarters: 1 });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.company_id, '123');
  assert.equal(result.payload.facts.length, 5);
  assert.deepEqual(
    result.payload.facts.map(({ provider_fact_id }) => provider_fact_id),
    [
      'ownership.promoter_holding.quarterly.2025-09-30',
      'ownership.foreign_institutional_holding.quarterly.2025-09-30',
      'ownership.domestic_institutional_holding.quarterly.2025-09-30',
      'ownership.public_holding.quarterly.2025-09-30',
      'ownership.promoter_pledge.quarterly.2025-09-30',
    ],
  );
  assert.equal(result.payload.facts[0].normalized_value, '71.74');
  assert.equal(result.payload.facts[0].normalized_unit, 'percent');
  assert.equal(JSON.stringify(result).includes('Unmapped category'), false);
  assert.equal(provider.observed.url.endsWith('/shareholding/'), true);
});

test('sorts fiscal-quarter labels and applies the requested quarter bound', async () => {
  const result = await handler(fixture({
    headers: ['Category', 'Q4 FY25', 'Q1 FY26', 'Q2 FY26'],
    rows: [{ category: 'Public', values: ['5.1', '5.2', '5.3'] }],
  }))({ issuer: ISSUER, quarters: 2, include_promoter_pledge: false });

  assert.equal(result.status, 'success');
  assert.deepEqual(
    result.payload.facts.map(({ period_end, normalized_value }) => [period_end, normalized_value]),
    [['2025-09-30', '5.3'], ['2025-06-30', '5.2']],
  );
});

test('preserves requested but undisclosed promoter pledge as unknown evidence', async () => {
  const source = history();
  source.rows = source.rows.filter(({ category }) => category !== 'Promoter Pledge');
  const result = await handler(fixture(source))({ issuer: ISSUER, quarters: 1 });

  assert.equal(result.status, 'success');
  const pledge = result.payload.facts.find(({ line_item_id }) => (
    line_item_id === 'ownership.promoter_pledge'
  ));
  assert.equal(pledge.availability_status, 'unknown');
  assert.equal(pledge.normalized_value, null);
  assert.equal(pledge.provider_source_id, null);
  assert.equal(result.payload.limitations.some((value) => value.includes('not displayed')), true);

  const omitted = await handler(fixture(source))({
    issuer: ISSUER, quarters: 1, include_promoter_pledge: false,
  });
  assert.equal(
    omitted.payload.facts.some(({ line_item_id }) => line_item_id === 'ownership.promoter_pledge'),
    false,
  );
});

test('rejects historical cutoffs, missing slugs, and conflicting identities', async () => {
  const provider = fixture(history());
  const getShareholding = handler(provider);
  assert.equal((await getShareholding({
    issuer: ISSUER, as_of_date: '2026-08-27',
  })).status, 'unavailable');
  assert.equal((await getShareholding({
    issuer: { ...ISSUER, provider_slug: null },
  })).status, 'unavailable');
  assert.equal(provider.observed.calls, 0);

  const mismatch = await handler(fixture({
    ...history(), metadata: { ...METADATA, symbol: 'INFY' },
  }))({ issuer: ISSUER });
  assert.equal(mismatch.status, 'unavailable');
});

test('fails closed for malformed periods, duplicate categories, rows, and values', async () => {
  const malformed = [
    { headers: ['Category', 'Unknown'], rows: [{ category: 'Public', values: ['5'] }] },
    {
      headers: ['Category', 'Mar 25'],
      rows: [
        { category: 'Public', values: ['5'] },
        { category: 'Public holding', values: ['5'] },
      ],
    },
    {
      headers: ['Category', 'Mar 25'],
      rows: Array.from({ length: 101 }, () => ({ category: 'Unknown', values: ['1'] })),
    },
    { headers: ['Category', 'Mar 25'], rows: [{ category: 'Public', values: ['101'] }] },
    { headers: ['Category', 'Mar 25'], rows: [{ category: 'Unknown', values: ['5'] }] },
  ];
  for (const value of malformed) {
    assert.equal((await handler(fixture(value))({ issuer: ISSUER })).status, 'unavailable');
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
  const secret = 'Bearer shareholding-secret';
  const failed = await handler(fixture({ failure: new Error(secret) }))({ issuer: ISSUER });
  assert.equal(failed.status, 'unavailable');
  assert.equal(JSON.stringify(failed).includes(secret), false);
});

test('validates dependencies, arguments, and clock output', async () => {
  assert.throws(() => createShareholdingHandler({ browserRunner: null }), /browser runner/);
  assert.throws(
    () => createShareholdingHandler({ browserRunner: fixture().runner, clock: null }),
    /clock must be callable/,
  );
  const badClock = createShareholdingHandler({
    browserRunner: fixture().runner, clock: () => new Date('invalid'),
  });
  assert.equal((await badClock({ issuer: ISSUER })).status, 'unavailable');
  assert.equal((await handler(fixture())({
    issuer: ISSUER, authorization: 'Bearer forbidden',
  })).status, 'unavailable');
});
