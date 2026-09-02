const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const MAX_PERIODS = 120;
const MAX_ROWS = 500;
const MAX_CELLS = 20_000;


export function createFinancialStatementSnapshotExtractor({
  browserRunner,
  definition: definitionValue,
  clock = () => new Date(),
}) {
  const definition = validateDefinition(definitionValue);
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError(`${definition.displayName} extraction requires a browser runner`);
  }
  if (typeof clock !== 'function') {
    throw new TypeError(`${definition.displayName} extraction clock must be callable`);
  }
  return async function extractFinancialStatement(argumentsValue) {
    const request = validateRequest(argumentsValue, definition);
    const retrievedAt = clock();
    if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
      throw new TypeError(`${definition.displayName} extraction clock returned an invalid date`);
    }
    return browserRunner.run(async (page) => {
      const response = await page.goto(
        `${BASE_URL}/company/${request.issuer.provider_slug}/financials/`,
        { timeout: 20_000, waitUntil: 'commit' },
      );
      if (!responseMatchesIssuerPath(response, request.issuer.provider_slug)) {
        throw new TypeError(`${definition.displayName} response did not match the resolved issuer`);
      }
      await page.waitForFunction(financialTablesReady, undefined, { timeout: 20_000 });
      const tables = await page.evaluate(readEmbeddedFinancialTables);
      const raw = normalizeEmbeddedFinancialStatementData(
        tables,
        request.reporting_basis,
        definition,
      );
      return normalizeFinancialStatement(raw, request, retrievedAt, definition);
    });
  };
}

export function normalizeEmbeddedFinancialStatementData(
  value,
  reportingBasis,
  definitionValue,
) {
  const definition = validateDefinition(definitionValue);
  requireObject(value, 'Embedded financial tables payload');
  const basis = enumValue(
    reportingBasis,
    `${definition.displayName} reporting basis`,
    Object.keys(definition.basisKeys),
  );
  const source = value[definition.basisKeys[basis]];
  requireObject(source, `Embedded ${basis} ${definition.displayName}`);
  const periods = providerPeriods(source.report_dates, definition);
  const skipped = skippedPeriods(source.skip_report_dates ?? [], definition);
  if (!Array.isArray(source.data) || source.data.length === 0) {
    throw new TypeError(`Embedded ${definition.displayName} rows must be a non-empty array`);
  }

  const rows = [];
  const keyCounts = new Map();
  const visit = (providerRows, parentRowKey, depth) => {
    if (!Array.isArray(providerRows)) {
      throw new TypeError(`Embedded ${definition.displayName} child rows must be an array`);
    }
    for (const providerRow of providerRows) {
      requireObject(providerRow, `Embedded ${definition.displayName} row`);
      if (
        definition.providerDepthMode === 'strict'
        && providerRow.index !== depth + 1
      ) {
        throw new TypeError(`Embedded ${definition.displayName} row depth is inconsistent`);
      }
      const label = text(providerRow.name, `Embedded ${definition.displayName} row label`, 300);
      const baseKey = makeKey(label, `row_${rows.length}`);
      const occurrence = (keyCounts.get(baseKey) ?? 0) + 1;
      keyCounts.set(baseKey, occurrence);
      const rowKey = occurrence === 1 ? baseKey : `${baseKey}_${occurrence}`;
      const children = providerRow.sub_section === '' ? [] : providerRow.sub_section;
      if (!Array.isArray(children)) {
        throw new TypeError(`Embedded ${definition.displayName} sub-section is invalid`);
      }
      const suppliedPeriodCount = Array.isArray(providerRow.value)
        ? providerRow.value.length
        : 0;
      const primaryValues = providerArray(
        providerRow.value,
        periods.length,
        'primary',
        definition,
      );
      const yoyValues = optionalProviderArray(
        providerRow.value_yoy,
        suppliedPeriodCount,
        periods.length,
        'year-over-year',
        definition,
        definition.auxiliaryAlignment,
      );
      const percentageValues = optionalProviderArray(
        providerRow.value_perc,
        suppliedPeriodCount,
        periods.length,
        'percentage',
        definition,
        definition.auxiliaryAlignment,
      );
      const rowMetadata = definition.classifyRow === null
        ? {}
        : normalizedRowMetadata(
          definition.classifyRow(Object.freeze({ ...providerRow })),
          definition,
        );
      rows.push({
        row_key: rowKey,
        original_label: label,
        parent_row_key: parentRowKey,
        depth,
        row_kind: children.length > 0 ? 'section' : 'metric',
        display_order: rows.length,
        ...rowMetadata,
        values: primaryValues.map((sourceValue, index) => {
          if (sourceValue !== null && (typeof sourceValue !== 'number' || !Number.isFinite(sourceValue))) {
            throw new TypeError(`Embedded ${definition.displayName} value must be finite or null`);
          }
          return {
            period_key: periods[index].period_key,
            source_value: sourceValue === null ? null : String(sourceValue),
            yoy_change: auxiliaryValue(yoyValues[index]),
            percentage_of_parent: auxiliaryValue(percentageValues[index]),
            availability_status: sourceValue === null ? 'unknown' : 'available',
          };
        }),
      });
      if (rows.length > MAX_ROWS) {
        throw new RangeError(`Embedded ${definition.displayName} exceeds the row safety bound`);
      }
      visit(children, rowKey, depth + 1);
    }
  };
  visit(source.data, null, 0);
  if (rows.length * periods.length > MAX_CELLS) {
    throw new RangeError(`Embedded ${definition.displayName} exceeds the cell safety bound`);
  }
  return {
    reporting_basis: basis,
    source_unit: definition.sourceUnit,
    normalized_unit: definition.normalizedUnit,
    skipped_report_dates: skipped,
    all_sections_expanded: true,
    periods,
    rows,
  };
}

