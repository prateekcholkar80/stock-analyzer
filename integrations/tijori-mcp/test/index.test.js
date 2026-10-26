import assert from 'node:assert/strict';
import { chmod, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';

import {
  assertSupportedNodeVersion,
  createConfiguredTijoriMcpServer,
  createTijoriMcpServer,
  createUnavailableRegistry,
  FILING_STAGING_DIRECTORY_ENV,
  TOOL_INPUT_SCHEMAS,
} from '../src/index.js';
import { successResult } from '../src/result-envelope.js';
import {
  PROVIDER_CONTRACT_ENV,
  SESSION_FILE_ENV,
} from '../src/session-boundary.js';
import { createToolRegistry } from '../src/tool-registry.js';


const TOOL_NAMES = [
  'search_company',
  'resolve_company_ids',
  'get_company_overview',
  'get_financials',
  'get_shareholding',
  'list_company_filings',
  'download_company_filing',
];

function registryWith(overrides = {}) {
  return createToolRegistry(Object.fromEntries(TOOL_NAMES.map((toolName) => [
    toolName,
    overrides[toolName] ?? (async () => successResult(toolName, {})),
  ])));
}

async function connectedClient(registry) {
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'jarvis-tijori-test', version: '1.0.0' });
  const server = createTijoriMcpServer(registry);
  await Promise.all([
    server.connect(serverTransport),
    client.connect(clientTransport),
  ]);
  return { client, server };
}

async function connectServer(server) {
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'jarvis-tijori-test', version: '1.0.0' });
  await Promise.all([
    server.connect(serverTransport),
    client.connect(clientTransport),
  ]);
  return { client, server };
}

function providerBrowserRunner() {
  const state = { route: null };
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
  const metadata = {
    company_id: 123,
    exchange: 'NSE',
    isin: 'INE467B01029',
    legal_name: 'Tata Consultancy Services Limited',
    symbol: 'TCS',
  };
  const page = {
    async evaluate(callback) {
      if (callback.toString().includes('#companyFilings')) {
        return {
          provider_years: ['2026'],
          rows: [
            {
              label: 'Earnings Release',
              class_names: ['name__cell', 'earnings-release'],
              groups: [{
                provider_year: '2026',
                entries: [{
                  kind: 'available',
                  label: 'Jan',
                  href: (
                    'https://files.tijorifinance.com/insight/india/123/'
                    + 'Earnings%20Release/ER-Jan26.pdf'
                  ),
                }],
              }],
            },
            {
              label: 'Conference Call',
              class_names: ['name__cell', 'conference-call'],
              groups: [{
                provider_year: '2026',
                entries: [{
                  kind: 'available',
                  label: 'Jan',
                  href: (
                    'https://files.tijorifinance.com/insight/india/123/'
                    + 'Conference%20Call/CC-Jan26.pdf'
                  ),
                }],
              }],
            },
          ],
        };
      }
      if (state.route === 'financials') {
        return {
          metadata,
          sections: [{
            statement: 'income_statement', period_type: 'annual', unit: '₹ in Cr.',
            headers: ['Mar 25'], rows: [{ metric: 'Revenue', values: ['255,324'] }],
          }],
        };
      }
      if (state.route === 'shareholding') {
        return {
          metadata,
          headers: ['Category', 'Mar 25'],
          rows: [{ category: 'Promoter', values: ['71.74'] }],
        };
      }
      if (callback.toString().includes('.custom_ratio')) {
        return { metadata, ratios: [{ label: 'P/E', value: '30.45' }] };
      }
      return metadata;
    },
    async goto(url) {
      if (url.includes('/api/v1/ind/company_search/')) {
        state.route = 'search';
        return response([{
          name: 'TCS', slug: 'tata-consultancy-services', type: 'companies',
        }]);
      }
      state.route = url.endsWith('/financials/')
        ? 'financials'
        : url.endsWith('/shareholding/') ? 'shareholding' : 'company';
      return response('company page', url);
    },
    async waitForSelector() {},
    async waitForFunction() {},
  };
  return Object.freeze({ run: async (task) => task(page) });
}

