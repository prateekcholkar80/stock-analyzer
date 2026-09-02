import {
  chmod,
  link,
  lstat,
  open,
  readFile,
  realpath,
  unlink,
} from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { basename, dirname, isAbsolute, join } from 'node:path';
import process from 'node:process';

import { chromium } from 'playwright';

import {
  captureAuthenticationBaseline,
  verifyAuthenticatedTransition,
} from './authentication-transition.js';


const HOME_URL = 'https://www.tijorifinance.com/';
const ALLOWED_HOST = 'tijorifinance.com';
const SESSION_FILE_PATTERN = /^[a-f0-9]{64}\.json$/;
const MIN_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 15 * 60_000;
const MAX_SESSION_BYTES = 5_000_000;
const AUTHENTICATION_POLL_MS = 250;

export class InteractiveSessionError extends Error {
  constructor() {
    super('Interactive Tijori session provisioning could not complete safely');
    this.name = 'InteractiveSessionError';
  }
}

export async function provisionInteractiveSession(
  {
    sessionFile,
    timeoutMs = 5 * 60_000,
  },
  {
    browserType = chromium,
    authenticationBaselineCapture = captureAuthenticationBaseline,
    authenticationTransition = verifyAuthenticatedTransition,
    idFactory = randomUUID,
  } = {},
) {
  validateInputs({
    sessionFile,
    timeoutMs,
    browserType,
    authenticationBaselineCapture,
    authenticationTransition,
    idFactory,
  });
  const sessionRoot = dirname(sessionFile);
  await validateRoot(sessionRoot);
  await requireAbsent(sessionFile);

  let browser;
  let context;
  let temporaryFile;
  try {
    browser = await browserType.launch({ headless: false });
    context = await browser.newContext({
      acceptDownloads: false,
      locale: 'en-IN',
      serviceWorkers: 'block',
      timezoneId: 'Asia/Kolkata',
    });
    await context.route('**/*', routeRequest);
    const page = await context.newPage();
    installPageControls(context, page);

    const response = await page.goto(HOME_URL, {
      timeout: Math.min(timeoutMs, 30_000),
      waitUntil: 'domcontentloaded',
    });
    const responseStatus = response?.status();
    if (
      !Number.isInteger(responseStatus)
      || responseStatus < 200
      || responseStatus >= 400
    ) {
      throw new InteractiveSessionError();
    }

    const baseline = await authenticationBaselineCapture({ page, context });
    const authentication = await waitForAuthenticationTransition({
      page,
      context,
      baseline,
      timeoutMs,
      authenticationTransition,
    });
    if (
      authentication?.authenticated !== true
      || authentication?.status !== 'authenticated'
    ) {
      throw new InteractiveSessionError();
    }

    temporaryFile = await reserveTemporaryFile(sessionRoot, idFactory);
    await context.storageState({ path: temporaryFile, indexedDB: true });
    await chmod(temporaryFile, 0o600);
    const content = await validateSessionArtifact(temporaryFile);
    await syncFile(temporaryFile);
    await requireAbsent(sessionFile);
    await installWithoutReplacement(temporaryFile, sessionFile);
    temporaryFile = undefined;
    await syncDirectory(sessionRoot);

    return Object.freeze({
      session_reference_hash: createHash('sha256').update(content).digest('hex'),
      status: 'ready',
    });
  } catch (error) {
    if (error instanceof InteractiveSessionError) throw error;
    throw new InteractiveSessionError();
  } finally {
    await removeQuietly(temporaryFile);
    await closeQuietly(context);
    await closeQuietly(browser);
  }
}

function validateInputs({
  sessionFile,
  timeoutMs,
  browserType,
  authenticationBaselineCapture,
  authenticationTransition,
  idFactory,
}) {
  if (
    typeof sessionFile !== 'string'
    || !isAbsolute(sessionFile)
    || !SESSION_FILE_PATTERN.test(basename(sessionFile))
  ) {
    throw new TypeError('Interactive provisioning requires a scoped session target');
  }
  if (
    !Number.isInteger(timeoutMs)
    || timeoutMs < MIN_TIMEOUT_MS
    || timeoutMs > MAX_TIMEOUT_MS
  ) {
    throw new TypeError('Interactive provisioning timeout is outside safe bounds');
  }
  if (browserType === null || typeof browserType?.launch !== 'function') {
    throw new TypeError('Interactive provisioning requires a browser launcher');
  }
  if (
    typeof authenticationBaselineCapture !== 'function'
    || typeof authenticationTransition !== 'function'
    || typeof idFactory !== 'function'
  ) {
    throw new TypeError('Interactive provisioning dependencies are invalid');
  }
}

