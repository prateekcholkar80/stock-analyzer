import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmod, mkdtemp, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { createTijoriProviderRegistry } from '../src/provider-registry.js';


const TOOL_NAMES = [
  'search_company',
  'resolve_company_ids',
  'get_company_overview',
  'get_financials',
  'get_shareholding',
  'list_company_filings',
  'download_company_filing',
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
const SOURCE_PAGE_URL = (
  'https://www.tijorifinance.com/company/tata-consultancy-services/'
);
const EARNINGS_URL = (
  'https://files.tijorifinance.com/insight/india/123/'
  + 'Earnings%20Release/ER-Jan26.pdf'
);
const PDF_BYTES = Buffer.from('%PDF-1.7\nprovider registry fixture\n%%EOF\n');

function searchBrowser() {
  const state = { calls: 0, slug: null };
  const searchPayload = [{
    name: 'TCS',
    slug: 'tata-consultancy-services',
    type: 'companies',
  }];
  const response = (payload, responseUrl = 'https://www.tijorifinance.com/') => {
    const body = Buffer.from(
      typeof payload === 'string' ? payload : JSON.stringify(payload),
    );
    return {
      async body() { return body; },
      async headers() { return { 'content-length': String(body.length) }; },
      status() { return 200; },
      url() { return responseUrl; },
    };
  };
  const page = {
    context() {
      return { async cookies() { return []; } };
    },
    async evaluate(callback) {
      if (callback.toString().includes('#companyFilings')) {
        return {
          provider_years: ['2026', '2025'],
          rows: [
            {
              label: 'Earnings Release',
              class_names: ['name__cell', 'earnings-release'],
              groups: [
                {
                  provider_year: '2026',
                  entries: [{
                    kind: 'available',
                    label: 'Jan',
                    href: (
                      'https://files.tijorifinance.com/insight/india/123/'
                      + 'Earnings%20Release/ER-Jan26.pdf'
                    ),
                  }],
                },
                {
                  provider_year: '2025',
                  entries: [{ kind: 'unavailable', label: 'Mar', href: null }],
                },
              ],
            },
            {
              label: 'Conference Call',
              class_names: ['name__cell', 'conference-call'],
              groups: [
                {
                  provider_year: '2026',
                  entries: [{
                    kind: 'available',
                    label: 'Jan',
                    href: (
                      'https://files.tijorifinance.com/insight/india/123/'
                      + 'Conference%20Call/CC-Jan26.pdf'
                    ),
                  }],
                },
                { provider_year: '2025', entries: [] },
              ],
            },
          ],
        };
      }
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
      return response(match ? 'company page' : searchPayload, url);
    },
    async waitForSelector() {},
    async waitForFunction() {},
  };
  return {
    runner: { async run(task) { return task(page); } },
    state,
  };
}

test('exposes exactly the approved seven-tool registry', () => {
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

test('routes list_company_filings through issuer-bound discovery', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  const result = await registry.invoke('list_company_filings', {
    issuer: RESOLVED_ISSUER,
    categories: ['earnings_release', 'conference_call'],
    max_documents: 10,
  });

  assert.equal(result.status, 'success');
  assert.equal(result.payload.filings.length, 2);
  assert.equal(result.payload.filings[0].provider_company_id, '123');
  assert.equal(result.payload.filings[0].period.state, 'unclassified');
  assert.deepEqual(result.payload.unavailable_period_labels, [
    'earnings_release:Mar 2025',
    'conference_call:2025',
  ]);
  assert.equal(browser.state.slug, 'tata-consultancy-services');
  assert.equal(browser.state.calls, 1);
});

test('fails filing download closed when no staging boundary is configured', async () => {
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({ browserRunner: browser.runner });

  const result = await registry.invoke(
    'download_company_filing',
    filingDownloadRequest(),
  );
  assert.equal(result.status, 'unavailable');
  assert.equal(browser.state.calls, 0);
});

test('routes filing download only through a configured staging boundary', async (context) => {
  const stagingDirectory = await mkdtemp(join(tmpdir(), 'jarvis-registry-filings-'));
  await chmod(stagingDirectory, 0o700);
  context.after(async () => rm(stagingDirectory, { recursive: true, force: true }));
  const browser = searchBrowser();
  const registry = createTijoriProviderRegistry({
    browserRunner: browser.runner,
    filingStagingDirectory: stagingDirectory,
    filingDownloadFetcher: async (url) => syntheticPdfResponse(url),
    filingDownloadClock: () => new Date('2026-09-14T12:00:00+05:30'),
  });

  const result = await registry.invoke(
    'download_company_filing',
    filingDownloadRequest(),
  );

  assert.equal(result.status, 'success');
  assert.equal(result.payload.provider_company_id, '123');
  assert.equal(result.payload.validation_status, 'validated');
  assert.match(result.payload.staged_artifact_key, /^[a-f0-9]{64}\.pdf$/);
  assert.equal(JSON.stringify(result).includes(stagingDirectory), false);
  assert.deepEqual(await readdir(stagingDirectory), [
    result.payload.staged_artifact_key,
  ]);
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

function filingDownloadRequest() {
  const digest = createHash('sha256')
    .update(`earnings_release\n${EARNINGS_URL}`, 'utf8')
    .digest('hex');
  return {
    issuer: RESOLVED_ISSUER,
    filing_reference: {
      filing_reference_id: `filing.tcs.${digest.slice(0, 32)}`,
      category: 'earnings_release',
      provider_company_id: '123',
      provider_slug: 'tata-consultancy-services',
      source_page_url: SOURCE_PAGE_URL,
      artifact_url: EARNINGS_URL,
      discovery_fingerprint: 'a'.repeat(64),
    },
    expected_source_url: EARNINGS_URL,
    expected_provider_company_id: '123',
  };
}

function syntheticPdfResponse(url) {
  const chunks = [PDF_BYTES];
  return {
    status: 200,
    url,
    headers: new Headers({
      'content-type': 'application/pdf',
      'content-length': String(PDF_BYTES.byteLength),
    }),
    body: new ReadableStream({
      pull(controller) {
        const chunk = chunks.shift();
        if (chunk === undefined) controller.close();
        else controller.enqueue(new Uint8Array(chunk));
      },
    }),
  };
}
