import { failureResult, successResult } from './result-envelope.js';
import { createBalanceSheetSnapshotExtractor } from './balance-sheet.js';
import { createCashFlowSnapshotExtractor } from './cash-flow.js';
import { createGrowthTableSnapshotExtractor } from './growth-table.js';
import { createProfitAndLossSnapshotExtractor } from './profit-and-loss.js';
import { createQuarterlyResultsSnapshotExtractor } from './quarterly-results.js';
import { createRatiosSnapshotExtractor } from './ratios.js';
import { validateToolArguments } from './tool-inputs.js';


const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const MAX_ROWS = 300;
const MAX_PERIODS = 40;
const MAX_FACTS = 2_000;
const STRUCTURED_DOCUMENT_EXTRACTORS = Object.freeze({
  growth_table: createGrowthTableSnapshotExtractor,
  balance_sheet: createBalanceSheetSnapshotExtractor,
  profit_and_loss: createProfitAndLossSnapshotExtractor,
  cash_flow: createCashFlowSnapshotExtractor,
  ratios: createRatiosSnapshotExtractor,
  quarterly_results: createQuarterlyResultsSnapshotExtractor,
});

const SUPPORTED_STATEMENTS = new Set([
  'income_statement',
  'balance_sheet',
  'cash_flow',
]);
const SUPPORTED_PERIOD_TYPES = new Set(['annual', 'quarterly']);

const LINE_ITEMS = Object.freeze({
  income_statement: Object.freeze([
    mapping(['revenue', 'sales', 'total revenue'], 'revenue', 'Revenue', 'monetary'),
    mapping(['operating profit', 'ebitda'], 'operating_profit', 'Operating profit', 'monetary'),
    mapping(['depreciation', 'depreciation and amortisation'], 'depreciation', 'Depreciation and amortisation', 'monetary'),
    mapping(['interest', 'finance cost', 'finance costs'], 'interest_expense', 'Interest expense', 'monetary'),
    mapping(['profit before tax', 'pbt'], 'profit_before_tax', 'Profit before tax', 'monetary'),
    mapping(['tax', 'tax expense'], 'tax_expense', 'Tax expense', 'monetary'),
    mapping(['net profit', 'profit after tax', 'pat'], 'net_income', 'Net income', 'monetary'),
    mapping(['eps', 'earnings per share'], 'earnings_per_share', 'Earnings per share', 'per_share'),
  ]),
  balance_sheet: Object.freeze([
    mapping(['equity share capital', 'share capital'], 'share_capital', 'Share capital', 'monetary'),
    mapping(['reserves', 'reserves and surplus'], 'reserves', 'Reserves and surplus', 'monetary'),
    mapping(['borrowings', 'total borrowings', 'debt'], 'borrowings', 'Borrowings', 'monetary'),
    mapping(['total liabilities'], 'total_liabilities', 'Total liabilities', 'monetary'),
    mapping(['fixed assets', 'net fixed assets'], 'fixed_assets', 'Fixed assets', 'monetary'),
    mapping(['investments'], 'investments', 'Investments', 'monetary'),
    mapping(['inventory', 'inventories'], 'inventory', 'Inventory', 'monetary'),
    mapping(['trade receivables', 'receivables', 'debtors'], 'trade_receivables', 'Trade receivables', 'monetary'),
    mapping(['cash and cash equivalents', 'cash equivalents', 'cash'], 'cash_and_equivalents', 'Cash and cash equivalents', 'monetary'),
    mapping(['total assets'], 'total_assets', 'Total assets', 'monetary'),
  ]),
  cash_flow: Object.freeze([
    mapping(['cash from operating activity', 'cash flow from operating activities', 'cfo'], 'cash_from_operations', 'Cash from operating activities', 'monetary'),
    mapping(['cash from investing activity', 'cash flow from investing activities', 'cfi'], 'cash_from_investing', 'Cash from investing activities', 'monetary'),
    mapping(['cash from financing activity', 'cash flow from financing activities', 'cff'], 'cash_from_financing', 'Cash from financing activities', 'monetary'),
    mapping(['net cash flow', 'net change in cash'], 'net_change_in_cash', 'Net change in cash', 'monetary'),
  ]),
});

