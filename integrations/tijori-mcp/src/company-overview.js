import { failureResult, successResult } from './result-envelope.js';
import { createBenchmarkingFinancialsExtractor } from './benchmarking-financials.js';
import { createPeerComparisonSnapshotExtractor } from './peer-comparison.js';
import { validateToolArguments } from './tool-inputs.js';


const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const MAX_RATIOS = 100;

const FACT_MAPPINGS = Object.freeze([
  Object.freeze({
    labels: ['market cap', 'market capitalization', 'mcap'],
    factId: 'overview.market_cap',
    statement: 'valuation',
    standardName: 'Market capitalization',
    valueKind: 'monetary',
    currency: 'INR',
    sourceUnit: 'INR crore',
    normalizedUnit: 'INR crore',
    parser: parseCrore,
  }),
  Object.freeze({
    labels: ['p e', 'pe', 'stock p e', 'price earnings'],
    factId: 'overview.pe_ratio',
    statement: 'valuation',
    standardName: 'Price to earnings ratio',
    valueKind: 'ratio',
    currency: null,
    sourceUnit: 'ratio',
    normalizedUnit: 'ratio',
    parser: parseRatio,
  }),
  Object.freeze({
    labels: ['roe', 'return on equity'],
    factId: 'overview.return_on_equity',
    statement: 'kpi_schedule',
    standardName: 'Return on equity',
    valueKind: 'percentage',
    currency: null,
    sourceUnit: 'percent',
    normalizedUnit: 'percent',
    parser: parsePercentage,
  }),
  Object.freeze({
    labels: ['roce', 'return on capital employed'],
    factId: 'overview.return_on_capital_employed',
    statement: 'kpi_schedule',
    standardName: 'Return on capital employed',
    valueKind: 'percentage',
    currency: null,
    sourceUnit: 'percent',
    normalizedUnit: 'percent',
    parser: parsePercentage,
  }),
]);

export function createCompanyOverviewHandler({ browserRunner, clock = () => new Date() }) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Company overview requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Company overview clock must be callable');
  }

  return async function getCompanyOverview(argumentsValue) {
    let argumentsNormalized;
    let observedAt;
    let observedDate;
    try {
      argumentsNormalized = validateToolArguments(
        'get_company_overview',
        argumentsValue,
      );
      observedAt = clock();
      observedDate = istDate(observedAt);
    } catch {
      return failureResult('get_company_overview', 'unavailable');
    }
    if (
      argumentsNormalized.as_of_date !== null
      && argumentsNormalized.as_of_date !== observedDate
    ) {
      return failureResult('get_company_overview', 'unavailable');
    }

    if (argumentsNormalized.document_type === 'peer_comparison') {
      try {
        const extractPeerComparison = createPeerComparisonSnapshotExtractor({
          browserRunner,
          clock: () => observedAt,
        });
        const document = await extractPeerComparison({
          issuer: argumentsNormalized.issuer,
        });
        return successResult('get_company_overview', { document });
      } catch {
        return failureResult('get_company_overview', 'unavailable');
      }
    }

    if (argumentsNormalized.document_type === 'benchmarking_financials') {
      try {
        const extractBenchmarkingFinancials = createBenchmarkingFinancialsExtractor({
          browserRunner,
          clock: () => observedAt,
        });
        const document = await extractBenchmarkingFinancials({
          issuer: argumentsNormalized.issuer,
        });
        return successResult('get_company_overview', { document });
      } catch {
        return failureResult('get_company_overview', 'unavailable');
      }
    }

    const slug = argumentsNormalized.issuer.provider_slug?.trim().toLowerCase();
    if (slug === undefined || !SLUG_PATTERN.test(slug)) {
      return failureResult('get_company_overview', 'unavailable');
    }

    try {
      const outcome = await browserRunner.run(async (page) => {
        const response = await page.goto(`${BASE_URL}/company/${slug}/`, {
          timeout: 15_000,
          waitUntil: 'domcontentloaded',
        });
        const status = responseStatus(response);
        if (status !== null) return { status };
        await page.waitForSelector('.custom_ratio', { timeout: 10_000 });
        return {
          status: 'success',
          value: await extractOverview(page),
        };
      });
      if (outcome.status !== 'success') {
        return failureResult('get_company_overview', outcome.status);
      }
      const payload = normalizeOverview(
        outcome.value,
        argumentsNormalized.issuer,
        slug,
        observedDate,
      );
      return payload === null
        ? failureResult('get_company_overview', 'unavailable')
        : successResult('get_company_overview', payload);
    } catch {
      return failureResult('get_company_overview', 'unavailable');
    }
  };
}

async function extractOverview(page) {
  return page.evaluate(() => {
    let metadata = null;
    const scripts = Array.from(document.querySelectorAll('script:not([src])'));
    for (const script of scripts.slice(0, 100)) {
      const text = script.textContent?.trim() ?? '';
      if (!text.startsWith('{') || !text.includes('company_id')) continue;
      try {
        const parsed = JSON.parse(text);
        metadata = {
          company_id: parsed.company_id,
          exchange: parsed.exchange
            ?? parsed.exchange_code
            ?? parsed.primary_exchange
            ?? parsed.stock_exchange,
          symbol: parsed.symbol,
        };
        break;
      } catch {
        // Ignore malformed scripts without returning their contents.
      }
    }
    const ratios = Array.from(document.querySelectorAll('.custom_ratio'))
      .slice(0, 101)
      .map((element) => ({
        label: element.querySelector('.custom_ratio__field_name')?.textContent?.trim(),
        value: element.querySelector('.custom_ratio__field_value')?.textContent
          ?.trim()
          .replace(/\s+/g, ' '),
      }));
    return { metadata, ratios };
  });
}

