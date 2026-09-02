const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const BASIS_VALUES = new Set(['consolidated', 'standalone', 'not_applicable']);
const MAX_COLUMNS = 120;
const MAX_ROWS = 500;
const MAX_CELLS = 5_000;


/** Extract one complete Growth Table scenario without exposing browser state. */
export function createGrowthTableSnapshotExtractor({ browserRunner, clock = () => new Date() }) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Growth Table extraction requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Growth Table extraction clock must be callable');
  }

  return async function extractGrowthTable(argumentsValue) {
    const request = validateRequest(argumentsValue);
    const retrievedAt = clock();
    if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
      throw new TypeError('Growth Table extraction clock returned an invalid date');
    }

    return browserRunner.run(async (page) => {
      const response = await page.goto(
        `${BASE_URL}/company/${request.issuer.provider_slug}/financials/`,
        { timeout: 20_000, waitUntil: 'commit' },
      );
      if (!responseMatchesIssuerPath(response, request.issuer.provider_slug)) {
        throw new TypeError('Growth Table response did not match the resolved issuer');
      }

      await page.waitForFunction(financialTableSourceReady, undefined, { timeout: 20_000 });
      const embeddedGrowth = await page.evaluate(readEmbeddedGrowthPayload);
      if (embeddedGrowth !== null) {
        const raw = normalizeEmbeddedGrowthData(embeddedGrowth);
        return normalizeGrowthTable(raw, request, retrievedAt);
      }

      // Guarded fallback for provider pages that do not embed fin_tables_data.
      await clickExactVisibleText(page, 'Growth Table');
      await page.waitForFunction(growthTableReady, undefined, { timeout: 20_000 });
      await expandVisibleGrowthSections(page);
      await page.waitForFunction(growthTableStable, undefined, { timeout: 20_000 });

      const raw = await page.evaluate(readRenderedGrowthTable, 'not_applicable');
      return normalizeGrowthTable(raw, request, retrievedAt);
    });
  };
}

/** Convert the allowlisted embedded growth payload into the MCP document shape. */
export function normalizeEmbeddedGrowthData(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Embedded Growth Table payload must be an object');
  }
  if (!Array.isArray(value.report_dates)
      || value.report_dates.length === 0
      || value.report_dates.length > MAX_COLUMNS) {
    throw new TypeError('Embedded Growth Table periods must be a bounded non-empty array');
  }
  const columns = value.report_dates.map((label, displayOrder) => {
    const sourceLabel = boundedString(label, 'Embedded Growth Table period label', 80);
    const periodToken = makeIdentifier(sourceLabel, `column_${displayOrder}`).slice(0, 72);
    return {
      column_key: boundedIdentifier(
        `period_${periodToken}`,
        'Embedded Growth Table period',
        80,
      ),
      source_label: sourceLabel,
      display_order: displayOrder,
    };
  });
  requireUnique(columns.map(({ column_key }) => column_key), 'Embedded Growth Table periods');
  if (!Array.isArray(value.data) || value.data.length === 0) {
    throw new TypeError('Embedded Growth Table rows must be a non-empty array');
  }

  const rows = [];
  const keyCounts = new Map();
  const visit = (providerRows, parentRowKey, depth) => {
    if (!Array.isArray(providerRows)) {
      throw new TypeError('Embedded Growth Table child rows must be an array');
    }
    for (const providerRow of providerRows) {
      if (providerRow === null || typeof providerRow !== 'object' || Array.isArray(providerRow)) {
        throw new TypeError('Embedded Growth Table row must be an object');
      }
      if (providerRow.index !== depth + 1) {
        throw new TypeError('Embedded Growth Table row depth is inconsistent');
      }
      const originalLabel = boundedString(
        providerRow.name,
        'Embedded Growth Table row label',
        300,
      );
      const baseKey = makeIdentifier(originalLabel, `row_${rows.length}`);
      const occurrence = (keyCounts.get(baseKey) ?? 0) + 1;
      keyCounts.set(baseKey, occurrence);
      const rowKey = occurrence === 1 ? baseKey : `${baseKey}_${occurrence}`;
      const children = providerRow.sub_section === ''
        ? []
        : providerRow.sub_section;
      if (!Array.isArray(children)) {
        throw new TypeError('Embedded Growth Table sub-section is invalid');
      }
      validateOptionalProviderArray(providerRow.value_yoy, columns.length, 'year-over-year');
      validateOptionalProviderArray(providerRow.value_perc, columns.length, 'percentage');
      if (!Array.isArray(providerRow.value) || providerRow.value.length !== columns.length) {
        throw new TypeError('Embedded Growth Table row must cover every period');
      }
      rows.push({
        row_key: rowKey,
        original_label: originalLabel,
        parent_row_key: parentRowKey,
        depth,
        row_kind: children.length > 0 ? 'section' : 'metric',
        display_order: rows.length,
        values: providerRow.value.map((sourceValue, index) => {
          if (sourceValue !== null
              && (typeof sourceValue !== 'number' || !Number.isFinite(sourceValue))) {
            throw new TypeError('Embedded Growth Table value must be finite or null');
          }
          return {
            column_key: columns[index].column_key,
            source_value: sourceValue === null ? null : String(sourceValue),
            yoy_change: providerRow.value_yoy?.[index] ?? null,
            percentage_of_parent: providerRow.value_perc?.[index] ?? null,
            availability_status: sourceValue === null ? 'unknown' : 'available',
          };
        }),
      });
      if (rows.length > MAX_ROWS) {
        throw new RangeError('Embedded Growth Table exceeds the row safety bound');
      }
      visit(children, rowKey, depth + 1);
    }
  };
  visit(value.data, null, 0);

  return {
    reporting_basis: 'not_applicable',
    unit: 'percent',
    all_sections_expanded: true,
    columns,
    rows,
  };
}