export function createFinancialsHandler({ browserRunner, clock = () => new Date() }) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Financial retrieval requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Financial retrieval clock must be callable');
  }

  return async function getFinancials(argumentsValue) {
    let request;
    let observedAt;
    let observedDate;
    try {
      request = validateToolArguments('get_financials', argumentsValue);
      observedAt = clock();
      observedDate = istDate(observedAt);
    } catch {
      return failureResult('get_financials', 'unavailable');
    }
    if (request.as_of_date !== null && request.as_of_date !== observedDate) {
      return failureResult('get_financials', 'unavailable');
    }
    if (Object.hasOwn(STRUCTURED_DOCUMENT_EXTRACTORS, request.document_type)) {
      try {
        const extractorFactory = STRUCTURED_DOCUMENT_EXTRACTORS[request.document_type];
        const extractDocument = extractorFactory({
          browserRunner,
          clock: () => observedAt,
        });
        const document = await extractDocument({
          issuer: request.issuer,
          reporting_basis: request.reporting_basis,
        });
        return successResult('get_financials', { document });
      } catch {
        return failureResult('get_financials', 'unavailable');
      }
    }
    const requestedStatements = request.statements.filter((value) => (
      SUPPORTED_STATEMENTS.has(value)
    ));
    const requestedPeriods = request.period_types.filter((value) => (
      SUPPORTED_PERIOD_TYPES.has(value)
    ));
    if (requestedStatements.length === 0 || requestedPeriods.length === 0) {
      return failureResult('get_financials', 'unavailable');
    }
    const slug = request.issuer.provider_slug?.trim().toLowerCase();
    if (slug === undefined || !SLUG_PATTERN.test(slug)) {
      return failureResult('get_financials', 'unavailable');
    }

    try {
      const outcome = await browserRunner.run(async (page) => {
        const response = await page.goto(`${BASE_URL}/company/${slug}/financials/`, {
          timeout: 15_000,
          waitUntil: 'domcontentloaded',
        });
        const status = responseStatus(response);
        if (status !== null) return { status };
        await page.waitForSelector([
          '#profit_and_loss_table',
          '#balance_sheet_table',
          '#cash_flow_table',
          '#quarterly_results_table',
          'table.dataTable',
        ].join(', '), { timeout: 10_000 });
        return { status: 'success', value: await extractFinancials(page) };
      });
      if (outcome.status !== 'success') {
        return failureResult('get_financials', outcome.status);
      }
      const payload = normalizeFinancials(outcome.value, request, {
        observedDate,
        requestedPeriods,
        requestedStatements,
        slug,
      });
      return payload === null
        ? failureResult('get_financials', 'unavailable')
        : successResult('get_financials', payload);
    } catch {
      return failureResult('get_financials', 'unavailable');
    }
  };
}

async function extractFinancials(page) {
  return page.evaluate(() => {
    let metadata = null;
    for (const script of Array.from(document.querySelectorAll('script:not([src])')).slice(0, 100)) {
      const text = script.textContent?.trim() ?? '';
      if (!text.startsWith('{') || !text.includes('company_id')) continue;
      try {
        const parsed = JSON.parse(text);
        metadata = {
          company_id: parsed.company_id,
          exchange: parsed.exchange ?? parsed.exchange_code ?? parsed.primary_exchange ?? parsed.stock_exchange,
          symbol: parsed.symbol,
        };
        break;
      } catch {}
    }
    const configs = [
      ['income_statement', 'annual', 'profit_and_loss'],
      ['balance_sheet', 'annual', 'balance_sheet'],
      ['cash_flow', 'annual', 'cash_flow'],
      ['income_statement', 'quarterly', 'quarterly_results'],
    ];
    const sections = configs.map(([statement, periodType, sectionId]) => {
      const wrapper = document.getElementById(`${sectionId}_table_wrapper`)
        ?? document.getElementById(`company_table_innertab_${sectionId}_content`)
          ?.querySelector('.dt-container')
        ?? document.getElementById(`${sectionId}_table`);
      if (!wrapper) return null;
      const container = wrapper.closest('[id$="_content"]') ?? wrapper.parentElement;
      const unit = container?.querySelector('.unit,.units,[class*="unit"]')
        ?.textContent?.trim()
        ?? wrapper.querySelector('thead th.unit_val')?.textContent?.trim()
        ?? null;
      const headers = Array.from(wrapper.querySelectorAll('thead th.headerItem'))
        .slice(0, 41)
        .map((element) => element.textContent?.trim() ?? '');
      const rows = Array.from(wrapper.querySelectorAll('tbody tr'))
        .slice(0, 301)
        .map((row) => ({
          metric: row.getAttribute('data-id')
            ?? row.querySelector('td.firstcol')?.textContent?.trim()
            ?? null,
          values: Array.from(row.querySelectorAll('td.knowledge.numericvalue'))
            .slice(0, 41)
            .map((cell) => {
              const text = cell.textContent?.trim().replace(/\s+/g, ' ') ?? '';
              return text === '' || text === '-' || text === '—' ? null : text;
            }),
        }));
      return { statement, period_type: periodType, unit, headers, rows };
    }).filter(Boolean);
    return { metadata, sections };
  });
}

