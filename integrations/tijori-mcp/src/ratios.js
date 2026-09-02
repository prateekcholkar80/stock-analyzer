import {
  createFinancialStatementSnapshotExtractor,
  normalizeEmbeddedFinancialStatementData,
  normalizeFinancialStatement,
} from './financial-statement-document.js';


const RATIOS = Object.freeze({
  documentType: 'ratios',
  displayName: 'Ratios',
  basisKeys: Object.freeze({
    consolidated: 'fr_c',
    standalone: 'fr_s',
  }),
  sourceUnit: 'mixed',
  normalizedUnit: 'mixed',
  classifyRow: classifyRatioRow,
});


export function createRatiosSnapshotExtractor(options) {
  return createFinancialStatementSnapshotExtractor({
    ...options,
    definition: RATIOS,
  });
}

export function normalizeEmbeddedRatiosData(value, reportingBasis) {
  return normalizeEmbeddedFinancialStatementData(
    value,
    reportingBasis,
    RATIOS,
  );
}

export function normalizeRatios(raw, requestValue, retrievedAt) {
  return normalizeFinancialStatement(
    raw,
    requestValue,
    retrievedAt,
    RATIOS,
  );
}

function classifyRatioRow(row) {
  const label = String(row.name ?? '').trim().toLowerCase();
  if (label.endsWith('ratios')) {
    return units('other', 'not applicable', 'not applicable');
  }
  if (label.includes('(crs)')) {
    return units('monetary', 'Rs. Cr.', 'INR crore');
  }
  if (label.includes('%')) {
    return units('percentage', 'percent', 'percent');
  }
  if (
    label.includes('days')
    || label === 'cash conversion cycle'
  ) {
    return units('other', 'days', 'days');
  }
  if (label.includes('eps')) {
    return units('per_share', 'per share', 'per share');
  }
  return units('ratio', 'ratio', 'ratio');
}

function units(valueKind, sourceUnit, normalizedUnit) {
  return {
    value_kind: valueKind,
    source_unit: sourceUnit,
    normalized_unit: normalizedUnit,
  };
}