function normalizeOverview(value, issuer, slug, observedDate) {
  if (
    value === null
    || typeof value !== 'object'
    || value.metadata === null
    || typeof value.metadata !== 'object'
    || !Array.isArray(value.ratios)
    || value.ratios.length > MAX_RATIOS
  ) {
    return null;
  }
  const companyId = boundedString(value.metadata.company_id, 128);
  const providerExchange = boundedString(
    value.metadata.exchange,
    32,
  )?.toUpperCase();
  const exchange = providerExchange ?? issuer.exchange;
  const symbol = boundedString(value.metadata.symbol, 100)?.toUpperCase();
  if (
    companyId === null
    || !ID_PATTERN.test(companyId)
    || !MARKET_PATTERN.test(exchange)
    || symbol === null
    || !MARKET_PATTERN.test(symbol)
    || exchange !== issuer.exchange
    || symbol !== issuer.symbol
    || (
      issuer.provider_company_id !== null
      && companyId !== issuer.provider_company_id
    )
  ) {
    return null;
  }

  const factsById = new Map();
  for (const row of value.ratios) {
    const fact = normalizeRatio(row, observedDate);
    if (fact === null) continue;
    const existing = factsById.get(fact.provider_fact_id);
    if (existing !== undefined && existing.source_value !== fact.source_value) {
      return null;
    }
    factsById.set(fact.provider_fact_id, fact);
  }
  const facts = [...factsById.values()];
  if (facts.length === 0) return null;
  const sourceId = 'tijori-overview';
  for (const fact of facts) fact.provider_source_id = sourceId;

  const limitations = [
    'Provider-standardized secondary evidence; reconcile material values against primary company or exchange disclosures.',
    'Metric-specific reporting periods were not disclosed on the overview page; values use the observation date.',
  ];
  if (providerExchange === undefined) {
    limitations.push(
      'The provider page omitted its exchange field; Jarvis retained the previously resolved issuer exchange after company-ID and symbol verification.',
    );
  }

  return {
    company_id: companyId,
    exchange,
    symbol,
    sources: [{
      provider_source_id: sourceId,
      source_name: 'Tijori standardized company overview',
      location: `${BASE_URL}/company/${slug}/`,
      period_covered: `Overview observed ${observedDate}`,
      as_of_date: observedDate,
      published_at: null,
    }],
    facts,
    limitations,
  };
}

function normalizeRatio(row, observedDate) {
  if (row === null || typeof row !== 'object') return null;
  const label = boundedString(row.label, 300, true);
  const sourceValue = boundedString(row.value, 500, true);
  if (label === null || sourceValue === null) return null;
  const normalizedLabel = comparable(label);
  const mapping = FACT_MAPPINGS.find(({ labels }) => labels.includes(normalizedLabel));
  if (mapping === undefined) return null;
  const normalizedValue = mapping.parser(sourceValue);
  if (normalizedValue === null) return null;
  return {
    provider_fact_id: mapping.factId,
    provider_source_id: null,
    statement: mapping.statement,
    line_item_original: label,
    line_item_standard: mapping.standardName,
    line_item_id: mapping.factId,
    period_label: `Observed ${observedDate}`,
    period_type: 'ltm',
    period_start: null,
    period_end: observedDate,
    value_kind: mapping.valueKind,
    source_value: sourceValue,
    normalized_value: normalizedValue,
    currency: mapping.currency,
    source_unit: mapping.sourceUnit,
    normalized_unit: mapping.normalizedUnit,
    availability_status: 'available',
  };
}

function parseCrore(value) {
  if (!/\b(?:cr|crore)\b/i.test(value) || /\blakh\b/i.test(value)) return null;
  return parseFiniteDecimal(value.replace(/\b(?:cr|crore)\b\.?/gi, ''));
}

function parsePercentage(value) {
  if (!value.includes('%')) return null;
  return parseFiniteDecimal(value.replaceAll('%', ''));
}

function parseRatio(value) {
  if (value.includes('%') || /\b(?:cr\.?|crore|lakh)\b/i.test(value)) return null;
  return parseFiniteDecimal(value.replace(/x$/i, ''));
}

function parseFiniteDecimal(value) {
  const compact = value.replace(/[₹,\s]/g, '');
  const negative = /^\(.*\)$/.test(compact);
  const numeric = negative ? `-${compact.slice(1, -1)}` : compact;
  if (!/^-?(?:\d+\.?\d*|\.\d+)$/.test(numeric)) return null;
  const parsed = Number(numeric);
  return Number.isFinite(parsed) ? numeric : null;
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

function istDate(value) {
  if (!(value instanceof Date) || Number.isNaN(value.valueOf())) {
    throw new TypeError('Company overview clock returned an invalid date');
  }
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value);
  const fields = Object.fromEntries(parts.map(({ type, value: part }) => [type, part]));
  return `${fields.year}-${fields.month}-${fields.day}`;
}

function comparable(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function boundedString(value, maxLength, collapse = false) {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const text = String(value).trim();
  const normalized = collapse ? text.replace(/\s+/g, ' ') : text;
  return normalized && normalized.length <= maxLength ? normalized : null;
}
