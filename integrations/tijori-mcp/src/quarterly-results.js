import {
  createFinancialStatementSnapshotExtractor,
  normalizeEmbeddedFinancialStatementData,
  normalizeFinancialStatement,
} from './financial-statement-document.js';


const QUARTERLY_RESULTS = Object.freeze({
  documentType: 'quarterly_results',
  displayName: 'Quarterly Results',
  basisKeys: Object.freeze({
    consolidated: 'qt_c',
    standalone: 'qt_s',
  }),
  sourceUnit: 'mixed',
  normalizedUnit: 'mixed',
  providerDepthMode: 'nested',
  auxiliaryAlignment: 'trailing',
  classifyRow: classifyQuarterlyResultsRow,
});


export function createQuarterlyResultsSnapshotExtractor(options) {
  return createFinancialStatementSnapshotExtractor({
    ...options,
    definition: QUARTERLY_RESULTS,
  });
}

export function normalizeEmbeddedQuarterlyResultsData(value, reportingBasis) {
  return normalizeEmbeddedFinancialStatementData(
    value,
    reportingBasis,
    QUARTERLY_RESULTS,
  );
}

export function normalizeQuarterlyResults(raw, requestValue, retrievedAt) {
  return normalizeFinancialStatement(
    raw,
    requestValue,
    retrievedAt,
    QUARTERLY_RESULTS,
  );
}

function classifyQuarterlyResultsRow(row) {
  const field = String(row.field ?? '').trim().toLowerCase();
  const label = String(row.name ?? '').trim().toLowerCase();
  if (label === 'quarterly ratios') {
    return units('other', 'not applicable', 'not applicable');
  }
  if (label === 'eps' || label.includes('earnings per share')) {
    return units('per_share', 'per share', 'per share');
  }
  if (label.includes('%') || label.includes('margin') || field.includes('margin')) {
    return units('percentage', 'percent', 'percent');
  }
  return units('monetary', 'Rs. Cr.', 'INR crore');
}

function units(valueKind, sourceUnit, normalizedUnit) {
  return {
    value_kind: valueKind,
    source_unit: sourceUnit,
    normalized_unit: normalizedUnit,
  };
}
