import { createHash } from 'node:crypto';

const ALLOWED_HOST = 'tijorifinance.com';
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

export async function captureAuthenticationBaseline({ page, context }) {
  validateBrowserObjects(page, context);
  const state = await context.storageState();
  return Object.freeze({ stateFingerprints: trustedStateFingerprints(state) });
}

export async function verifyAuthenticatedTransition({ page, context, baseline }) {
  validateBrowserObjects(page, context);
  if (
    baseline === null
    || typeof baseline !== 'object'
    || !(baseline.stateFingerprints instanceof Set)
  ) {
    throw new TypeError('Authentication baseline is invalid');
  }
  try {
    const currentUrl = safeProviderUrl(page.url());
    if (currentUrl === null || isLoginPath(currentUrl)) return rejected();
    const [visibleLogin, visibleChallenge, state] = await Promise.all([
      visibleCount(page, LOGIN_SELECTOR),
      visibleCount(page, CHALLENGE_SELECTOR),
      context.storageState(),
    ]);
    if (
      visibleLogin > 0
      || visibleChallenge > 0
      || !hasNewTrustedState(state, baseline.stateFingerprints)
    ) {
      return rejected();
    }

    const shellEvidence = await page.evaluate(findVisibleAccountShellEvidence);
    if (
      shellEvidence === null
      || typeof shellEvidence !== 'object'
      || shellEvidence.loginActionVisible !== false
      || !Number.isInteger(shellEvidence.accountMarkerCount)
      || shellEvidence.accountMarkerCount < 2
    ) {
      return rejected();
    }
    return Object.freeze({ authenticated: true, status: 'authenticated' });
  } catch {
    return rejected();
  }
}

export async function verifyPersistedAuthenticatedSession({ page, context }) {
  validateBrowserObjects(page, context);
  return verifyAuthenticatedTransition({
    page,
    context,
    baseline: Object.freeze({ stateFingerprints: new Set() }),
  });
}

function validateBrowserObjects(page, context) {
  if (
    page === null
    || typeof page?.url !== 'function'
    || typeof page?.locator !== 'function'
    || typeof page?.evaluate !== 'function'
    || typeof page?.goto !== 'function'
    || context === null
    || typeof context?.storageState !== 'function'
  ) {
    throw new TypeError('Authentication transition requires browser state');
  }
}

function trustedStateFingerprints(state) {
  if (
    state === null
    || typeof state !== 'object'
    || !Array.isArray(state.cookies)
    || !Array.isArray(state.origins)
  ) {
    throw new TypeError('Authentication browser state is invalid');
  }
  const result = new Set();
  for (const cookie of state.cookies) {
    if (!isFirstPartySecureCookie(cookie)) continue;
    result.add(fingerprint(['cookie', cookieIdentity(cookie), cookie.value]));
  }
  for (const originState of state.origins) {
    const origin = safeProviderOrigin(originState?.origin);
    if (origin === null || !Array.isArray(originState?.localStorage)) continue;
    for (const item of originState.localStorage) {
      if (
        typeof item?.name !== 'string'
        || item.name.length === 0
        || typeof item?.value !== 'string'
      ) {
        continue;
      }
      result.add(fingerprint(['localStorage', origin, item.name, item.value]));
    }
  }
  return result;
}

function hasNewTrustedState(state, baseline) {
  const current = trustedStateFingerprints(state);
  for (const item of current) {
    if (!baseline.has(item)) return true;
  }
  return false;
}

function fingerprint(parts) {
  return createHash('sha256').update(JSON.stringify(parts)).digest('hex');
}

function isFirstPartySecureCookie(cookie) {
  if (
    cookie === null
    || typeof cookie !== 'object'
    || typeof cookie.name !== 'string'
    || cookie.name.length === 0
    || typeof cookie.value !== 'string'
    || typeof cookie.domain !== 'string'
    || typeof cookie.path !== 'string'
    || cookie.secure !== true
  ) {
    return false;
  }
  const domain = cookie.domain.replace(/^\./, '').toLowerCase();
  return domain === ALLOWED_HOST || domain.endsWith(`.${ALLOWED_HOST}`);
}

function cookieIdentity(cookie) {
  return `${cookie.name}\0${cookie.domain.toLowerCase()}\0${cookie.path}`;
}

function safeProviderOrigin(value) {
  const target = safeProviderUrl(value);
  if (target === null || target.pathname !== '/' || target.search || target.hash) {
    return null;
  }
  return target.origin;
}

async function visibleCount(page, selector) {
  const locator = page.locator(selector);
  const count = await locator.count();
  if (!Number.isInteger(count) || count < 0) return 0;
  const bounded = Math.min(count, 100);
  const visibility = await Promise.all(
    Array.from({ length: bounded }, (_, index) => locator.nth(index).isVisible()),
  );
  return visibility.filter((visible) => visible === true).length;
}

function safeProviderUrl(value) {
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

function isLoginPath(target) {
  return /\/(?:login|signin|sign-in)(?:\/|$)/i.test(target.pathname);
}

function findVisibleAccountShellEvidence() {
  const accountLabels = new Set([
    'alerts',
    'portfolio',
    'raw material',
    'watchlist',
  ]);
  const loginLabels = new Set(['log in', 'login', 'sign in']);
  const observedAccountLabels = new Set();
  let loginActionVisible = false;
  for (const element of document.querySelectorAll('a,button,[role="button"]')) {
    if (element.getClientRects().length === 0) continue;
    const label = (element.textContent ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
    if (accountLabels.has(label)) observedAccountLabels.add(label);
    if (loginLabels.has(label)) loginActionVisible = true;
  }
  return {
    accountMarkerCount: observedAccountLabels.size,
    loginActionVisible,
  };
}

function rejected() {
  return Object.freeze({ authenticated: false, status: 'unavailable' });
}
