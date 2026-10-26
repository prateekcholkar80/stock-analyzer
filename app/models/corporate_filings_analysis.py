"""Immutable contracts for grounded LLM analysis of company filings.

Python owns document preparation, identity, coverage, citation lineage and
response validation.  An LLM may describe the released evidence, but it may
not change deterministic bindings or introduce citations, speakers or chunks
that were not present in the supplied quarterly bundle.
"""

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamental_filing_evidence import (
    FundamentalFilingCitation,
    FundamentalFilingParticipantAffiliation,
    FundamentalFilingSpeakerRole,
)
from app.models.fundamental_filings import (
    FundamentalFilingCategory,
    FundamentalFilingPeriod,
    FundamentalFilingPeriodState,
    FundamentalFilingPeriodType,
    FundamentalFilingSourceClass,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
    ProviderConnectionScope,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){2}$"
_ARTIFACT_ID_PATTERN = r"^filing_artifact:[a-f0-9]{64}$"
_PACKAGE_ID_PATTERN = r"^filing_package:[a-f0-9]{64}$"
_CHUNK_ID_PATTERN = r"^filing_chunk:[a-f0-9]{64}$"
_MAX_CHUNK_TEXT = 60_000


def _fingerprint(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _require_unique_canonical(
    values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field_name} must be unique and canonical")
    return values


class CorporateFilingsAnalysisKind(StrEnum):
    CONFERENCE_CALL = "conference_call"
    EARNINGS_RELEASE = "earnings_release"
    QUARTERLY_COMBINED = "quarterly_combined"


class CorporateFilingsSourceQuality(StrEnum):
    PRIMARY_COMPANY_DISCLOSURE = "primary_company_disclosure"
    VERBATIM_TRANSCRIPT = "verbatim_transcript"
    SECONDARY_DERIVED_SUMMARY = "secondary_derived_summary"


class CorporateFilingsAnalysisStatus(StrEnum):
    COMPLETED = "completed"
    ABSTAINED = "abstained"
    FAILED = "failed"


class CorporateFilingsGroundingStatus(StrEnum):
    GROUNDED = "grounded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INVALID_EVIDENCE = "invalid_evidence"


class CorporateFilingsCoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class CorporateFilingsFindingKind(StrEnum):
    OBSERVED_FACT = "observed_fact"
    MANAGEMENT_CLAIM = "management_claim"
    FORWARD_GUIDANCE = "forward_guidance"
    ANALYST_CONCERN = "analyst_concern"
    MANAGEMENT_RESPONSE = "management_response"
    RISK = "risk"
    CONTRADICTION = "contradiction"
    JHUNJHUNWALA_STYLE_INTERPRETATION = (
        "jhunjhunwala_style_interpretation"
    )


class CorporateFilingsFindingConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CorporateFilingsInterpretationPrinciple(StrEnum):
    OPPORTUNITY_AND_RUNWAY = "opportunity_and_runway"
    COMPETITIVE_ABILITY = "competitive_ability"
    SCALABILITY = "scalability"
    MANAGEMENT_EXECUTION = "management_execution"
    CAPITAL_ALLOCATION = "capital_allocation"
    CONCENTRATION = "concentration"
    THESIS_MONITORING = "thesis_monitoring"


class CorporateFilingsFailureStage(StrEnum):
    PREFLIGHT = "preflight"
    LLM_INVOCATION = "llm_invocation"
    OUTPUT_VALIDATION = "output_validation"
    GROUNDING_VALIDATION = "grounding_validation"


class CorporateFilingsFailureCategory(StrEnum):
    INVALID_INPUT = "invalid_input"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    UNSUPPORTED_CITATION = "unsupported_citation"
    UNKNOWN = "unknown"


class CorporateFilingsAnalysisSpeakerReference(FundamentalModel):
    """Deterministically released speaker identity for LLM attribution."""

    speaker_turn_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    participant_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    speaker_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: FundamentalFilingSpeakerRole
    affiliation: FundamentalFilingParticipantAffiliation

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        identified = self.participant_id is not None or self.speaker_name is not None
        if self.role is FundamentalFilingSpeakerRole.UNKNOWN:
            if identified or self.affiliation is not (
                FundamentalFilingParticipantAffiliation.UNKNOWN
            ):
                raise ValueError("unknown speaker cannot carry inferred identity")
        elif self.speaker_name is None:
            raise ValueError("classified speaker requires its released name")
        if (
            self.role is FundamentalFilingSpeakerRole.MANAGEMENT
            and self.affiliation is not FundamentalFilingParticipantAffiliation.ISSUER
        ):
            raise ValueError("management speaker must be issuer affiliated")
        if (
            self.role is FundamentalFilingSpeakerRole.ANALYST
            and self.affiliation is not FundamentalFilingParticipantAffiliation.EXTERNAL
        ):
            raise ValueError("analyst speaker must be externally affiliated")
        return self


class CorporateFilingsAnalysisChunk(FundamentalModel):
    """One deterministic, page-preserving unit supplied to the LLM."""

    sequence: int = Field(ge=0, le=100_000)
    artifact_id: str = Field(pattern=_ARTIFACT_ID_PATTERN)
    page_numbers: tuple[int, ...] = Field(min_length=1, max_length=100)
    source_element_ids: tuple[str, ...] = Field(min_length=1, max_length=2_000)
    citation_ids: tuple[str, ...] = Field(min_length=1, max_length=2_000)
    speaker_turn_ids: tuple[str, ...] = Field(default=(), max_length=2_000)
    text: str = Field(min_length=1, max_length=_MAX_CHUNK_TEXT)

    @field_validator(
        "source_element_ids",
        "citation_ids",
        "speaker_turn_ids",
    )
    @classmethod
    def validate_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_canonical(values, "filing chunk identifiers")

    @field_validator("page_numbers")
    @classmethod
    def validate_pages(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(value < 1 or value > 5_000 for value in values):
            raise ValueError("filing chunk page is out of range")
        if values != tuple(sorted(set(values))):
            raise ValueError("filing chunk pages must be unique and ordered")
        return values

    @computed_field
    @property
    def chunk_id(self) -> str:
        payload = self.model_dump(mode="json", exclude_computed_fields=True)
        return f"filing_chunk:{_fingerprint(payload)}"


class CorporateFilingsAnalysisDocument(FundamentalModel):
    """A releaseable extraction transformed into bounded LLM chunks."""

    artifact_id: str = Field(pattern=_ARTIFACT_ID_PATTERN)
    content_sha256: str = Field(pattern=_FINGERPRINT_PATTERN)
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    extraction_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    validation_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    extraction_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    validation_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    category: FundamentalFilingCategory
    source_class: FundamentalFilingSourceClass
    source_quality: CorporateFilingsSourceQuality
    extracted_at: datetime
    citations: tuple[FundamentalFilingCitation, ...] = Field(
        min_length=1,
        max_length=200_000,
    )
    speakers: tuple[CorporateFilingsAnalysisSpeakerReference, ...] = Field(
        default=(),
        max_length=100_000,
    )
    chunks: tuple[CorporateFilingsAnalysisChunk, ...] = Field(
        min_length=1,
        max_length=10_000,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=1_000)

    @field_validator("extracted_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing analysis extraction timestamp")

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 1_000 for value in values):
            raise ValueError("filing analysis limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("filing analysis limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        expected_source = {
            FundamentalFilingCategory.EARNINGS_RELEASE: (
                FundamentalFilingSourceClass.COMPANY_AUTHORED_EARNINGS_RELEASE
            ),
            FundamentalFilingCategory.CONFERENCE_CALL: (
                FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
            ),
        }[self.category]
        if self.source_class is not expected_source:
            raise ValueError("analysis document source class is inconsistent")
        expected_quality = {
            FundamentalFilingCategory.EARNINGS_RELEASE: (
                CorporateFilingsSourceQuality.PRIMARY_COMPANY_DISCLOSURE
            ),
            FundamentalFilingCategory.CONFERENCE_CALL: (
                CorporateFilingsSourceQuality.VERBATIM_TRANSCRIPT
            ),
        }[self.category]
        if self.source_quality is not expected_quality and self.source_quality is not (
            CorporateFilingsSourceQuality.SECONDARY_DERIVED_SUMMARY
        ):
            raise ValueError("analysis document source quality is inconsistent")

        citation_ids = tuple(item.citation_id for item in self.citations)
        _require_unique_canonical(citation_ids, "analysis document citations")
        if any(
            item.artifact_id != self.artifact_id
            or item.content_sha256 != self.content_sha256
            for item in self.citations
        ):
            raise ValueError("analysis citations must match their document")
        speakers = {item.speaker_turn_id: item for item in self.speakers}
        if len(speakers) != len(self.speakers):
            raise ValueError("analysis document speakers must be unique")
        chunk_ids = tuple(item.chunk_id for item in self.chunks)
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("analysis document chunks must be unique")
        sequences = tuple(item.sequence for item in self.chunks)
        if sequences != tuple(range(len(self.chunks))):
            raise ValueError("analysis document chunk sequence must be contiguous")
        available_citations = set(citation_ids)
        for chunk in self.chunks:
            if chunk.artifact_id != self.artifact_id:
                raise ValueError("analysis chunk belongs to another document")
            if not set(chunk.citation_ids).issubset(available_citations):
                raise ValueError("analysis chunk cites unknown document evidence")
            if not set(chunk.speaker_turn_ids).issubset(speakers):
                raise ValueError("analysis chunk references an unknown speaker")
            cited_pages = {
                item.page_number
                for item in self.citations
                if item.citation_id in chunk.citation_ids
            }
            if not cited_pages.issubset(set(chunk.page_numbers)):
                raise ValueError("analysis chunk omits one of its cited pages")
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class CorporateFilingsQuarterlyBundle(FundamentalModel):
    """Immutable LLM input package for one issuer and fiscal quarter."""

    schema_version: Literal["jarvis.corporate_filings_quarterly_bundle.v1"] = (
        "jarvis.corporate_filings_quarterly_bundle.v1"
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    filing_package_id: str = Field(pattern=_PACKAGE_ID_PATTERN)
    filing_package_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    period: FundamentalFilingPeriod
    analysis_kind: CorporateFilingsAnalysisKind
    assembled_at: datetime
    documents: tuple[CorporateFilingsAnalysisDocument, ...] = Field(
        min_length=1,
        max_length=2,
    )

    @field_validator("assembled_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing analysis bundle timestamp")

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        if (
            self.period.state is not FundamentalFilingPeriodState.RESOLVED
            or self.period.period_type is not FundamentalFilingPeriodType.QUARTERLY
        ):
            raise ValueError("filing analysis requires a resolved fiscal quarter")
        if self.filing_package_id != (
            f"filing_package:{self.filing_package_fingerprint}"
        ):
            raise ValueError("filing analysis package identity is inconsistent")
        categories = tuple(item.category for item in self.documents)
        if len(categories) != len(set(categories)):
            raise ValueError("filing analysis bundle categories must be unique")
        required = {
            CorporateFilingsAnalysisKind.CONFERENCE_CALL: {
                FundamentalFilingCategory.CONFERENCE_CALL
            },
            CorporateFilingsAnalysisKind.EARNINGS_RELEASE: {
                FundamentalFilingCategory.EARNINGS_RELEASE
            },
            CorporateFilingsAnalysisKind.QUARTERLY_COMBINED: {
                FundamentalFilingCategory.CONFERENCE_CALL,
                FundamentalFilingCategory.EARNINGS_RELEASE,
            },
        }[self.analysis_kind]
        if set(categories) != required:
            raise ValueError("filing analysis bundle document set is incomplete")
        if any(item.extracted_at > self.assembled_at for item in self.documents):
            raise ValueError("filing analysis bundle predates document extraction")
        return self

    @computed_field
    @property
    def bundle_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class CorporateFilingsAnalysisPolicy(FundamentalModel):
    """Versioned prompt, skill and bounded model-execution identity."""

    policy_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    policy_version: str = Field(pattern=_VERSION_PATTERN)
    prompt_sha256: str = Field(pattern=_FINGERPRINT_PATTERN)
    skill_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    skill_version: str = Field(pattern=_VERSION_PATTERN)
    skill_sha256: str = Field(pattern=_FINGERPRINT_PATTERN)
    model_configuration_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    max_input_tokens: int = Field(ge=1, le=1_000_000)
    max_output_tokens: int = Field(ge=1, le=100_000)
    max_model_calls: int = Field(ge=1, le=100)
    require_citations: Literal[True] = True
    require_adverse_evidence: Literal[True] = True
    permit_trade_decision: Literal[False] = False
    permit_uncited_company_claims: Literal[False] = False

    @computed_field
    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class CorporateFilingsAnalysisReuseKey(FundamentalModel):
    """Exact immutable cache identity for one document-analysis execution."""

    tenant_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    issuer_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    bundle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    model_configuration_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @computed_field
    @property
    def reuse_key_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class CorporateFilingsAnalysisRequest(FundamentalModel):
    """Preflight-safe request containing only deterministic released input."""

    schema_version: Literal["jarvis.corporate_filings_analysis_request.v1"] = (
        "jarvis.corporate_filings_analysis_request.v1"
    )
    request_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    operation_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    bundle: CorporateFilingsQuarterlyBundle
    policy: CorporateFilingsAnalysisPolicy
    requested_at: datetime
    as_of: datetime

    @field_validator("requested_at", "as_of")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "corporate filing analysis timestamp")

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.requested_at > self.as_of:
            raise ValueError("filing analysis request cannot follow its as-of time")
        if self.bundle.assembled_at > self.as_of:
            raise ValueError("filing analysis cannot use a future bundle")
        return self

    @computed_field
    @property
    def reuse_key(self) -> CorporateFilingsAnalysisReuseKey:
        issuer_fingerprint = _fingerprint(
            self.bundle.issuer.model_dump(mode="json")
        )
        return CorporateFilingsAnalysisReuseKey(
            tenant_id=self.bundle.connection.tenant_id,
            provider_connection_id=(
                self.bundle.connection.provider_connection_id
            ),
            issuer_fingerprint=issuer_fingerprint,
            bundle_fingerprint=self.bundle.bundle_fingerprint,
            policy_fingerprint=self.policy.policy_fingerprint,
            model_configuration_fingerprint=(
                self.policy.model_configuration_fingerprint
            ),
        )

    @computed_field
    @property
    def request_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class CorporateFilingsAnalysisFinding(FundamentalModel):
    finding_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    kind: CorporateFilingsFindingKind
    statement: str = Field(min_length=1, max_length=5_000)
    confidence: CorporateFilingsFindingConfidence
    citation_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    chunk_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    speaker_turn_ids: tuple[str, ...] = Field(default=(), max_length=64)
    principle: CorporateFilingsInterpretationPrinciple | None = None

    @field_validator("citation_ids", "chunk_ids", "speaker_turn_ids")
    @classmethod
    def validate_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_canonical(values, "analysis finding references")

    @model_validator(mode="after")
    def validate_interpretation(self) -> Self:
        is_interpretation = self.kind is (
            CorporateFilingsFindingKind.JHUNJHUNWALA_STYLE_INTERPRETATION
        )
        if is_interpretation != (self.principle is not None):
            raise ValueError(
                "only a framework interpretation may carry its principle"
            )
        return self


class CorporateFilingsMissingEvidence(FundamentalModel):
    missing_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    question: str = Field(min_length=1, max_length=1_000)
    searched_chunk_ids: tuple[str, ...] = Field(min_length=1, max_length=512)

    @field_validator("searched_chunk_ids")
    @classmethod
    def validate_chunks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_canonical(values, "searched filing chunks")


class CorporateFilingsAnalysisCoverage(FundamentalModel):
    status: CorporateFilingsCoverageStatus
    expected_documents: int = Field(ge=1, le=2)
    analyzed_documents: int = Field(ge=0, le=2)
    expected_chunks: int = Field(ge=1, le=20_000)
    analyzed_chunks: int = Field(ge=0, le=20_000)
    available_citations: int = Field(ge=1, le=400_000)
    cited_citations: int = Field(ge=0, le=400_000)
    unresolved_speaker_turns: int = Field(ge=0, le=100_000)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        if self.analyzed_documents > self.expected_documents:
            raise ValueError("analyzed document count exceeds supplied documents")
        if self.analyzed_chunks > self.expected_chunks:
            raise ValueError("analyzed chunk count exceeds supplied chunks")
        expected_status = CorporateFilingsCoverageStatus.COMPLETE
        if not self.analyzed_documents or not self.analyzed_chunks:
            expected_status = CorporateFilingsCoverageStatus.FAILED
        elif (
            self.analyzed_documents != self.expected_documents
            or self.analyzed_chunks != self.expected_chunks
        ):
            expected_status = CorporateFilingsCoverageStatus.PARTIAL
        if self.status is not expected_status:
            raise ValueError("filing analysis coverage status is inconsistent")
        return self


class CorporateFilingsTokenUsage(FundamentalModel):
    input_tokens: int = Field(ge=0, le=10_000_000)
    output_tokens: int = Field(ge=0, le=10_000_000)
    model_calls: int = Field(ge=0, le=100)

    @computed_field
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CorporateFilingsAnalysisExecution(FundamentalModel):
    model_id: str = Field(min_length=1, max_length=200)
    model_configuration_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    started_at: datetime
    completed_at: datetime
    token_usage: CorporateFilingsTokenUsage

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing analysis execution timestamp")

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("filing analysis cannot complete before it starts")
        return self

    @computed_field
    @property
    def latency_ms(self) -> int:
        return round((self.completed_at - self.started_at).total_seconds() * 1_000)


class CorporateFilingsAnalysisFailure(FundamentalModel):
    stage: CorporateFilingsFailureStage
    category: CorporateFilingsFailureCategory
    retryable: bool
    sanitized_message: str = Field(min_length=1, max_length=500)

    @field_validator("sanitized_message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("filing analysis failure must be a single line")
        return value


_RESULT_COLLECTION_KINDS = {
    "observed_facts": CorporateFilingsFindingKind.OBSERVED_FACT,
    "management_claims": CorporateFilingsFindingKind.MANAGEMENT_CLAIM,
    "guidance": CorporateFilingsFindingKind.FORWARD_GUIDANCE,
    "analyst_concerns": CorporateFilingsFindingKind.ANALYST_CONCERN,
    "management_responses": CorporateFilingsFindingKind.MANAGEMENT_RESPONSE,
    "risks": CorporateFilingsFindingKind.RISK,
    "contradictions": CorporateFilingsFindingKind.CONTRADICTION,
    "jhunjhunwala_style_interpretations": (
        CorporateFilingsFindingKind.JHUNJHUNWALA_STYLE_INTERPRETATION
    ),
}


class CorporateFilingsAnalysisResult(FundamentalModel):
    """Strict LLM result; release requires separate request-bound validation."""

    schema_version: Literal["jarvis.corporate_filings_analysis.v1"] = (
        "jarvis.corporate_filings_analysis.v1"
    )
    analysis_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    reuse_key_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    bundle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    status: CorporateFilingsAnalysisStatus
    grounding_status: CorporateFilingsGroundingStatus
    coverage: CorporateFilingsAnalysisCoverage
    observed_facts: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(), max_length=1_000
    )
    management_claims: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(), max_length=1_000
    )
    guidance: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(), max_length=1_000
    )
    analyst_concerns: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(), max_length=1_000
    )
    management_responses: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(), max_length=1_000
    )
    risks: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(), max_length=1_000
    )
    contradictions: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(), max_length=1_000
    )
    missing_evidence: tuple[CorporateFilingsMissingEvidence, ...] = Field(
        default=(), max_length=1_000
    )
    jhunjhunwala_style_interpretations: tuple[
        CorporateFilingsAnalysisFinding, ...
    ] = Field(default=(), max_length=1_000)
    limitations: tuple[str, ...] = Field(default=(), max_length=1_000)
    execution: CorporateFilingsAnalysisExecution | None = None
    failure: CorporateFilingsAnalysisFailure | None = None

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 1_000 for value in values):
            raise ValueError("filing analysis limitations must be bounded")
        if values != tuple(sorted(set(values))):
            raise ValueError(
                "filing analysis limitations must be unique and canonical"
            )
        return values

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        all_findings: list[CorporateFilingsAnalysisFinding] = []
        for field_name, expected_kind in _RESULT_COLLECTION_KINDS.items():
            findings = getattr(self, field_name)
            if any(item.kind is not expected_kind for item in findings):
                raise ValueError(
                    f"{field_name} contains a mismatched finding kind"
                )
            finding_ids = tuple(item.finding_id for item in findings)
            if finding_ids != tuple(sorted(set(finding_ids))):
                raise ValueError(f"{field_name} must be unique and canonical")
            all_findings.extend(findings)
        finding_ids = tuple(item.finding_id for item in all_findings)
        missing_ids = tuple(item.missing_id for item in self.missing_evidence)
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("filing analysis finding IDs must be globally unique")
        if missing_ids != tuple(sorted(set(missing_ids))):
            raise ValueError("missing-evidence IDs must be unique and canonical")
        if set(finding_ids).intersection(missing_ids):
            raise ValueError("finding and missing-evidence IDs cannot overlap")
        if any(
            len(item.citation_ids) < 2 for item in self.contradictions
        ):
            raise ValueError("contradictions require at least two citations")

        if self.status is CorporateFilingsAnalysisStatus.COMPLETED:
            if (
                self.grounding_status is not CorporateFilingsGroundingStatus.GROUNDED
                or self.coverage.status is not CorporateFilingsCoverageStatus.COMPLETE
                or self.execution is None
                or self.failure is not None
                or not all_findings
            ):
                raise ValueError(
                    "completed filing analysis must be grounded and complete"
                )
        else:
            if all_findings or self.failure is None:
                raise ValueError(
                    "unreleased filing analysis cannot contain findings"
                )
            if self.status is CorporateFilingsAnalysisStatus.ABSTAINED:
                if self.grounding_status is not (
                    CorporateFilingsGroundingStatus.INSUFFICIENT_EVIDENCE
                ):
                    raise ValueError("abstention requires insufficient evidence")
            elif self.grounding_status is CorporateFilingsGroundingStatus.GROUNDED:
                raise ValueError("failed filing analysis cannot be grounded")
        return self

    @computed_field
    @property
    def releaseable(self) -> bool:
        return (
            self.status is CorporateFilingsAnalysisStatus.COMPLETED
            and self.grounding_status is CorporateFilingsGroundingStatus.GROUNDED
            and self.coverage.status is CorporateFilingsCoverageStatus.COMPLETE
            and self.failure is None
        )

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


