import assert from 'node:assert/strict';
import test from 'node:test';

import { validateToolArguments } from '../src/tool-inputs.js';


const issuer = {
  exchange: 'nse',
  symbol: 'tcs',
  legal_name: 'Tata Consultancy Services Limited',
  isin: 'ine467b01029',
  provider_company_id: '123',
  provider_slug: 'tata-consultancy-services',
};

test('normalizes and defaults company-search arguments', () => {
  const result = validateToolArguments('search_company', {
    query: '  Tata   Consultancy  ',
    exchanges: ['nse', 'BSE'],
  });

  assert.deepEqual(result, {
    query: 'Tata Consultancy',
    exchanges: ['NSE', 'BSE'],
    max_results: 10,
  });
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.exchanges), true);
});

test('validates issuer resolution hints and defaults', () => {
  assert.deepEqual(validateToolArguments('resolve_company_ids', {
    locator: { exchange: 'nse', symbol: 'tcs' },
  }), {
    locator: {
      exchange: 'NSE',
      symbol: 'TCS',
      legal_name: null,
      isin: null,
      provider_company_id: null,
      provider_slug: null,
    },
    max_candidates: 10,
  });

  assert.throws(
    () => validateToolArguments('resolve_company_ids', { locator: {} }),
    /identity hint/,
  );
  assert.throws(
    () => validateToolArguments('resolve_company_ids', {
      locator: { exchange: 'NSE', legal_name: 'TCS Limited' },
    }),
    /requires a symbol/,
  );
});

test('validates and normalizes company-overview arguments', () => {
  const result = validateToolArguments('get_company_overview', {
    issuer,
    as_of_date: '2026-08-28',
  });

  assert.equal(result.issuer.exchange, 'NSE');
  assert.equal(result.issuer.symbol, 'TCS');
  assert.equal(result.issuer.isin, 'INE467B01029');
  assert.equal(result.as_of_date, '2026-08-28');
  assert.equal(Object.isFrozen(result.issuer), true);
});

test('accepts structured company-overview document requests', () => {
  assert.deepEqual(
    validateToolArguments('get_company_overview', {
      issuer,
      document_type: 'peer_comparison',
    }),
    {
      issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
      as_of_date: null,
      document_type: 'peer_comparison',
    },
  );
  assert.deepEqual(
    validateToolArguments('get_company_overview', {
      issuer,
      document_type: 'benchmarking_financials',
    }),
    {
      issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
      as_of_date: null,
      document_type: 'benchmarking_financials',
    },
  );
  assert.throws(
    () => validateToolArguments('get_company_overview', {
      issuer,
      document_type: 'benchmarking',
    }),
    /document_type contains an unsupported value/,
  );
});

test('applies safe financial defaults and accepts supported selections', () => {
  assert.deepEqual(validateToolArguments('get_financials', { issuer }), {
    issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
    as_of_date: null,
    statements: ['income_statement', 'balance_sheet', 'cash_flow'],
    period_types: ['annual', 'quarterly'],
    max_periods: 12,
  });

  const selected = validateToolArguments('get_financials', {
    issuer,
    statements: ['segment', 'kpi_schedule'],
    period_types: ['ltm'],
    max_periods: 40,
  });
  assert.deepEqual(selected.statements, ['segment', 'kpi_schedule']);
  assert.deepEqual(selected.period_types, ['ltm']);
});

test('accepts explicit Growth Table document scenarios independently', () => {
  assert.deepEqual(validateToolArguments('get_financials', {
    issuer,
    document_type: 'growth_table',
    reporting_basis: 'not_applicable',
  }), {
    issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
    as_of_date: null,
    document_type: 'growth_table',
    reporting_basis: 'not_applicable',
  });
});

test('accepts explicit Balance Sheet scenarios for both reporting bases', () => {
  for (const reportingBasis of ['consolidated', 'standalone']) {
    assert.deepEqual(validateToolArguments('get_financials', {
      issuer,
      as_of_date: '2026-08-30',
      document_type: 'balance_sheet',
      reporting_basis: reportingBasis,
    }), {
      issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
      as_of_date: '2026-08-30',
      document_type: 'balance_sheet',
      reporting_basis: reportingBasis,
    });
  }
});

test('accepts explicit Profit and Loss scenarios for both reporting bases', () => {
  for (const reportingBasis of ['consolidated', 'standalone']) {
    assert.deepEqual(validateToolArguments('get_financials', {
      issuer,
      as_of_date: '2026-08-30',
      document_type: 'profit_and_loss',
      reporting_basis: reportingBasis,
    }), {
      issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
      as_of_date: '2026-08-30',
      document_type: 'profit_and_loss',
      reporting_basis: reportingBasis,
    });
  }
});

test('accepts explicit Cash Flow scenarios for both reporting bases', () => {
  for (const reportingBasis of ['consolidated', 'standalone']) {
    assert.deepEqual(validateToolArguments('get_financials', {
      issuer,
      as_of_date: '2026-08-30',
      document_type: 'cash_flow',
      reporting_basis: reportingBasis,
    }), {
      issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
      as_of_date: '2026-08-30',
      document_type: 'cash_flow',
      reporting_basis: reportingBasis,
    });
  }
});

