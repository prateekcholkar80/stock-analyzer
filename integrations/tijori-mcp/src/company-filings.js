import { createHash } from 'node:crypto';

import { validateListCompanyFilingsArguments } from './tool-inputs.js';


const BASE_URL = 'https://www.tijorifinance.com';
const ARTIFACT_ORIGIN = 'https://files.tijorifinance.com';
const TABLE_SELECTOR = '#companyFilings';
const MONTHS = new Map([
  ['jan', 1], ['feb', 2], ['mar', 3], ['apr', 4],
  ['may', 5], ['jun', 6], ['jul', 7], ['aug', 8],
  ['sep', 9], ['oct', 10], ['nov', 11], ['dec', 12],
]);
const CATEGORY_DEFINITIONS = Object.freeze({
  earnings_release: Object.freeze({
    class_name: 'earnings-release',
    label: 'Earnings Release',
    source_class: 'company_authored_earnings_release',
    artifact_directory: 'Earnings Release',
  }),
  conference_call: Object.freeze({
    class_name: 'conference-call',
    label: 'Conference Call',
    source_class: 'earnings_call_transcript',
    artifact_directory: 'Conference Call',
  }),
});
const MAX_PROVIDER_YEARS = 100;
const MAX_ROWS = 30;
const MAX_LINKS_PER_CELL = 20;

/** Discover bounded, issuer-bound Company Filings references. */
export function createCompanyFilingsDiscovery({
  browserRunner,
  clock = () => new Date(),
}) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Company Filings discovery requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Company Filings discovery clock must be callable');
  }
  return async function discoverCompanyFilings(argumentsValue) {
    const request = validateListCompanyFilingsArguments(argumentsValue);
    const discoveredAt = clock();
    requireValidDate(discoveredAt, 'Company Filings discovery clock');
    const sourcePageUrl = companyPageUrl(request.issuer.provider_slug);

    return browserRunner.run(async (page) => {
      const response = await page.goto(sourcePageUrl, {
        timeout: 20_000,
        waitUntil: 'domcontentloaded',
      });
      if (!responseMatchesIssuerPath(response, request.issuer.provider_slug)) {
        throw new TypeError(
          'Company Filings response did not match the resolved issuer',
        );
      }
      await page.waitForSelector(TABLE_SELECTOR, { timeout: 20_000 });
      const raw = await page.evaluate(readCompanyFilingsSnapshot);
      return normalizeCompanyFilings(raw, request, discoveredAt);
    });
  };
}