export function normalizeGrowthTable(raw, requestValue, retrievedAt) {
  const request = validateRequest(requestValue);
  if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
    throw new TypeError('Growth Table retrieval time is invalid');
  }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new TypeError('Growth Table DOM snapshot must be an object');
  }
  if (raw.reporting_basis !== 'not_applicable') {
    throw new TypeError('Growth Table must use its provider basis');
  }
  if (raw.all_sections_expanded !== true) {
    throw new TypeError('Growth Table contains collapsed sections');
  }
  const unit = boundedString(raw.unit, 'Growth Table unit', 80);
  const columns = normalizeColumns(raw.columns);
  const rows = normalizeRows(raw.rows, columns);
  if (rows.length * columns.length > MAX_CELLS) {
    throw new RangeError('Growth Table exceeds the cell safety bound');
  }

  return Object.freeze({
    schema_version: 'tijori.financial_document.v1',
    document_type: 'growth_table',
    reporting_basis: 'not_applicable',
    issuer: Object.freeze({ ...request.issuer }),
    source: Object.freeze({
      provider: 'tijori',
      location: `${BASE_URL}/company/${request.issuer.provider_slug}/financials/`,
      retrieved_at: retrievedAt.toISOString(),
    }),
    unit,
    all_sections_expanded: true,
    columns: Object.freeze(columns),
    rows: Object.freeze(rows),
    extraction: Object.freeze({
      column_count: columns.length,
      row_count: rows.length,
      status: 'complete',
    }),
  });
}

function financialTableSourceReady() {
  const embedded = document.getElementById('fin_tables_data');
  if (embedded !== null && embedded.textContent?.trim()) return true;
  return document.querySelector('#growth_table_table') !== null;
}

function readEmbeddedGrowthPayload() {
  const embedded = document.getElementById('fin_tables_data');
  if (embedded === null) return null;
  const parsed = JSON.parse(embedded.textContent ?? '');
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new TypeError('Embedded financial tables payload must be an object');
  }
  if (!Object.hasOwn(parsed, 'growth')) {
    throw new TypeError('Embedded financial tables payload has no Growth Table');
  }
  return parsed.growth;
}

async function clickExactVisibleText(page, label) {
  const locator = page.getByText(label, { exact: true }).filter({ visible: true });
  if (await locator.count() !== 1) {
    throw new TypeError(`Growth Table could not confirm ${label}`);
  }
  await locator.click();
}

async function expandVisibleGrowthSections(page) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const collapsed = page.locator(
      '[data-jarvis-financial-tab="growth_table"] [aria-expanded="false"], '
      + '[id*="growth" i] [aria-expanded="false"]',
    );
    const count = await collapsed.count();
    if (count === 0) return;
    for (let index = 0; index < count; index += 1) {
      await collapsed.nth(index).click();
    }
  }
  throw new TypeError('Growth Table expansion did not converge');
}

