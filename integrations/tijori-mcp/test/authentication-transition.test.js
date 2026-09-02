import assert from 'node:assert/strict';
import test from 'node:test';

import {
  captureAuthenticationBaseline,
  verifyAuthenticatedTransition,
  verifyPersistedAuthenticatedSession,
} from '../src/authentication-transition.js';

const anonymousCookie = {
  name: 'session',
  value: 'anonymous-value',
  domain: '.tijorifinance.com',
  path: '/',
  secure: true,
};
const authenticatedCookie = { ...anonymousCookie, value: 'authenticated-value' };

function fixture({
  accountMarkerCount = 2,
  challengeVisible = 0,
  cookies = [authenticatedCookie],
  origins = [],
  loginActionVisible = false,
  loginVisible = 0,
  startUrl = 'https://www.tijorifinance.com/',
} = {}) {
  const observed = { evaluated: 0 };
  const page = {
    async evaluate() {
      observed.evaluated += 1;
      return { accountMarkerCount, loginActionVisible };
    },
    async goto() { throw new Error('authentication proof must not navigate'); },
    locator(selector) {
      const count = selector.includes('challenge-form') ? challengeVisible : loginVisible;
      return {
        async count() { return count; },
        nth(index) {
          return { async isVisible() { return index < count; } };
        },
      };
    },
    url() { return startUrl; },
  };
  const context = { async storageState() { return { cookies, origins }; } };
  return { context, observed, page };
}

async function anonymousBaseline() {
  const initial = fixture({ cookies: [anonymousCookie] });
  return captureAuthenticationBaseline(initial);
}

test('accepts changed secure first-party state plus authenticated account shell', async () => {
  const browser = fixture();
  const baseline = await anonymousBaseline();

  const result = await verifyAuthenticatedTransition({ ...browser, baseline });

  assert.deepEqual(result, { authenticated: true, status: 'authenticated' });
  assert.equal(Object.isFrozen(result), true);
  assert.equal(browser.observed.evaluated, 1);
});

test('rejects unchanged, insecure, and third-party cookie state', async () => {
  const baseline = await anonymousBaseline();
  for (const cookies of [
    [anonymousCookie],
    [{ ...authenticatedCookie, secure: false }],
    [{ ...authenticatedCookie, domain: '.example.com' }],
  ]) {
    const result = await verifyAuthenticatedTransition({
      ...fixture({ cookies }),
      baseline,
    });
    assert.deepEqual(result, { authenticated: false, status: 'unavailable' });
  }
});

test('verifies an already-persisted secure first-party session', async () => {
  const browser = fixture();

  const result = await verifyPersistedAuthenticatedSession(browser);

  assert.deepEqual(result, { authenticated: true, status: 'authenticated' });
});

test('persisted verification still requires secure first-party session state', async () => {
  for (const cookies of [
    [],
    [{ ...authenticatedCookie, secure: false }],
    [{ ...authenticatedCookie, domain: '.example.com' }],
  ]) {
    assert.deepEqual(
      await verifyPersistedAuthenticatedSession(fixture({ cookies })),
      { authenticated: false, status: 'unavailable' },
    );
  }
});

test('accepts changed first-party local storage when Tijori does not rotate a cookie', async () => {
  const baselineBrowser = fixture({ cookies: [], origins: [] });
  const baseline = await captureAuthenticationBaseline(baselineBrowser);
  const browser = fixture({
    cookies: [],
    origins: [{
      origin: 'https://www.tijorifinance.com',
      localStorage: [{ name: 'authenticated-session', value: 'opaque-value' }],
    }],
  });

  assert.deepEqual(
    await verifyAuthenticatedTransition({ ...browser, baseline }),
    { authenticated: true, status: 'authenticated' },
  );
});

test('rejects unchanged and non-Tijori local storage', async () => {
  const stored = [{
    origin: 'https://www.tijorifinance.com',
    localStorage: [{ name: 'session', value: 'unchanged' }],
  }];
  const baseline = await captureAuthenticationBaseline(
    fixture({ cookies: [], origins: stored }),
  );

  for (const origins of [
    stored,
    [{
      origin: 'https://example.com',
      localStorage: [{ name: 'session', value: 'changed' }],
    }],
  ]) {
    assert.deepEqual(
      await verifyAuthenticatedTransition({
        ...fixture({ cookies: [], origins }),
        baseline,
      }),
      { authenticated: false, status: 'unavailable' },
    );
  }
});

test('rejects visible login and challenge controls before shell probing', async () => {
  const baseline = await anonymousBaseline();
  for (const browser of [
    fixture({ loginVisible: 1 }),
    fixture({ challengeVisible: 1 }),
    fixture({ startUrl: 'https://www.tijorifinance.com/login/' }),
  ]) {
    assert.deepEqual(
      await verifyAuthenticatedTransition({ ...browser, baseline }),
      { authenticated: false, status: 'unavailable' },
    );
    assert.equal(browser.observed.evaluated, 0);
  }
});

test('requires multiple account-shell markers without a visible sign-in action', async () => {
  const baseline = await anonymousBaseline();
  for (const browser of [
    fixture({ accountMarkerCount: 0 }),
    fixture({ accountMarkerCount: 1 }),
    fixture({ loginActionVisible: true }),
  ]) {
    assert.deepEqual(
      await verifyAuthenticatedTransition({ ...browser, baseline }),
      { authenticated: false, status: 'unavailable' },
    );
  }
});

test('fails closed without reflecting browser or cookie failures', async () => {
  const baseline = await anonymousBaseline();
  const secret = 'Bearer provider-secret';
  const browser = fixture();
  browser.context.storageState = async () => { throw new Error(secret); };

  const result = await verifyAuthenticatedTransition({ ...browser, baseline });

  assert.deepEqual(result, { authenticated: false, status: 'unavailable' });
  assert.equal(JSON.stringify(result).includes(secret), false);
});

test('validates browser dependencies and baseline contracts', async () => {
  await assert.rejects(
    () => captureAuthenticationBaseline({ page: null, context: null }),
    /browser state/,
  );
  const browser = fixture();
  await assert.rejects(
    () => verifyAuthenticatedTransition({ ...browser, baseline: {} }),
    /baseline is invalid/,
  );
});
