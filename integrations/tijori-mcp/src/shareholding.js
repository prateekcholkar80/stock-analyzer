import { failureResult, successResult } from './result-envelope.js';
import { validateToolArguments } from './tool-inputs.js';


const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const MAX_PROVIDER_ROWS = 100;
const MAX_QUARTERS = 40;

const OWNERSHIP_MAPPINGS = Object.freeze([
  mapping(
    ['promoter', 'promoters', 'promoter holding', 'promoter and promoter group'],
    'promoter_holding',
    'Promoter holding',
  ),
  mapping(
    ['fii', 'fii holding', 'fpis', 'foreign institutional investors', 'foreign portfolio investors'],
    'foreign_institutional_holding',
    'Foreign institutional holding',
  ),
  mapping(
    ['dii', 'dii holding', 'domestic institutional investors'],
    'domestic_institutional_holding',
    'Domestic institutional holding',
  ),
  mapping(
    ['public', 'public holding', 'public shareholding', 'retail and others'],
    'public_holding',
    'Public holding',
  ),
  mapping(
    ['promoter pledge', 'promoter pledged', 'pledged promoter shares', 'promoter shares pledged'],
    'promoter_pledge',
    'Promoter pledge',
  ),
]);

export function createShareholdingHandler({ browserRunner, clock = () => new Date() }) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Shareholding retrieval requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Shareholding retrieval clock must be callable');
  }

  return async function getShareholding(argumentsValue) {
    let request;
    let observedDate;
    try {
      request = validateToolArguments('get_shareholding', argumentsValue);
      observedDate = istDate(clock());
    } catch {
      return failureResult('get_shareholding', 'unavailable');
    }
    if (request.as_of_date !== null && request.as_of_date !== observedDate) {
      return failureResult('get_shareholding', 'unavailable');
    }
    const slug = request.issuer.provider_slug?.trim().toLowerCase();
    if (slug === undefined || !SLUG_PATTERN.test(slug)) {
      return failureResult('get_shareholding', 'unavailable');
    }

    try {
      const outcome = await browserRunner.run(async (page) => {
        const response = await page.goto(`${BASE_URL}/company/${slug}/shareholding/`, {
          timeout: 15_000,
          waitUntil: 'domcontentloaded',
        });
        const status = responseStatus(response);
        if (status !== null) return { status };
        await page.waitForSelector('table', { timeout: 10_000 });
        return { status: 'success', value: await extractShareholding(page) };
      });
      if (outcome.status !== 'success') {
        return failureResult('get_shareholding', outcome.status);
      }
      const payload = normalizeShareholding(outcome.value, request, slug, observedDate);
      return payload === null
        ? failureResult('get_shareholding', 'unavailable')
        : successResult('get_shareholding', payload);
    } catch {
      return failureResult('get_shareholding', 'unavailable');
    }
  };
}

async function extractShareholding(page) {
  return page.evaluate(() => {
    let metadata = null;
    for (const script of Array.from(document.querySelectorAll('script:not([src])')).slice(0, 100)) {
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
        // Ignore malformed inline metadata without releasing its contents.
      }
    }

    let selected = null;
    let selectedColumns = 0;
    for (const table of Array.from(document.querySelectorAll('table')).slice(0, 50)) {
      const text = table.textContent?.toLowerCase() ?? '';
      if (!text.includes('promoter')) continue;
      if (!/(public|fii|foreign|dii|institution)/.test(text)) continue;
      const firstRow = table.querySelector('tr');
      const columns = firstRow?.querySelectorAll('th,td').length ?? 0;
      if (columns > selectedColumns) {
        selected = table;
        selectedColumns = columns;
      }
    }
    if (selected === null) return { metadata, headers: [], rows: [] };

    const tableRows = Array.from(selected.querySelectorAll('tr'));
    const headers = Array.from(tableRows[0]?.querySelectorAll('th,td') ?? [])
      .slice(0, 42)
      .map((cell) => cell.textContent?.trim().replace(/\s+/g, ' ') ?? '');
    const rows = tableRows.slice(1, 102).map((row) => {
      const cells = Array.from(row.querySelectorAll('th,td'))
        .slice(0, 42)
        .map((cell) => cell.textContent?.trim().replace(/\s+/g, ' ') ?? '');
      return {
        category: cells[0] || null,
        values: cells.slice(1).map((value) => (
          value === '' || value === '-' || value === '—' ? null : value
        )),
      };
    });
    return { metadata, headers, rows };
  });
}

