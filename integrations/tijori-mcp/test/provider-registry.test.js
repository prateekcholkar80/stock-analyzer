import assert from 'node:assert/strict';
import test from 'node:test';

import { createTijoriProviderRegistry } from '../src/provider-registry.js';


const TOOL_NAMES = [
  'search_company',
  'resolve_company_ids',
  'get_company_overview',
  'get_financials',
  'get_shareholding',
];
const ISSUER = {
  exchange: 'NSE',
  symbol: 'TCS',
  legal_name: 'Tata Consultancy Services Limited',
};
const RESOLVED_ISSUER = {
  ...ISSUER,
  provider_company_id: '123',
  provider_slug: 'tata-consultancy-services',
};

function searchBrowser() {
  const state = { calls: 0, slug: null };
  const searchPayload = [{
    name: 'TCS',
    slug: 'tata-consultancy-services',
    type: 'companies',
  }];
  const response = (payload) => {
    const body = Buffer.from(
      typeof payload === 'string' ? payload : JSON.stringify(payload),
    );
    return {
      async body() { return body; },
      async headers() { return { 'content-length': String(body.length) }; },
      status() { return 200; },
    };
  };
  const page = {
    async evaluate(callback) {
      if (callback.toString().includes('selectedColumns')) {
        return {
          metadata: {
            company_id: 123,
            exchange: 'NSE',
            symbol: 'TCS',
          },
          headers: ['Category', 'Mar 25', 'Jun 25'],
          rows: [
            { category: 'Promoter', values: ['71.74', '71.74'] },
            { category: 'FII', values: ['12.70', '12.50'] },
            { category: 'DII', values: ['10.20', '10.50'] },
            { category: 'Public', values: ['5.36', '5.26'] },
          ],
        };
      }
      if (callback.toString().includes('quarterly_results')) {
        return {
          metadata: {
            company_id: 123,
            exchange: 'NSE',
            symbol: 'TCS',
          },
          sections: [{
            statement: 'income_statement',
            period_type: 'annual',
            unit: '₹ in Cr.',
            headers: ['Mar 24', 'Mar 25'],
            rows: [
              { metric: 'Revenue', values: ['240,893', '255,324'] },
              { metric: 'Net Profit', values: ['45,908', '48,797'] },
            ],
          }],
        };
      }
      if (callback.toString().includes('.custom_ratio')) {
        return {
          metadata: {
            company_id: 123,
            exchange: 'NSE',
            symbol: 'TCS',
          },
          ratios: [{ label: 'P/E', value: '30.45' }],
        };
      }
      return {
        company_id: 123,
        exchange: 'NSE',
        isin: 'INE467B01029',
        legal_name: 'Tata Consultancy Services Limited',
        symbol: 'TCS',
      };
    },
    async goto(url) {
      state.calls += 1;
      const match = url.match(
        /\/company\/([a-z0-9-]+)\/(?:financials\/|shareholding\/)?$/,
      );
      if (match) state.slug = match[1];
      return response(match ? 'company page' : searchPayload);
    },
    async waitForSelector() {},
  };
  return {
    runner: { async run(task) { return task(page); } },
    state,
  };
}

test('exposes exactly the approved five-tool registry', () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  assert.deepEqual(
    registry.listTools().map(({ name }) => name),
    TOOL_NAMES,
  );
  assert.equal(Object.isFrozen(registry), true);
});

test('routes search_company through the bounded provider handler', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  const result = await registry.invoke('search_company', {
    query: 'TCS',
    exchanges: ['NSE'],
    max_results: 10,
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.companies[0].symbol, 'TCS');
  assert.equal(result.payload.companies[0].exchange, 'NSE');
  assert.equal(browser.state.slug, 'tata-consultancy-services');
  assert.equal(browser.state.calls, 2);
});

test('routes resolve_company_ids through company-page metadata', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  const result = await registry.invoke('resolve_company_ids', {
    locator: {
      exchange: 'NSE',
      symbol: 'TCS',
      provider_slug: 'tata-consultancy-services',
    },
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.status, 'resolved');
  assert.equal(result.payload.selected_company_id, '123');
  assert.equal(result.payload.companies[0].symbol, 'TCS');
  assert.equal(browser.state.slug, 'tata-consultancy-services');
  assert.equal(browser.state.calls, 1);
});

test('routes get_company_overview through deterministic evidence normalization', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  const result = await registry.invoke('get_company_overview', {
    issuer: RESOLVED_ISSUER,
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.company_id, '123');
  assert.equal(result.payload.facts.length, 1);
  assert.equal(result.payload.facts[0].provider_fact_id, 'overview.pe_ratio');
  assert.equal(result.payload.facts[0].normalized_value, '30.45');
  assert.equal(result.payload.limitations.length, 2);
  assert.equal(browser.state.calls, 1);
});

test('routes get_financials through deterministic evidence normalization', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  const result = await registry.invoke('get_financials', {
    issuer: RESOLVED_ISSUER,
    statements: ['income_statement'],
    period_types: ['annual'],
    max_periods: 1,
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.company_id, '123');
  assert.equal(result.payload.facts.length, 2);
  assert.equal(
    result.payload.facts[0].provider_fact_id,
    'income_statement.revenue.annual.2025-03-31',
  );
  assert.equal(result.payload.facts[0].normalized_value, '255324');
  assert.equal(result.payload.facts[0].normalized_unit, 'INR crore');
  assert.equal(browser.state.slug, 'tata-consultancy-services');
  assert.equal(browser.state.calls, 1);
});

test('routes get_shareholding through deterministic evidence normalization', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  const result = await registry.invoke('get_shareholding', {
    issuer: RESOLVED_ISSUER,
    quarters: 1,
    include_promoter_pledge: false,
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.company_id, '123');
  assert.equal(result.payload.facts.length, 4);
  assert.equal(
    result.payload.facts[0].provider_fact_id,
    'ownership.promoter_holding.quarterly.2025-06-30',
  );
  assert.equal(result.payload.facts[0].normalized_value, '71.74');
  assert.equal(result.payload.facts[0].normalized_unit, 'percent');
  assert.equal(browser.state.slug, 'tata-consultancy-services');
  assert.equal(browser.state.calls, 1);
});

test('retains strict input validation before provider dispatch', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  await assert.rejects(
    () => registry.invoke('get_financials', {
      issuer: ISSUER,
      authorization: 'Bearer must-not-pass',
    }),
    /unsupported field/,
  );
  assert.equal(browser.state.calls, 0);
});

test('rejects missing or malformed browser runners during composition', () => {
  for (const browserRunner of [null, undefined, {}, { run: null }]) {
    assert.throws(
      () => createTijoriProviderRegistry({ browserRunner }),
      /browser runner/,
    );
  }
});