async function waitForAuthenticationTransition({
  page,
  context,
  baseline,
  timeoutMs,
  authenticationTransition,
}) {
  const deadline = Date.now() + timeoutMs;
  do {
    const authentication = await authenticationTransition({
      page,
      context,
      baseline,
    });
    if (
      authentication?.authenticated === true
      && authentication?.status === 'authenticated'
    ) {
      return authentication;
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await page.waitForTimeout(Math.min(AUTHENTICATION_POLL_MS, remaining));
  } while (Date.now() < deadline);
  throw new InteractiveSessionError();
}

async function validateRoot(sessionRoot) {
  try {
    const [info, canonical] = await Promise.all([
      lstat(sessionRoot),
      realpath(sessionRoot),
    ]);
    if (
      canonical !== sessionRoot
      || info.isSymbolicLink()
      || !info.isDirectory()
      || info.uid !== currentUserId()
      || (info.mode & 0o077) !== 0
    ) {
      throw new InteractiveSessionError();
    }
  } catch (error) {
    if (error instanceof InteractiveSessionError) throw error;
    throw new InteractiveSessionError();
  }
}

async function requireAbsent(path) {
  try {
    await lstat(path);
  } catch (error) {
    if (error?.code === 'ENOENT') return;
    throw new InteractiveSessionError();
  }
  throw new InteractiveSessionError();
}

async function reserveTemporaryFile(sessionRoot, idFactory) {
  const identifier = idFactory();
  if (typeof identifier !== 'string' || !/^[A-Za-z0-9-]{1,80}$/.test(identifier)) {
    throw new InteractiveSessionError();
  }
  const path = join(sessionRoot, `.provision-${identifier}.tmp`);
  const handle = await open(path, 'wx', 0o600);
  await handle.close();
  return path;
}

async function validateSessionArtifact(path) {
  const info = await lstat(path);
  if (
    info.isSymbolicLink()
    || !info.isFile()
    || info.uid !== currentUserId()
    || info.nlink !== 1
    || (info.mode & 0o077) !== 0
    || info.size < 2
    || info.size > MAX_SESSION_BYTES
  ) {
    throw new InteractiveSessionError();
  }
  const content = await readFile(path);
  if (content.length !== info.size) throw new InteractiveSessionError();
  let parsed;
  try {
    parsed = JSON.parse(content.toString('utf8'));
  } catch {
    throw new InteractiveSessionError();
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new InteractiveSessionError();
  }
  return content;
}

async function installWithoutReplacement(source, target) {
  const sourceInfo = await lstat(source);
  let linked = false;
  try {
    await link(source, target);
    linked = true;
    await unlink(source);
    const targetInfo = await lstat(target);
    if (
      !targetInfo.isFile()
      || targetInfo.dev !== sourceInfo.dev
      || targetInfo.ino !== sourceInfo.ino
      || targetInfo.nlink !== 1
    ) {
      throw new InteractiveSessionError();
    }
  } catch (error) {
    if (linked) await removeQuietly(target);
    throw error;
  }
}

async function syncFile(path) {
  const handle = await open(path, 'r');
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function syncDirectory(path) {
  const handle = await open(path, 'r');
  try {
    await handle.sync();
  } finally {
    await handle.close();
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
  if (allowed) await route.continue();
  else await route.abort('blockedbyclient');
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
    // Cleanup failures are sanitized and never mask the primary result.
  }
}

async function removeQuietly(path) {
  if (path === undefined) return;
  try {
    await unlink(path);
  } catch {
    // A reserved temporary file is always best-effort cleaned on failure.
  }
}

async function cancelQuietly(download) {
  try {
    await download.cancel();
  } catch {}
}

async function dismissQuietly(dialog) {
  try {
    await dialog.dismiss();
  } catch {}
}

function currentUserId() {
  if (typeof process.getuid !== 'function') throw new InteractiveSessionError();
  return process.getuid();
}
