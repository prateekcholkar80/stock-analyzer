export type FinancialDocumentType =
  | "growth_table"
  | "balance_sheet"
  | "profit_and_loss"
  | "cash_flow"
  | "ratios"
  | "quarterly_results";

export type FinancialReportingBasis =
  | "consolidated"
  | "standalone"
  | "not_applicable";

export type StructuredDocumentReference = {
  schema_version: "jarvis.browser_structured_document_ref.v1";
  cache_entry_id: string;
  document_id: string;
  document_type: FinancialDocumentType;
  reporting_basis: FinancialReportingBasis;
  exchange: string;
  symbol: string;
  source: "CACHE" | "PROVIDER";
  document_fingerprint: string;
  retrieved_at: string;
  stored_at: string;
  expires_at: string;
  all_sections_expanded: true;
};

export type BenchmarkingFinancialsReference = {
  schema_version: "jarvis.browser_benchmarking_financials_ref.v1";
  cache_entry_id: string;
  document_id: string;
  document_type: "benchmarking_financials";
  reporting_basis: "not_applicable";
  exchange: string;
  symbol: string;
  source: "CACHE" | "PROVIDER";
  document_fingerprint: string;
  observation_date: string;
  retrieved_at: string;
  stored_at: string;
  expires_at: string;
  all_rows_captured: true;
  company_count: number;
  row_count: number;
};

export type BenchmarkingCompany = {
  company_key: string;
  legal_name: string;
  provider_slug: string;
  is_subject: boolean;
  display_order: number;
};

export type BenchmarkingCell = {
  company_key: string;
  source_value: string | null;
  normalized_value: string | null;
  availability_status: string;
  is_best: boolean;
};

export type BenchmarkingRow = {
  row_key: string;
  parent_row_key: string | null;
  original_label: string;
  depth: number;
  row_kind: "section" | "metric";
  section: string;
  value_kind: string;
  source_unit: string;
  normalized_unit: string;
  provider_hidden: boolean;
  display_order: number;
  cells: BenchmarkingCell[];
};

export type BenchmarkingFinancialsDocumentResponse = {
  schema_version: "jarvis.http_benchmarking_financials.v1";
  operation_id: string;
  reference: BenchmarkingFinancialsReference;
  document: {
    schema_version: "jarvis.http_benchmarking_financials_payload.v1";
    document_id: string;
    issuer: {
      exchange: string;
      symbol: string;
      legal_name: string;
      isin: string | null;
      provider_company_id: string | null;
      provider_slug: string | null;
    };
    observation_date: string;
    reporting_basis: "not_applicable";
    companies: BenchmarkingCompany[];
    rows: BenchmarkingRow[];
    retrieved_at: string;
    expires_at: string;
    all_rows_captured: true;
    validation_status: string;
    limitations: string[];
    document_fingerprint: string;
  };
};

export type FinancialDocumentPeriod = {
  period_key: string;
  source_label: string;
  period_type: string | null;
  start_date: string | null;
  end_date: string | null;
  display_order: number;
};

export type FinancialDocumentCell = {
  period_key: string;
  source_value: string | null;
  normalized_value: string | null;
  source_unit: string | null;
  normalized_unit: string | null;
  yoy_change: string | null;
  percentage_of_parent: string | null;
  availability_status: string;
};

export type FinancialDocumentRow = {
  row_key: string;
  original_label: string;
  standardized_label: string | null;
  parent_row_key: string | null;
  depth: number;
  row_kind: "section" | "total" | "subtotal" | "component" | "metric";
  value_kind: string;
  display_order: number;
  cells: FinancialDocumentCell[];
};

export type StructuredFinancialDocumentResponse = {
  schema_version: "jarvis.http_financial_document.v1";
  operation_id: string;
  reference: StructuredDocumentReference;
  document: {
    schema_version: "jarvis.http_financial_document_payload.v1";
    document_id: string;
    issuer: {
      exchange: string;
      symbol: string;
      legal_name: string;
      isin: string | null;
      provider_company_id: string | null;
      provider_slug: string | null;
    };
    document_type: FinancialDocumentType;
    reporting_basis: FinancialReportingBasis;
    currency: string | null;
    source_unit: string;
    skipped_period_labels: string[];
    periods: FinancialDocumentPeriod[];
    rows: FinancialDocumentRow[];
    retrieved_at: string;
    expires_at: string;
    all_sections_expanded: true;
    validation_status: string;
    limitations: string[];
    document_fingerprint: string;
  };
};

export class FinancialDocumentClientError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "FinancialDocumentClientError";
    this.status = status;
  }
}