test('accepts explicit Ratios scenarios for both reporting bases', () => {
  for (const reportingBasis of ['consolidated', 'standalone']) {
    assert.deepEqual(validateToolArguments('get_financials', {
      issuer,
      as_of_date: '2026-08-30',
      document_type: 'ratios',
      reporting_basis: reportingBasis,
    }), {
      issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
      as_of_date: '2026-08-30',
      document_type: 'ratios',
      reporting_basis: reportingBasis,
    });
  }
});

test('accepts explicit Quarterly Results scenarios for both reporting bases', () => {
  for (const reportingBasis of ['consolidated', 'standalone']) {
    assert.deepEqual(validateToolArguments('get_financials', {
      issuer,
      as_of_date: '2026-08-30',
      document_type: 'quarterly_results',
      reporting_basis: reportingBasis,
    }), {
      issuer: { ...issuer, exchange: 'NSE', symbol: 'TCS', isin: 'INE467B01029' },
      as_of_date: '2026-08-30',
      document_type: 'quarterly_results',
      reporting_basis: reportingBasis,
    });
  }
});

test('rejects incomplete, unsupported, or mixed financial document dimensions', () => {
  assert.throws(
    () => validateToolArguments('get_financials', {
      issuer, document_type: 'growth_table',
    }),
    /require document_type and reporting_basis/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', {
      issuer, reporting_basis: 'consolidated',
    }),
    /require document_type and reporting_basis/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', {
      issuer, document_type: 'reverse_dcf', reporting_basis: 'consolidated',
    }),
    /document_type contains an unsupported value/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', {
      issuer, document_type: 'growth_table', reporting_basis: 'consolidated',
    }),
    /requires the not_applicable reporting basis exclusively/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', {
      issuer, document_type: 'balance_sheet', reporting_basis: 'not_applicable',
    }),
    /requires the not_applicable reporting basis exclusively/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', {
      issuer,
      document_type: 'growth_table',
      reporting_basis: 'consolidated',
      max_periods: 12,
    }),
    /cannot mix legacy statement selections/,
  );
});

test('applies shareholding defaults and validates explicit bounds', () => {
  const defaults = validateToolArguments('get_shareholding', { issuer });
  assert.equal(defaults.quarters, 8);
  assert.equal(defaults.include_promoter_pledge, true);

  const explicit = validateToolArguments('get_shareholding', {
    issuer,
    quarters: 40,
    include_promoter_pledge: false,
  });
  assert.equal(explicit.quarters, 40);
  assert.equal(explicit.include_promoter_pledge, false);
});

test('rejects unknown tools, fields, and missing required fields', () => {
  assert.throws(() => validateToolArguments('download_report', {}), /unapproved/);
  assert.throws(
    () => validateToolArguments('search_company', { query: 'TCS', cookie: 'secret' }),
    /unsupported field/,
  );
  assert.throws(
    () => validateToolArguments('search_company', {}),
    /missing a required field/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', { issuer: [], statements: [] }),
    /plain object/,
  );
});

test('rejects blank, oversized, invalid, duplicate, and out-of-range values', () => {
  for (const query of ['', ' ', 'x'.repeat(201)]) {
    assert.throws(
      () => validateToolArguments('search_company', { query }),
      /non-blank and bounded/,
    );
  }
  assert.throws(
    () => validateToolArguments('search_company', {
      query: 'TCS',
      exchanges: ['NSE', 'nse'],
    }),
    /unique/,
  );
  assert.throws(
    () => validateToolArguments('search_company', { query: 'TCS', max_results: 26 }),
    /integer from 1 to 25/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', { issuer, statements: [] }),
    /at least one/,
  );
  assert.throws(
    () => validateToolArguments('get_financials', {
      issuer,
      period_types: ['forecast'],
    }),
    /unsupported value/,
  );
  assert.throws(
    () => validateToolArguments('get_shareholding', { issuer, quarters: 0 }),
    /integer from 1 to 40/,
  );
});

test('rejects invalid identities, dates, and scalar types', () => {
  assert.throws(
    () => validateToolArguments('get_company_overview', {
      issuer: { ...issuer, isin: 'INVALID' },
    }),
    /ISIN format/,
  );
  for (const asOfDate of ['2026-02-29', '28-08-2026', 20260828]) {
    assert.throws(
      () => validateToolArguments('get_company_overview', {
        issuer,
        as_of_date: asOfDate,
      }),
      /calendar date/,
    );
  }
  assert.throws(
    () => validateToolArguments('get_shareholding', {
      issuer,
      include_promoter_pledge: 'yes',
    }),
    /must be a boolean/,
  );
});

test('does not mutate caller-owned arguments', () => {
  const candidate = { query: 'TCS', exchanges: ['nse'] };
  const result = validateToolArguments('search_company', candidate);

  candidate.exchanges[0] = 'bse';
  assert.deepEqual(result.exchanges, ['NSE']);
});
