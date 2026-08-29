const PROBE_URL = 'https://www.tijorifinance.com/';
const ALLOWED_HOST = 'tijorifinance.com';
const DEFAULT_TIMEOUT_MS = 15_000;

const AUTHENTICATED_SELECTOR = [
  'a[href*="/logout"]',
  'form[action*="/logout"]',
  '[data-testid*="user-menu"]',
  '[data-testid*="account-menu"]',
  '[aria-label*="user menu" i]',
  '[aria-label*="profile menu" i]',
].join(',');
const LOGIN_SELECTOR = [
  'input[type="password"]',
  'form[action*="/login"]',
  'form[action*="/signin"]',
  'a[href*="/login"]',
  'a[href*="/signin"]',
].join(',');
const CHALLENGE_SELECTOR = [
  '#challenge-form',
  '[id*="cf-challenge"]',
  'form[action*="challenge"]',
  'iframe[src*="captcha"]',
].join(',');

export async function probeAuthentication(
  browserRunner,
  { timeoutMs = DEFAULT_TIMEOUT_MS } = {},
) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Authentication probe requires a browser runner');
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1_000 || timeoutMs > 30_000) {
    throw new TypeError('Authentication probe timeout is outside safe bounds');
  }

  try {
    const status = await browserRunner.run(async (page) => {
      const response = await page.goto(PROBE_URL, {
        timeout: timeoutMs,
        waitUntil: 'domcontentloaded',
      });
      const responseStatus = response?.status();
      if (responseStatus === 401) return 'authentication_required';
      if (
        !Number.isInteger(responseStatus)
        || responseStatus < 200
        || responseStatus >= 400
      ) {
        return 'unavailable';
      }

      const finalUrl = safeUrl(page.url());
      if (finalUrl === null) return 'unavailable';
      if (/\/(?:login|signin|sign-in)(?:\/|$)/i.test(finalUrl.pathname)) {
        return 'authentication_required';
      }

      const [challengeCount, loginCount, authenticatedCount] = await Promise.all([
        boundedCount(page, CHALLENGE_SELECTOR),
        boundedCount(page, LOGIN_SELECTOR),
        boundedCount(page, AUTHENTICATED_SELECTOR),
      ]);
      if (challengeCount > 0) return 'unavailable';
      if (loginCount > 0) return 'authentication_required';
      if (authenticatedCount > 0) return 'authenticated';
      return 'unavailable';
    });
    return probeResult(status);
  } catch {
    return probeResult('unavailable');
  }
}

function safeUrl(value) {
  try {
    const target = new URL(value);
    if (
      target.protocol !== 'https:'
      || target.username !== ''
      || target.password !== ''
      || (
        target.hostname !== ALLOWED_HOST
        && !target.hostname.endsWith(`.${ALLOWED_HOST}`)
      )
    ) {
      return null;
    }
    return target;
  } catch {
    return null;
  }
}

async function boundedCount(page, selector) {
  const count = await page.locator(selector).count();
  return Number.isInteger(count) && count >= 0 ? Math.min(count, 100) : 0;
}

function probeResult(status) {
  const approved = new Set([
    'authenticated',
    'authentication_required',
    'unavailable',
  ]);
  const normalized = approved.has(status) ? status : 'unavailable';
  return Object.freeze({
    authenticated: normalized === 'authenticated',
    status: normalized,
  });
}