function growthTableReady() {
  const tables = Array.from(document.querySelectorAll('table')).filter((table) => {
    const owner = table.closest('[data-jarvis-financial-tab="growth_table"], [id*="growth" i]');
    if (owner === null) return false;
    const style = getComputedStyle(table);
    return style.display !== 'none' && style.visibility !== 'hidden';
  });
  return tables.some((table) => (
    table.querySelectorAll('tr').length > 1
    && table.querySelectorAll('tr:first-child th,tr:first-child td').length > 1
  ));
}

function growthTableStable() {
  const tables = Array.from(document.querySelectorAll('table')).filter((table) => {
    const owner = table.closest('[data-jarvis-financial-tab="growth_table"], [id*="growth" i]');
    if (owner === null) return false;
    const style = getComputedStyle(table);
    return style.display !== 'none' && style.visibility !== 'hidden';
  });
  if (tables.length === 0) return false;
  if (tables.some((table) => (
    table.querySelector('[aria-expanded="false"]') !== null
    || table.closest('[aria-busy="true"]') !== null
  ))) return false;
  const signature = tables.map((table) => (
    `${table.querySelectorAll('tr').length}:${table.textContent?.length ?? 0}`
  )).join('|');
  const stateKey = '__jarvisGrowthTableStability';
  const previous = globalThis[stateKey];
  globalThis[stateKey] = {
    signature,
    stableChecks: previous?.signature === signature
      ? previous.stableChecks + 1
      : 0,
  };
  return globalThis[stateKey].stableChecks >= 2;
}

function readRenderedGrowthTable(reportingBasis) {
  const clean = (value) => String(value ?? '').trim().replace(/\s+/g, ' ');
  const makeKey = (value) => {
    const normalized = clean(value).toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
    return normalized || 'row';
  };
  const tables = Array.from(document.querySelectorAll('table')).filter((table) => {
    const owner = table.closest('[data-jarvis-financial-tab="growth_table"], [id*="growth" i]');
    if (owner === null) return false;
    const style = getComputedStyle(table);
    return style.display !== 'none' && style.visibility !== 'hidden';
  });
  const table = tables.sort((left, right) => (
    right.querySelectorAll('tr').length - left.querySelectorAll('tr').length
  ))[0];
  if (!table) return null;
  const headerRow = table.querySelector('thead tr') ?? table.querySelector('tr');
  const headerCells = Array.from(headerRow?.querySelectorAll('th,td') ?? []);
  const columns = headerCells.slice(1).map((cell, displayOrder) => ({
    column_key: `column_${displayOrder}`,
    source_label: clean(cell.textContent),
    display_order: displayOrder,
  }));
  const bodyRows = table.querySelectorAll('tbody tr').length > 0
    ? Array.from(table.querySelectorAll('tbody tr'))
    : Array.from(table.querySelectorAll('tr')).slice(1);
  const keyCounts = new Map();
  const hierarchy = [];
  const rows = bodyRows.map((row, displayOrder) => {
    const cells = Array.from(row.querySelectorAll('th,td'));
    const originalLabel = clean(cells[0]?.textContent);
    const baseKey = makeKey(row.getAttribute('data-id') || originalLabel || `row_${displayOrder}`);
    const occurrence = (keyCounts.get(baseKey) ?? 0) + 1;
    keyCounts.set(baseKey, occurrence);
    const rowKey = occurrence === 1 ? baseKey : `${baseKey}_${occurrence}`;
    const declaredDepth = Number.parseInt(
      row.getAttribute('aria-level') ?? row.getAttribute('data-level') ?? '',
      10,
    );
    const padding = Number.parseFloat(getComputedStyle(cells[0]).paddingLeft || '0');
    const depth = Number.isInteger(declaredDepth) && declaredDepth > 0
      ? declaredDepth - 1
      : Math.max(0, Math.round(padding / 20));
    hierarchy.length = depth;
    const parentRowKey = depth === 0 ? null : hierarchy[depth - 1] ?? null;
    hierarchy[depth] = rowKey;
    const expandable = row.querySelector('[aria-expanded]') !== null;
    return {
      row_key: rowKey,
      original_label: originalLabel,
      parent_row_key: parentRowKey,
      depth,
      row_kind: expandable ? 'section' : 'metric',
      display_order: displayOrder,
      values: columns.map((column, index) => {
        const sourceValue = clean(cells[index + 1]?.textContent);
        return {
          column_key: column.column_key,
          source_value: sourceValue === '' || sourceValue === '-' || sourceValue === '—'
            ? null
            : sourceValue,
          yoy_change: null,
          percentage_of_parent: null,
          availability_status: sourceValue === '' || sourceValue === '-' || sourceValue === '—'
            ? 'unknown'
            : 'available',
        };
      }),
    };
  });
  return {
    reporting_basis: reportingBasis,
    unit: clean(
      table.closest('[data-jarvis-financial-tab="growth_table"]')
        ?.querySelector('[data-unit],.unit,.units,[class*="unit"]')?.textContent
      ?? headerCells[0]?.textContent,
    ),
    all_sections_expanded: !table.querySelector('[aria-expanded="false"]'),
    columns,
    rows,
  };
}