function normalizeFinancials(value, request, options) {
  const identity = resolvedIdentity(value?.metadata, request.issuer);
  if (
    value === null
    || typeof value !== 'object'
    || identity === null
    || !Array.isArray(value.sections)
    || value.sections.length > 4
  ) return null;

  const companyId = identity.companyId;
  const facts = [];
  let omittedAmbiguousLineItems = 0;
  for (const section of value.sections) {
    if (
      section === null
      || typeof section !== 'object'
      || !options.requestedStatements.includes(section.statement)
      || !options.requestedPeriods.includes(section.period_type)
    ) continue;
    const sectionResult = normalizeSection(section, request.max_periods);
    if (sectionResult === null) return null;
    facts.push(...sectionResult.facts);
    omittedAmbiguousLineItems += sectionResult.omittedAmbiguousLineItems;
    if (facts.length > MAX_FACTS) return null;
  }
  if (facts.length === 0) return null;
  const sourceId = 'tijori-financials';
  for (const fact of facts) {
    if (fact.availability_status === 'available') fact.provider_source_id = sourceId;
  }
  const limitations = [
    'Provider-standardized secondary evidence; reconcile material values against primary company or exchange disclosures.',
    'The provider page does not establish point-in-time availability or restatement history for historical backtesting.',
  ];
  const unsupportedStatements = request.statements.filter((item) => !SUPPORTED_STATEMENTS.has(item));
  const unsupportedPeriods = request.period_types.filter((item) => !SUPPORTED_PERIOD_TYPES.has(item));
  if (unsupportedStatements.length > 0) {
    limitations.push(`Unsupported requested statements were omitted: ${unsupportedStatements.join(', ')}.`);
  }
  if (unsupportedPeriods.length > 0) {
    limitations.push(`Unsupported requested period types were omitted: ${unsupportedPeriods.join(', ')}.`);
  }
  if (!identity.providerExchangeObserved) {
    limitations.push(
      'The provider page omitted its exchange field; Jarvis retained the previously resolved issuer exchange after company-ID and symbol verification.',
    );
  }
  if (identity.providerCompanyIdVaries) {
    limitations.push(
      'The financial page exposed a different provider-internal company ID; Jarvis retained the previously resolved issuer ID after slug and symbol verification.',
    );
  }
  if (omittedAmbiguousLineItems > 0) {
    limitations.push(
      `${omittedAmbiguousLineItems} ambiguous duplicated financial line item(s) were omitted instead of selecting a provider row arbitrarily.`,
    );
  }
  return {
    company_id: companyId,
    exchange: identity.exchange,
    symbol: identity.symbol,
    sources: [{
      provider_source_id: sourceId,
      source_name: 'Tijori standardized financial tables',
      location: `${BASE_URL}/company/${options.slug}/financials/`,
      period_covered: `Financial tables observed ${options.observedDate}`,
      as_of_date: options.observedDate,
      published_at: null,
    }],
    facts,
    limitations,
  };
}

function normalizeSection(section, maxPeriods) {
  if (
    !SUPPORTED_STATEMENTS.has(section.statement)
    || !SUPPORTED_PERIOD_TYPES.has(section.period_type)
    || !Array.isArray(section.headers)
    || section.headers.length === 0
    || section.headers.length > MAX_PERIODS + 1
    || !Array.isArray(section.rows)
    || section.rows.length > MAX_ROWS
  ) return null;
  const unit = monetaryUnit(section.unit);
  if (unit === null) return null;
  const periods = section.headers
    .map((label, index) => ({ ...parsePeriod(label, section.period_type), index }))
    .filter(({ end }) => end !== null)
    .sort((left, right) => right.end.localeCompare(left.end))
    .slice(0, maxPeriods);
  if (periods.length === 0) return null;

  const facts = [];
  const seen = new Set();
  const mappedRows = section.rows.map((row) => {
    if (row === null || typeof row !== 'object' || !Array.isArray(row.values)) {
      return { item: undefined, row };
    }
    const original = boundedString(row.metric, 300, true);
    const item = original === null
      ? undefined
      : LINE_ITEMS[section.statement].find(({ labels }) => (
        labels.includes(comparable(original))
      ));
    return { item, original, row };
  });
  const itemCounts = new Map();
  for (const { item } of mappedRows) {
    if (item !== undefined) itemCounts.set(item.id, (itemCounts.get(item.id) ?? 0) + 1);
  }
  const ambiguousItems = new Set(
    [...itemCounts.entries()]
      .filter(([, count]) => count > 1)
      .map(([itemId]) => itemId),
  );
  for (const { item, original, row } of mappedRows) {
    if (item === undefined || original === undefined || ambiguousItems.has(item.id)) continue;
    for (const period of periods) {
      const factId = `${section.statement}.${item.id}.${section.period_type}.${period.end}`;
      if (seen.has(factId)) return null;
      seen.add(factId);
      const sourceValue = boundedString(row.values[period.index], 500, true);
      const numeric = sourceValue === null ? null : parseNumber(sourceValue);
      if (sourceValue !== null && numeric === null) return null;
      const perShare = item.kind === 'per_share';
      facts.push({
        provider_fact_id: factId,
        provider_source_id: null,
        statement: section.statement,
        line_item_original: original,
        line_item_standard: item.standard,
        line_item_id: `${section.statement}.${item.id}`,
        period_label: period.label,
        period_type: section.period_type,
        period_start: period.start,
        period_end: period.end,
        value_kind: item.kind,
        source_value: sourceValue,
        normalized_value: numeric,
        currency: sourceValue === null ? null : 'INR',
        source_unit: sourceValue === null ? null : (perShare ? 'INR per share' : unit),
        normalized_unit: sourceValue === null ? null : (perShare ? 'INR per share' : unit),
        availability_status: sourceValue === null ? 'unknown' : 'available',
      });
    }
  }
  return {
    facts,
    omittedAmbiguousLineItems: ambiguousItems.size,
  };
}

