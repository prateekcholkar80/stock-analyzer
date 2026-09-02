import { failureResult, successResult } from './result-envelope.js';
import { createSearchCompanyHandler } from './search-company.js';
import { validateToolArguments } from './tool-inputs.js';


const BASE_URL = 'https://www.tijorifinance.com';
const IDENTIFIER_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const ISIN_PATTERN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

export function createResolveCompanyIdsHandler({ browserRunner }) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Company resolution requires a browser runner');
  }
  const searchCompany = createSearchCompanyHandler({ browserRunner });

  return async function resolveCompanyIds(argumentsValue) {
    let argumentsNormalized;
    try {
      argumentsNormalized = validateToolArguments(
        'resolve_company_ids',
        argumentsValue,
      );
    } catch {
      return failureResult('resolve_company_ids', 'unavailable');
    }

    const { locator, max_candidates: maxCandidates } = argumentsNormalized;
    if (locator.provider_slug !== null) {
      return resolveProviderSlug(browserRunner, locator, locator.provider_slug);
    }
    const query = locator.symbol ?? locator.isin ?? locator.legal_name;
    if (query === null) {
      return failureResult('resolve_company_ids', 'unavailable');
    }

    const searchResult = await searchCompany({
      query,
      exchanges: locator.exchange === null ? [] : [locator.exchange],
      max_results: maxCandidates,
    });
    if (searchResult.status !== 'success') {
      return failureResult('resolve_company_ids', searchResult.status);
    }
    const candidates = searchResult.payload.companies
      .filter((company) => identityMatches(company, locator))
      .slice(0, maxCandidates);
    return resolutionEnvelope(candidates);
  };
}

async function resolveProviderSlug(browserRunner, locator, providerSlug) {
  const slug = providerSlug.trim().toLowerCase();
  if (!SLUG_PATTERN.test(slug)) {
    return failureResult('resolve_company_ids', 'unavailable');
  }
  try {
    const outcome = await browserRunner.run(async (page) => {
      const response = await page.goto(`${BASE_URL}/company/${slug}/`, {
        timeout: 15_000,
        waitUntil: 'domcontentloaded',
      });
      const status = responseStatus(response);
      if (status !== null) return { status };
      const metadata = await extractCompanyMetadata(page);
      const company = normalizeCompany(metadata, slug, locator);
      return company === null
        ? { status: 'unavailable' }
        : { status: 'success', company };
    });
    if (outcome.status !== 'success') {
      return failureResult('resolve_company_ids', outcome.status);
    }
    if (!identityMatches(outcome.company, locator)) {
      return failureResult('resolve_company_ids', 'not_found');
    }
    return resolutionEnvelope([outcome.company]);
  } catch {
    return failureResult('resolve_company_ids', 'unavailable');
  }
}

function resolutionEnvelope(candidates) {
  if (candidates.length === 0) {
    return failureResult('resolve_company_ids', 'not_found');
  }
  const resolved = candidates.length === 1;
  return successResult('resolve_company_ids', {
    status: resolved ? 'resolved' : 'ambiguous',
    companies: candidates,
    selected_company_id: resolved ? candidates[0].company_id : null,
  });
}

function identityMatches(company, locator) {
  return (
    (locator.exchange === null || company.exchange === locator.exchange)
    && (locator.symbol === null || company.symbol === locator.symbol)
    && (
      locator.legal_name === null
      || comparable(company.legal_name) === comparable(locator.legal_name)
    )
    && (locator.isin === null || company.isin === locator.isin)
    && (
      locator.provider_company_id === null
      || company.company_id === locator.provider_company_id
    )
    && (locator.provider_slug === null || company.slug === locator.provider_slug)
  );
}

async function extractCompanyMetadata(page) {
  return page.evaluate(() => {
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
        // Ignore malformed scripts without returning their contents.
      }
    }
    return null;
  });
}

function normalizeCompany(value, slug, locator) {
  if (value === null || typeof value !== 'object') return null;
  const companyId = normalizedString(value.company_id, 128);
  const legalName = normalizedString(value.legal_name, 300, true);
  const symbol = normalizedString(value.symbol, 100)?.toUpperCase();
  const providerExchange = normalizedString(value.exchange, 32)?.toUpperCase();
  const exchange = providerExchange ?? locator.exchange ?? undefined;
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
  const match = directMatch(locator, {
    company_id: companyId,
    slug,
    legal_name: legalName,
    exchange,
    symbol,
    isin,
  }, providerExchange !== undefined);
  return {
    company_id: companyId,
    slug,
    legal_name: legalName,
    exchange,
    symbol,
    isin,
    match_kind: match.kind,
    match_score: 1,
    matched_on: match.fields,
  };
}

function directMatch(locator, company, providerExchangeObserved) {
  if (locator.provider_company_id === company.company_id) {
    return { kind: 'provider_id', fields: ['provider_company_id'] };
  }
  if (locator.isin !== null && locator.isin === company.isin) {
    return { kind: 'exact_isin', fields: ['isin'] };
  }
  if (locator.symbol !== null && locator.symbol === company.symbol) {
    const fields = locator.exchange === null
      ? ['symbol']
      : [
        'symbol',
        providerExchangeObserved ? 'exchange' : 'requested_exchange',
      ];
    return { kind: 'exact_symbol', fields };
  }
  if (
    locator.legal_name !== null
    && comparable(locator.legal_name) === comparable(company.legal_name)
  ) {
    return { kind: 'exact_legal_name', fields: ['legal_name'] };
  }
  return { kind: 'provider_slug', fields: ['provider_slug'] };
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

function comparable(value) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function normalizedString(value, maxLength, collapse = false) {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const text = String(value).trim();
  const normalized = collapse ? text.replace(/\s+/g, ' ') : text;
  return normalized && normalized.length <= maxLength ? normalized : null;
}