function normalizeShareholding(value, request, slug, observedDate) {
  if (
    value === null
    || typeof value !== 'object'
    || !validIdentity(value.metadata, request.issuer)
    || !Array.isArray(value.headers)
    || value.headers.length < 2
    || value.headers.length > MAX_QUARTERS + 1
    || !Array.isArray(value.rows)
    || value.rows.length > MAX_PROVIDER_ROWS
  ) return null;

  const periods = value.headers.slice(1).map((label, index) => ({
    ...parseQuarter(label),
    index,
  }));
  if (periods.some(({ end }) => end === null)) return null;
  const uniqueEnds = new Set(periods.map(({ end }) => end));
  if (uniqueEnds.size !== periods.length) return null;
  const selectedPeriods = periods
    .sort((left, right) => right.end.localeCompare(left.end))
    .slice(0, request.quarters);

  const rowsByItem = new Map();
  for (const row of value.rows) {
    if (row === null || typeof row !== 'object' || !Array.isArray(row.values)) continue;
    const original = boundedString(row.category, 300, true);
    if (original === null) continue;
    const item = OWNERSHIP_MAPPINGS.find(({ labels }) => (
      labels.includes(comparable(original))
    ));
    if (item === undefined) continue;
    if (item.id === 'promoter_pledge' && !request.include_promoter_pledge) continue;
    if (rowsByItem.has(item.id)) return null;
    rowsByItem.set(item.id, { item, original, values: row.values });
  }
  const observedRows = [...rowsByItem.values()].filter(({ item }) => (
    item.id !== 'promoter_pledge'
  ));
  if (observedRows.length === 0) return null;

  if (request.include_promoter_pledge && !rowsByItem.has('promoter_pledge')) {
    const pledge = OWNERSHIP_MAPPINGS.find(({ id }) => id === 'promoter_pledge');
    rowsByItem.set('promoter_pledge', {
      item: pledge,
      original: 'Promoter pledge (not displayed)',
      values: [],
    });
  }

  const sourceId = 'tijori-shareholding';
  const facts = [];
  for (const { item, original, values } of rowsByItem.values()) {
    for (const period of selectedPeriods) {
      const sourceValue = boundedString(values[period.index], 500, true);
      const numeric = sourceValue === null ? null : parsePercentage(sourceValue);
      if (sourceValue !== null && numeric === null) return null;
      facts.push({
        provider_fact_id: `ownership.${item.id}.quarterly.${period.end}`,
        provider_source_id: sourceValue === null ? null : sourceId,
        statement: 'ownership',
        line_item_original: original,
        line_item_standard: item.standard,
        line_item_id: `ownership.${item.id}`,
        period_label: period.label,
        period_type: 'quarterly',
        period_start: null,
        period_end: period.end,
        value_kind: 'percentage',
        source_value: sourceValue,
        normalized_value: numeric,
        currency: null,
        source_unit: sourceValue === null ? null : 'percent',
        normalized_unit: sourceValue === null ? null : 'percent',
        availability_status: sourceValue === null ? 'unknown' : 'available',
      });
    }
  }
  if (facts.length === 0) return null;

  const limitations = [
    'Provider-standardized secondary evidence; reconcile ownership values against primary company or exchange disclosures.',
    'The provider page does not establish point-in-time publication timing or subsequent restatement history for historical backtesting.',
    'Only explicitly displayed ownership categories are mapped; missing categories and totals are not inferred.',
  ];
  if (request.include_promoter_pledge && !value.rows.some((row) => {
    const category = boundedString(row?.category, 300, true);
    return category !== null && OWNERSHIP_MAPPINGS.some(({ id, labels }) => (
      id === 'promoter_pledge' && labels.includes(comparable(category))
    ));
  })) {
    limitations.push('Promoter pledge was requested but was not displayed by the provider and is returned as unknown.');
  }

  return {
    company_id: String(value.metadata.company_id).trim(),
    exchange: String(value.metadata.exchange).toUpperCase(),
    symbol: String(value.metadata.symbol).toUpperCase(),
    sources: [{
      provider_source_id: sourceId,
      source_name: 'Tijori standardized shareholding history',
      location: `${BASE_URL}/company/${slug}/shareholding/`,
      period_covered: `Quarterly ownership history observed ${observedDate}`,
      as_of_date: observedDate,
      published_at: null,
    }],
    facts,
    limitations,
  };
}

function parseQuarter(value) {
  const label = boundedString(value, 80, true);
  if (label === null) return { label: '', end: null };
  const monthMatch = label.match(/^(Mar|Jun|Sep|Dec)[ -]?(\d{2}|\d{4})$/i);
  if (monthMatch) {
    const year = normalizeYear(monthMatch[2]);
    const month = { mar: 3, jun: 6, sep: 9, dec: 12 }[monthMatch[1].toLowerCase()];
    return { label, end: lastDay(year, month) };
  }
  const quarter = label.match(/^Q([1-4])\s*FY\s*(\d{2}|\d{4})$/i);
  if (quarter) {
    const fiscalYear = normalizeYear(quarter[2]);
    const number = Number(quarter[1]);
    const endYear = number === 4 ? fiscalYear : fiscalYear - 1;
    const endMonth = [0, 6, 9, 12, 3][number];
    return { label, end: lastDay(endYear, endMonth) };
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(label)) {
    const parsed = new Date(`${label}T00:00:00Z`);
    if (!Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === label) {
      return { label, end: label };
    }
  }
  return { label, end: null };
}

function validIdentity(value, issuer) {
  if (value === null || typeof value !== 'object') return false;
  const companyId = boundedString(value.company_id, 128);
  const exchange = boundedString(value.exchange, 32)?.toUpperCase();
  const symbol = boundedString(value.symbol, 100)?.toUpperCase();
  return companyId !== null
    && ID_PATTERN.test(companyId)
    && exchange !== null
    && MARKET_PATTERN.test(exchange)
    && symbol !== null
    && MARKET_PATTERN.test(symbol)
    && exchange === issuer.exchange
    && symbol === issuer.symbol
    && (issuer.provider_company_id === null || companyId === issuer.provider_company_id);
}

function parsePercentage(value) {
  const compact = value.replace(/[,%\s]/g, '').replace(/%$/, '');
  if (!/^(?:\d+\.?\d*|\.\d+)$/.test(compact)) return null;
  const numeric = Number(compact);
  return Number.isFinite(numeric) && numeric >= 0 && numeric <= 100 ? compact : null;
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

function mapping(labels, id, standard) {
  return Object.freeze({ labels: Object.freeze(labels), id, standard });
}

function normalizeYear(value) {
  const number = Number(value);
  return value.length === 2 ? 2000 + number : number;
}

function lastDay(year, month) {
  return `${year}-${String(month).padStart(2, '0')}-${new Date(Date.UTC(year, month, 0)).getUTCDate()}`;
}

function istDate(value) {
  if (!(value instanceof Date) || Number.isNaN(value.valueOf())) throw new TypeError('Invalid clock');
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
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
