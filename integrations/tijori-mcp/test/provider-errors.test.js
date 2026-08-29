import assert from 'node:assert/strict';
import test from 'node:test';

import {
  classifyProviderFailure,
  ProviderFailure,
  providerFailureResult,
} from '../src/provider-errors.js';


test('maps explicit provider failures and codes to approved statuses', () => {
  assert.equal(
    classifyProviderFailure(new ProviderFailure('paywalled')),
    'paywalled',
  );
  assert.equal(
    classifyProviderFailure({ provider_status: 'not_entitled' }),
    'not_entitled',
  );
  assert.equal(
    classifyProviderFailure({ code: 'session-expired' }),
    'authentication_required',
  );
  assert.equal(
    classifyProviderFailure({ code: 'TOO MANY REQUESTS' }),
    'rate_limited',
  );
});

test('maps supported HTTP statuses without releasing response details', () => {
  const expectations = new Map([
    [401, 'authentication_required'],
    [402, 'paywalled'],
    [403, 'not_entitled'],
    [404, 'not_found'],
    [408, 'unavailable'],
    [425, 'rate_limited'],
    [429, 'rate_limited'],
    [500, 'unavailable'],
    [503, 'unavailable'],
  ]);

  for (const [status, expected] of expectations) {
    assert.equal(classifyProviderFailure({ status }), expected);
  }
  assert.equal(classifyProviderFailure({ statusCode: '429' }), 'rate_limited');
  assert.equal(classifyProviderFailure({ http_status: 503 }), 'unavailable');
});

test('uses constrained message matching for browser-originated failures', () => {
  const expectations = new Map([
    ['Premium required to view this report', 'paywalled'],
    ['Access denied for this account', 'not_entitled'],
    ['Your session expired; please login again', 'authentication_required'],
    ['Company TCS not found', 'not_found'],
    ['Too many requests; retry after 30 seconds', 'rate_limited'],
    ['Browser page closed unexpectedly', 'unavailable'],
  ]);

  for (const [message, expected] of expectations) {
    assert.equal(classifyProviderFailure(new Error(message)), expected);
  }
});

test('fails closed for unknown, malformed, and ambiguous failures', () => {
  for (const failure of [
    null,
    undefined,
    42,
    {},
    { status: 400 },
    { provider_status: 'success' },
    { code: 'SOMETHING_NEW' },
    'The request did not work',
  ]) {
    assert.equal(classifyProviderFailure(failure), 'unavailable');
  }
});

test('explicit classification takes precedence over incidental message text', () => {
  assert.equal(
    classifyProviderFailure({
      provider_status: 'not_entitled',
      status: 401,
      message: 'rate limited',
    }),
    'not_entitled',
  );
  assert.equal(
    classifyProviderFailure({
      code: 'COMPANY_NOT_FOUND',
      status: 503,
      message: 'session expired',
    }),
    'not_found',
  );
});

test('produces a null-payload envelope that cannot leak provider secrets', () => {
  const secret = 'Bearer must-never-escape';
  const failure = Object.assign(
    new Error(`Session expired: ${secret}`),
    {
      headers: { authorization: secret, cookie: 'private-session' },
      response_body: `<html>${secret}</html>`,
      stack: `provider stack ${secret}`,
      url: `https://example.invalid/?token=${secret}`,
    },
  );

  const result = providerFailureResult('get_financials', failure);
  const serialized = JSON.stringify(result);

  assert.deepEqual(result, {
    payload: null,
    status: 'authentication_required',
    tool_name: 'get_financials',
  });
  assert.equal(serialized.includes(secret), false);
  assert.equal(serialized.includes('private-session'), false);
  assert.equal(serialized.includes('provider stack'), false);
});

test('rejects unsupported explicit ProviderFailure statuses', () => {
  assert.throws(
    () => new ProviderFailure('provider_exploded'),
    /approved failure status/,
  );
});

test('does not trust throwing property getters', () => {
  const hostile = {};
  Object.defineProperty(hostile, 'provider_status', {
    get() {
      throw new Error('do not expose this value');
    },
  });
  Object.defineProperty(hostile, 'message', {
    get() {
      throw new Error('Bearer secret');
    },
  });

  assert.equal(classifyProviderFailure(hostile), 'unavailable');
});