/** Normalize a rendered Company Filings table without inferring fiscal periods. */
export function normalizeCompanyFilings(raw, requestValue, discoveredAt) {
  const request = validateListCompanyFilingsArguments(requestValue);
  requireValidDate(discoveredAt, 'Company Filings discovery time');
  requirePlainObject(raw, 'Company Filings snapshot');
  if (!Array.isArray(raw.provider_years) || !Array.isArray(raw.rows)) {
    throw new TypeError('Company Filings snapshot requires years and rows');
  }
  if (
    raw.provider_years.length === 0
    || raw.provider_years.length > MAX_PROVIDER_YEARS
  ) {
    throw new RangeError('Company Filings year count is outside its safety bound');
  }
  if (raw.rows.length === 0 || raw.rows.length > MAX_ROWS) {
    throw new RangeError('Company Filings row count is outside its safety bound');
  }

  const providerYears = raw.provider_years.map(normalizeProviderYear);
  requireUnique(providerYears, 'Company Filings provider years');
  const requestedCategories = new Set(request.categories);
  const categoryRows = new Map();

  for (const row of raw.rows) {
    const normalized = normalizeCategoryRow(row, providerYears);
    if (normalized === null) continue;
    if (categoryRows.has(normalized.category)) {
      throw new TypeError('Company Filings category rows must be unique');
    }
    categoryRows.set(normalized.category, normalized);
  }
  for (const category of requestedCategories) {
    if (!categoryRows.has(category)) {
      throw new TypeError(`Company Filings table is missing ${category}`);
    }
  }

  const sourcePageUrl = companyPageUrl(request.issuer.provider_slug);
  const candidates = [];
  const coverage = [];
  const observedArtifactUrls = new Set();
  for (const category of request.categories) {
    const row = categoryRows.get(category);
    for (const group of row.groups) {
      const entries = group.entries.map((entry) => normalizeEntry(
        entry,
        category,
        group.provider_year,
        request.issuer,
        sourcePageUrl,
      ));
      const available = entries.filter(({ kind }) => kind === 'available');
      const unavailableLabels = entries
        .filter(({ kind }) => kind === 'unavailable')
        .map(({ provider_label }) => provider_label);
      for (const entry of available) {
        if (observedArtifactUrls.has(entry.artifact_url)) {
          throw new TypeError('Company Filings artifact URLs must be unique');
        }
        observedArtifactUrls.add(entry.artifact_url);
        if (withinThroughMonth(entry, request.through_date)) candidates.push(entry);
      }
      coverage.push(Object.freeze({
        category,
        provider_year: group.provider_year,
        status: available.length === 0 ? 'unavailable' : 'available',
        document_count: available.length,
        unavailable_labels: Object.freeze(unavailableLabels),
      }));
    }
  }

  const selected = candidates.slice(0, request.max_documents);
  const truncated = candidates.length > selected.length;
  const unavailablePeriodLabels = coverage.flatMap((item) => {
    if (item.status !== 'unavailable') return [];
    if (item.unavailable_labels.length === 0) {
      return [`${item.category}:${item.provider_year}`];
    }
    return item.unavailable_labels.map((label) => (
      `${item.category}:${label} ${item.provider_year}`
    ));
  });
  const limitations = [
    'Provider year and month labels are discovery metadata, not validated fiscal periods.',
    'Fiscal year and quarter remain unclassified until validated from the downloaded document.',
  ];
  if (request.from_fiscal_year !== null) {
    limitations.push(
      'The fiscal-year lower bound was not applied because discovery labels do not establish fiscal periods.',
    );
  }
  if (request.through_date !== null) {
    limitations.push(
      'The through-date filter uses provider calendar month only; exact publication dates are unavailable.',
    );
  }
  if (truncated) limitations.push('Discovery results were truncated at max_documents.');

  const fingerprintInput = {
    schema_version: 'tijori.company_filings.v1',
    issuer: request.issuer,
    categories: request.categories,
    source_page_url: sourcePageUrl,
    discovered_at: discoveredAt.toISOString(),
    filings: selected,
    coverage,
    unavailable_period_labels: unavailablePeriodLabels,
  };
  const discoveryFingerprint = sha256(canonicalJson(fingerprintInput));
  const filings = selected.map((entry) => Object.freeze({
    filing_reference_id: entry.filing_reference_id,
    category: entry.category,
    provider_company_id: request.issuer.provider_company_id,
    provider_slug: request.issuer.provider_slug,
    source_page_url: sourcePageUrl,
    artifact_url: entry.artifact_url,
    discovery_fingerprint: discoveryFingerprint,
    source_class: entry.source_class,
    period: Object.freeze({
      state: 'unclassified',
      period_type: 'unclassified',
      source_label: entry.source_label,
      fiscal_year: null,
      fiscal_quarter: null,
      period_start: null,
      period_end: null,
    }),
    title: entry.title,
    provider_label: entry.provider_label,
    provider_year: entry.provider_year,
    provider_month: entry.provider_month,
    original_filename: entry.original_filename,
    provider_published_at: null,
    available_at: null,
    discovered_at: discoveredAt.toISOString(),
  }));

  return deepFreeze({
    schema_version: 'tijori.company_filings.v1',
    issuer: { ...request.issuer },
    source: {
      provider: 'tijori',
      location: sourcePageUrl,
      discovered_at: discoveredAt.toISOString(),
    },
    requested_filters: {
      categories: [...request.categories],
      from_fiscal_year: request.from_fiscal_year,
      through_date: request.through_date,
      max_documents: request.max_documents,
    },
    discovery_fingerprint: discoveryFingerprint,
    filings,
    coverage,
    unavailable_period_labels: unavailablePeriodLabels,
    validation_status: 'validated',
    limitations,
    extraction: {
      filing_count: filings.length,
      available_candidate_count: candidates.length,
      coverage_cell_count: coverage.length,
      truncated,
      status: 'complete',
    },
  });
}

