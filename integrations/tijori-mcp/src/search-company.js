import { failureResult, successResult } from './result-envelope.js';
import { validateToolArguments } from './tool-inputs.js';


const BASE_URL = 'https://www.tijorifinance.com';
const MAX_SEARCH_BYTES = 256_000;
const MAX_PROVIDER_ROWS = 100;
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const IDENTIFIER_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const ISIN_PATTERN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

export function createSearchCompanyHandler({ browserRunner }) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Company search requires a browser runner');
  }

  return async function searchCompany(argumentsValue) {
    let argumentsNormalized;
    try {
      argumentsNormalized = validateToolArguments('search_company', argumentsValue);
    } catch {
      return failureResult('search_company', 'unavailable');
    }

    try {
      const outcome = await browserRunner.run(
        (page) => retrieveCompanies(page, argumentsNormalized),
      );
      if (outcome.status !== 'success') {
        return failureResult('search_company', outcome.status);
      }
      return successResult('search_company', {
        companies: outcome.companies,
      });
    } catch {
      return failureResult('search_company', 'unavailable');
    }
  };
}

async function retrieveCompanies(page, argumentsValue) {
  const searchUrl = new URL('/api/v1/ind/company_search/', BASE_URL);
  searchUrl.searchParams.set('q', argumentsValue.query);
  const searchResponse = await page.goto(searchUrl.href, {
    timeout: 15_000,
    waitUntil: 'domcontentloaded',
  });
  const responseFailure = responseStatus(searchResponse);
  if (responseFailure !== null) return { status: responseFailure };
  const encoded = await boundedBody(searchResponse);
  if (encoded === null) return { status: 'unavailable' };

  let raw;
  try {
    raw = JSON.parse(encoded.toString('utf8'));
  } catch {
    return { status: 'unavailable' };
  }
  if (!Array.isArray(raw) || raw.length > MAX_PROVIDER_ROWS) {
    return { status: 'unavailable' };
  }

  const slugs = uniqueCompanySlugs(raw);
  if (slugs.length === 0) return { status: 'not_found' };

  const companies = [];
  let completeProviderRecords = 0;
  const identities = new Set();
  for (const slug of slugs) {
    const metadata = await retrieveCompanyMetadata(page, slug);
    if (metadata.status !== 'success') return metadata;
    const company = normalizeCompany(metadata.value, slug, argumentsValue.query);
    if (company === null) continue;
    completeProviderRecords += 1;
    if (
      argumentsValue.exchanges.length > 0
      && !argumentsValue.exchanges.includes(company.exchange)
    ) {
      continue;
    }
    const identity = [company.company_id, company.exchange, company.symbol].join(':');
    if (identities.has(identity)) continue;
    identities.add(identity);
    companies.push(company);
    if (companies.length === argumentsValue.max_results) break;
  }

  if (companies.length > 0) return { status: 'success', companies };
  return {
    status: completeProviderRecords > 0 ? 'not_found' : 'unavailable',
  };
}

function uniqueCompanySlugs(rows) {
  const slugs = [];
  const seen = new Set();
  for (const row of rows) {
    if (
      row === null
      || typeof row !== 'object'
      || row.type !== 'companies'
      || typeof row.slug !== 'string'
    ) {
      continue;
    }
    const slug = row.slug.trim().toLowerCase();
    if (!SLUG_PATTERN.test(slug) || seen.has(slug)) continue;
    seen.add(slug);
    slugs.push(slug);
  }
  return slugs;
}