test('registers exactly seven bounded tools with accurate annotations', async (context) => {
  const { client, server } = await connectedClient(createUnavailableRegistry());
  context.after(async () => server.close());

  const result = await client.listTools();
  assert.deepEqual(result.tools.map(({ name }) => name), TOOL_NAMES);
  for (const tool of result.tools) {
    assert.equal(
      tool.annotations.readOnlyHint,
      tool.name !== 'download_company_filing',
    );
    assert.equal(tool.annotations.destructiveHint, false);
    assert.equal(tool.annotations.idempotentHint, true);
    assert.equal(tool.annotations.openWorldHint, true);
    assert.equal(tool.inputSchema.type, 'object');
  }
  const companyOverview = result.tools.find(
    ({ name }) => name === 'get_company_overview',
  );
  assert.deepEqual(
    companyOverview.inputSchema.properties.document_type.enum,
    ['peer_comparison', 'benchmarking_financials'],
  );
  assert.equal(
    companyOverview.inputSchema.required.includes('document_type'),
    false,
  );
  const financials = result.tools.find(({ name }) => name === 'get_financials');
  assert.deepEqual(
    financials.inputSchema.properties.document_type.enum,
    [
      'financial_package',
      'growth_table',
      'balance_sheet',
      'profit_and_loss',
      'cash_flow',
      'ratios',
      'quarterly_results',
    ],
  );
  assert.deepEqual(
    financials.inputSchema.properties.reporting_basis.enum,
    ['consolidated', 'standalone', 'not_applicable'],
  );
  assert.equal(
    financials.inputSchema.required.includes('document_type'),
    false,
  );
  assert.equal(
    financials.inputSchema.required.includes('reporting_basis'),
    false,
  );
});

test('defines bounded public schemas for filing discovery and download', () => {
  const listSchema = TOOL_INPUT_SCHEMAS.list_company_filings;
  const downloadSchema = TOOL_INPUT_SCHEMAS.download_company_filing;
  const resolvedIssuer = {
    exchange: 'NSE',
    symbol: 'COFORGE',
    legal_name: 'Coforge Ltd.',
    isin: 'INE591G01017',
    provider_company_id: '242',
    provider_slug: 'niit-technologies-limited',
  };
  const filingReference = {
    filing_reference_id: 'filing.coforge.fy2027.q1.earnings-release',
    category: 'earnings_release',
    provider_company_id: '242',
    provider_slug: 'niit-technologies-limited',
    source_page_url: (
      'https://www.tijorifinance.com/company/niit-technologies-limited/'
    ),
    artifact_url: (
      'https://files.tijorifinance.com/insight/india/242/ER-Jun26.pdf'
    ),
    discovery_fingerprint: 'a'.repeat(64),
  };

  assert.equal(listSchema.safeParse({
    issuer: resolvedIssuer,
    categories: ['earnings_release', 'conference_call'],
    from_fiscal_year: 2017,
    through_date: '2026-09-14',
    max_documents: 200,
  }).success, true);
  assert.equal(downloadSchema.safeParse({
    issuer: resolvedIssuer,
    filing_reference: filingReference,
    expected_source_url: filingReference.artifact_url,
    expected_provider_company_id: '242',
  }).success, true);
});

test('public filing schemas reject unresolved, excessive, and unsafe shapes', () => {
  const listSchema = TOOL_INPUT_SCHEMAS.list_company_filings;
  const downloadSchema = TOOL_INPUT_SCHEMAS.download_company_filing;
  const resolvedIssuer = {
    exchange: 'NSE',
    symbol: 'COFORGE',
    legal_name: 'Coforge Ltd.',
    provider_company_id: '242',
    provider_slug: 'niit-technologies-limited',
  };
  const filingReference = {
    filing_reference_id: 'filing.coforge.fy2027.q1.earnings-release',
    category: 'earnings_release',
    provider_company_id: '242',
    provider_slug: 'niit-technologies-limited',
    source_page_url: (
      'https://www.tijorifinance.com/company/niit-technologies-limited/'
    ),
    artifact_url: (
      'https://files.tijorifinance.com/insight/india/242/ER-Jun26.pdf'
    ),
    discovery_fingerprint: 'a'.repeat(64),
  };

  assert.equal(listSchema.safeParse({
    issuer: { ...resolvedIssuer, provider_company_id: undefined },
  }).success, false);
  assert.equal(listSchema.safeParse({
    issuer: resolvedIssuer,
    categories: ['annual_report'],
  }).success, false);
  assert.equal(listSchema.safeParse({
    issuer: resolvedIssuer,
    max_documents: 201,
  }).success, false);
  assert.equal(listSchema.safeParse({
    issuer: resolvedIssuer,
    local_path: '/tmp/filings',
  }).success, false);
  assert.equal(downloadSchema.safeParse({
    issuer: resolvedIssuer,
    filing_reference: {
      ...filingReference,
      artifact_url: 'file:///tmp/ER-Jun26.pdf',
    },
    expected_source_url: filingReference.artifact_url,
    expected_provider_company_id: '242',
  }).success, false);
  assert.equal(downloadSchema.safeParse({
    issuer: resolvedIssuer,
    filing_reference: { ...filingReference, cookie: 'secret' },
    expected_source_url: filingReference.artifact_url,
    expected_provider_company_id: '242',
  }).success, false);
});