export function normalizeFinancialStatement(raw, requestValue, retrievedAt, definitionValue) {
  const definition = validateDefinition(definitionValue);
  const request = validateRequest(requestValue, definition);
  if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
    throw new TypeError(`${definition.displayName} retrieval time is invalid`);
  }
  requireObject(raw, `${definition.displayName} snapshot`);
  if (raw.reporting_basis !== request.reporting_basis) {
    throw new TypeError(`${definition.displayName} reporting basis does not match the request`);
  }
  if (raw.all_sections_expanded !== true) {
    throw new TypeError(`${definition.displayName} contains collapsed sections`);
  }
  const periods = normalizedPeriods(raw.periods, definition);
  const rows = normalizedRows(raw.rows, periods, definition);
  const skipped = skippedPeriods(raw.skipped_report_dates ?? [], definition);
  return Object.freeze({
    schema_version: 'tijori.financial_document.v1',
    document_type: definition.documentType,
    reporting_basis: request.reporting_basis,
    issuer: Object.freeze({ ...request.issuer }),
    source: Object.freeze({
      provider: 'tijori',
      location: `${BASE_URL}/company/${request.issuer.provider_slug}/financials/`,
      retrieved_at: retrievedAt.toISOString(),
    }),
    source_unit: text(raw.source_unit, `${definition.displayName} source unit`, 80),
    normalized_unit: text(raw.normalized_unit, `${definition.displayName} normalized unit`, 80),
    skipped_report_dates: Object.freeze(skipped),
    all_sections_expanded: true,
    periods: Object.freeze(periods),
    rows: Object.freeze(rows),
    extraction: Object.freeze({
      period_count: periods.length,
      row_count: rows.length,
      cell_count: periods.length * rows.length,
      maximum_depth: Math.max(...rows.map(({ depth }) => depth)),
      status: 'complete',
    }),
  });
}

function validateDefinition(value) {
  requireObject(value, 'Financial statement definition');
  requireObject(value.basisKeys, 'Financial statement basis keys');
  const definition = {
    documentType: identifier(value.documentType, 'Financial statement document type', 80),
    displayName: text(value.displayName, 'Financial statement name', 80),
    basisKeys: Object.freeze({
      consolidated: identifier(value.basisKeys.consolidated, 'Consolidated payload key', 80),
      standalone: identifier(value.basisKeys.standalone, 'Standalone payload key', 80),
    }),
    sourceUnit: text(value.sourceUnit, 'Financial statement source unit', 80),
    normalizedUnit: text(value.normalizedUnit, 'Financial statement normalized unit', 80),
    classifyRow: value.classifyRow ?? null,
    providerDepthMode: value.providerDepthMode ?? 'strict',
    auxiliaryAlignment: value.auxiliaryAlignment ?? 'exact',
  };
  if (definition.classifyRow !== null && typeof definition.classifyRow !== 'function') {
    throw new TypeError('Financial statement row classifier must be callable');
  }
  if (definition.basisKeys.consolidated === definition.basisKeys.standalone) {
    throw new TypeError('Financial statement basis keys must be distinct');
  }
  if (!['strict', 'nested'].includes(definition.providerDepthMode)) {
    throw new TypeError('Financial statement provider depth mode is invalid');
  }
  if (!['exact', 'trailing'].includes(definition.auxiliaryAlignment)) {
    throw new TypeError('Financial statement auxiliary alignment is invalid');
  }
  return Object.freeze(definition);
}

function financialTablesReady() {
  const embedded = document.getElementById('fin_tables_data');
  return embedded !== null && Boolean(embedded.textContent?.trim());
}

