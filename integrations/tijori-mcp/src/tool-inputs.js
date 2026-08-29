import { APPROVED_TOOL_NAMES } from './result-envelope.js';


const APPROVED_TOOLS = new Set(APPROVED_TOOL_NAMES);
const EXCHANGE_PATTERN = /^[A-Z0-9_.:-]+$/;
const ISIN_PATTERN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
const FINANCIAL_STATEMENTS = new Set([
  'income_statement',
  'balance_sheet',
  'cash_flow',
  'kpi_schedule',
  'segment',
  'equity_risk_debt_liquidity_context',
  'share_count',
  'working_capital',
  'capital_allocation',
  'adjustment',
]);
const PERIOD_TYPES = new Set(['annual', 'quarterly', 'monthly', 'ytd', 'ltm']);
const DEFAULT_STATEMENTS = Object.freeze([
  'income_statement',
  'balance_sheet',
  'cash_flow',
]);
const DEFAULT_PERIOD_TYPES = Object.freeze(['annual', 'quarterly']);

export function validateToolArguments(toolName, candidate) {
  if (!APPROVED_TOOLS.has(toolName)) {
    throw new TypeError('Cannot validate arguments for an unapproved tool');
  }
  requirePlainObject(candidate, 'Tool arguments');

  const validators = {
    search_company: validateSearchCompany,
    resolve_company_ids: validateResolveCompanyIds,
    get_company_overview: validateCompanyOverview,
    get_financials: validateFinancials,
    get_shareholding: validateShareholding,
  };
  return deepFreeze(validators[toolName](candidate));
}

function validateSearchCompany(value) {
  requireExactKeys(value, ['query'], ['exchanges', 'max_results']);
  const query = boundedString(value.query, 'query', 200, { collapse: true });
  const exchanges = uniqueStringArray(
    value.exchanges ?? [],
    'exchanges',
    20,
    (exchange) => {
      const normalized = boundedString(exchange, 'exchange', 32).toUpperCase();
      if (!EXCHANGE_PATTERN.test(normalized)) {
        throw new TypeError('Exchange must be a bounded market identifier');
      }
      return normalized;
    },
  );
  return {
    query,
    exchanges,
    max_results: boundedInteger(value.max_results ?? 10, 'max_results', 1, 25),
  };
}

function validateResolveCompanyIds(value) {
  requireExactKeys(value, ['locator'], ['max_candidates']);
  return {
    locator: validateLocator(value.locator),
    max_candidates: boundedInteger(
      value.max_candidates ?? 10,
      'max_candidates',
      2,
      25,
    ),
  };
}

function validateCompanyOverview(value) {
  requireExactKeys(value, ['issuer'], ['as_of_date']);
  return evidenceArguments(value);
}

function validateFinancials(value) {
  requireExactKeys(
    value,
    ['issuer'],
    ['as_of_date', 'statements', 'period_types', 'max_periods'],
  );
  return {
    ...evidenceArguments(value),
    statements: enumArray(
      value.statements ?? DEFAULT_STATEMENTS,
      'statements',
      FINANCIAL_STATEMENTS,
    ),
    period_types: enumArray(
      value.period_types ?? DEFAULT_PERIOD_TYPES,
      'period_types',
      PERIOD_TYPES,
    ),
    max_periods: boundedInteger(value.max_periods ?? 12, 'max_periods', 1, 40),
  };
}

function validateShareholding(value) {
  requireExactKeys(
    value,
    ['issuer'],
    ['as_of_date', 'quarters', 'include_promoter_pledge'],
  );
  const includePromoterPledge = value.include_promoter_pledge ?? true;
  if (typeof includePromoterPledge !== 'boolean') {
    throw new TypeError('include_promoter_pledge must be a boolean');
  }
  return {
    ...evidenceArguments(value),
    quarters: boundedInteger(value.quarters ?? 8, 'quarters', 1, 40),
    include_promoter_pledge: includePromoterPledge,
  };
}

function evidenceArguments(value) {
  return {
    issuer: validateIssuer(value.issuer),
    as_of_date: nullableDate(value.as_of_date ?? null, 'as_of_date'),
  };
}