test('registers filing discovery and authenticated-boundary download schemas', async (
  context,
) => {
  const { client, server } = await connectedClient(createUnavailableRegistry());
  context.after(async () => server.close());

  const result = await client.listTools();
  assert.deepEqual(result.tools.map(({ name }) => name), TOOL_NAMES);
  assert.equal(
    result.tools.some(({ name }) => name === 'list_company_filings'),
    true,
  );
  assert.equal(
    result.tools.some(({ name }) => name === 'download_company_filing'),
    true,
  );
});

test('admits Peer Comparison at the public MCP boundary', async (context) => {
  let received;
  const registry = registryWith({
    get_company_overview: async (argumentsValue) => {
      received = argumentsValue;
      return successResult('get_company_overview', { document: {} });
    },
  });
  const { client, server } = await connectedClient(registry);
  context.after(async () => server.close());

  const issuer = {
    exchange: 'NSE',
    symbol: 'COFORGE',
    legal_name: 'Coforge Ltd.',
    provider_company_id: 'coforge-1',
    provider_slug: 'coforge-ltd',
  };
  const result = await client.callTool({
    name: 'get_company_overview',
    arguments: { issuer, document_type: 'peer_comparison' },
  });

  assert.equal(result.isError, false);
  assert.equal(received.document_type, 'peer_comparison');
});

test('admits Benchmarking Financials at the public MCP boundary', async (context) => {
  let received;
  const registry = registryWith({
    get_company_overview: async (argumentsValue) => {
      received = argumentsValue;
      return successResult('get_company_overview', { document: {} });
    },
  });
  const { client, server } = await connectedClient(registry);
  context.after(async () => server.close());

  const issuer = {
    exchange: 'NSE',
    symbol: 'COFORGE',
    legal_name: 'Coforge Ltd.',
    provider_company_id: 'coforge-1',
    provider_slug: 'coforge-ltd',
  };
  const result = await client.callTool({
    name: 'get_company_overview',
    arguments: { issuer, document_type: 'benchmarking_financials' },
  });

  assert.equal(result.isError, false);
  assert.equal(received.document_type, 'benchmarking_financials');
});

test('admits the Growth Table provider basis at the public MCP boundary', async (context) => {
  let received;
  const registry = registryWith({
    get_financials: async (argumentsValue) => {
      received = argumentsValue;
      return successResult('get_financials', { document: {} });
    },
  });
  const { client, server } = await connectedClient(registry);
  context.after(async () => server.close());

  const issuer = {
    exchange: 'NSE',
    symbol: 'COFORGE',
    legal_name: 'Coforge Ltd.',
    provider_company_id: 'coforge-1',
    provider_slug: 'coforge-ltd',
  };
  const result = await client.callTool({
    name: 'get_financials',
    arguments: {
      issuer,
      document_type: 'growth_table',
      reporting_basis: 'not_applicable',
    },
  });

  assert.equal(result.isError, false);
  assert.equal(received.document_type, 'growth_table');
  assert.equal(received.reporting_basis, 'not_applicable');
});

test('advertises an unauthenticated state when no session is attached to the factory', async (context) => {
  const { client, server } = await connectedClient(createUnavailableRegistry());
  context.after(async () => server.close());

  assert.deepEqual(client.getServerCapabilities().experimental.jarvisTijori, {
    authenticated: false,
    providerContractVersion: 'tijori.unconfigured.v1',
  });
});