/** Read the provider table in the browser; exported for deterministic fixtures. */
export function readCompanyFilingsSnapshot() {
  const table = document.querySelector('#companyFilings');
  if (!(table instanceof HTMLTableElement)) {
    throw new TypeError('Company Filings table is unavailable');
  }
  const providerYears = Array.from(
    table.querySelectorAll('thead tr:first-child th.data__cell'),
  ).map((cell) => cell.textContent?.trim().replace(/\s+/g, ' ') ?? '');
  const rows = Array.from(table.querySelectorAll('tbody > tr')).map((row) => {
    const nameCell = row.querySelector(':scope > td.name__cell');
    const groups = Array.from(
      row.querySelectorAll(':scope > td.data__cell'),
    ).map((cell, index) => ({
      provider_year: providerYears[index] ?? null,
      entries: [
        ...Array.from(cell.querySelectorAll('a[href]')).map((link) => ({
          kind: 'available',
          label: link.textContent?.trim().replace(/\s+/g, ' ') ?? '',
          href: link.getAttribute('href'),
        })),
        ...Array.from(cell.querySelectorAll('.disabled')).map((label) => ({
          kind: 'unavailable',
          label: label.textContent?.trim().replace(/\s+/g, ' ') ?? '',
          href: null,
        })),
      ],
    }));
    return {
      label: nameCell?.textContent?.trim().replace(/\s+/g, ' ') ?? '',
      class_names: nameCell === null ? [] : Array.from(nameCell.classList),
      groups,
    };
  });
  return { provider_years: providerYears, rows };
}

function normalizeCategoryRow(row, providerYears) {
  requirePlainObject(row, 'Company Filings row');
  const label = boundedString(row.label, 'Company Filings row label', 120);
  if (!Array.isArray(row.class_names) || !Array.isArray(row.groups)) {
    throw new TypeError('Company Filings row requires classes and year groups');
  }
  const matched = Object.entries(CATEGORY_DEFINITIONS).filter(([, definition]) => (
    row.class_names.includes(definition.class_name)
  ));
  if (matched.length === 0) return null;
  if (matched.length !== 1) {
    throw new TypeError('Company Filings row category is ambiguous');
  }
  const [category, definition] = matched[0];
  if (comparable(label) !== comparable(definition.label)) {
    throw new TypeError('Company Filings category label is malformed');
  }
  if (row.groups.length !== providerYears.length) {
    throw new TypeError('Company Filings category row does not cover every year');
  }
  return {
    category,
    groups: row.groups.map((group, index) => normalizeGroup(
      group,
      providerYears[index],
    )),
  };
}

function normalizeGroup(group, expectedYear) {
  requirePlainObject(group, 'Company Filings year group');
  if (normalizeProviderYear(group.provider_year) !== expectedYear) {
    throw new TypeError('Company Filings year group is misaligned');
  }
  if (!Array.isArray(group.entries) || group.entries.length > MAX_LINKS_PER_CELL) {
    throw new RangeError('Company Filings year group exceeds its safety bound');
  }
  return { provider_year: expectedYear, entries: group.entries };
}

