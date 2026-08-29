import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BrowserSessionError,
  createBrowserSessionRunner,
} from '../src/browser-session.js';


const SESSION_FILE = `/private/tmp/${'c'.repeat(64)}.json`;
const SESSION_BOUNDARY = Object.freeze({
  authenticated: true,
  providerContractVersion: 'tijori.local_contract.v1',
  sessionFile: SESSION_FILE,
});

function browserFixture() {
  const state = {
    browserClosed: 0,
    browserOptions: null,
    contextClosed: 0,
    contextEvents: {},
    contextOptions: null,
    pageEvents: {},
    routeHandler: null,
  };
  const page = {
    on(event, handler) {
      state.pageEvents[event] = handler;
    },
  };
  const context = {
    async close() {
      state.contextClosed += 1;
    },
    async newPage() {
      return page;
    },
    on(event, handler) {
      state.contextEvents[event] = handler;
    },
    async route(pattern, handler) {
      assert.equal(pattern, '**/*');
      state.routeHandler = handler;
    },
  };
  const browser = {
    async close() {
      state.browserClosed += 1;
    },
    async newContext(options) {
      state.contextOptions = options;
      return context;
    },
  };
  const browserType = {
    async launch(options) {
      state.browserOptions = options;
      return browser;
    },
  };
  return { browserType, page, state };
}

function routeFor(url) {
  const calls = [];
  return {
    calls,
    route: {
      async abort(reason) {
        calls.push(['abort', reason]);
      },
      async continue() {
        calls.push(['continue']);
      },
      request() {
        return { url: () => url };
      },
    },
  };
}

test('opens an isolated headless IST context from the session path and closes it', async () => {
  const { browserType, page, state } = browserFixture();
  const runner = createBrowserSessionRunner(SESSION_BOUNDARY, { browserType });

  const result = await runner.run(async (receivedPage) => {
    assert.equal(receivedPage, page);
    return { company: 'TCS' };
  });

  assert.deepEqual(result, { company: 'TCS' });
  assert.deepEqual(state.browserOptions, { headless: true });
  assert.deepEqual(state.contextOptions, {
    acceptDownloads: false,
    locale: 'en-IN',
    serviceWorkers: 'block',
    storageState: SESSION_FILE,
    timezoneId: 'Asia/Kolkata',
  });
  assert.equal(state.contextClosed, 1);
  assert.equal(state.browserClosed, 1);
});

test('allows only credential-free HTTPS requests to Tijori-owned hosts', async () => {
  const { browserType, state } = browserFixture();
  const runner = createBrowserSessionRunner(SESSION_BOUNDARY, { browserType });
  await runner.run(async () => undefined);

  for (const url of [
    'https://tijorifinance.com/',
    'https://www.tijorifinance.com/company/tcs',
    'https://files.tijorifinance.com/report.pdf',
  ]) {
    const candidate = routeFor(url);
    await state.routeHandler(candidate.route);
    assert.deepEqual(candidate.calls, [['continue']]);
  }

  for (const url of [
    'http://www.tijorifinance.com/company/tcs',
    'https://tijorifinance.com.evil.example/',
    'https://evil.example/?redirect=tijorifinance.com',
    'https://user:password@www.tijorifinance.com/',
    'data:text/plain,secret',
    'not a URL',
  ]) {
    const candidate = routeFor(url);
    await state.routeHandler(candidate.route);
    assert.deepEqual(candidate.calls, [['abort', 'blockedbyclient']]);
  }
});

test('closes popups, cancels downloads, and dismisses dialogs', async () => {
  const { browserType, state } = browserFixture();
  const runner = createBrowserSessionRunner(SESSION_BOUNDARY, { browserType });
  await runner.run(async () => undefined);

  let popupClosed = 0;
  let downloadCancelled = 0;
  let dialogDismissed = 0;
  await state.contextEvents.page({ close: async () => { popupClosed += 1; } });
  await state.pageEvents.download({ cancel: async () => { downloadCancelled += 1; } });
  await state.pageEvents.dialog({ dismiss: async () => { dialogDismissed += 1; } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(popupClosed, 1);
  assert.equal(downloadCancelled, 1);
  assert.equal(dialogDismissed, 1);
});

test('sanitizes task and launcher failures while always closing available resources', async () => {
  const fixture = browserFixture();
  const runner = createBrowserSessionRunner(SESSION_BOUNDARY, {
    browserType: fixture.browserType,
  });
  await assert.rejects(
    () => runner.run(async () => {
      throw new Error('Bearer secret-provider-value');
    }),
    (error) => (
      error instanceof BrowserSessionError
      && !error.message.includes('secret-provider-value')
    ),
  );
  assert.equal(fixture.state.contextClosed, 1);
  assert.equal(fixture.state.browserClosed, 1);

  const failedLaunch = createBrowserSessionRunner(SESSION_BOUNDARY, {
    browserType: {
      async launch() {
        throw new Error('cookie=private');
      },
    },
  });
  await assert.rejects(() => failedLaunch.run(async () => undefined), BrowserSessionError);
});

test('serializes tasks through one runner', async () => {
  let active = 0;
  let maximumActive = 0;
  const browserType = {
    async launch() {
      return {
        async close() {},
        async newContext() {
          return {
            async close() {},
            async newPage() {
              return { on() {} };
            },
            on() {},
            async route() {},
          };
        },
      };
    },
  };
  const runner = createBrowserSessionRunner(SESSION_BOUNDARY, { browserType });
  const task = () => runner.run(async () => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    await new Promise((resolve) => setImmediate(resolve));
    active -= 1;
  });

  await Promise.all([task(), task(), task()]);
  assert.equal(maximumActive, 1);
});

test('rejects unvalidated boundaries, launchers, and tasks', async () => {
  for (const boundary of [
    null,
    {},
    { ...SESSION_BOUNDARY, authenticated: false },
    { ...SESSION_BOUNDARY, sessionFile: '/tmp/session.json' },
  ]) {
    assert.throws(() => createBrowserSessionRunner(boundary), /validated session/);
  }
  assert.throws(
    () => createBrowserSessionRunner(SESSION_BOUNDARY, { browserType: null }),
    /browser launcher/,
  );

  const runner = createBrowserSessionRunner(SESSION_BOUNDARY, {
    browserType: browserFixture().browserType,
  });
  await assert.rejects(() => runner.run(null), /must be callable/);
});
