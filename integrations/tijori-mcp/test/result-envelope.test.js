import assert from 'node:assert/strict';
import test from 'node:test';

import {
  APPROVED_TOOL_NAMES,
  failureResult,
  serializeToolResult,
  successResult,
  TOOL_STATUSES,
  validateToolResult,
} from '../src/result-envelope.js';


test('builds a deterministic immutable success envelope', () => {
  const result = successResult('search_company', {
    z_value: 2,
    companies: [{ symbol: 'TCS', exchange: 'NSE' }],
    a_value: 1,
  });

  assert.deepEqual(result, {
    payload: {
      a_value: 1,
      companies: [{ exchange: 'NSE', symbol: 'TCS' }],
      z_value: 2,
    },
    status: 'success',
    tool_name: 'search_company',
  });
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.payload.companies[0]), true);
  assert.equal(
    serializeToolResult(result),
    '{"payload":{"a_value":1,"companies":[{"exchange":"NSE","symbol":"TCS"}],"z_value":2},"status":"success","tool_name":"search_company"}',
  );
});

test('supports every approved tool and failure status', () => {
  for (const toolName of APPROVED_TOOL_NAMES) {
    assert.equal(successResult(toolName, {}).tool_name, toolName);
    for (const status of TOOL_STATUSES.filter((item) => item !== 'success')) {
      assert.deepEqual(failureResult(toolName, status), {
        payload: null,
        status,
        tool_name: toolName,
      });
    }
  }
});

test('rejects unknown tools, statuses, and extra result fields', () => {
  assert.throws(() => successResult('fetch_document', {}), /unapproved/);
  assert.throws(
    () => failureResult('search_company', 'provider_exploded'),
    /approved failure status/,
  );
  assert.throws(
    () => validateToolResult({
      tool_name: 'search_company',
      status: 'success',
      payload: {},
      raw_response: 'forbidden',
    }),
    /unsupported field/,
  );
});

test('requires object payload only for successful results', () => {
  for (const payload of [null, [], 'value', 10, true]) {
    assert.throws(
      () => successResult('search_company', payload),
      /object payload/,
    );
  }
  assert.throws(
    () => validateToolResult({
      tool_name: 'search_company',
      status: 'not_found',
      payload: { provider_error: 'raw' },
    }),
    /cannot release a payload/,
  );
});

test('rejects sensitive fields at any nesting level', () => {
  for (const key of [
    'password',
    'cookie',
    'authorization',
    'csrf',
    'session',
    'token',
    'api_key',
    'secret',
    'access_token',
    'refresh_token',
    'client_secret',
    'sessionid',
    'csrf_token',
  ]) {
    assert.throws(
      () => successResult('search_company', { safe: { [key]: 'hidden' } }),
      /prohibited sensitive field/,
    );
  }
});

test('rejects non-finite and non-JSON values', () => {
  for (const value of [Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.throws(
      () => successResult('search_company', { value }),
      /finite/,
    );
  }
  for (const value of [1n, new Date(), new Map(), undefined, () => {}]) {
    assert.throws(
      () => successResult('search_company', { value }),
      /non-JSON/,
    );
  }
});

test('rejects excessive strings, keys, depth, and node count', () => {
  assert.throws(
    () => successResult('search_company', { value: 'x'.repeat(10_001) }),
    /string exceeds/,
  );
  assert.throws(
    () => successResult('search_company', { ['x'.repeat(161)]: true }),
    /key is blank or oversized/,
  );

  let nested = {};
  let cursor = nested;
  for (let index = 0; index < 9; index += 1) {
    cursor.next = {};
    cursor = cursor.next;
  }
  assert.throws(
    () => successResult('search_company', nested),
    /structure limits/,
  );
  assert.throws(
    () => successResult('search_company', {
      values: Array.from({ length: 10_001 }, () => null),
    }),
    /structure limits/,
  );

  const oversized = {};
  for (let index = 0; index < 110; index += 1) {
    oversized[`field_${index}`] = 'x'.repeat(9_999);
  }
  assert.throws(
    () => successResult('search_company', oversized),
    /response size limit/,
  );
});

test('does not mutate caller-owned input during normalization', () => {
  const payload = { rows: [{ value: 1 }] };
  const result = successResult('get_financials', payload);

  assert.notEqual(result.payload, payload);
  assert.notEqual(result.payload.rows, payload.rows);
  payload.rows[0].value = 99;
  assert.equal(result.payload.rows[0].value, 1);
});