async function retrieveCompanyMetadata(page, slug) {
  const response = await page.goto(`${BASE_URL}/company/${slug}/`, {
    timeout: 15_000,
    waitUntil: 'domcontentloaded',
  });
  const failure = responseStatus(response);
  if (failure !== null) return { status: failure };
  const value = await page.evaluate(() => {
    const scripts = Array.from(document.querySelectorAll('script:not([src])'));
    for (const script of scripts.slice(0, 100)) {
      const text = script.textContent?.trim() ?? '';
      if (!text.startsWith('{') || !text.includes('company_id')) continue;
      try {
        const parsed = JSON.parse(text);
        return {
          company_id: parsed.company_id,
          exchange: parsed.exchange
            ?? parsed.exchange_code
            ?? parsed.primary_exchange
            ?? parsed.stock_exchange,
          isin: parsed.isin ?? parsed.ISIN,
          legal_name: parsed.company ?? parsed.legal_name,
          symbol: parsed.symbol,
        };
      } catch {
        // Ignore non-JSON inline scripts without returning their contents.
      }
    }
    return null;
  });
  return { status: 'success', value };
}

function normalizeCompany(value, slug, query) {
  if (value === null || typeof value !== 'object') return null;
  const companyId = normalizedString(value.company_id, 128);
  const legalName = normalizedString(value.legal_name, 300, true);
  const symbol = normalizedString(value.symbol, 100)?.toUpperCase();
  const exchange = normalizedString(value.exchange, 32)?.toUpperCase();
  const isin = value.isin === undefined || value.isin === null
    ? null
    : normalizedString(value.isin, 12)?.toUpperCase();
  if (
    companyId === null
    || !IDENTIFIER_PATTERN.test(companyId)
    || legalName === null
    || symbol === null
    || !MARKET_PATTERN.test(symbol)
    || exchange === null
    || !MARKET_PATTERN.test(exchange)
    || (isin !== null && !ISIN_PATTERN.test(isin))
  ) {
    return null;
  }

  const match = matchEvidence(query, legalName, symbol);
  return {
    company_id: companyId,
    slug,
    legal_name: legalName,
    exchange,
    symbol,
    isin,
    match_kind: match.kind,
    match_score: match.score,
    matched_on: match.fields,
  };
}

function matchEvidence(query, legalName, symbol) {
  const normalizedQuery = normalizeComparable(query);
  if (normalizedQuery === normalizeComparable(symbol)) {
    return { kind: 'exact_symbol', score: 1, fields: ['symbol'] };
  }
  if (normalizedQuery === normalizeComparable(legalName)) {
    return { kind: 'exact_legal_name', score: 1, fields: ['legal_name'] };
  }
  const queryTokens = new Set(normalizedQuery.split(' ').filter(Boolean));
  const nameTokens = new Set(normalizeComparable(legalName).split(' ').filter(Boolean));
  const overlap = [...queryTokens].filter((token) => nameTokens.has(token)).length;
  const denominator = Math.max(queryTokens.size, nameTokens.size, 1);
  const score = Math.max(0.01, Math.min(0.99, overlap / denominator));
  return { kind: 'fuzzy_name', score, fields: ['provider_search', 'legal_name'] };
}

function normalizeComparable(value) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function normalizedString(value, maxLength, collapse = false) {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const text = String(value).trim();
  const normalized = collapse ? text.replace(/\s+/g, ' ') : text;
  return normalized && normalized.length <= maxLength ? normalized : null;
}

function responseStatus(response) {
  if (response === null || typeof response?.status !== 'function') return 'unavailable';
  const status = response.status();
  if (status >= 200 && status < 300) return null;
  if (status === 401 || status === 403) return 'authentication_required';
  if (status === 402) return 'paywalled';
  if (status === 404) return 'not_found';
  if (status === 429) return 'rate_limited';
  return 'unavailable';
}

async function boundedBody(response) {
  if (typeof response.headers !== 'function' || typeof response.body !== 'function') {
    return null;
  }
  const headers = await response.headers();
  const declaredLength = Number.parseInt(headers['content-length'] ?? '', 10);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_SEARCH_BYTES) {
    return null;
  }
  const body = await response.body();
  return Buffer.isBuffer(body) && body.length <= MAX_SEARCH_BYTES ? body : null;
}