test('advertises authenticated only after a positive session probe', async (context) => {
  const createdRoot = await mkdtemp(join(tmpdir(), 'jarvis-tijori-index-'));
  const sessionRoot = await realpath(createdRoot);
  await chmod(sessionRoot, 0o700);
  const sessionFile = join(sessionRoot, `${'b'.repeat(64)}.json`);
  await writeFile(sessionFile, '{}', { mode: 0o600 });
  context.after(async () => rm(sessionRoot, { recursive: true, force: true }));

  let probedRunner;
  let receivedBoundary;
  const syntheticRunner = Object.freeze({ run: async () => undefined });
  const configuredServer = await createConfiguredTijoriMcpServer(
    createUnavailableRegistry(),
    {
      [SESSION_FILE_ENV]: sessionFile,
      [PROVIDER_CONTRACT_ENV]: 'tijori.local_contract.v1',
    },
    {
      browserSessionFactory(boundary) {
        receivedBoundary = boundary;
        return syntheticRunner;
      },
      async authenticationProbe(runner) {
        probedRunner = runner;
        return { authenticated: true, status: 'authenticated' };
      },
    },
  );
  const { client, server } = await connectServer(configuredServer);
  context.after(async () => server.close());

  const metadata = client.getServerCapabilities().experimental.jarvisTijori;
  assert.deepEqual(metadata, {
    authenticated: true,
    providerContractVersion: 'tijori.local_contract.v1',
  });
  assert.equal(JSON.stringify(metadata).includes(sessionFile), false);
  assert.equal(Object.hasOwn(metadata, 'sessionFile'), false);
  assert.equal(receivedBoundary.sessionFile, sessionFile);
  assert.equal(probedRunner, syntheticRunner);
});

test('gates every tool when the session requires authentication', async (context) => {
  const createdRoot = await mkdtemp(join(tmpdir(), 'jarvis-tijori-expired-'));
  const sessionRoot = await realpath(createdRoot);
  await chmod(sessionRoot, 0o700);
  const sessionFile = join(sessionRoot, `${'d'.repeat(64)}.json`);
  await writeFile(sessionFile, '{}', { mode: 0o600 });
  context.after(async () => rm(sessionRoot, { recursive: true, force: true }));

  let calls = 0;
  const configuredServer = await createConfiguredTijoriMcpServer(
    registryWith({
      search_company: async () => {
        calls += 1;
        return successResult('search_company', {});
      },
    }),
    {
      [SESSION_FILE_ENV]: sessionFile,
      [PROVIDER_CONTRACT_ENV]: 'tijori.local_contract.v1',
    },
    {
      browserSessionFactory: () => ({ run: async () => undefined }),
      authenticationProbe: async () => ({
        authenticated: false,
        status: 'authentication_required',
      }),
    },
  );
  const { client, server } = await connectServer(configuredServer);
  context.after(async () => server.close());

  assert.equal(
    client.getServerCapabilities().experimental.jarvisTijori.authenticated,
    false,
  );
  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: 'TCS' },
  });
  assert.equal(calls, 0);
  assert.deepEqual(JSON.parse(result.content[0].text), {
    payload: null,
    status: 'authentication_required',
    tool_name: 'search_company',
  });
});

test('wires the default provider registry only after a successful probe', async (context) => {
  const createdRoot = await mkdtemp(join(tmpdir(), 'jarvis-tijori-provider-'));
  const sessionRoot = await realpath(createdRoot);
  await chmod(sessionRoot, 0o700);
  const sessionFile = join(sessionRoot, `${'f'.repeat(64)}.json`);
  await writeFile(sessionFile, '{}', { mode: 0o600 });
  context.after(async () => rm(sessionRoot, { recursive: true, force: true }));

  const syntheticRunner = Object.freeze({ run: async () => undefined });
  let providerRunner;
  let providerFilingStagingDirectory;
  let searchCalls = 0;
  const configuredServer = await createConfiguredTijoriMcpServer(
    undefined,
    {
      [SESSION_FILE_ENV]: sessionFile,
      [PROVIDER_CONTRACT_ENV]: 'tijori.local_contract.v1',
      [FILING_STAGING_DIRECTORY_ENV]: '/private/jarvis/filing-staging',
    },
    {
      browserSessionFactory: () => syntheticRunner,
      authenticationProbe: async () => ({
        authenticated: true,
        status: 'authenticated',
      }),
      providerRegistryFactory({ browserRunner, filingStagingDirectory }) {
        providerRunner = browserRunner;
        providerFilingStagingDirectory = filingStagingDirectory;
        return registryWith({
          search_company: async () => {
            searchCalls += 1;
            return successResult('search_company', { companies: [] });
          },
        });
      },
    },
  );
  const { client, server } = await connectServer(configuredServer);
  context.after(async () => server.close());

  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: 'TCS' },
  });
  assert.equal(providerRunner, syntheticRunner);
  assert.equal(
    providerFilingStagingDirectory,
    '/private/jarvis/filing-staging',
  );
  assert.equal(searchCalls, 1);
  assert.equal(result.isError, false);
  assert.equal(JSON.parse(result.content[0].text).status, 'success');
});