function parseDocument(
  value: unknown,
  expectedOperationId: string,
  expectedCacheEntryId: string,
): StructuredFinancialDocumentResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new FinancialDocumentClientError(502, "Jarvis returned an invalid financial document.");
  }
  const response = value as StructuredFinancialDocumentResponse;
  if (
    response.schema_version !== "jarvis.http_financial_document.v1"
    || response.operation_id !== expectedOperationId
    || response.reference?.cache_entry_id !== expectedCacheEntryId
    || response.document?.schema_version !== "jarvis.http_financial_document_payload.v1"
    || response.document.document_id !== response.reference.document_id
    || response.document.document_fingerprint !== response.reference.document_fingerprint
    || !Array.isArray(response.document.periods)
    || !Array.isArray(response.document.rows)
    || response.document.all_sections_expanded !== true
  ) {
    throw new FinancialDocumentClientError(502, "Jarvis returned an invalid financial document.");
  }
  return response;
}

function parseBenchmarkingFinancials(
  value: unknown,
  expectedOperationId: string,
  expectedCacheEntryId: string,
): BenchmarkingFinancialsDocumentResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new FinancialDocumentClientError(502, "Jarvis returned an invalid benchmarking matrix.");
  }
  const response = value as BenchmarkingFinancialsDocumentResponse;
  if (
    response.schema_version !== "jarvis.http_benchmarking_financials.v1"
    || response.operation_id !== expectedOperationId
    || response.reference?.cache_entry_id !== expectedCacheEntryId
    || response.document?.schema_version !== "jarvis.http_benchmarking_financials_payload.v1"
    || response.document.document_id !== response.reference.document_id
    || response.document.document_fingerprint !== response.reference.document_fingerprint
    || response.document.observation_date !== response.reference.observation_date
    || !Array.isArray(response.document.companies)
    || !Array.isArray(response.document.rows)
    || response.document.companies.length !== response.reference.company_count
    || response.document.rows.length !== response.reference.row_count
    || response.document.all_rows_captured !== true
  ) {
    throw new FinancialDocumentClientError(502, "Jarvis returned an invalid benchmarking matrix.");
  }
  return response;
}

export async function fetchStructuredFinancialDocument(options: {
  apiBase: string;
  sessionId: string;
  operationId: string;
  cacheEntryId: string;
  accessToken: string;
  fetcher?: typeof fetch;
}): Promise<StructuredFinancialDocumentResponse> {
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try {
    response = await fetcher(
      `${options.apiBase.replace(/\/$/, "")}/api/v1/sessions/`
      + `${encodeURIComponent(options.sessionId)}/operations/`
      + `${encodeURIComponent(options.operationId)}/financial-documents/`
      + encodeURIComponent(options.cacheEntryId),
      { headers: { "X-Jarvis-Session-Token": options.accessToken } },
    );
  } catch {
    throw new FinancialDocumentClientError(0, "The financial document service could not be reached.");
  }
  if (!response.ok) {
    const message = response.status === 410
      ? "This cached financial document has expired. Refresh fundamentals to retrieve it again."
      : response.status === 401
        ? "The Jarvis browser session is no longer authorized."
        : response.status === 404
          ? "This financial document is not available to the current operation."
          : "The financial document is currently unavailable.";
    throw new FinancialDocumentClientError(response.status, message);
  }
  return parseDocument(
    await response.json(),
    options.operationId,
    options.cacheEntryId,
  );
}

export async function fetchBenchmarkingFinancials(options: {
  apiBase: string;
  sessionId: string;
  operationId: string;
  cacheEntryId: string;
  accessToken: string;
  fetcher?: typeof fetch;
}): Promise<BenchmarkingFinancialsDocumentResponse> {
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try {
    response = await fetcher(
      `${options.apiBase.replace(/\/$/, "")}/api/v1/sessions/`
      + `${encodeURIComponent(options.sessionId)}/operations/`
      + `${encodeURIComponent(options.operationId)}/benchmarking-financials/`
      + encodeURIComponent(options.cacheEntryId),
      { headers: { "X-Jarvis-Session-Token": options.accessToken } },
    );
  } catch {
    throw new FinancialDocumentClientError(0, "The benchmarking service could not be reached.");
  }
  if (!response.ok) {
    const message = response.status === 410
      ? "This cached benchmarking matrix has expired. Refresh fundamentals to retrieve it again."
      : response.status === 401
        ? "The Jarvis browser session is no longer authorized."
        : response.status === 404
          ? "This benchmarking matrix is not available to the current operation."
          : "The benchmarking matrix is currently unavailable.";
    throw new FinancialDocumentClientError(response.status, message);
  }
  return parseBenchmarkingFinancials(
    await response.json(),
    options.operationId,
    options.cacheEntryId,
  );
}
