import assert from 'node:assert/strict';
import test from 'node:test';

import { failureResult, successResult } from '../src/result-envelope.js';
import {
  createToolRegistry,
  TOOL_DEFINITIONS,
} from '../src/tool-registry.js';


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

function handlers(overrides = {}) {
  return Object.fromEntries(TOOL_NAMES.map((toolName) => [
    toolName,
    overrides[toolName] ?? (async () => successResult(toolName, {})),
  ]));
}

test('exposes only the five approved immutable tool definitions', () => {
  const registry = createToolRegistry(handlers());

  assert.deepEqual(
    registry.listTools().map(({ name }) => name),
    TOOL_NAMES,
  );
  assert.equal(registry.listTools(), TOOL_DEFINITIONS);
  assert.equal(Object.isFrozen(registry), true);
  assert.equal(Object.isFrozen(TOOL_DEFINITIONS), true);
  assert.equal(
    TOOL_DEFINITIONS.every((definition) => (
      Object.isFrozen(definition) && definition.description.length > 0
    )),
    true,
  );
});

test('requires one callable handler for every approved tool', () => {
  const missing = handlers();
  delete missing.get_shareholding;
  assert.throws(() => createToolRegistry(missing), /exact tool allow-list/);

  assert.throws(
    () => createToolRegistry({ ...handlers(), fetch_report: () => ({}) }),
    /exact tool allow-list/,
  );
  assert.throws(
    () => createToolRegistry({ ...handlers(), get_financials: null }),
    /not callable/,
  );
  assert.throws(() => createToolRegistry(null), /plain object/);
});

test('invokes an async handler with caller arguments and validates its result', async () => {
  let received;
  const registry = createToolRegistry(handlers({
    search_company: async (argumentsValue) => {
      received = argumentsValue;
      return successResult('search_company', {
        companies: [{ exchange: 'NSE', symbol: argumentsValue.query }],
      });
    },
  }));

  const result = await registry.invoke('search_company', {
    query: 'TCS',
    max_results: 5,
  });

  assert.deepEqual(received, {
    query: 'TCS',
    exchanges: [],
    max_results: 5,
  });
  assert.equal(Object.isFrozen(received), true);
  assert.equal(Object.isFrozen(received.exchanges), true);
  assert.deepEqual(result.payload.companies, [{ exchange: 'NSE', symbol: 'TCS' }]);
  assert.equal(Object.isFrozen(result), true);
});

test('accepts a validated failure envelope from a handler', async () => {
  const registry = createToolRegistry(handlers({
    get_financials: async () => failureResult('get_financials', 'paywalled'),
  }));

  assert.deepEqual(await registry.invoke('get_financials', { issuer: ISSUER }), {
    payload: null,
    status: 'paywalled',
    tool_name: 'get_financials',
  });
});

test('rejects unknown tools and non-object arguments before handler execution', async () => {
  let called = false;
  const registry = createToolRegistry(handlers({
    search_company: async () => {
      called = true;
      return successResult('search_company', {});
    },
  }));

  await assert.rejects(() => registry.invoke('fetch_report', {}), /unapproved tool/);
  for (const value of [null, [], 'TCS', 1]) {
    await assert.rejects(
      () => registry.invoke('search_company', value),
      /plain object/,
    );
  }
  assert.equal(called, false);
});

test('normalizes arguments and rejects unsupported fields before handler execution', async () => {
  let received;
  let calls = 0;
  const registry = createToolRegistry(handlers({
    search_company: async (argumentsValue) => {
      calls += 1;
      received = argumentsValue;
      return successResult('search_company', {});
    },
  }));

  await registry.invoke('search_company', {
    query: '  Tata   Consultancy ',
    exchanges: ['nse'],
  });
  assert.deepEqual(received, {
    query: 'Tata Consultancy',
    exchanges: ['NSE'],
    max_results: 10,
  });

  await assert.rejects(
    () => registry.invoke('search_company', {
      query: 'TCS',
      authorization: 'Bearer must-not-reach-handler',
    }),
    /unsupported field/,
  );
  assert.equal(calls, 1);
});

test('rejects malformed and mismatched handler results', async () => {
  const malformed = createToolRegistry(handlers({
    search_company: async () => ({ raw_provider_response: 'forbidden' }),
  }));
  await assert.rejects(
    () => malformed.invoke('search_company', { query: 'TCS' }),
    /unsupported field/,
  );

  const mismatched = createToolRegistry(handlers({
    search_company: async () => successResult('resolve_company_ids', {}),
  }));
  await assert.rejects(
    () => mismatched.invoke('search_company', { query: 'TCS' }),
    /mismatched tool result/,
  );
});

test('copies the handler table so caller mutation cannot alter routing', async () => {
  const source = handlers();
  const registry = createToolRegistry(source);
  source.search_company = async () => failureResult('search_company', 'unavailable');

  const result = await registry.invoke('search_company', { query: 'TCS' });
  assert.equal(result.status, 'success');
});