test('runs the six non-download provider handlers through authenticated composition', async (context) => {
  const createdRoot = await mkdtemp(join(tmpdir(), 'jarvis-tijori-six-tools-'));
  const sessionRoot = await realpath(createdRoot);
  await chmod(sessionRoot, 0o700);
  const sessionFile = join(sessionRoot, `${'9'.repeat(64)}.json`);
  await writeFile(sessionFile, '{}', { mode: 0o600 });
  context.after(async () => rm(sessionRoot, { recursive: true, force: true }));

  const configuredServer = await createConfiguredTijoriMcpServer(
    undefined,
    {
      [SESSION_FILE_ENV]: sessionFile,
      [PROVIDER_CONTRACT_ENV]: 'tijori.local_contract.v1',
    },
    {
      browserSessionFactory: providerBrowserRunner,
      authenticationProbe: async () => ({
        authenticated: true,
        status: 'authenticated',
      }),
    },
  );
  const { client, server } = await connectServer(configuredServer);
  context.after(async () => server.close());

  const issuer = {
    exchange: 'NSE', symbol: 'TCS', legal_name: 'Tata Consultancy Services Limited',
    provider_company_id: '123', provider_slug: 'tata-consultancy-services',
  };
  const calls = [
    ['search_company', { query: 'TCS', exchanges: ['NSE'] }],
    ['resolve_company_ids', { locator: issuer }],
    ['get_company_overview', { issuer }],
    ['get_financials', {
      issuer, statements: ['income_statement'], period_types: ['annual'], max_periods: 1,
    }],
    ['get_shareholding', { issuer, quarters: 1, include_promoter_pledge: false }],
    ['list_company_filings', { issuer }],
  ];
  for (const [name, argumentsValue] of calls) {
    const result = await client.callTool({ name, arguments: argumentsValue });
    const envelope = JSON.parse(result.content[0].text);
    assert.equal(result.isError, false, name);
    assert.equal(envelope.tool_name, name);
    assert.equal(envelope.status, 'success');
  }
});

test('does not invoke the default provider registry while authentication is unavailable', async (context) => {
  const createdRoot = await mkdtemp(join(tmpdir(), 'jarvis-tijori-provider-gate-'));
  const sessionRoot = await realpath(createdRoot);
  await chmod(sessionRoot, 0o700);
  const sessionFile = join(sessionRoot, `${'1'.repeat(64)}.json`);
  await writeFile(sessionFile, '{}', { mode: 0o600 });
  context.after(async () => rm(sessionRoot, { recursive: true, force: true }));

  let providerCalls = 0;
  let providerCompositions = 0;
  const configuredServer = await createConfiguredTijoriMcpServer(
    undefined,
    {
      [SESSION_FILE_ENV]: sessionFile,
      [PROVIDER_CONTRACT_ENV]: 'tijori.local_contract.v1',
    },
    {
      browserSessionFactory: () => ({ run: async () => undefined }),
      authenticationProbe: async () => ({
        authenticated: false,
        status: 'unavailable',
      }),
      providerRegistryFactory() {
        providerCompositions += 1;
        return registryWith({
          search_company: async () => {
            providerCalls += 1;
            return successResult('search_company', {});
          },
        });
      },
    },
  );
  const { client, server } = await connectServer(configuredServer);
  context.after(async () => server.close());

  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: 'TCS' },
  });
  assert.equal(providerCompositions, 0);
  assert.equal(providerCalls, 0);
  assert.equal(result.isError, true);
  assert.equal(JSON.parse(result.content[0].text).status, 'unavailable');
});

