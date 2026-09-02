import assert from 'node:assert/strict';
import test from 'node:test';

import { createSearchCompanyHandler } from '../src/search-company.js';


function response(status, payload, headers = {}) {
  const body = Buffer.from(
    typeof payload === 'string' ? payload : JSON.stringify(payload),
  );
  return {
    async body() {
      return body;
    },
    headers() {
      return { 'content-length': String(body.length), ...headers };
    },
    status() {
      return status;
    },
  };
}

function fixture({ searchPayload, searchStatus = 200, metadata = {} }) {
  const observed = { navigations: [] };
  const page = {
    currentSlug: null,
    async evaluate() {
      return metadata[this.currentSlug] ?? null;
    },
    async goto(url, options) {
      observed.navigations.push({ url, options });
      const match = url.match(/\/company\/([a-z0-9-]+)\/$/);
      if (match) {
        this.currentSlug = match[1];
        return response(200, 'company page');
      }
      return response(searchStatus, searchPayload);
    },
  };
  return {
    observed,
    runner: { async run(task) { return task(page); } },
  };
}

const tcsMetadata = {
  company_id: 123,
  exchange: 'nse',
  isin: 'ine467b01029',
  legal_name: 'Tata Consultancy Services Limited',
  symbol: 'tcs',
};

test('returns bounded provider-derived company records', async () => {
  const provider = fixture({
    searchPayload: [
      { name: 'TCS', slug: 'tata-consultancy-services', type: 'companies' },
      { name: 'IT Services', slug: 'it-services', type: 'sector' },
    ],
    metadata: { 'tata-consultancy-services': tcsMetadata },
  });
  const handler = createSearchCompanyHandler({ browserRunner: provider.runner });

  const result = await handler({ query: 'TCS', exchanges: ['NSE'], max_results: 10 });

  assert.deepEqual(result, {
    payload: {
      companies: [{
        company_id: '123',
        slug: 'tata-consultancy-services',
        legal_name: 'Tata Consultancy Services Limited',
        exchange: 'NSE',
        symbol: 'TCS',
        isin: 'INE467B01029',
        match_kind: 'exact_symbol',
        match_score: 1,
        matched_on: ['symbol'],
      }],
    },
    status: 'success',
    tool_name: 'search_company',
  });
  assert.equal(
    provider.observed.navigations[0].url,
    'https://www.tijorifinance.com/api/v1/ind/company_search/?q=TCS',
  );
  assert.equal(Object.isFrozen(result.payload.companies[0]), true);
});

test('uses one explicit exchange constraint when provider metadata omits it', async () => {
  const provider = fixture({
    searchPayload: [
      { name: 'First', slug: 'first-company', type: 'companies' },
      { name: 'Second', slug: 'second-company', type: 'companies' },
      { name: 'Third', slug: 'third-company', type: 'companies' },
    ],
    metadata: {
      'first-company': { ...tcsMetadata, company_id: 1, exchange: 'BSE' },
      'second-company': {
        ...tcsMetadata,
        company_id: 2,
        exchange: undefined,
        symbol: 'TCS2',
      },
      'third-company': { ...tcsMetadata, company_id: 3, symbol: 'TCS3' },
    },
  });
  const handler = createSearchCompanyHandler({ browserRunner: provider.runner });

  const result = await handler({ query: 'TCS', exchanges: ['NSE'], max_results: 1 });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.companies.length, 1);
  assert.equal(result.payload.companies[0].company_id, '2');
  assert.equal(result.payload.companies[0].exchange, 'NSE');
  assert.deepEqual(
    result.payload.companies[0].matched_on,
    ['provider_search', 'legal_name', 'requested_exchange'],
  );
  assert.equal(provider.observed.navigations.length, 3);
});

test('distinguishes no results from unusable provider identity metadata', async () => {
  const empty = fixture({ searchPayload: [] });
  const emptyResult = await createSearchCompanyHandler({
    browserRunner: empty.runner,
  })({ query: 'Missing' });
  assert.equal(emptyResult.status, 'not_found');

  const incomplete = fixture({
    searchPayload: [{ name: 'TCS', slug: 'tcs', type: 'companies' }],
    metadata: { tcs: { company_id: 1, legal_name: 'TCS', symbol: 'TCS' } },
  });
  const incompleteResult = await createSearchCompanyHandler({
    browserRunner: incomplete.runner,
  })({ query: 'TCS' });
  assert.equal(incompleteResult.status, 'unavailable');
  assert.equal(incompleteResult.payload, null);

  const ambiguousExchange = await createSearchCompanyHandler({
    browserRunner: incomplete.runner,
  })({ query: 'TCS', exchanges: ['NSE', 'BSE'] });
  assert.equal(ambiguousExchange.status, 'unavailable');
  assert.equal(ambiguousExchange.payload, null);
});

test('drops malformed rows, duplicate slugs, and duplicate identities', async () => {
  const provider = fixture({
    searchPayload: [
      null,
      { slug: '../escape', type: 'companies' },
      { slug: 'tcs', type: 'sector' },
      { slug: 'tcs', type: 'companies' },
      { slug: 'TCS', type: 'companies' },
      { slug: 'tcs-copy', type: 'companies' },
    ],
    metadata: { tcs: tcsMetadata, 'tcs-copy': tcsMetadata },
  });
  const result = await createSearchCompanyHandler({
    browserRunner: provider.runner,
  })({ query: 'TCS' });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.companies.length, 1);
  assert.equal(provider.observed.navigations.length, 3);
});

test('maps provider statuses without releasing response bodies', async () => {
  const expectations = new Map([
    [401, 'authentication_required'],
    [403, 'authentication_required'],
    [402, 'paywalled'],
    [404, 'not_found'],
    [429, 'rate_limited'],
    [503, 'unavailable'],
  ]);
  for (const [status, expected] of expectations) {
    const secret = 'Bearer must-not-escape';
    const provider = fixture({ searchPayload: secret, searchStatus: status });
    const result = await createSearchCompanyHandler({
      browserRunner: provider.runner,
    })({ query: 'TCS' });
    assert.equal(result.status, expected);
    assert.equal(JSON.stringify(result).includes(secret), false);
  }
});

test('fails closed for malformed and oversized search responses', async () => {
  for (const searchPayload of [
    '<html>not json</html>',
    { companies: [] },
    Array.from({ length: 101 }, (_, index) => ({
      name: `Company ${index}`,
      slug: `company-${index}`,
      type: 'companies',
    })),
    'x'.repeat(256_001),
  ]) {
    const provider = fixture({ searchPayload });
    const result = await createSearchCompanyHandler({
      browserRunner: provider.runner,
    })({ query: 'TCS' });
    assert.equal(result.status, 'unavailable');
  }
});

test('sanitizes browser failures and rejects invalid dependencies or arguments', async () => {
  assert.throws(
    () => createSearchCompanyHandler({ browserRunner: null }),
    /browser runner/,
  );
  const secret = 'cookie=must-not-escape';
  const handler = createSearchCompanyHandler({
    browserRunner: { async run() { throw new Error(secret); } },
  });
  const failed = await handler({ query: 'TCS' });
  assert.equal(failed.status, 'unavailable');
  assert.equal(JSON.stringify(failed).includes(secret), false);

  const invalid = await handler({ query: 'TCS', authorization: secret });
  assert.equal(invalid.status, 'unavailable');
  assert.equal(JSON.stringify(invalid).includes(secret), false);
});
