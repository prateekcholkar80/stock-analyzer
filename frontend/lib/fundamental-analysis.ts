export type FundamentalAnalysis = {
  schema_version: "jarvis.fundamental_analysis.v1";
  analysis_id: string;
  evidence_coverage_and_freshness: string;
  financial_strengths: string[];
  financial_risks_and_conflicts: string[];
  preliminary_financial_health: string;
  confidence_percentage: number;
  valuation_observations: string[];
  missing_evidence_and_limitations: string[];
  document_citations: string[];
  runtime_skill_id: string;
  runtime_skill_version: string;
  runtime_skill_prompt_sha256: string;
  model_id: string;
  generated_at: string;
};

export type FundamentalPackageReference = {
  schema_version: "jarvis.browser_fundamental_package_ref.v1";
  package_id: string;
  manifest_fingerprint: string;
  expires_at: string;
};

export type FundamentalConfidenceTone = "positive" | "caution" | "limited";

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const PACKAGE_ID_PATTERN = /^fundamental_package:([a-f0-9]{64})$/;

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(
    (item) => typeof item === "string" && item.trim().length > 0,
  );
}

/** Validate the untrusted HTTP projection without recalculating its verdict. */
export function parseFundamentalAnalysis(value: unknown): FundamentalAnalysis | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Record<string, unknown>;
  if (
    candidate.schema_version !== "jarvis.fundamental_analysis.v1"
    || typeof candidate.analysis_id !== "string"
    || candidate.analysis_id.length === 0
    || typeof candidate.evidence_coverage_and_freshness !== "string"
    || candidate.evidence_coverage_and_freshness.length === 0
    || !isStringArray(candidate.financial_strengths)
    || !isStringArray(candidate.financial_risks_and_conflicts)
    || typeof candidate.preliminary_financial_health !== "string"
    || candidate.preliminary_financial_health.length === 0
    || typeof candidate.confidence_percentage !== "number"
    || !Number.isFinite(candidate.confidence_percentage)
    || candidate.confidence_percentage < 0
    || candidate.confidence_percentage > 100
    || !isStringArray(candidate.valuation_observations)
    || !isStringArray(candidate.missing_evidence_and_limitations)
    || !isStringArray(candidate.document_citations)
    || candidate.document_citations.length === 0
    || typeof candidate.runtime_skill_id !== "string"
    || candidate.runtime_skill_id.length === 0
    || typeof candidate.runtime_skill_version !== "string"
    || candidate.runtime_skill_version.length === 0
    || typeof candidate.runtime_skill_prompt_sha256 !== "string"
    || !SHA256_PATTERN.test(candidate.runtime_skill_prompt_sha256)
    || typeof candidate.model_id !== "string"
    || candidate.model_id.length === 0
    || typeof candidate.generated_at !== "string"
    || !Number.isFinite(Date.parse(candidate.generated_at))
  ) {
    return null;
  }
  return candidate as FundamentalAnalysis;
}

/** Validate traceability metadata without fetching or trusting package content. */
export function parseFundamentalPackageReference(
  value: unknown,
): FundamentalPackageReference | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Record<string, unknown>;
  if (
    candidate.schema_version !== "jarvis.browser_fundamental_package_ref.v1"
    || typeof candidate.package_id !== "string"
    || typeof candidate.manifest_fingerprint !== "string"
    || !SHA256_PATTERN.test(candidate.manifest_fingerprint)
    || typeof candidate.expires_at !== "string"
    || !Number.isFinite(Date.parse(candidate.expires_at))
  ) {
    return null;
  }
  const packageIdMatch = PACKAGE_ID_PATTERN.exec(candidate.package_id);
  if (
    packageIdMatch === null
    || packageIdMatch[1] !== candidate.manifest_fingerprint
  ) {
    return null;
  }
  return candidate as FundamentalPackageReference;
}

export function fundamentalConfidenceTone(
  confidencePercentage: number,
): FundamentalConfidenceTone {
  if (confidencePercentage >= 70) return "positive";
  if (confidencePercentage >= 45) return "caution";
  return "limited";
}