function parsePeriod(value, periodType) {
  const label = boundedString(value, 80, true);
  if (label === null) return { label: '', start: null, end: null };
  const monthMatch = label.match(/^(Mar|Jun|Sep|Dec)[ '\-]?(\d{2}|\d{4})$/i);
  if (monthMatch) {
    const year = normalizeYear(monthMatch[2]);
    const month = { mar: 3, jun: 6, sep: 9, dec: 12 }[monthMatch[1].toLowerCase()];
    if (periodType === 'annual' && month !== 3) return { label, start: null, end: null };
    const end = lastDay(year, month);
    const start = periodType === 'annual'
      ? `${year - 1}-04-01`
      : isoDate(year, month - 2, 1);
    return { label, start, end };
  }
  if (periodType === 'annual') {
    const annual = label.match(/^(?:FY\s*)?(?:(\d{4})[-/])?(\d{2}|\d{4})$/i);
    if (annual) {
      const year = normalizeYear(annual[2]);
      return { label, start: `${year - 1}-04-01`, end: `${year}-03-31` };
    }
  }
  const quarter = label.match(/^Q([1-4])\s*FY\s*(\d{2}|\d{4})$/i);
  if (periodType === 'quarterly' && quarter) {
    const fiscalYear = normalizeYear(quarter[2]);
    const number = Number(quarter[1]);
    const endYear = number === 4 ? fiscalYear : fiscalYear - 1;
    const endMonth = [0, 6, 9, 12, 3][number];
    return {
      label,
      start: isoDate(endYear, endMonth - 2, 1),
      end: lastDay(endYear, endMonth),
    };
  }
  return { label, start: null, end: null };
}

function resolvedIdentity(value, issuer) {
  if (value === null || typeof value !== 'object') return null;
  const providerCompanyId = boundedString(value.company_id, 128);
  const companyId = issuer.provider_company_id ?? providerCompanyId;
  const providerExchange = boundedString(value.exchange, 32)?.toUpperCase();
  const exchange = providerExchange ?? issuer.exchange;
  const symbol = boundedString(value.symbol, 100)?.toUpperCase();
  const valid = providerCompanyId !== null
    && ID_PATTERN.test(providerCompanyId)
    && companyId !== null
    && ID_PATTERN.test(companyId)
    && MARKET_PATTERN.test(exchange)
    && symbol !== null
    && MARKET_PATTERN.test(symbol)
    && exchange === issuer.exchange
    && symbol === issuer.symbol;
  return valid
    ? {
      companyId,
      exchange,
      providerCompanyIdVaries: issuer.provider_company_id !== null
        && providerCompanyId !== issuer.provider_company_id,
      providerExchangeObserved: providerExchange !== undefined,
      symbol,
    }
    : null;
}

function monetaryUnit(value) {
  const unit = boundedString(value, 80, true)?.toLowerCase() ?? '';
  return /(?:₹|rs\.?|inr).*\b(?:cr\.?|crore)\b|\b(?:cr\.?|crore)\b.*(?:₹|rs\.?|inr)/i.test(unit)
    ? 'INR crore'
    : null;
}

function parseNumber(value) {
  const compact = value.replace(/[₹,%\s]/g, '');
  const negative = /^\(.*\)$/.test(compact);
  const numeric = negative ? `-${compact.slice(1, -1)}` : compact;
  if (!/^-?(?:\d+\.?\d*|\.\d+)$/.test(numeric)) return null;
  return Number.isFinite(Number(numeric)) ? numeric : null;
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

function mapping(labels, id, standard, kind) {
  return Object.freeze({ labels, id, standard, kind });
}

function normalizeYear(value) {
  const number = Number(value);
  return value.length === 2 ? 2000 + number : number;
}

function lastDay(year, month) {
  return isoDate(year, month, new Date(Date.UTC(year, month, 0)).getUTCDate());
}

function isoDate(year, month, day) {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
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
