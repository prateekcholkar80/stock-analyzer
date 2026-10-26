import {
  normalizeEmbeddedBalanceSheetData,
  normalizeBalanceSheet,
} from './balance-sheet.js';
import {
  normalizeEmbeddedCashFlowData,
  normalizeCashFlow,
} from './cash-flow.js';
import {
  normalizeEmbeddedGrowthData,
  normalizeGrowthTable,
} from './growth-table.js';
import {
  normalizeEmbeddedProfitAndLossData,
  normalizeProfitAndLoss,
} from './profit-and-loss.js';
import {
  normalizeEmbeddedQuarterlyResultsData,
  normalizeQuarterlyResults,
} from './quarterly-results.js';
import {
  normalizeEmbeddedRatiosData,
  normalizeRatios,
} from './ratios.js';


const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const MARKET_PATTERN = /^[A-Z0-9&_.:-]+$/;
const REPORTING_BASES = Object.freeze(['consolidated', 'standalone']);


/** Extract every required financial document from one immutable page snapshot. */
export function createFinancialDocumentPackageExtractor({
  browserRunner,
  clock = () => new Date(),
}) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Financial package extraction requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Financial package extraction clock must be callable');
  }

  return async function extractFinancialDocumentPackage(argumentsValue) {
    const request = validateRequest(argumentsValue);
    const retrievedAt = clock();
    if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
      throw new TypeError('Financial package extraction clock returned an invalid date');
    }

    return browserRunner.run(async (page) => {
      const response = await page.goto(
        `${BASE_URL}/company/${request.issuer.provider_slug}/financials/`,
        { timeout: 20_000, waitUntil: 'domcontentloaded' },
      );
      if (!responseMatchesIssuerPath(response, request.issuer.provider_slug)) {
        throw new TypeError('Financial package response did not match the resolved issuer');
      }
      await page.waitForFunction(financialTablesReady, undefined, { timeout: 20_000 });
      const tables = await page.evaluate(readEmbeddedFinancialTables);
      return normalizeEmbeddedFinancialDocumentPackage(tables, request, retrievedAt);
    });
  };
}

/** Normalize one provider snapshot into the complete required document set. */
export function normalizeEmbeddedFinancialDocumentPackage(
  tables,
  requestValue,
  retrievedAt,
) {
  const request = validateRequest(requestValue);
  if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
    throw new TypeError('Financial package retrieval time is invalid');
  }
  if (tables === null || typeof tables !== 'object' || Array.isArray(tables)) {
    throw new TypeError('Embedded financial package must be an object');
  }

  const documents = [];
  const growthRequest = {
    issuer: request.issuer,
    reporting_basis: 'not_applicable',
  };
  documents.push(normalizeGrowthTable(
    normalizeEmbeddedGrowthData(tables.growth),
    growthRequest,
    retrievedAt,
  ));

  for (const reportingBasis of REPORTING_BASES) {
    const documentRequest = {
      issuer: request.issuer,
      reporting_basis: reportingBasis,
    };
    documents.push(normalizeBalanceSheet(
      normalizeEmbeddedBalanceSheetData(tables, reportingBasis),
      documentRequest,
      retrievedAt,
    ));
    documents.push(normalizeProfitAndLoss(
      normalizeEmbeddedProfitAndLossData(tables, reportingBasis),
      documentRequest,
      retrievedAt,
    ));
    documents.push(normalizeCashFlow(
      normalizeEmbeddedCashFlowData(tables, reportingBasis),
      documentRequest,
      retrievedAt,
    ));
    documents.push(normalizeRatios(
      normalizeEmbeddedRatiosData(tables, reportingBasis),
      documentRequest,
      retrievedAt,
    ));
    documents.push(normalizeQuarterlyResults(
      normalizeEmbeddedQuarterlyResultsData(tables, reportingBasis),
      documentRequest,
      retrievedAt,
    ));
  }

  if (documents.length !== 11) {
    throw new TypeError('Financial package is incomplete');
  }
  return Object.freeze({
    schema_version: 'tijori.financial_document_package.v1',
    issuer: request.issuer,
    source: Object.freeze({
      provider: 'tijori',
      location: `${BASE_URL}/company/${request.issuer.provider_slug}/financials/`,
      retrieved_at: retrievedAt.toISOString(),
    }),
    documents: Object.freeze(documents),
    extraction: Object.freeze({
      document_count: documents.length,
      status: 'complete',
    }),
  });
}

function validateRequest(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Financial package request must be an object');
  }
  const issuer = value.issuer;
  if (issuer === null || typeof issuer !== 'object' || Array.isArray(issuer)) {
    throw new TypeError('Financial package issuer must be an object');
  }
  const exchange = boundedText(issuer.exchange, 'issuer exchange', 32).toUpperCase();
  const symbol = boundedText(issuer.symbol, 'issuer symbol', 100).toUpperCase();
  const legalName = boundedText(issuer.legal_name, 'issuer name', 300);
  const providerSlug = boundedText(
    issuer.provider_slug,
    'issuer slug',
    240,
  ).toLowerCase();
  if (!MARKET_PATTERN.test(exchange)
      || !MARKET_PATTERN.test(symbol)
      || !SLUG_PATTERN.test(providerSlug)) {
    throw new TypeError('Financial package issuer identity is invalid');
  }
  return Object.freeze({
    issuer: Object.freeze({
      exchange,
      symbol,
      legal_name: legalName,
      provider_company_id: issuer.provider_company_id ?? null,
      provider_slug: providerSlug,
    }),
  });
}

function financialTablesReady() {
  const embedded = document.getElementById('fin_tables_data');
  const source = embedded?.textContent?.trim();
  if (!source) return false;
  try {
    const parsed = JSON.parse(source);
    return parsed !== null
      && typeof parsed === 'object'
      && !Array.isArray(parsed);
  } catch {
    return false;
  }
}

function readEmbeddedFinancialTables() {
  const embedded = document.getElementById('fin_tables_data');
  return embedded === null ? null : JSON.parse(embedded.textContent ?? '');
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

function boundedText(value, field, maximumLength) {
  if (typeof value !== 'string') {
    throw new TypeError(`Financial package ${field} must be text`);
  }
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (!normalized || normalized.length > maximumLength) {
    throw new TypeError(`Financial package ${field} must be bounded`);
  }
  return normalized;
}
