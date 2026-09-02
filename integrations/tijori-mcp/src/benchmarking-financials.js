const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const ROW_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,80}$/;
const MAX_COMPANIES = 30;
const MAX_ROWS = 300;
const MAX_CELLS = 9_000;


/** Extract the complete Financial matrix, including provider-hidden rows. */
export function createBenchmarkingFinancialsExtractor({
  browserRunner,
  clock = () => new Date(),
}) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Benchmarking extraction requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Benchmarking extraction clock must be callable');
  }
  return async function extractBenchmarkingFinancials(argumentsValue) {
    const request = validateRequest(argumentsValue);
    const retrievedAt = clock();
    validDate(retrievedAt, 'Benchmarking extraction clock result');
    return browserRunner.run(async (page) => {
      const location = `${BASE_URL}/company/${request.issuer.provider_slug}/benchmarking/`;
      const response = await page.goto(location, {
        timeout: 20_000,
        waitUntil: 'domcontentloaded',
      });
      if (!responseMatchesPath(response, request.issuer.provider_slug)) {
        throw new TypeError('Benchmarking response did not match the resolved issuer');
      }
      await page.waitForSelector(
        '#benchmarking-financial table.bch_table tbody tr[tableRow]',
        { timeout: 20_000 },
      );
      const raw = await page.evaluate(readBenchmarkingFinancialMatrix);
      return normalizeBenchmarkingFinancials(raw, request, retrievedAt);
    });
  };
}

/** Normalize one rendered, hierarchical cross-company matrix. */
export function normalizeBenchmarkingFinancials(raw, requestValue, retrievedAt) {
  const request = validateRequest(requestValue);
  validDate(retrievedAt, 'Benchmarking retrieval time');
  requireObject(raw, 'Benchmarking snapshot');
  if (!Array.isArray(raw.companies) || !Array.isArray(raw.rows)) {
    throw new TypeError('Benchmarking snapshot requires companies and rows');
  }
  if (raw.companies.length < 2 || raw.companies.length > MAX_COMPANIES) {
    throw new RangeError('Benchmarking company count is outside its safety bound');
  }
  if (raw.rows.length === 0 || raw.rows.length > MAX_ROWS) {
    throw new RangeError('Benchmarking row count is outside its safety bound');
  }
  if (raw.companies.length * raw.rows.length > MAX_CELLS) {
    throw new RangeError('Benchmarking snapshot exceeds its cell safety bound');
  }

  const companies = raw.companies.map((company, index) => normalizeCompany(
    company,
    index,
    request.issuer.provider_slug,
  ));
  unique(companies.map(({ provider_slug }) => provider_slug), 'Benchmarking companies');
  if (companies.filter(({ is_subject }) => is_subject).length !== 1) {
    throw new TypeError('Benchmarking must identify the subject exactly once');
  }

  const knownRows = new Map();
  const rows = raw.rows.map((row, index) => {
    const normalized = normalizeRow(row, index, companies, knownRows);
    knownRows.set(normalized.row_key, normalized);
    return normalized;
  });
  unique(rows.map(({ row_key }) => row_key), 'Benchmarking row keys');

  return Object.freeze({
    schema_version: 'tijori.benchmarking_financials.v1',
    document_type: 'benchmarking_financials',
    reporting_basis: 'not_applicable',
    issuer: Object.freeze({ ...request.issuer }),
    source: Object.freeze({
      provider: 'tijori',
      location: `${BASE_URL}/company/${request.issuer.provider_slug}/benchmarking/`,
      retrieved_at: retrievedAt.toISOString(),
    }),
    observation_date: istDate(retrievedAt),
    all_rows_captured: true,
    companies: Object.freeze(companies),
    rows: Object.freeze(rows),
    extraction: Object.freeze({
      company_count: companies.length,
      row_count: rows.length,
      cell_count: companies.length * rows.length,
      maximum_depth: Math.max(...rows.map(({ depth }) => depth)),
      provider_hidden_row_count: rows.filter(({ provider_hidden }) => provider_hidden).length,
      status: 'complete',
    }),
  });
}

function readBenchmarkingFinancialMatrix() {
  const table = document.querySelector('#benchmarking-financial table.bch_table');
  if (!(table instanceof HTMLTableElement)) {
    throw new TypeError('Benchmarking Financial table is unavailable');
  }
  const companies = Array.from(table.querySelectorAll('thead th.compnaycol')).map((header) => {
    const identity = header.querySelector('[goto]');
    return {
      legal_name: identity?.textContent?.trim().replace(/\s+/g, ' ') ?? '',
      href: identity?.getAttribute('goto') ?? '',
    };
  });
  const rows = Array.from(table.querySelectorAll('tbody tr[tableRow]')).map((row) => {
    const cells = Array.from(row.querySelectorAll(':scope > td'));
    const rawCount = row.getAttribute('count_items')?.trim() ?? '';
    return {
      provider_row_id: row.getAttribute('myid') ?? row.id,
      parent_provider_row_id: row.getAttribute('parent') || null,
      depth: Number(row.getAttribute('index')) - 1,
      provider_section: row.getAttribute('tableRow'),
      provider_hidden: row.getAttribute('should_hide') === 'yes',
      has_children: /^\d+$/.test(rawCount) && Number(rawCount) > 0,
      label: row.querySelector('.nameofmetriccol')?.textContent
        ?.trim().replace(/\s+/g, ' ') ?? '',
      values: cells.slice(1).map((cell) => ({
        source_value: cell.textContent?.trim().replace(/\s+/g, ' ') ?? '',
        is_best: cell.querySelector('.isBest') !== null,
      })),
    };
  });
  return { companies, rows };
}