function validateOptionalProviderArray(value, expectedLength, field) {
  if (value === undefined) return;
  if (!Array.isArray(value) || value.length !== expectedLength) {
    throw new TypeError(`Embedded Growth Table ${field} values must cover every period`);
  }
  if (value.some((item) => item !== null && typeof item !== 'string')) {
    throw new TypeError(`Embedded Growth Table ${field} value is invalid`);
  }
}

function makeIdentifier(value, fallback) {
  const normalized = String(value).trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

function normalizeColumns(value) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_COLUMNS) {
    throw new TypeError('Growth Table columns must be a bounded non-empty array');
  }
  const columns = value.map((column, index) => {
    if (column === null || typeof column !== 'object' || Array.isArray(column)) {
      throw new TypeError('Growth Table column must be an object');
    }
    return Object.freeze({
      column_key: boundedIdentifier(column.column_key, 'Growth Table column key', 80),
      source_label: boundedString(column.source_label, 'Growth Table column label', 80),
      display_order: boundedInteger(column.display_order, 'Growth Table column order', 0, 200),
    });
  });
  requireUnique(columns.map(({ column_key }) => column_key), 'Growth Table column keys');
  requireUnique(columns.map(({ display_order }) => display_order), 'Growth Table column order');
  if (columns.some(({ display_order }, index) => display_order !== index)) {
    throw new TypeError('Growth Table columns must use contiguous display order');
  }
  return columns;
}

function normalizeRows(value, columns) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_ROWS) {
    throw new TypeError('Growth Table rows must be a bounded non-empty array');
  }
  const rows = value.map((row, index) => normalizeRow(row, columns, index));
  requireUnique(rows.map(({ row_key }) => row_key), 'Growth Table row keys');
  requireUnique(rows.map(({ display_order }) => display_order), 'Growth Table row order');
  const rowsByKey = new Map(rows.map((row) => [row.row_key, row]));
  for (const row of rows) {
    if (row.display_order !== rows.indexOf(row)) {
      throw new TypeError('Growth Table rows must use contiguous display order');
    }
    if (row.depth === 0 && row.parent_row_key !== null) {
      throw new TypeError('Growth Table root row cannot have a parent');
    }
    if (row.depth > 0) {
      const parent = rowsByKey.get(row.parent_row_key);
      if (parent === undefined || parent.depth !== row.depth - 1 || parent.display_order >= row.display_order) {
        throw new TypeError('Growth Table row hierarchy is invalid');
      }
    }
  }
  return rows;
}