function readEmbeddedFinancialTables() {
  const embedded = document.getElementById('fin_tables_data');
  return embedded === null ? null : JSON.parse(embedded.textContent ?? '');
}

function providerPeriods(value, definition) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_PERIODS) {
    throw new TypeError(`Embedded ${definition.displayName} periods must be bounded and non-empty`);
  }
  const periods = value.map((label, displayOrder) => {
    const sourceLabel = text(label, `Embedded ${definition.displayName} period label`, 80);
    const token = makeKey(sourceLabel, `period_${displayOrder}`).slice(0, 70);
    return {
      period_key: identifier(`period_${token}`, `${definition.displayName} period key`, 80),
      source_label: sourceLabel,
      display_order: displayOrder,
    };
  });
  unique(periods.map(({ period_key }) => period_key), `${definition.displayName} period keys`);
  return periods;
}

function normalizedPeriods(value, definition) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_PERIODS) {
    throw new TypeError(`${definition.displayName} periods must be bounded and non-empty`);
  }
  const periods = value.map((period, index) => {
    requireObject(period, `${definition.displayName} period`);
    const result = Object.freeze({
      period_key: identifier(period.period_key, `${definition.displayName} period key`, 80),
      source_label: text(period.source_label, `${definition.displayName} period label`, 80),
      display_order: integer(period.display_order, `${definition.displayName} period order`, 0, 200),
    });
    if (result.display_order !== index) {
      throw new TypeError(`${definition.displayName} periods must use contiguous display order`);
    }
    return result;
  });
  unique(periods.map(({ period_key }) => period_key), `${definition.displayName} period keys`);
  return periods;
}

function normalizedRows(value, periods, definition) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_ROWS) {
    throw new TypeError(`${definition.displayName} rows must be bounded and non-empty`);
  }
  const rows = value.map((row, index) => {
    requireObject(row, `${definition.displayName} row`);
    if (!Array.isArray(row.values) || row.values.length !== periods.length) {
      throw new TypeError(`${definition.displayName} row must cover every period`);
    }
    const values = row.values.map((cell, cellIndex) => {
      requireObject(cell, `${definition.displayName} cell`);
      const periodKey = periods[cellIndex].period_key;
      if (cell.period_key !== periodKey) {
        throw new TypeError(`${definition.displayName} cell order does not match periods`);
      }
      const status = enumValue(cell.availability_status, `${definition.displayName} cell availability`, ['available', 'unknown']);
      const sourceValue = cell.source_value === null
        ? null
        : text(cell.source_value, `${definition.displayName} cell value`, 500);
      if ((status === 'available') !== (sourceValue !== null)) {
        throw new TypeError(`${definition.displayName} cell availability contradicts value`);
      }
      return Object.freeze({
        period_key: periodKey,
        source_value: sourceValue,
        yoy_change: optionalText(cell.yoy_change, `${definition.displayName} YoY change`, 80),
        percentage_of_parent: optionalText(cell.percentage_of_parent, `${definition.displayName} percentage`, 80),
        availability_status: status,
      });
    });
    return Object.freeze({
      row_key: identifier(row.row_key, `${definition.displayName} row key`, 200),
      original_label: text(row.original_label, `${definition.displayName} row label`, 300),
      parent_row_key: row.parent_row_key === null
        ? null
        : identifier(row.parent_row_key, `${definition.displayName} parent row key`, 200),
      depth: integer(row.depth, `${definition.displayName} row depth`, 0, 20),
      row_kind: enumValue(row.row_kind, `${definition.displayName} row kind`, ['section', 'total', 'subtotal', 'component', 'metric']),
      display_order: integer(row.display_order, `${definition.displayName} row order`, 0, MAX_ROWS - 1),
      ...(definition.classifyRow === null
        ? {}
        : normalizedRowMetadata(row, definition)),
      values: Object.freeze(values),
    });
  });
  unique(rows.map(({ row_key }) => row_key), `${definition.displayName} row keys`);
  const rowsByKey = new Map(rows.map((row) => [row.row_key, row]));
  rows.forEach((row, index) => {
    if (row.display_order !== index) {
      throw new TypeError(`${definition.displayName} rows must use contiguous display order`);
    }
    if (row.depth === 0 && row.parent_row_key !== null) {
      throw new TypeError(`${definition.displayName} root row cannot have a parent`);
    }
    if (row.depth > 0) {
      const parent = rowsByKey.get(row.parent_row_key);
      if (parent === undefined || parent.depth !== row.depth - 1 || parent.display_order >= index) {
        throw new TypeError(`${definition.displayName} row hierarchy is invalid`);
      }
    }
  });
  return rows;
}