function normalizeEntry(entry, category, providerYear, issuer, sourcePageUrl) {
  requirePlainObject(entry, 'Company Filings entry');
  const allowedKeys = new Set(['kind', 'label', 'href']);
  if (Object.keys(entry).some((key) => !allowedKeys.has(key))) {
    throw new TypeError('Company Filings entry contains unsupported fields');
  }
  const kind = boundedString(entry.kind, 'Company Filings entry kind', 20);
  const providerLabel = boundedString(
    entry.label,
    'Company Filings entry label',
    80,
  );
  if (kind === 'unavailable') {
    if (entry.href !== null && entry.href !== undefined) {
      throw new TypeError('Unavailable Company Filings entry cannot contain a URL');
    }
    return { kind, provider_label: providerLabel };
  }
  if (kind !== 'available') {
    throw new TypeError('Company Filings entry kind is unsupported');
  }
  const providerMonth = MONTHS.get(providerLabel.toLowerCase());
  if (providerMonth === undefined) {
    throw new TypeError('Company Filings document label must be a calendar month');
  }
  const artifactUrl = validateArtifactUrl(
    entry.href,
    issuer.provider_company_id,
    CATEGORY_DEFINITIONS[category].artifact_directory,
  );
  const parsed = new URL(artifactUrl);
  const originalFilename = decodeURIComponent(parsed.pathname.split('/').at(-1));
  const sourceLabel = `${providerLabel} ${providerYear}`;
  const identifierHash = sha256(`${category}\n${artifactUrl}`);
  return {
    kind,
    filing_reference_id: `filing.${issuer.symbol.toLowerCase()}.${identifierHash.slice(0, 32)}`,
    category,
    source_class: CATEGORY_DEFINITIONS[category].source_class,
    source_page_url: sourcePageUrl,
    artifact_url: artifactUrl,
    source_label: sourceLabel,
    title: `${CATEGORY_DEFINITIONS[category].label} — ${sourceLabel}`,
    provider_label: providerLabel,
    provider_year: providerYear,
    provider_month: providerMonth,
    original_filename: originalFilename,
  };
}

function validateArtifactUrl(value, providerCompanyId, categoryDirectory) {
  const normalized = boundedString(value, 'Company Filings artifact URL', 2_000);
  let parsed;
  try {
    parsed = new URL(normalized);
  } catch {
    throw new TypeError('Company Filings artifact URL is invalid');
  }
  if (
    parsed.origin !== ARTIFACT_ORIGIN
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
  ) {
    throw new TypeError('Company Filings artifact URL is outside the approved provider');
  }
  let path;
  try {
    path = decodeURIComponent(parsed.pathname);
  } catch {
    throw new TypeError('Company Filings artifact URL has invalid encoding');
  }
  const prefix = `/insight/india/${providerCompanyId}/${categoryDirectory}/`;
  if (!path.startsWith(prefix)) {
    throw new TypeError('Company Filings artifact URL is not issuer/category bound');
  }
  const filename = path.slice(prefix.length);
  if (
    !filename
    || filename.length > 300
    || filename.includes('/')
    || !/\.pdf$/i.test(filename)
    || [...filename].some((character) => character.charCodeAt(0) < 32)
  ) {
    throw new TypeError('Company Filings artifact filename is invalid');
  }
  return parsed.toString();
}

function withinThroughMonth(entry, throughDate) {
  if (throughDate === null) return true;
  const [year, month] = throughDate.split('-').map(Number);
  return (
    entry.provider_year < year
    || (entry.provider_year === year && entry.provider_month <= month)
  );
}

function normalizeProviderYear(value) {
  const normalized = boundedString(value, 'Company Filings provider year', 4);
  if (!/^\d{4}$/.test(normalized)) {
    throw new TypeError('Company Filings provider year is malformed');
  }
  const year = Number(normalized);
  if (year < 1900 || year > 2200) {
    throw new RangeError('Company Filings provider year is outside its safety bound');
  }
  return year;
}

function companyPageUrl(slug) {
  return `${BASE_URL}/company/${slug}/`;
}

function responseMatchesIssuerPath(response, slug) {
  if (response === null || typeof response?.url !== 'function') return false;
  try {
    const url = new URL(response.url());
    return url.origin === BASE_URL && url.pathname === `/company/${slug}/`;
  } catch {
    return false;
  }
}

function comparable(value) {
  return value.trim().replace(/\s+/g, ' ').toLowerCase();
}

function sha256(value) {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(',')}}`;
  }
  return JSON.stringify(value);
}

function boundedString(value, field, maxLength) {
  if (typeof value !== 'string') throw new TypeError(`${field} must be a string`);
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (!normalized || normalized.length > maxLength) {
    throw new TypeError(`${field} must be non-blank and bounded`);
  }
  return normalized;
}

function requireValidDate(value, field) {
  if (!(value instanceof Date) || Number.isNaN(value.valueOf())) {
    throw new TypeError(`${field} returned an invalid date`);
  }
}

function requirePlainObject(value, field) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${field} must be an object`);
  }
}

function requireUnique(values, field) {
  if (new Set(values).size !== values.length) {
    throw new TypeError(`${field} must be unique`);
  }
}

function deepFreeze(value) {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
