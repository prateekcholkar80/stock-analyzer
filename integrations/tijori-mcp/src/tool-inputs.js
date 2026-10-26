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
const FINANCIAL_DOCUMENT_TYPES = new Set([
  'financial_package',
  'growth_table',
  'balance_sheet',
  'profit_and_loss',
  'cash_flow',
  'ratios',
  'quarterly_results',
]);
const FINANCIAL_REPORTING_BASES = new Set([
  'consolidated',
  'standalone',
  'not_applicable',
]);
const DEFAULT_STATEMENTS = Object.freeze([
  'income_statement',
  'balance_sheet',
  'cash_flow',
]);
const DEFAULT_PERIOD_TYPES = Object.freeze(['annual', 'quarterly']);
const COMPANY_OVERVIEW_DOCUMENT_TYPES = new Set([
  'peer_comparison',
  'benchmarking_financials',
]);
const FILING_CATEGORIES = new Set([
  'earnings_release',
  'conference_call',
]);
const FILING_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const FINGERPRINT_PATTERN = /^[a-f0-9]{64}$/;
const SENSITIVE_URL_PARAMETER_PATTERN = (
  /(?:authorization|cookie|csrf|password|passwd|secret|session|token)/i
);

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
    list_company_filings: validateListCompanyFilings,
    download_company_filing: validateDownloadCompanyFiling,
  };
  return deepFreeze(validators[toolName](candidate));
}

export function validateListCompanyFilingsArguments(candidate) {
  requirePlainObject(candidate, 'Tool arguments');
  return deepFreeze(validateListCompanyFilings(candidate));
}

export function validateDownloadCompanyFilingArguments(candidate) {
  requirePlainObject(candidate, 'Tool arguments');
  return deepFreeze(validateDownloadCompanyFiling(candidate));
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
  requireExactKeys(value, ['issuer'], ['as_of_date', 'document_type']);
  const normalized = evidenceArguments(value);
  if (value.document_type === undefined) {
    return normalized;
  }
  return {
    ...normalized,
    document_type: enumScalar(
      value.document_type,
      'document_type',
      COMPANY_OVERVIEW_DOCUMENT_TYPES,
    ),
  };
}

