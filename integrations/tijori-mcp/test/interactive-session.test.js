import assert from 'node:assert/strict';
import {
  chmod,
  lstat,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import {
  InteractiveSessionError,
  provisionInteractiveSession,
} from '../src/interactive-session.js';


const SESSION_NAME = `${'a'.repeat(64)}.json`;
const SESSION_CONTENT = '{"cookies":[],"origins":[]}';

async function sessionRoot(context) {
  const created = await mkdtemp(join(tmpdir(), 'jarvis-tijori-interactive-'));
  const root = await realpath(created);
  await chmod(root, 0o700);
  context.after(async () => rm(root, { recursive: true, force: true }));
  return root;
}

function browserFixture({ content = SESSION_CONTENT, responseStatus = 200 } = {}) {
  const state = {
    browserClosed: 0,
    browserOptions: null,
    contextClosed: 0,
    contextEvents: {},
    contextOptions: null,
    goto: null,
    pageEvents: {},
    routeHandler: null,
    storageOptions: null,
    waitTimeouts: [],
  };
  const page = {
    async goto(url, options) {
      state.goto = { options, url };
      return { status: () => responseStatus };
    },
    on(event, handler) {
      state.pageEvents[event] = handler;
    },
    async waitForTimeout(timeout) { state.waitTimeouts.push(timeout); },
  };
  const context = {
    async close() { state.contextClosed += 1; },
    async newPage() { return page; },
    on(event, handler) { state.contextEvents[event] = handler; },
    async route(pattern, handler) {
      assert.equal(pattern, '**/*');
      state.routeHandler = handler;
    },
    async storageState(options) {
      state.storageOptions = options;
      await writeFile(options.path, content, { mode: 0o600 });
    },
  };
  const browser = {
    async close() { state.browserClosed += 1; },
    async newContext(options) {
      state.contextOptions = options;
      return context;
    },
  };
  return {
    browserType: {
      async launch(options) {
        state.browserOptions = options;
        return browser;
      },
    },
    page,
    state,
  };
}

function routeFor(url) {
  const calls = [];
  return {
    calls,
    route: {
      async abort(reason) { calls.push(['abort', reason]); },
      async continue() { calls.push(['continue']); },
      request() { return { url: () => url }; },
    },
  };
}

test('uses a visible isolated browser and atomically stores owner-only state', async (context) => {
  const root = await sessionRoot(context);
  const sessionFile = join(root, SESSION_NAME);
  const fixture = browserFixture();
  let transitionInput;
  const baseline = Object.freeze({ marker: 'anonymous' });

  const result = await provisionInteractiveSession(
    { sessionFile, timeoutMs: 30_000 },
    {
      browserType: fixture.browserType,
      authenticationBaselineCapture: async (input) => {
        assert.equal(input.page, fixture.page);
        return baseline;
      },
      authenticationTransition: async (input) => {
        transitionInput = input;
        return { authenticated: true, status: 'authenticated' };
      },
      idFactory: () => 'fixed-id',
    },
  );

  assert.deepEqual(fixture.state.browserOptions, { headless: false });
  assert.deepEqual(fixture.state.contextOptions, {
    acceptDownloads: false,
    locale: 'en-IN',
    serviceWorkers: 'block',
    timezoneId: 'Asia/Kolkata',
  });
  assert.equal(fixture.state.goto.url, 'https://www.tijorifinance.com/');
  assert.equal(fixture.state.goto.options.timeout, 30_000);
  assert.equal(transitionInput.page, fixture.page);
  assert.equal(transitionInput.baseline, baseline);
  assert.deepEqual(fixture.state.waitTimeouts, []);
  assert.equal(fixture.state.storageOptions.indexedDB, true);
  assert.notEqual(fixture.state.storageOptions.path, sessionFile);
  assert.equal(await readFile(sessionFile, 'utf8'), SESSION_CONTENT);
  assert.equal((await lstat(sessionFile)).mode & 0o077, 0);
  assert.equal(result.status, 'ready');
  assert.match(result.session_reference_hash, /^[a-f0-9]{64}$/);
  assert.equal(JSON.stringify(result).includes(sessionFile), false);
  assert.equal(fixture.state.contextClosed, 1);
  assert.equal(fixture.state.browserClosed, 1);
  assert.deepEqual(await readdir(root), [SESSION_NAME]);
});

test('allows only credential-free HTTPS requests to Tijori-owned hosts', async (context) => {
  const root = await sessionRoot(context);
  const fixture = browserFixture();
  await provisionInteractiveSession(
    { sessionFile: join(root, SESSION_NAME), timeoutMs: 30_000 },
    {
      browserType: fixture.browserType,
      authenticationBaselineCapture: async () => ({}),
      authenticationTransition: async () => ({ authenticated: true, status: 'authenticated' }),
      idFactory: () => 'route-test',
    },
  );

  for (const url of [
    'https://tijorifinance.com/',
    'https://www.tijorifinance.com/login/',
  ]) {
    const candidate = routeFor(url);
    await fixture.state.routeHandler(candidate.route);
    assert.deepEqual(candidate.calls, [['continue']]);
  }
  for (const url of [
    'http://www.tijorifinance.com/',
    'https://tijorifinance.com.evil.example/',
    'https://user:password@tijorifinance.com/',
    'data:text/plain,secret',
  ]) {
    const candidate = routeFor(url);
    await fixture.state.routeHandler(candidate.route);
    assert.deepEqual(candidate.calls, [['abort', 'blockedbyclient']]);
  }
});

test('refuses to overwrite an existing session before browser launch', async (context) => {
  const root = await sessionRoot(context);
  const sessionFile = join(root, SESSION_NAME);
  await writeFile(sessionFile, SESSION_CONTENT, { mode: 0o600 });
  const fixture = browserFixture();

  await assert.rejects(
    () => provisionInteractiveSession(
      { sessionFile, timeoutMs: 30_000 },
      { browserType: fixture.browserType },
    ),
    InteractiveSessionError,
  );
  assert.equal(fixture.state.browserOptions, null);
  assert.equal(await readFile(sessionFile, 'utf8'), SESSION_CONTENT);
});

test('rejects insecure roots and unsafe session targets', async (context) => {
  const root = await sessionRoot(context);
  await chmod(root, 0o755);
  await assert.rejects(
    () => provisionInteractiveSession(
      { sessionFile: join(root, SESSION_NAME), timeoutMs: 30_000 },
      { browserType: browserFixture().browserType },
    ),
    InteractiveSessionError,
  );
  await chmod(root, 0o700);

  for (const sessionFile of [
    join(root, 'session.json'),
    'relative-session.json',
  ]) {
    await assert.rejects(
      () => provisionInteractiveSession(
        { sessionFile, timeoutMs: 30_000 },
        { browserType: browserFixture().browserType },
      ),
      /scoped session target/,
    );
  }
});

test('fails closed on negative authentication and cleans temporary state', async (context) => {
  const root = await sessionRoot(context);
  const fixture = browserFixture();
  const secret = 'Bearer interactive-secret';

  await assert.rejects(
    () => provisionInteractiveSession(
      { sessionFile: join(root, SESSION_NAME), timeoutMs: 30_000 },
      {
        browserType: fixture.browserType,
        authenticationBaselineCapture: async () => ({}),
        authenticationTransition: async () => {
          throw new Error(secret);
        },
      },
    ),
    (error) => (
      error instanceof InteractiveSessionError
      && !error.message.includes(secret)
    ),
  );
  assert.deepEqual(await readdir(root), []);
  assert.equal(fixture.state.contextClosed, 1);
  assert.equal(fixture.state.browserClosed, 1);
});

test('rejects malformed or oversized browser state and removes temporary files', async (context) => {
  for (const content of ['[]', 'not-json', `{"padding":"${'x'.repeat(5_000_000)}"}`]) {
    const root = await sessionRoot(context);
    const fixture = browserFixture({ content });
    await assert.rejects(
      () => provisionInteractiveSession(
        { sessionFile: join(root, SESSION_NAME), timeoutMs: 30_000 },
        {
          browserType: fixture.browserType,
          authenticationBaselineCapture: async () => ({}),
          authenticationTransition: async () => ({ authenticated: true, status: 'authenticated' }),
          idFactory: () => 'bad-state',
        },
      ),
      InteractiveSessionError,
    );
    assert.deepEqual(await readdir(root), []);
  }
});

test('validates dependencies and timeout before browser launch', async () => {
  const target = `/private/tmp/${SESSION_NAME}`;
  for (const timeoutMs of [29_999, 900_001, 1.5, '30000']) {
    await assert.rejects(
      () => provisionInteractiveSession({ sessionFile: target, timeoutMs }),
      /timeout is outside safe bounds/,
    );
  }
  await assert.rejects(
    () => provisionInteractiveSession(
      { sessionFile: target, timeoutMs: 30_000 },
      { browserType: null },
    ),
    /browser launcher/,
  );
  await assert.rejects(
    () => provisionInteractiveSession(
      { sessionFile: target, timeoutMs: 30_000 },
      { authenticationTransition: null },
    ),
    /dependencies are invalid/,
  );
  await assert.rejects(
    () => provisionInteractiveSession(
      { sessionFile: target, timeoutMs: 30_000 },
      { authenticationBaselineCapture: null },
    ),
    /dependencies are invalid/,
  );
});
