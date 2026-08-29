import { failureResult } from './result-envelope.js';


const FAILURE_STATUSES = new Set([
  'not_found',
  'not_entitled',
  'paywalled',
  'authentication_required',
  'rate_limited',
  'unavailable',
]);

const CODE_TO_STATUS = new Map([
  ['AUTHENTICATION_REQUIRED', 'authentication_required'],
  ['INVALID_SESSION', 'authentication_required'],
  ['SESSION_EXPIRED', 'authentication_required'],
  ['UNAUTHORIZED', 'authentication_required'],
  ['NOT_ENTITLED', 'not_entitled'],
  ['FORBIDDEN', 'not_entitled'],
  ['PAYWALLED', 'paywalled'],
  ['PAYMENT_REQUIRED', 'paywalled'],
  ['NOT_FOUND', 'not_found'],
  ['COMPANY_NOT_FOUND', 'not_found'],
  ['RATE_LIMITED', 'rate_limited'],
  ['TOO_MANY_REQUESTS', 'rate_limited'],
  ['UNAVAILABLE', 'unavailable'],
  ['TIMEOUT', 'unavailable'],
  ['NETWORK_ERROR', 'unavailable'],
]);

const HTTP_STATUS_TO_RESULT = new Map([
  [401, 'authentication_required'],
  [402, 'paywalled'],
  [403, 'not_entitled'],
  [404, 'not_found'],
  [408, 'unavailable'],
  [425, 'rate_limited'],
  [429, 'rate_limited'],
]);

export class ProviderFailure extends Error {
  constructor(status) {
    if (!FAILURE_STATUSES.has(status)) {
      throw new TypeError('Provider failure requires an approved failure status');
    }
    super(`Provider request failed: ${status}`);
    this.name = 'ProviderFailure';
    this.status = status;
  }
}

export function providerFailureResult(toolName, failure) {
  return failureResult(toolName, classifyProviderFailure(failure));
}

export function classifyProviderFailure(failure) {
  if (failure instanceof ProviderFailure) return failure.status;

  const explicitStatus = readString(failure, 'provider_status');
  if (FAILURE_STATUSES.has(explicitStatus)) return explicitStatus;

  const code = normalizeCode(readString(failure, 'code'));
  if (CODE_TO_STATUS.has(code)) return CODE_TO_STATUS.get(code);

  const httpStatus = readHttpStatus(failure);
  if (HTTP_STATUS_TO_RESULT.has(httpStatus)) {
    return HTTP_STATUS_TO_RESULT.get(httpStatus);
  }
  if (httpStatus >= 500 && httpStatus <= 599) return 'unavailable';

  return classifySafeMessage(readMessage(failure));
}

function classifySafeMessage(message) {
  const normalized = message.slice(0, 2_000).toLowerCase();
  if (!normalized) return 'unavailable';

  if (/\b(paywall(?:ed)?|payment required|premium required|subscription required)\b/.test(normalized)) {
    return 'paywalled';
  }
  if (/\b(not entitled|access denied|permission denied|forbidden)\b/.test(normalized)) {
    return 'not_entitled';
  }
  if (/\b(login required|sign[ -]?in required|session expired|unauthori[sz]ed)\b/.test(normalized)) {
    return 'authentication_required';
  }
  if (/\b(company|instrument|symbol)\b.{0,24}\bnot found\b/.test(normalized)) {
    return 'not_found';
  }
  if (/\b(rate limit(?:ed)?|too many requests|retry after)\b/.test(normalized)) {
    return 'rate_limited';
  }
  return 'unavailable';
}

function readHttpStatus(failure) {
  for (const key of ['status', 'statusCode', 'http_status']) {
    const value = readValue(failure, key);
    if (Number.isInteger(value)) return value;
    if (typeof value === 'string' && /^\d{3}$/.test(value)) {
      return Number.parseInt(value, 10);
    }
  }
  return 0;
}

function readMessage(failure) {
  if (typeof failure === 'string') return failure;
  return readString(failure, 'message');
}

function readString(value, key) {
  const candidate = readValue(value, key);
  return typeof candidate === 'string' ? candidate : '';
}

function readValue(value, key) {
  if (value === null || (typeof value !== 'object' && typeof value !== 'function')) {
    return undefined;
  }
  try {
    return value[key];
  } catch {
    return undefined;
  }
}

function normalizeCode(code) {
  return code.trim().toUpperCase().replaceAll('-', '_').replaceAll(' ', '_');
}