function normalizeCompany(value, index, subjectSlug) {
  requireObject(value, 'Benchmarking company');
  const providerSlug = slugFromHref(value.href);
  return Object.freeze({
    company_key: `company_${providerSlug.replaceAll('-', '_')}`,
    legal_name: text(value.legal_name, 'Benchmarking company name', 300),
    provider_slug: providerSlug,
    is_subject: providerSlug === subjectSlug,
    display_order: index,
  });
}

function normalizeRow(value, index, companies, knownRows) {
  requireObject(value, 'Benchmarking row');
  const rowKey = rowId(value.provider_row_id, 'Benchmarking row ID');
  const parentRowKey = value.parent_provider_row_id === null
    ? null
    : rowId(value.parent_provider_row_id, 'Benchmarking parent row ID');
  const depth = integer(value.depth, 'Benchmarking row depth', 0, 10);
  if (parentRowKey === null && depth !== 0) {
    throw new TypeError('Benchmarking root row must have depth zero');
  }
  if (parentRowKey !== null) {
    const parent = knownRows.get(parentRowKey);
    if (parent === undefined || parent.depth + 1 !== depth) {
      throw new TypeError('Benchmarking row hierarchy is inconsistent');
    }
  }
  if (!Array.isArray(value.values) || value.values.length !== companies.length) {
    throw new TypeError('Benchmarking row must cover every company');
  }
  const values = value.values.map((cell, companyIndex) => {
    requireObject(cell, 'Benchmarking cell');
    const sourceValue = nullableSourceValue(cell.source_value);
    if (typeof cell.is_best !== 'boolean') {
      throw new TypeError('Benchmarking best-value marker must be boolean');
    }
    return Object.freeze({
      company_key: companies[companyIndex].company_key,
      source_value: sourceValue,
      availability_status: sourceValue === null ? 'unknown' : 'available',
      is_best: cell.is_best,
    });
  });
  if (typeof value.has_children !== 'boolean' || typeof value.provider_hidden !== 'boolean') {
    throw new TypeError('Benchmarking row state must be explicit');
  }
  return Object.freeze({
    row_key: rowKey,
    parent_row_key: parentRowKey,
    original_label: text(value.label, 'Benchmarking row label', 300),
    depth,
    row_kind: value.has_children ? 'section' : 'metric',
    provider_section: text(value.provider_section, 'Benchmarking provider section', 80),
    provider_hidden: value.provider_hidden,
    display_order: index,
    values: Object.freeze(values),
  });
}

function validateRequest(value) {
  requireObject(value, 'Benchmarking request');
  if (Object.keys(value).join(',') !== 'issuer') {
    throw new TypeError('Benchmarking request contains unsupported fields');
  }
  requireObject(value.issuer, 'Benchmarking issuer');
  const providerSlug = text(
    value.issuer.provider_slug,
    'Benchmarking issuer provider slug',
    240,
  ).toLowerCase();
  if (!SLUG_PATTERN.test(providerSlug)) {
    throw new TypeError('Benchmarking issuer provider slug is invalid');
  }
  for (const field of ['exchange', 'symbol', 'legal_name', 'provider_company_id']) {
    text(value.issuer[field], `Benchmarking issuer ${field}`, 300);
  }
  return { issuer: { ...value.issuer, provider_slug: providerSlug } };
}

function responseMatchesPath(response, slug) {
  if (response === null || typeof response?.url !== 'function') return false;
  try {
    const url = new URL(response.url());
    return url.origin === BASE_URL && url.pathname === `/company/${slug}/benchmarking/`;
  } catch {
    return false;
  }
}

function slugFromHref(value) {
  const href = text(value, 'Benchmarking company link', 500);
  let url;
  try {
    url = new URL(href, BASE_URL);
  } catch {
    throw new TypeError('Benchmarking company link is invalid');
  }
  const match = /^\/company\/([a-z0-9][a-z0-9-]{0,239})\/?$/.exec(url.pathname);
  if (url.origin !== BASE_URL || match === null) {
    throw new TypeError('Benchmarking company link is outside the provider');
  }
  return match[1];
}

function nullableSourceValue(value) {
  if (typeof value !== 'string') {
    throw new TypeError('Benchmarking source value must be text');
  }
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (normalized.length > 160) {
    throw new TypeError('Benchmarking source value exceeds its safety bound');
  }
  return normalized === '' || /^(?:-|—|na|n\/a)$/i.test(normalized)
    ? null
    : normalized;
}

function requireObject(value, label) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
}

function text(value, label, maximum) {
  if (typeof value !== 'string') throw new TypeError(`${label} must be text`);
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (normalized.length === 0 || normalized.length > maximum) {
    throw new TypeError(`${label} is outside its safety bound`);
  }
  return normalized;
}

function rowId(value, label) {
  const normalized = text(value, label, 80);
  if (!ROW_ID_PATTERN.test(normalized)) throw new TypeError(`${label} is invalid`);
  return normalized;
}

function integer(value, label, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${label} must be a bounded integer`);
  }
  return value;
}

function unique(values, label) {
  if (values.length !== new Set(values).size) {
    throw new TypeError(`${label} must be unique`);
  }
}

function validDate(value, label) {
  if (!(value instanceof Date) || Number.isNaN(value.valueOf())) {
    throw new TypeError(`${label} is invalid`);
  }
}

function istDate(value) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value);
  const selected = Object.fromEntries(
    parts.map(({ type, value: part }) => [type, part]),
  );
  return `${selected.year}-${selected.month}-${selected.day}`;
}