function validateFinancials(value) {
  requireExactKeys(
    value,
    ['issuer'],
    [
      'as_of_date',
      'statements',
      'period_types',
      'max_periods',
      'document_type',
      'reporting_basis',
    ],
  );
  const documentRequested = value.document_type !== undefined;
  const basisSupplied = value.reporting_basis !== undefined;
  if (documentRequested !== basisSupplied) {
    throw new TypeError(
      'Financial document requests require document_type and reporting_basis',
    );
  }
  if (documentRequested) {
    if (
      value.statements !== undefined
      || value.period_types !== undefined
      || value.max_periods !== undefined
    ) {
      throw new TypeError(
        'Financial document requests cannot mix legacy statement selections',
      );
    }
    const documentType = enumScalar(
      value.document_type,
      'document_type',
      FINANCIAL_DOCUMENT_TYPES,
    );
    const reportingBasis = enumScalar(
      value.reporting_basis,
      'reporting_basis',
      FINANCIAL_REPORTING_BASES,
    );
    const basisIndependentDocument = (
      documentType === 'financial_package'
      || documentType === 'growth_table'
    );
    if (basisIndependentDocument !== (reportingBasis === 'not_applicable')) {
      throw new TypeError(
        'The selected Financial Package or Growth Table requires the not_applicable '
        + 'reporting basis exclusively',
      );
    }
    return {
      ...evidenceArguments(value),
      document_type: documentType,
      reporting_basis: reportingBasis,
    };
  }
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

function enumScalar(value, field, approved) {
  if (typeof value !== 'string' || !approved.has(value)) {
    throw new TypeError(`${field} contains an unsupported value`);
  }
  return value;
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

function validateListCompanyFilings(value) {
  requireExactKeys(
    value,
    ['issuer'],
    [
      'categories',
      'from_fiscal_year',
      'through_date',
      'max_documents',
    ],
  );
  return {
    issuer: validateResolvedIssuer(value.issuer),
    categories: enumArray(
      value.categories ?? [...FILING_CATEGORIES],
      'categories',
      FILING_CATEGORIES,
    ),
    from_fiscal_year: nullableInteger(
      value.from_fiscal_year ?? null,
      'from_fiscal_year',
      1900,
      2200,
    ),
    through_date: nullableDate(value.through_date ?? null, 'through_date'),
    max_documents: boundedInteger(
      value.max_documents ?? 100,
      'max_documents',
      1,
      200,
    ),
  };
}

function validateDownloadCompanyFiling(value) {
  requireExactKeys(value, [
    'issuer',
    'filing_reference',
    'expected_source_url',
    'expected_provider_company_id',
  ], []);
  const issuer = validateResolvedIssuer(value.issuer);
  const filingReference = validateFilingReference(value.filing_reference);
  const expectedSourceUrl = credentialFreeHttpsUrl(
    value.expected_source_url,
    'expected_source_url',
  );
  const expectedProviderCompanyId = boundedIdentifier(
    value.expected_provider_company_id,
    'expected_provider_company_id',
    128,
  );
  if (
    issuer.provider_company_id !== expectedProviderCompanyId
    || filingReference.provider_company_id !== expectedProviderCompanyId
  ) {
    throw new TypeError(
      'Download company identity must match the verified issuer and reference',
    );
  }
  if (issuer.provider_slug !== filingReference.provider_slug) {
    throw new TypeError(
      'Download provider slug must match the verified issuer',
    );
  }
  if (filingReference.artifact_url !== expectedSourceUrl) {
    throw new TypeError(
      'Download source URL must exactly match the discovered filing reference',
    );
  }
  return {
    issuer,
    filing_reference: filingReference,
    expected_source_url: expectedSourceUrl,
    expected_provider_company_id: expectedProviderCompanyId,
  };
}

function validateFilingReference(value) {
  requirePlainObject(value, 'filing_reference');
  requireExactKeys(value, [
    'filing_reference_id',
    'category',
    'provider_company_id',
    'provider_slug',
    'source_page_url',
    'artifact_url',
    'discovery_fingerprint',
  ], []);
  const fingerprint = boundedString(
    value.discovery_fingerprint,
    'filing_reference.discovery_fingerprint',
    64,
  );
  if (!FINGERPRINT_PATTERN.test(fingerprint)) {
    throw new TypeError(
      'filing_reference.discovery_fingerprint must be a SHA-256 value',
    );
  }
  return {
    filing_reference_id: boundedIdentifier(
      value.filing_reference_id,
      'filing_reference.filing_reference_id',
      180,
    ),
    category: enumScalar(
      value.category,
      'filing_reference.category',
      FILING_CATEGORIES,
    ),
    provider_company_id: boundedIdentifier(
      value.provider_company_id,
      'filing_reference.provider_company_id',
      128,
    ),
    provider_slug: boundedIdentifier(
      value.provider_slug,
      'filing_reference.provider_slug',
      240,
    ),
    source_page_url: credentialFreeHttpsUrl(
      value.source_page_url,
      'filing_reference.source_page_url',
    ),
    artifact_url: credentialFreeHttpsUrl(
      value.artifact_url,
      'filing_reference.artifact_url',
    ),
    discovery_fingerprint: fingerprint,
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

function validateResolvedIssuer(value) {
  const issuer = validateIssuer(value);
  if (
    issuer.provider_company_id === null
    || issuer.provider_slug === null
  ) {
    throw new TypeError(
      'Filing operations require a resolved provider company identity',
    );
  }
  return issuer;
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

function nullableInteger(value, field, minimum, maximum) {
  return value === null
    ? null
    : boundedInteger(value, field, minimum, maximum);
}

function boundedIdentifier(value, field, maxLength) {
  const normalized = boundedString(value, field, maxLength);
  if (!FILING_ID_PATTERN.test(normalized)) {
    throw new TypeError(`${field} must be a bounded identifier`);
  }
  return normalized;
}

function credentialFreeHttpsUrl(value, field) {
  const normalized = boundedString(value, field, 2_000);
  let parsed;
  try {
    parsed = new URL(normalized);
  } catch {
    throw new TypeError(`${field} must be a valid HTTPS URL`);
  }
  if (
    parsed.protocol !== 'https:'
    || !parsed.hostname
    || parsed.username
    || parsed.password
    || parsed.hash
  ) {
    throw new TypeError(`${field} must be a credential-free HTTPS URL`);
  }
  for (const parameter of parsed.searchParams.keys()) {
    if (SENSITIVE_URL_PARAMETER_PATTERN.test(parameter)) {
      throw new TypeError(`${field} cannot contain sensitive URL parameters`);
    }
  }
  return normalized;
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