function normalizeRow(row, columns, index) {
  if (row === null || typeof row !== 'object' || Array.isArray(row)) {
    throw new TypeError('Growth Table row must be an object');
  }
  if (!Array.isArray(row.values) || row.values.length !== columns.length) {
    throw new TypeError('Growth Table row must cover every column');
  }
  const values = row.values.map((cell, cellIndex) => {
    if (cell === null || typeof cell !== 'object' || Array.isArray(cell)) {
      throw new TypeError('Growth Table cell must be an object');
    }
    const expectedKey = columns[cellIndex].column_key;
    if (cell.column_key !== expectedKey) {
      throw new TypeError('Growth Table cell order does not match its columns');
    }
    const status = cell.availability_status;
    if (status !== 'available' && status !== 'unknown') {
      throw new TypeError('Growth Table cell availability is invalid');
    }
    const sourceValue = cell.source_value === null
      ? null
      : boundedString(cell.source_value, 'Growth Table cell value', 500);
    const yoyChange = optionalBoundedString(
      cell.yoy_change,
      'Growth Table year-over-year change',
      80,
    );
    const percentageOfParent = optionalBoundedString(
      cell.percentage_of_parent,
      'Growth Table percentage of parent',
      80,
    );
    if ((status === 'available') !== (sourceValue !== null)) {
      throw new TypeError('Growth Table cell availability contradicts its value');
    }
    return Object.freeze({
      column_key: expectedKey,
      source_value: sourceValue,
      yoy_change: yoyChange,
      percentage_of_parent: percentageOfParent,
      availability_status: status,
    });
  });
  return Object.freeze({
    row_key: boundedIdentifier(row.row_key, 'Growth Table row key', 200),
    original_label: boundedString(row.original_label, 'Growth Table row label', 300),
    parent_row_key: row.parent_row_key === null
      ? null
      : boundedIdentifier(row.parent_row_key, 'Growth Table parent row key', 200),
    depth: boundedInteger(row.depth, 'Growth Table row depth', 0, 20),
    row_kind: enumValue(row.row_kind, 'Growth Table row kind', ['section', 'total', 'subtotal', 'component', 'metric']),
    display_order: boundedInteger(row.display_order, 'Growth Table row order', 0, MAX_ROWS - 1),
    values: Object.freeze(values),
  });
}

function validateRequest(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Growth Table request must be an object');
  }
  const basis = enumValue(value.reporting_basis, 'Growth Table reporting basis', [...BASIS_VALUES]);
  const issuer = value.issuer;
  if (issuer === null || typeof issuer !== 'object' || Array.isArray(issuer)) {
    throw new TypeError('Growth Table issuer must be an object');
  }
  const exchange = boundedString(issuer.exchange, 'Growth Table issuer exchange', 32).toUpperCase();
  const symbol = boundedString(issuer.symbol, 'Growth Table issuer symbol', 100).toUpperCase();
  const legalName = boundedString(issuer.legal_name, 'Growth Table issuer name', 300);
  const providerSlug = boundedString(issuer.provider_slug, 'Growth Table issuer slug', 240).toLowerCase();
  if (!MARKET_PATTERN.test(exchange) || !MARKET_PATTERN.test(symbol) || !SLUG_PATTERN.test(providerSlug)) {
    throw new TypeError('Growth Table issuer identity is invalid');
  }
  return Object.freeze({
    reporting_basis: basis,
    issuer: Object.freeze({
      exchange,
      symbol,
      legal_name: legalName,
      provider_company_id: issuer.provider_company_id ?? null,
      provider_slug: providerSlug,
    }),
  });
}

function responseMatchesIssuerPath(response, slug) {
  if (typeof response?.url !== 'function') return false;
  try {
    const target = new URL(response.url());
    return target.protocol === 'https:'
      && target.hostname === 'www.tijorifinance.com'
      && target.pathname === `/company/${slug}/financials/`;
  } catch {
    return false;
  }
}

function enumValue(value, field, allowed) {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    throw new TypeError(`${field} is invalid`);
  }
  return value;
}

function boundedIdentifier(value, field, maxLength) {
  const normalized = boundedString(value, field, maxLength);
  if (!/^[a-z][a-z0-9_.:-]*$/.test(normalized)) {
    throw new TypeError(`${field} must be a bounded identifier`);
  }
  return normalized;
}

function boundedString(value, field, maxLength) {
  if (typeof value !== 'string') throw new TypeError(`${field} must be a string`);
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (!normalized || normalized.length > maxLength) {
    throw new TypeError(`${field} must be non-blank and bounded`);
  }
  return normalized;
}

function optionalBoundedString(value, field, maxLength) {
  if (value === undefined || value === null) return null;
  return boundedString(value, field, maxLength);
}

function boundedInteger(value, field, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${field} must be an integer from ${minimum} to ${maximum}`);
  }
  return value;
}

function requireUnique(values, field) {
  if (new Set(values).size !== values.length) {
    throw new TypeError(`${field} must be unique`);
  }
}