function validateLocator(value) {
  requirePlainObject(value, 'locator');
  requireExactKeys(value, [], [
    'exchange',
    'symbol',
    'legal_name',
    'isin',
    'provider_company_id',
    'provider_slug',
  ]);
  const locator = optionalIdentityFields(value);
  if (![
    locator.symbol,
    locator.legal_name,
    locator.isin,
    locator.provider_company_id,
    locator.provider_slug,
  ].some((item) => item !== null)) {
    throw new TypeError('Issuer locator requires an identity hint');
  }
  if (locator.exchange !== null && locator.symbol === null) {
    throw new TypeError('Exchange hint requires a symbol');
  }
  return locator;
}

function validateIssuer(value) {
  requirePlainObject(value, 'issuer');
  requireExactKeys(value, ['exchange', 'symbol', 'legal_name'], [
    'isin',
    'provider_company_id',
    'provider_slug',
  ]);
  return {
    exchange: boundedString(value.exchange, 'issuer.exchange', 32).toUpperCase(),
    symbol: boundedString(value.symbol, 'issuer.symbol', 100).toUpperCase(),
    legal_name: boundedString(value.legal_name, 'issuer.legal_name', 300),
    isin: optionalIsin(value.isin),
    provider_company_id: optionalString(
      value.provider_company_id,
      'issuer.provider_company_id',
      128,
    ),
    provider_slug: optionalString(value.provider_slug, 'issuer.provider_slug', 240),
  };
}

function optionalIdentityFields(value) {
  return {
    exchange: optionalUpperString(value.exchange, 'locator.exchange', 32),
    symbol: optionalUpperString(value.symbol, 'locator.symbol', 100),
    legal_name: optionalString(value.legal_name, 'locator.legal_name', 300),
    isin: optionalIsin(value.isin),
    provider_company_id: optionalString(
      value.provider_company_id,
      'locator.provider_company_id',
      128,
    ),
    provider_slug: optionalString(value.provider_slug, 'locator.provider_slug', 240),
  };
}

function optionalIsin(value) {
  const normalized = optionalUpperString(value, 'isin', 12);
  if (normalized !== null && !ISIN_PATTERN.test(normalized)) {
    throw new TypeError('isin must use the 12-character ISIN format');
  }
  return normalized;
}

function enumArray(value, field, approved) {
  return uniqueStringArray(value, field, approved.size, (item) => {
    if (!approved.has(item)) throw new TypeError(`${field} contains an unsupported value`);
    return item;
  }, { requireItems: true });
}

function uniqueStringArray(value, field, maxItems, normalize, options = {}) {
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new TypeError(`${field} must be a bounded array`);
  }
  if (options.requireItems && value.length === 0) {
    throw new TypeError(`${field} must contain at least one value`);
  }
  const normalized = value.map((item) => {
    if (typeof item !== 'string') throw new TypeError(`${field} values must be strings`);
    return normalize(item);
  });
  if (new Set(normalized).size !== normalized.length) {
    throw new TypeError(`${field} values must be unique`);
  }
  return normalized;
}

function optionalUpperString(value, field, maxLength) {
  const normalized = optionalString(value, field, maxLength);
  return normalized === null ? null : normalized.toUpperCase();
}

function optionalString(value, field, maxLength) {
  return value === undefined || value === null
    ? null
    : boundedString(value, field, maxLength);
}

function boundedString(value, field, maxLength, options = {}) {
  if (typeof value !== 'string') throw new TypeError(`${field} must be a string`);
  const normalized = options.collapse
    ? value.trim().replace(/\s+/g, ' ')
    : value.trim();
  if (!normalized || normalized.length > maxLength) {
    throw new TypeError(`${field} must be non-blank and bounded`);
  }
  return normalized;
}

function boundedInteger(value, field, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${field} must be an integer from ${minimum} to ${maximum}`);
  }
  return value;
}

function nullableDate(value, field) {
  if (value === null) return null;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new TypeError(`${field} must be an ISO calendar date or null`);
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new TypeError(`${field} must be a valid calendar date`);
  }
  return value;
}

function requireExactKeys(value, required, optional) {
  const allowed = new Set([...required, ...optional]);
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new TypeError('Tool arguments contain an unsupported field');
  }
  if (required.some((key) => !Object.hasOwn(value, key))) {
    throw new TypeError('Tool arguments are missing a required field');
  }
}

function requirePlainObject(value, field) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${field} must be a plain object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new TypeError(`${field} must be a plain object`);
  }
}

function deepFreeze(value) {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
