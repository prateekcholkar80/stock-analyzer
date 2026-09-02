import {
  createFinancialStatementSnapshotExtractor,
  normalizeEmbeddedFinancialStatementData,
  normalizeFinancialStatement,
} from './financial-statement-document.js';


const BALANCE_SHEET = Object.freeze({
  documentType: 'balance_sheet',
  displayName: 'Balance Sheet',
  basisKeys: Object.freeze({
    consolidated: 'bs_c_s',
    standalone: 'bs_s_s',
  }),
  sourceUnit: 'Rs. Cr.',
  normalizedUnit: 'INR crore',
});


export function createBalanceSheetSnapshotExtractor(options) {
  return createFinancialStatementSnapshotExtractor({
    ...options,
    definition: BALANCE_SHEET,
  });
}

export function normalizeEmbeddedBalanceSheetData(value, reportingBasis) {
  return normalizeEmbeddedFinancialStatementData(
    value,
    reportingBasis,
    BALANCE_SHEET,
  );
}

export function normalizeBalanceSheet(raw, requestValue, retrievedAt) {
  return normalizeFinancialStatement(
    raw,
    requestValue,
    retrievedAt,
    BALANCE_SHEET,
  );
}
