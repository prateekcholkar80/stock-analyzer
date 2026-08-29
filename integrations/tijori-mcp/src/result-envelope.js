const APPROVED_TOOL_NAMES = Object.freeze([
  'search_company',
  'resolve_company_ids',
  'get_company_overview',
  'get_financials',
  'get_shareholding',
]);

const TOOL_STATUSES = Object.freeze([
  'success',
  'not_found',
  'not_entitled',
  'paywalled',
  'authentication_required',
  'rate_limited',
  'unavailable',
]);

const APPROVED_TOOLS = new Set(APPROVED_TOOL_NAMES);
const APPROVED_STATUSES = new Set(TOOL_STATUSES);
const FAILURE_STATUSES = new Set(
  TOOL_STATUSES.filter((status) => status !== 'success'),
);
const PROHIBITED_KEYS = new Set([
  'api_key',
  'apikey',
  'authorization',
  'cookie',
  'csrf',
  'password',
  'passwd',
  'secret',
  'session',
  'set-cookie',
  'token',
]);

const MAX_PAYLOAD_BYTES = 1_000_000;
const MAX_JSON_DEPTH = 8;
const MAX_JSON_NODES = 10_000;
const MAX_KEY_LENGTH = 160;
const MAX_STRING_LENGTH = 10_000;

export {
  APPROVED_TOOL_NAMES,
  MAX_PAYLOAD_BYTES,
  TOOL_STATUSES,
};

export function successResult(toolName, payload) {
  return validateToolResult({
    tool_name: toolName,
    status: 'success',
    payload,
  });
}

export function failureResult(toolName, status) {
  if (!FAILURE_STATUSES.has(status)) {
    throw new TypeError('Tool failure requires an approved failure status');
  }
  return validateToolResult({
    tool_name: toolName,
    status,
    payload: null,
  });
}

export function validateToolResult(candidate) {
  if (!isPlainObject(candidate)) {
    throw new TypeError('Tool result must be a plain object');
  }
  const keys = Object.keys(candidate).sort();
  if (
    keys.length !== 3
    || keys[0] !== 'payload'
    || keys[1] !== 'status'
    || keys[2] !== 'tool_name'
  ) {
    throw new TypeError('Tool result contains an unsupported field');
  }
  if (!APPROVED_TOOLS.has(candidate.tool_name)) {
    throw new TypeError('Tool result uses an unapproved tool name');
  }
  if (!APPROVED_STATUSES.has(candidate.status)) {
    throw new TypeError('Tool result uses an unsupported status');
  }
  if (candidate.status === 'success') {
    if (!isPlainObject(candidate.payload)) {
      throw new TypeError('Successful tool result requires an object payload');
    }
  } else if (candidate.payload !== null) {
    throw new TypeError('Failed tool result cannot release a payload');
  }

  const normalized = normalizeJson(candidate, 0, { count: 0 });
  const encoded = JSON.stringify(normalized);
  if (Buffer.byteLength(encoded, 'utf8') > MAX_PAYLOAD_BYTES) {
    throw new RangeError('Tool result exceeds the response size limit');
  }
  return deepFreeze(normalized);
}

export function serializeToolResult(candidate) {
  return JSON.stringify(validateToolResult(candidate));
}

function normalizeJson(value, depth, nodes) {
  nodes.count += 1;
  if (nodes.count > MAX_JSON_NODES || depth > MAX_JSON_DEPTH) {
    throw new RangeError('Tool result exceeds JSON structure limits');
  }
  if (value === null || typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new TypeError('Tool result numbers must be finite');
    }
    return value;
  }
  if (typeof value === 'string') {
    if (value.length > MAX_STRING_LENGTH) {
      throw new RangeError('Tool result string exceeds its size limit');
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => normalizeJson(item, depth + 1, nodes));
  }
  if (!isPlainObject(value)) {
    throw new TypeError('Tool result contains a non-JSON value');
  }

  const normalized = {};
  for (const key of Object.keys(value).sort()) {
    if (!key || key.length > MAX_KEY_LENGTH) {
      throw new RangeError('Tool result key is blank or oversized');
    }
    if (isProhibitedKey(key)) {
      throw new TypeError('Tool result contains a prohibited sensitive field');
    }
    normalized[key] = normalizeJson(value[key], depth + 1, nodes);
  }
  return normalized;
}

function isProhibitedKey(key) {
  const normalized = key.toLowerCase().replaceAll(' ', '_');
  return PROHIBITED_KEYS.has(normalized)
    || normalized === 'sessionid'
    || normalized.includes('password')
    || normalized.endsWith('_token')
    || normalized.endsWith('_secret')
    || normalized.endsWith('_cookie')
    || normalized.endsWith('_csrf');
}

function isPlainObject(value) {
  if (value === null || typeof value !== 'object') return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function deepFreeze(value) {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
