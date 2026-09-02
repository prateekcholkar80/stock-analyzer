import assert from 'node:assert/strict';
import test from 'node:test';

import { probeAuthentication } from '../src/authentication-probe.js';


function browserProbe({
  finalUrl = 'https://www.tijorifinance.com/',
  responseStatus = 200,
  authenticatedCount = 0,
  authenticatedVisibleCount = authenticatedCount,
  challengeCount = 0,
  challengeVisibleCount = challengeCount,
  loginCount = 0,
  loginVisibleCount = loginCount,
  failure,
} = {}) {
  const observed = {};
  const context = {};
  return {
    observed,
    runner: {
      async run(task) {
        if (failure) throw failure;
        return task({
          async goto(url, options) {
            observed.url = url;
            observed.options = options;
            return { status: () => responseStatus };
          },
          locator(selector) {
            const category = selector.includes('challenge-form')
              ? 'challenge'
              : selector.includes('input[type="password"]')
                ? 'login'
                : 'authenticated';
            const count = {
              authenticated: authenticatedCount,
              challenge: challengeCount,
              login: loginCount,
            }[category];
            const visibleCount = {
              authenticated: authenticatedVisibleCount,
              challenge: challengeVisibleCount,
              login: loginVisibleCount,
            }[category];
            return {
              async count() {
                return count;
              },
              nth(index) {
                return {
                  async isVisible() {
                    return index < visibleCount;
                  },
                };
              },
            };
          },
          context() {
            return context;
          },
          url() {
            return finalUrl;
          },
        });
      },
    },
  };
}

test('requires positive authenticated UI evidence', async () => {
  const fixture = browserProbe({ authenticatedCount: 1 });

  const result = await probeAuthentication(fixture.runner);

  assert.deepEqual(result, { authenticated: true, status: 'authenticated' });
  assert.equal(Object.isFrozen(result), true);
  assert.equal(fixture.observed.url, 'https://www.tijorifinance.com/');
  assert.deepEqual(fixture.observed.options, {
    timeout: 15_000,
    waitUntil: 'domcontentloaded',
  });
});

test('classifies login redirects and login controls as authentication required', async () => {
  for (const fixture of [
    browserProbe({ finalUrl: 'https://www.tijorifinance.com/login/' }),
    browserProbe({ finalUrl: 'https://www.tijorifinance.com/sign-in' }),
    browserProbe({ loginCount: 1, authenticatedCount: 1 }),
    browserProbe({ responseStatus: 401 }),
  ]) {
    assert.deepEqual(await probeAuthentication(fixture.runner), {
      authenticated: false,
      status: 'authentication_required',
    });
  }
});

test('ignores hidden login remnants when authenticated controls are visible', async () => {
  const fixture = browserProbe({
    authenticatedCount: 1,
    loginCount: 2,
    loginVisibleCount: 0,
  });

  assert.deepEqual(await probeAuthentication(fixture.runner), {
    authenticated: true,
    status: 'authenticated',
  });
});

test('does not accept hidden authenticated controls', async () => {
  const fixture = browserProbe({
    authenticatedCount: 1,
    authenticatedVisibleCount: 0,
  });

  assert.deepEqual(await probeAuthentication(fixture.runner), {
    authenticated: false,
    status: 'unavailable',
  });
});

test('accepts persisted account-page proof when Tijori exposes no profile control', async () => {
  const fixture = browserProbe();
  let received;

  const result = await probeAuthentication(fixture.runner, {
    persistedSessionVerifier: async (dependencies) => {
      received = dependencies;
      return { authenticated: true, status: 'authenticated' };
    },
  });

  assert.deepEqual(result, { authenticated: true, status: 'authenticated' });
  assert.equal(typeof received.page.url, 'function');
  assert.equal(received.context !== null, true);
});

test('fails closed when persisted account-page proof is unavailable', async () => {
  const fixture = browserProbe();

  const result = await probeAuthentication(fixture.runner, {
    persistedSessionVerifier: async () => ({
      authenticated: false,
      status: 'unavailable',
    }),
  });

  assert.deepEqual(result, { authenticated: false, status: 'unavailable' });
});

test('ignores hidden challenge remnants after positive authentication', async () => {
  const fixture = browserProbe({
    authenticatedCount: 1,
    challengeCount: 1,
    challengeVisibleCount: 0,
  });

  assert.deepEqual(await probeAuthentication(fixture.runner), {
    authenticated: true,
    status: 'authenticated',
  });
});

test('fails closed for challenge pages and ambiguous markup', async () => {
  for (const fixture of [
    browserProbe({ challengeCount: 1, authenticatedCount: 1 }),
    browserProbe(),
  ]) {
    assert.deepEqual(await probeAuthentication(fixture.runner), {
      authenticated: false,
      status: 'unavailable',
    });
  }
});

test('fails closed for unsafe redirects and unsuccessful provider responses', async () => {
  for (const fixture of [
    browserProbe({ finalUrl: 'http://www.tijorifinance.com/' }),
    browserProbe({ finalUrl: 'https://tijorifinance.com.evil.example/' }),
    browserProbe({ finalUrl: 'https://user:password@tijorifinance.com/' }),
    browserProbe({ finalUrl: 'not a URL' }),
    browserProbe({ responseStatus: 403 }),
    browserProbe({ responseStatus: 429 }),
    browserProbe({ responseStatus: 503 }),
    browserProbe({ responseStatus: undefined }),
  ]) {
    assert.deepEqual(await probeAuthentication(fixture.runner), {
      authenticated: false,
      status: 'unavailable',
    });
  }
});

test('sanitizes browser and DOM failures', async () => {
  const secret = 'Bearer secret-provider-value';
  for (const fixture of [
    browserProbe({ failure: new Error(secret) }),
    {
      runner: {
        async run(task) {
          return task({
            async goto() {
              throw new Error(secret);
            },
          });
        },
      },
    },
  ]) {
    const result = await probeAuthentication(fixture.runner);
    assert.deepEqual(result, { authenticated: false, status: 'unavailable' });
    assert.equal(JSON.stringify(result).includes(secret), false);
  }
});

test('validates the runner and bounded timeout without opening a browser', async () => {
  for (const runner of [null, {}, { run: null }]) {
    await assert.rejects(() => probeAuthentication(runner), /browser runner/);
  }
  const fixture = browserProbe({ authenticatedCount: 1 });
  for (const timeoutMs of [999, 30_001, 1.5, '15000']) {
    await assert.rejects(
      () => probeAuthentication(fixture.runner, { timeoutMs }),
      /outside safe bounds/,
    );
  }
  await assert.rejects(
    () => probeAuthentication(fixture.runner, { persistedSessionVerifier: null }),
    /persisted-session verifier/,
  );
  assert.deepEqual(fixture.observed, {});
});

test('accepts a bounded explicit timeout', async () => {
  const fixture = browserProbe({ authenticatedCount: 1 });

  const result = await probeAuthentication(fixture.runner, { timeoutMs: 1_000 });

  assert.equal(result.authenticated, true);
  assert.equal(fixture.observed.options.timeout, 1_000);
});