def validate_corporate_filings_analysis_response(
    request: CorporateFilingsAnalysisRequest,
    result: CorporateFilingsAnalysisResult,
) -> CorporateFilingsAnalysisResult:
    """Fail closed when an LLM response escapes its released evidence."""

    if result.request_fingerprint != request.request_fingerprint:
        raise ValueError("filing analysis response does not match its request")
    if result.reuse_key_fingerprint != request.reuse_key.reuse_key_fingerprint:
        raise ValueError("filing analysis response changes its reuse key")
    if result.bundle_fingerprint != request.bundle.bundle_fingerprint:
        raise ValueError("filing analysis response changes its bundle")
    if result.policy_fingerprint != request.policy.policy_fingerprint:
        raise ValueError("filing analysis response changes its policy")
    if result.execution is not None:
        if result.execution.model_configuration_fingerprint != (
            request.policy.model_configuration_fingerprint
        ):
            raise ValueError("filing analysis response changes its model policy")
        usage = result.execution.token_usage
        if usage.input_tokens > request.policy.max_input_tokens:
            raise ValueError("filing analysis exceeded its input-token budget")
        if usage.output_tokens > request.policy.max_output_tokens:
            raise ValueError("filing analysis exceeded its output-token budget")
        if usage.model_calls > request.policy.max_model_calls:
            raise ValueError("filing analysis exceeded its model-call budget")

    available_citations: set[str] = set()
    available_chunks: set[str] = set()
    speakers: dict[str, CorporateFilingsAnalysisSpeakerReference] = {}
    for document in request.bundle.documents:
        available_citations.update(item.citation_id for item in document.citations)
        available_chunks.update(item.chunk_id for item in document.chunks)
        speakers.update(
            (item.speaker_turn_id, item) for item in document.speakers
        )
    findings = tuple(
        item
        for field_name in _RESULT_COLLECTION_KINDS
        for item in getattr(result, field_name)
    )
    if any(
        not set(item.citation_ids).issubset(available_citations)
        or not set(item.chunk_ids).issubset(available_chunks)
        or not set(item.speaker_turn_ids).issubset(speakers)
        for item in findings
    ):
        raise ValueError("filing analysis contains unsupported evidence references")
    if any(
        not set(item.searched_chunk_ids).issubset(available_chunks)
        for item in result.missing_evidence
    ):
        raise ValueError("filing analysis missing-evidence search is unsupported")

    management_findings = (
        result.management_claims
        + result.management_responses
        + result.guidance
    )
    if request.bundle.analysis_kind is CorporateFilingsAnalysisKind.CONFERENCE_CALL:
        if any(not item.speaker_turn_ids for item in management_findings):
            raise ValueError(
                "conference-call management findings require a speaker"
            )
        if any(not item.speaker_turn_ids for item in result.analyst_concerns):
            raise ValueError("conference-call concerns require an analyst speaker")
    for item in management_findings:
        if item.speaker_turn_ids and any(
            speakers[value].role is not FundamentalFilingSpeakerRole.MANAGEMENT
            for value in item.speaker_turn_ids
        ):
            raise ValueError("management finding cites a non-management speaker")
    for item in result.analyst_concerns:
        if item.speaker_turn_ids and any(
            speakers[value].role is not FundamentalFilingSpeakerRole.ANALYST
            for value in item.speaker_turn_ids
        ):
            raise ValueError("analyst concern cites a non-analyst speaker")

    expected_documents = len(request.bundle.documents)
    expected_chunks = sum(len(item.chunks) for item in request.bundle.documents)
    available_count = len(available_citations)
    cited_citations = len({
        citation_id
        for item in findings
        for citation_id in item.citation_ids
    })
    coverage = result.coverage
    if (
        coverage.expected_documents != expected_documents
        or coverage.expected_chunks != expected_chunks
        or coverage.available_citations != available_count
        or coverage.cited_citations != cited_citations
    ):
        raise ValueError("filing analysis coverage changes deterministic counts")
    return result
