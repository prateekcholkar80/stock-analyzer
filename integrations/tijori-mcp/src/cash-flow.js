import {
  createFinancialStatementSnapshotExtractor,
  normalizeEmbeddedFinancialStatementData,
  normalizeFinancialStatement,
} from './financial-statement-document.js';


const CASH_FLOW = Object.freeze({
  documentType: 'cash_flow',
  displayName: 'Cash Flow',
  basisKeys: Object.freeze({
    consolidated: 'cf_c',
    standalone: 'cf_s',
  }),
  sourceUnit: 'Rs. Cr.',
  normalizedUnit: 'INR crore',
});


export function createCashFlowSnapshotExtractor(options) {
  return createFinancialStatementSnapshotExtractor({
    ...options,
    definition: CASH_FLOW,
  });
}

export function normalizeEmbeddedCashFlowData(value, reportingBasis) {
  return normalizeEmbeddedFinancialStatementData(
    value,
    reportingBasis,
    CASH_FLOW,
  );
}

export function normalizeCashFlow(raw, requestValue, retrievedAt) {
  return normalizeFinancialStatement(
    raw,
    requestValue,
    retrievedAt,
    CASH_FLOW,
  );
}
