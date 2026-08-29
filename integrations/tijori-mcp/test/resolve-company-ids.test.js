import assert from 'node:assert/strict';
import test from 'node:test';

import { createResolveCompanyIdsHandler } from '../src/resolve-company-ids.js';


const tcs = {
  company_id: 123,
  exchange: 'nse',
  isin: 'ine467b01029',
  legal_name: 'Tata Consultancy Services Limited',
  symbol: 'tcs',
};

function response(status, payload) {
  const body = Buffer.from(
    typeof payload === 'string' ? payload : JSON.stringify(payload),
  );
  return {
    async body() { return body; },
    async headers() { return { 'content-length': String(body.length) }; },
    status() { return status; },
  };
}

function fixture({ searchPayload = [], metadata = {}, pageStatuses = {} } = {}) {
  const observed = { navigations: [] };
  const page = {
    slug: null,
    async evaluate() {
      return metadata[this.slug] ?? null;
    },
    async goto(url) {
      observed.navigations.push(url);
      const match = url.match(/\/company\/([a-z0-9-]+)\/$/);
      if (match) {
        this.slug = match[1];
        return response(pageStatuses[this.slug] ?? 200, 'company page');
      }
      return response(200, searchPayload);
    },
  };
  return {
    observed,
    runner: { async run(task) { return task(page); } },
  };
}

test('resolves a provider slug from complete company-page metadata', async () => {
  const provider = fixture({ metadata: { 'tata-consultancy-services': tcs } });
  const handler = createResolveCompanyIdsHandler({ browserRunner: provider.runner });

  const result = await handler({
    locator: {
      exchange: 'NSE',
      symbol: 'TCS',
      provider_slug: 'tata-consultancy-services',
    },
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.status, 'resolved');
  assert.equal(result.payload.selected_company_id, '123');
  assert.deepEqual(result.payload.companies[0].matched_on, ['symbol', 'exchange']);
  assert.equal(provider.observed.navigations.length, 1);
});

test('resolves an exact symbol through bounded company search', async () => {
  const provider = fixture({
    searchPayload: [{
      name: 'TCS',
      slug: 'tata-consultancy-services',
      type: 'companies',
    }],
    metadata: { 'tata-consultancy-services': tcs },
  });
  const handler = createResolveCompanyIdsHandler({ browserRunner: provider.runner });

  const result = await handler({
    locator: { exchange: 'nse', symbol: 'tcs' },
    max_candidates: 10,
  });

  assert.equal(result.payload.status, 'resolved');
  assert.equal(result.payload.companies[0].symbol, 'TCS');
  assert.equal(result.payload.selected_company_id, '123');
  assert.equal(provider.observed.navigations.length, 2);
});

test('preserves ambiguity when multiple candidates satisfy every hint', async () => {
  const provider = fixture({
    searchPayload: [
      { name: 'Alpha', slug: 'alpha-one', type: 'companies' },
      { name: 'Alpha', slug: 'alpha-two', type: 'companies' },
    ],
    metadata: {
      'alpha-one': {
        ...tcs,
        company_id: 1,
        legal_name: 'Alpha Limited',
        symbol: 'ALPHA',
        isin: null,
      },
      'alpha-two': {
        ...tcs,
        company_id: 2,
        exchange: 'BSE',
        legal_name: 'Alpha Limited',
        symbol: 'ALPHA',
        isin: null,
      },
    },
  });
  const result = await createResolveCompanyIdsHandler({
    browserRunner: provider.runner,
  })({ locator: { symbol: 'ALPHA' } });

  assert.equal(result.payload.status, 'ambiguous');
  assert.equal(result.payload.companies.length, 2);
  assert.equal(result.payload.selected_company_id, null);
});

test('requires every supplied identity hint to agree', async () => {
  const provider = fixture({ metadata: { 'tata-consultancy-services': tcs } });
  const handler = createResolveCompanyIdsHandler({ browserRunner: provider.runner });

  const result = await handler({
    locator: {
      exchange: 'BSE',
      symbol: 'TCS',
      provider_slug: 'tata-consultancy-services',
    },
  });

  assert.equal(result.status, 'not_found');
  assert.equal(result.payload, null);
});

test('fails closed for provider-id-only requests and incomplete metadata', async () => {
  const provider = fixture({
    metadata: { tcs: { company_id: 123, symbol: 'TCS', legal_name: 'TCS' } },
  });
  const handler = createResolveCompanyIdsHandler({ browserRunner: provider.runner });

  const idOnly = await handler({ locator: { provider_company_id: '123' } });
  assert.equal(idOnly.status, 'unavailable');
  assert.equal(provider.observed.navigations.length, 0);

  const incomplete = await handler({ locator: { provider_slug: 'tcs' } });
  assert.equal(incomplete.status, 'unavailable');
});

test('maps direct-page provider failures to canonical statuses', async () => {
  const expectations = new Map([
    [401, 'authentication_required'],
    [403, 'authentication_required'],
    [402, 'paywalled'],
    [404, 'not_found'],
    [429, 'rate_limited'],
    [503, 'unavailable'],
  ]);
  for (const [status, expected] of expectations) {
    const provider = fixture({ pageStatuses: { tcs: status } });
    const result = await createResolveCompanyIdsHandler({
      browserRunner: provider.runner,
    })({ locator: { provider_slug: 'tcs' } });
    assert.equal(result.status, expected);
  }
});

test('sanitizes invalid arguments and browser failures', async () => {
  assert.throws(
    () => createResolveCompanyIdsHandler({ browserRunner: null }),
    /browser runner/,
  );
  const secret = 'Bearer must-not-escape';
  const handler = createResolveCompanyIdsHandler({
    browserRunner: { async run() { throw new Error(secret); } },
  });
  const browserFailure = await handler({ locator: { provider_slug: 'tcs' } });
  assert.equal(browserFailure.status, 'unavailable');
  assert.equal(JSON.stringify(browserFailure).includes(secret), false);

  const invalid = await handler({
    locator: { symbol: 'TCS' },
    cookie: secret,
  });
  assert.equal(invalid.status, 'unavailable');
  assert.equal(JSON.stringify(invalid).includes(secret), false);
});