function normalizedRowMetadata(value, definition) {
  requireObject(value, `${definition.displayName} row metadata`);
  return Object.freeze({
    value_kind: enumValue(
      value.value_kind,
      `${definition.displayName} row value kind`,
      ['monetary', 'percentage', 'count', 'per_share', 'ratio', 'other'],
    ),
    source_unit: text(value.source_unit, `${definition.displayName} row source unit`, 80),
    normalized_unit: text(
      value.normalized_unit,
      `${definition.displayName} row normalized unit`,
      80,
    ),
  });
}

function validateRequest(value, definition) {
  requireObject(value, `${definition.displayName} request`);
  const basis = enumValue(value.reporting_basis, `${definition.displayName} reporting basis`, Object.keys(definition.basisKeys));
  requireObject(value.issuer, `${definition.displayName} issuer`);
  const exchange = text(value.issuer.exchange, `${definition.displayName} issuer exchange`, 32).toUpperCase();
  const symbol = text(value.issuer.symbol, `${definition.displayName} issuer symbol`, 100).toUpperCase();
  const legalName = text(value.issuer.legal_name, `${definition.displayName} issuer name`, 300);
  const slug = text(value.issuer.provider_slug, `${definition.displayName} issuer slug`, 240).toLowerCase();
  if (!MARKET_PATTERN.test(exchange) || !MARKET_PATTERN.test(symbol) || !SLUG_PATTERN.test(slug)) {
    throw new TypeError(`${definition.displayName} issuer identity is invalid`);
  }
  return Object.freeze({
    reporting_basis: basis,
    issuer: Object.freeze({
      exchange,
      symbol,
      legal_name: legalName,
      provider_company_id: value.issuer.provider_company_id ?? null,
      provider_slug: slug,
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

function providerArray(value, periodCount, field, definition) {
  if (!Array.isArray(value) || value.length === 0 || value.length > periodCount) {
    throw new TypeError(`Embedded ${definition.displayName} ${field} values must fit its periods`);
  }
  if (value.some((item) => (
    item !== null
    && (typeof item !== 'number' || !Number.isFinite(item))
  ))) {
    throw new TypeError(`Embedded ${definition.displayName} ${field} value is invalid`);
  }
  return [...value, ...Array(periodCount - value.length).fill(null)];
}

function optionalProviderArray(
  value,
  suppliedCount,
  periodCount,
  field,
  definition,
  alignment,
) {
  if (value === undefined) return Array(periodCount).fill(null);
  if (
    !Array.isArray(value)
    || value.length > suppliedCount
    || (alignment === 'exact' && value.length !== suppliedCount)
  ) {
    throw new TypeError(`Embedded ${definition.displayName} ${field} values must align with primary values`);
  }
  if (value.some((item) => (
    item !== null
    && typeof item !== 'string'
    && (typeof item !== 'number' || !Number.isFinite(item))
  ))) {
    throw new TypeError(`Embedded ${definition.displayName} ${field} value is invalid`);
  }
  const leadingPadding = alignment === 'trailing'
    ? Array(suppliedCount - value.length).fill(null)
    : [];
  return [
    ...leadingPadding,
    ...value,
    ...Array(periodCount - suppliedCount).fill(null),
  ];
}

function auxiliaryValue(value) {
  if (value === undefined || value === null) return null;
  return typeof value === 'number' ? String(value) : value;
}

function skippedPeriods(value, definition) {
  if (!Array.isArray(value) || value.length > MAX_PERIODS) {
    throw new TypeError(`${definition.displayName} skipped periods must be a bounded array`);
  }
  const result = value.map((item) => text(item, `${definition.displayName} skipped period`, 80));
  unique(result, `${definition.displayName} skipped periods`);
  return result;
}

function makeKey(value, fallback) {
  return String(value).trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') || fallback;
}

function requireObject(value, field) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${field} must be an object`);
  }
}

function enumValue(value, field, allowed) {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    throw new TypeError(`${field} is invalid`);
  }
  return value;
}

function identifier(value, field, maxLength) {
  const result = text(value, field, maxLength);
  if (!/^[a-z][a-z0-9_.:-]*$/.test(result)) {
    throw new TypeError(`${field} must be a bounded identifier`);
  }
  return result;
}

function text(value, field, maxLength) {
  if (typeof value !== 'string') throw new TypeError(`${field} must be a string`);
  const result = value.trim().replace(/\s+/g, ' ');
  if (!result || result.length > maxLength) {
    throw new TypeError(`${field} must be non-blank and bounded`);
  }
  return result;
}

function optionalText(value, field, maxLength) {
  return value === undefined || value === null ? null : text(value, field, maxLength);
}

function integer(value, field, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${field} must be an integer from ${minimum} to ${maximum}`);
  }
  return value;
}

function unique(values, field) {
  if (new Set(values).size !== values.length) {
    throw new TypeError(`${field} must be unique`);
  }
}
