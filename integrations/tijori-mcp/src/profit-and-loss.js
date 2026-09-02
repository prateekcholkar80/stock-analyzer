import {
  createFinancialStatementSnapshotExtractor,
  normalizeEmbeddedFinancialStatementData,
  normalizeFinancialStatement,
} from './financial-statement-document.js';


const PROFIT_AND_LOSS = Object.freeze({
  documentType: 'profit_and_loss',
  displayName: 'Profit and Loss',
  basisKeys: Object.freeze({
    consolidated: 'pl_c_s',
    standalone: 'pl_s_s',
  }),
  sourceUnit: 'mixed',
  normalizedUnit: 'mixed',
  classifyRow: classifyProfitAndLossRow,
});


export function createProfitAndLossSnapshotExtractor(options) {
  return createFinancialStatementSnapshotExtractor({
    ...options,
    definition: PROFIT_AND_LOSS,
  });
}

export function normalizeEmbeddedProfitAndLossData(value, reportingBasis) {
  return normalizeEmbeddedFinancialStatementData(
    value,
    reportingBasis,
    PROFIT_AND_LOSS,
  );
}

export function normalizeProfitAndLoss(raw, requestValue, retrievedAt) {
  return normalizeFinancialStatement(
    raw,
    requestValue,
    retrievedAt,
    PROFIT_AND_LOSS,
  );
}

function classifyProfitAndLossRow(row) {
  const field = String(row.field ?? '').trim().toLowerCase();
  const formula = String(row.formula ?? '').trim().toLowerCase();
  const label = String(row.name ?? '').trim().toLowerCase();
  if (
    field === 'no_of_shares'
    || formula.includes('nsgrandtotal')
    || label.includes('number of shares')
  ) {
    return {
      value_kind: 'count',
      source_unit: 'crore shares',
      normalized_unit: 'crore shares',
    };
  }
  if (
    label.includes('%')
    || formula.includes('* 100')
    || formula.includes('*100')
    || formula.endsWith('_per')
  ) {
    return {
      value_kind: 'percentage',
      source_unit: 'percent',
      normalized_unit: 'percent',
    };
  }
  return {
    value_kind: 'monetary',
    source_unit: 'Rs. Cr.',
    normalized_unit: 'INR crore',
  };
}