test('fails closed when probe setup fails or returns an invalid result', async (context) => {
  const createdRoot = await mkdtemp(join(tmpdir(), 'jarvis-tijori-probe-fail-'));
  const sessionRoot = await realpath(createdRoot);
  await chmod(sessionRoot, 0o700);
  const sessionFile = join(sessionRoot, `${'e'.repeat(64)}.json`);
  await writeFile(sessionFile, '{}', { mode: 0o600 });
  context.after(async () => rm(sessionRoot, { recursive: true, force: true }));

  const configuredServer = await createConfiguredTijoriMcpServer(
    registryWith(),
    {
      [SESSION_FILE_ENV]: sessionFile,
      [PROVIDER_CONTRACT_ENV]: 'tijori.local_contract.v1',
    },
    {
      browserSessionFactory() {
        throw new Error('cookie=must-not-escape');
      },
      authenticationProbe: async () => ({ authenticated: true, status: 'invalid' }),
    },
  );
  const { client, server } = await connectServer(configuredServer);
  context.after(async () => server.close());

  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: 'TCS' },
  });
  assert.equal(
    client.getServerCapabilities().experimental.jarvisTijori.authenticated,
    false,
  );
  assert.equal(result.content[0].text.includes('must-not-escape'), false);
  assert.equal(JSON.parse(result.content[0].text).status, 'unavailable');
});

test('returns a canonical unavailable envelope before provider wiring', async (context) => {
  const { client, server } = await connectedClient(createUnavailableRegistry());
  context.after(async () => server.close());

  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: 'TCS' },
  });

  assert.equal(result.isError, true);
  assert.equal(result.content.length, 1);
  assert.deepEqual(JSON.parse(result.content[0].text), {
    payload: null,
    status: 'unavailable',
    tool_name: 'search_company',
  });
});

test('routes a valid call through normalization and returns a success envelope', async (context) => {
  let received;
  const registry = registryWith({
    search_company: async (argumentsValue) => {
      received = argumentsValue;
      return successResult('search_company', { companies: [] });
    },
  });
  const { client, server } = await connectedClient(registry);
  context.after(async () => server.close());

  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: '  Tata   Consultancy ', exchanges: ['nse'] },
  });

  assert.deepEqual(received, {
    query: 'Tata Consultancy',
    exchanges: ['NSE'],
    max_results: 10,
  });
  assert.equal(result.isError, false);
  assert.deepEqual(JSON.parse(result.content[0].text), {
    payload: { companies: [] },
    status: 'success',
    tool_name: 'search_company',
  });
});

test('fails closed without invoking a handler for unsupported request fields', async (context) => {
  let calls = 0;
  const registry = registryWith({
    search_company: async () => {
      calls += 1;
      return successResult('search_company', {});
    },
  });
  const { client, server } = await connectedClient(registry);
  context.after(async () => server.close());

  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: 'TCS', authorization: 'Bearer must-not-pass' },
  });

  assert.equal(calls, 0);
  assert.equal(result.isError, true);
  assert.equal(result.content[0].text.includes('must-not-pass'), false);
  assert.equal(JSON.parse(result.content[0].text).status, 'unavailable');
});

test('does not expose thrown handler messages through MCP results', async (context) => {
  const registry = registryWith({
    search_company: async () => {
      throw new Error('Bearer secret-provider-message');
    },
  });
  const { client, server } = await connectedClient(registry);
  context.after(async () => server.close());

  const result = await client.callTool({
    name: 'search_company',
    arguments: { query: 'TCS' },
  });

  assert.equal(result.isError, true);
  assert.equal(result.content[0].text.includes('secret-provider-message'), false);
  assert.equal(JSON.parse(result.content[0].text).status, 'unavailable');
});

test('accepts only the pinned Node 24 major for direct stdio startup', () => {
  assert.doesNotThrow(() => assertSupportedNodeVersion('24.12.0'));
  for (const version of ['22.18.0', '25.2.1', 'invalid']) {
    assert.throws(() => assertSupportedNodeVersion(version), /Node 24/);
  }
});
