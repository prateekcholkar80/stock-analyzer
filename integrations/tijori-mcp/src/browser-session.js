import { basename, isAbsolute } from 'node:path';

import { chromium } from 'playwright';


const SESSION_FILE_PATTERN = /^[a-f0-9]{64}\.json$/;
const ALLOWED_HOST = 'tijorifinance.com';

export class BrowserSessionError extends Error {
  constructor() {
    super('Tijori browser session could not complete safely');
    this.name = 'BrowserSessionError';
  }
}

export function createBrowserSessionRunner(
  sessionBoundary,
  { browserType = chromium } = {},
) {
  validateBoundary(sessionBoundary);
  if (browserType === null || typeof browserType.launch !== 'function') {
    throw new TypeError('Tijori browser runner requires a browser launcher');
  }

  let queue = Promise.resolve();
  return Object.freeze({
    run(task) {
      if (typeof task !== 'function') {
        return Promise.reject(new TypeError('Tijori browser task must be callable'));
      }
      const execution = queue.then(() => executeTask(
        browserType,
        sessionBoundary.sessionFile,
        task,
      ));
      queue = execution.catch(() => undefined);
      return execution;
    },
  });
}

async function executeTask(browserType, sessionFile, task) {
  let browser;
  let context;
  try {
    browser = await browserType.launch({ headless: true });
    context = await browser.newContext({
      acceptDownloads: false,
      locale: 'en-IN',
      serviceWorkers: 'block',
      storageState: sessionFile,
      timezoneId: 'Asia/Kolkata',
    });
    await context.route('**/*', routeRequest);
    const page = await context.newPage();
    installPageControls(context, page);
    return await task(page);
  } catch {
    throw new BrowserSessionError();
  } finally {
    await closeQuietly(context);
    await closeQuietly(browser);
  }
}

async function routeRequest(route) {
  let allowed = false;
  try {
    const target = new URL(route.request().url());
    allowed = target.protocol === 'https:'
      && target.username === ''
      && target.password === ''
      && (
        target.hostname === ALLOWED_HOST
        || target.hostname.endsWith(`.${ALLOWED_HOST}`)
      );
  } catch {
    allowed = false;
  }
  if (allowed) {
    await route.continue();
  } else {
    await route.abort('blockedbyclient');
  }
}

function installPageControls(context, primaryPage) {
  context.on('page', (page) => {
    if (page !== primaryPage) void closeQuietly(page);
  });
  primaryPage.on('download', (download) => {
    void cancelQuietly(download);
  });
  primaryPage.on('dialog', (dialog) => {
    void dismissQuietly(dialog);
  });
}

async function closeQuietly(resource) {
  if (resource === undefined || typeof resource.close !== 'function') return;
  try {
    await resource.close();
  } catch {
    // Cleanup failures must neither leak provider details nor mask prior failures.
  }
}

async function cancelQuietly(download) {
  try {
    await download.cancel();
  } catch {
    // Downloads remain disabled even when cancellation reports a provider error.
  }
}

async function dismissQuietly(dialog) {
  try {
    await dialog.dismiss();
  } catch {
    // Dialog text is intentionally ignored and never logged.
  }
}

function validateBoundary(value) {
  if (
    value === null
    || typeof value !== 'object'
    || value.authenticated !== true
    || typeof value.sessionFile !== 'string'
    || !isAbsolute(value.sessionFile)
    || !SESSION_FILE_PATTERN.test(basename(value.sessionFile))
    || typeof value.providerContractVersion !== 'string'
    || value.providerContractVersion.length === 0
  ) {
    throw new TypeError('Tijori browser runner requires a validated session boundary');
  }
}
