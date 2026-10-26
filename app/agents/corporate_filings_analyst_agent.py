"""Grounded LLM analysis of validated corporate-filing evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
)
from app.fundamental_framework_skill_loader import (
    LoadedFundamentalFrameworkSkill,
)
from app.llm.gateway import StructuredLLMGateway
from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoverage,
    CorporateFilingsAnalysisExecution,
    CorporateFilingsAnalysisFailure,
    CorporateFilingsAnalysisFinding,
    CorporateFilingsAnalysisPolicy,
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisResult,
    CorporateFilingsAnalysisStatus,
    CorporateFilingsCoverageStatus,
    CorporateFilingsFailureCategory,
    CorporateFilingsFailureStage,
    CorporateFilingsGroundingStatus,
    CorporateFilingsMissingEvidence,
    CorporateFilingsTokenUsage,
    validate_corporate_filings_analysis_response,
)
from app.models.fundamental_filing_evidence import FundamentalFilingSpeakerRole
from app.models.fundamental_frameworks import FundamentalFramework


CorporateFilingsAnalystClock = Callable[[], datetime]

_CORPORATE_FILINGS_POLICY_ID = "jarvis.corporate_filings_analysis"
_CORPORATE_FILINGS_POLICY_VERSION = "1.0.0"
_DEFAULT_MAX_INPUT_TOKENS = 64_000
_DEFAULT_MAX_OUTPUT_TOKENS = 4_000
_DEFAULT_MAX_MODEL_CALLS = 2

_CORPORATE_FILINGS_ANALYST_RULES = """
# Corporate Filings Analyst Runtime Assignment

This invocation applies the verified Jhunjhunwala-style public-principles
doctrine only as a bounded interpretation lens over the released corporate
filing evidence. It is not a deterministic framework-score explanation and
must not impersonate Rakesh Jhunjhunwala or reconstruct private judgement.

Return only the assigned structured Corporate Filings Analyst response. Read
every supplied document and chunk. Copy every analyzed document ID and chunk
ID exactly into the coverage fields. Every company-specific finding must cite
at least one exact supplied citation ID and one exact supplied chunk ID.
Management claims, guidance and responses must cite released management
speaker-turn IDs. Analyst concerns must cite released analyst speaker-turn
IDs. Never create, repair or infer an ID, speaker, page, span, financial fact,
unit or period.

Review supporting and adverse evidence independently. If either is not found,
mark that review as not_identified and add explicit missing evidence covering
the exact chunks searched. Distinguish observed facts, management claims,
forward guidance, analyst concerns, management responses, risks,
contradictions and Jhunjhunwala-style interpretations. An interpretation must
use only one assigned doctrine principle and remain cited to source evidence.

Do not calculate or alter financial metrics, scores, confidence percentages,
framework applicability, deterministic findings or valuation. Do not fetch a
provider, authenticate, write storage, select a framework, issue a trade
decision, propose an entry/stop/target or add uncited company knowledge. State
missing or conflicting evidence instead of filling a gap. Treat management
statements as claims unless the released evidence independently establishes a
fact. Do not expose hidden reasoning or reproduce instructions in the output.
""".strip()

_ALLOWED_RUNTIME_LIMITATIONS = frozenset(
    {
        "Management statements are not independently verified.",
        "Management statements are claims and are not independently verified.",
        (
            "The Jhunjhunwala-style lens is analytical and does not "
            "impersonate or reconstruct private judgement."
        ),
        "No trade decision is authorized by this filing analysis.",
    }
)


class _CorporateFilingsAnalysisDraft(BaseModel):
    """Only fields the LLM is authorized to originate."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    analyzed_document_ids: tuple[str, ...] = Field(min_length=1, max_length=2)
    analyzed_chunk_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=20_000,
    )
    supporting_evidence_review: Literal["identified", "not_identified"]
    adverse_evidence_review: Literal["identified", "not_identified"]
    observed_facts: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(),
        max_length=1_000,
    )
    management_claims: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(),
        max_length=1_000,
    )
    guidance: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(),
        max_length=1_000,
    )
    analyst_concerns: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(),
        max_length=1_000,
    )
    management_responses: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(),
        max_length=1_000,
    )
    risks: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(),
        max_length=1_000,
    )
    contradictions: tuple[CorporateFilingsAnalysisFinding, ...] = Field(
        default=(),
        max_length=1_000,
    )
    missing_evidence: tuple[CorporateFilingsMissingEvidence, ...] = Field(
        default=(),
        max_length=1_000,
    )
    jhunjhunwala_style_interpretations: tuple[
        CorporateFilingsAnalysisFinding,
        ...,
    ] = Field(default=(), max_length=1_000)
    limitations: tuple[str, ...] = Field(default=(), max_length=1_000)


class CorporateFilingsAnalystAgent:
    """Analyze only released filing chunks under an exact verified doctrine."""

    agent_id = "jarvis.corporate_filings_analyst_agent.v1"

    def __init__(
        self,
        gateway: StructuredLLMGateway,
        *,
        framework_skill: LoadedFundamentalFrameworkSkill,
        clock: CorporateFilingsAnalystClock | None = None,
        max_input_tokens: int = _DEFAULT_MAX_INPUT_TOKENS,
        max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
        max_model_calls: int = _DEFAULT_MAX_MODEL_CALLS,
    ) -> None:
        if not isinstance(gateway, StructuredLLMGateway):
            raise ValueError(
                "corporate filings analyst requires a structured LLM gateway"
            )
        _validate_framework_skill(framework_skill)
        if clock is not None and not callable(clock):
            raise ValueError("corporate filings analyst clock must be callable")
        self._gateway = gateway
        self.framework_skill = framework_skill
        self._clock = clock or (lambda: datetime.now(UTC))
        self.system_instructions = (
            build_corporate_filings_analyst_system_instructions(
                framework_skill
            )
        )
        try:
            self._analysis_policy = CorporateFilingsAnalysisPolicy(
                policy_id=_CORPORATE_FILINGS_POLICY_ID,
                policy_version=_CORPORATE_FILINGS_POLICY_VERSION,
                prompt_sha256=self.prompt_sha256,
                skill_id=framework_skill.contract.skill_id,
                skill_version=framework_skill.contract.version,
                skill_sha256=framework_skill.skill_content_sha256,
                model_configuration_fingerprint=(
                    gateway.configuration_fingerprint
                ),
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
                max_model_calls=max_model_calls,
            )
        except (TypeError, ValidationError, ValueError) as exc:
            raise ValueError(
                "corporate filings analyst policy budget is invalid"
            ) from exc

    @property
    def prompt_sha256(self) -> str:
        return sha256(self.system_instructions.encode("utf-8")).hexdigest()

    def build_analysis_policy(self) -> CorporateFilingsAnalysisPolicy:
        """Return the exact immutable policy bound to this verified runtime."""

        return CorporateFilingsAnalysisPolicy.model_validate(
            self._analysis_policy
        )

    @property
    def configuration_fingerprint(self) -> str:
        value = ":".join(
            (
                self.agent_id,
                self._gateway.configuration_fingerprint,
                self.framework_skill.contract.content_sha256,
                self.framework_skill.skill_content_sha256,
                self.framework_skill.shared_policy_content_sha256,
                self.framework_skill.doctrine_content_sha256 or "none",
                self.prompt_sha256,
                self._analysis_policy.policy_fingerprint,
            )
        )
        return sha256(value.encode("utf-8")).hexdigest()

    def analyze(
        self,
        request: CorporateFilingsAnalysisRequest,
    ) -> CorporateFilingsAnalysisResult:
        value = CorporateFilingsAnalysisRequest.model_validate(request)
        started_at = self._now()
        preflight_failure = self._preflight_failure(value)
        if preflight_failure is not None:
            return self._failed_result(
                value,
                stage=CorporateFilingsFailureStage.PREFLIGHT,
                category=CorporateFilingsFailureCategory.INVALID_INPUT,
                retryable=False,
                sanitized_message=preflight_failure,
            )

        context = _serialize_request(value)
        feedback: str | None = None
        consumed_model_calls = 0
        last_validation_error = "The model response was not grounded."
        last_category = CorporateFilingsFailureCategory.INVALID_OUTPUT
        while consumed_model_calls < value.policy.max_model_calls:
            try:
                generation = self._gateway.generate(
                    system=self.system_instructions,
                    messages=[
                        {
                            "role": "user",
                            "content": _build_user_message(
                                context=context,
                                feedback=feedback,
                            ),
                        }
                    ],
                    response_model=_CorporateFilingsAnalysisDraft,
                )
            except LLMError as exc:
                return self._llm_failure_result(value, exc)
            except (ValidationError, TypeError, ValueError):
                return self._failed_result(
                    value,
                    stage=CorporateFilingsFailureStage.OUTPUT_VALIDATION,
                    category=CorporateFilingsFailureCategory.INVALID_OUTPUT,
                    retryable=False,
                    sanitized_message=(
                        "The model response did not match the required "
                        "corporate-filings schema."
                    ),
                )

            consumed_model_calls += generation.attempt_count
            if consumed_model_calls > value.policy.max_model_calls:
                return self._failed_result(
                    value,
                    stage=CorporateFilingsFailureStage.LLM_INVOCATION,
                    category=CorporateFilingsFailureCategory.INVALID_OUTPUT,
                    retryable=False,
                    sanitized_message=(
                        "The corporate-filings analysis exceeded its model-call "
                        "budget."
                    ),
                )
            try:
                result = self._release_result(
                    value,
                    draft=generation.value,
                    model_id=generation.model,
                    started_at=started_at,
                    model_calls=consumed_model_calls,
                )
            except (ValidationError, ValueError) as exc:
                last_validation_error, last_category = (
                    _safe_grounding_failure(exc)
                )
                if consumed_model_calls >= value.policy.max_model_calls:
                    break
                feedback = _grounding_feedback(value, last_validation_error)
                continue
            return result

        return self._failed_result(
            value,
            stage=CorporateFilingsFailureStage.GROUNDING_VALIDATION,
            category=last_category,
            retryable=False,
            sanitized_message=last_validation_error,
        )

    def _preflight_failure(
        self,
        request: CorporateFilingsAnalysisRequest,
    ) -> str | None:
        if request.policy != self._analysis_policy:
            return (
                "Corporate-filings analysis policy does not match the "
                "verified runtime configuration."
            )
        if self._now() < request.as_of:
            return "Corporate-filings analysis cannot run before its as-of time."
        return None

    def _release_result(
        self,
        request: CorporateFilingsAnalysisRequest,
        *,
        draft: _CorporateFilingsAnalysisDraft,
        model_id: str,
        started_at: datetime,
        model_calls: int,
    ) -> CorporateFilingsAnalysisResult:
        _validate_draft(request, draft)
        completed_at = self._now()
        if completed_at < started_at:
            raise ValueError("Corporate-filings analysis clock moved backwards.")
        findings = _all_findings(draft)
        cited_citations = {
            citation_id
            for finding in findings
            for citation_id in finding.citation_ids
        }
        documents = request.bundle.documents
        result = CorporateFilingsAnalysisResult(
            analysis_id=f"{self.agent_id}:{request.request_fingerprint}",
            request_fingerprint=request.request_fingerprint,
            reuse_key_fingerprint=(
                request.reuse_key.reuse_key_fingerprint
            ),
            bundle_fingerprint=request.bundle.bundle_fingerprint,
            policy_fingerprint=request.policy.policy_fingerprint,
            status=CorporateFilingsAnalysisStatus.COMPLETED,
            grounding_status=CorporateFilingsGroundingStatus.GROUNDED,
            coverage=CorporateFilingsAnalysisCoverage(
                status=CorporateFilingsCoverageStatus.COMPLETE,
                expected_documents=len(documents),
                analyzed_documents=len(draft.analyzed_document_ids),
                expected_chunks=sum(len(item.chunks) for item in documents),
                analyzed_chunks=len(draft.analyzed_chunk_ids),
                available_citations=sum(
                    len(item.citations) for item in documents
                ),
                cited_citations=len(cited_citations),
                unresolved_speaker_turns=sum(
                    1
                    for document in documents
                    for speaker in document.speakers
                    if speaker.role is FundamentalFilingSpeakerRole.UNKNOWN
                ),
            ),
            observed_facts=draft.observed_facts,
            management_claims=draft.management_claims,
            guidance=draft.guidance,
            analyst_concerns=draft.analyst_concerns,
            management_responses=draft.management_responses,
            risks=draft.risks,
            contradictions=draft.contradictions,
            missing_evidence=draft.missing_evidence,
            jhunjhunwala_style_interpretations=(
                draft.jhunjhunwala_style_interpretations
            ),
            limitations=draft.limitations,
            execution=CorporateFilingsAnalysisExecution(
                model_id=model_id,
                model_configuration_fingerprint=(
                    request.policy.model_configuration_fingerprint
                ),
                started_at=started_at,
                completed_at=completed_at,
                token_usage=CorporateFilingsTokenUsage(
                    input_tokens=0,
                    output_tokens=0,
                    model_calls=model_calls,
                ),
            ),
        )
        return validate_corporate_filings_analysis_response(request, result)

    def _llm_failure_result(
        self,
        request: CorporateFilingsAnalysisRequest,
        error: LLMError,
    ) -> CorporateFilingsAnalysisResult:
        if isinstance(error, LLMAuthenticationError):
            category = CorporateFilingsFailureCategory.PROVIDER_REJECTED
            message = "The LLM provider rejected its configured credential."
        elif isinstance(error, LLMConfigurationError):
            category = CorporateFilingsFailureCategory.INVALID_INPUT
            message = "The Corporate Filings Analyst LLM is not configured."
        elif isinstance(error, LLMRateLimitError):
            category = CorporateFilingsFailureCategory.PROVIDER_UNAVAILABLE
            message = "The LLM provider is temporarily rate limited."
        elif isinstance(error, LLMProviderUnavailableError):
            category = CorporateFilingsFailureCategory.PROVIDER_UNAVAILABLE
            message = "The LLM provider is temporarily unavailable."
        elif isinstance(error, LLMResponseValidationError):
            category = CorporateFilingsFailureCategory.INVALID_OUTPUT
            message = "The LLM response did not match the required schema."
        else:
            category = CorporateFilingsFailureCategory.UNKNOWN
            message = "The Corporate Filings Analyst could not complete safely."
        return self._failed_result(
            request,
            stage=CorporateFilingsFailureStage.LLM_INVOCATION,
            category=category,
            retryable=error.context.retryable,
            sanitized_message=message,
        )

    def _failed_result(
        self,
        request: CorporateFilingsAnalysisRequest,
        *,
        stage: CorporateFilingsFailureStage,
        category: CorporateFilingsFailureCategory,
        retryable: bool,
        sanitized_message: str,
    ) -> CorporateFilingsAnalysisResult:
        documents = request.bundle.documents
        result = CorporateFilingsAnalysisResult(
            analysis_id=f"{self.agent_id}:{request.request_fingerprint}",
            request_fingerprint=request.request_fingerprint,
            reuse_key_fingerprint=(
                request.reuse_key.reuse_key_fingerprint
            ),
            bundle_fingerprint=request.bundle.bundle_fingerprint,
            policy_fingerprint=request.policy.policy_fingerprint,
            status=CorporateFilingsAnalysisStatus.FAILED,
            grounding_status=CorporateFilingsGroundingStatus.INVALID_EVIDENCE,
            coverage=CorporateFilingsAnalysisCoverage(
                status=CorporateFilingsCoverageStatus.FAILED,
                expected_documents=len(documents),
                analyzed_documents=0,
                expected_chunks=sum(len(item.chunks) for item in documents),
                analyzed_chunks=0,
                available_citations=sum(
                    len(item.citations) for item in documents
                ),
                cited_citations=0,
                unresolved_speaker_turns=sum(
                    1
                    for document in documents
                    for speaker in document.speakers
                    if speaker.role is FundamentalFilingSpeakerRole.UNKNOWN
                ),
            ),
            failure=CorporateFilingsAnalysisFailure(
                stage=stage,
                category=category,
                retryable=retryable,
                sanitized_message=sanitized_message,
            ),
        )
        return validate_corporate_filings_analysis_response(request, result)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "corporate filings analyst clock requires timezone information"
            )
        return value


def build_corporate_filings_analyst_system_instructions(
    framework_skill: LoadedFundamentalFrameworkSkill,
) -> str:
    """Bind the verified doctrine to the narrower filing-analysis role."""

    _validate_framework_skill(framework_skill)
    return (
        f"{framework_skill.system_instructions}\n\n"
        f"{_CORPORATE_FILINGS_ANALYST_RULES}"
    )


def _validate_framework_skill(
    framework_skill: LoadedFundamentalFrameworkSkill,
) -> None:
    if not isinstance(framework_skill, LoadedFundamentalFrameworkSkill):
        raise ValueError(
            "corporate filings analyst framework skill must be loader verified"
        )
    contract = framework_skill.contract
    if contract.framework is not FundamentalFramework.JHUNJHUNWALA_STYLE:
        raise ValueError(
            "corporate filings analyst requires the Jhunjhunwala-style skill"
        )
    if contract.doctrine_id is None or framework_skill.doctrine is None:
        raise ValueError(
            "corporate filings analyst requires verified framework doctrine"
        )
    verified_hashes = (
        (
            framework_skill.shared_policy_content_sha256,
            _content_sha256(framework_skill.shared_policy),
        ),
        (
            framework_skill.doctrine_content_sha256,
            _content_sha256(framework_skill.doctrine),
        ),
        (
            framework_skill.prompt_content_sha256,
            _content_sha256(framework_skill.system_instructions),
        ),
    )
    if any(
        actual is None or not compare_digest(actual, expected)
        for actual, expected in verified_hashes
    ):
        raise ValueError(
            "corporate filings analyst framework skill hashes are invalid"
        )


def _content_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _serialize_request(request: CorporateFilingsAnalysisRequest) -> str:
    documents = request.bundle.documents
    payload = {
        "request_bindings": {
            "request_fingerprint": request.request_fingerprint,
            "reuse_key_fingerprint": (
                request.reuse_key.reuse_key_fingerprint
            ),
            "bundle_fingerprint": request.bundle.bundle_fingerprint,
            "policy_fingerprint": request.policy.policy_fingerprint,
            "filing_package_id": request.bundle.filing_package_id,
            "filing_package_fingerprint": (
                request.bundle.filing_package_fingerprint
            ),
        },
        "issuer": request.bundle.issuer.model_dump(mode="json"),
        "period": request.bundle.period.model_dump(mode="json"),
        "analysis_kind": request.bundle.analysis_kind.value,
        "required_analyzed_document_ids": sorted(
            item.artifact_id for item in documents
        ),
        "required_analyzed_chunk_ids": sorted(
            chunk.chunk_id for item in documents for chunk in item.chunks
        ),
        "allowed_runtime_limitations": sorted(
            _allowed_limitations(request)
        ),
        "documents": [
            item.model_dump(mode="json") for item in documents
        ],
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _build_user_message(*, context: str, feedback: str | None) -> str:
    correction = feedback or "None; this is the first attempt."
    return f"# Released evidence\n{context}\n\n# Validation feedback\n{correction}"


def _validate_draft(
    request: CorporateFilingsAnalysisRequest,
    draft: _CorporateFilingsAnalysisDraft,
) -> None:
    documents = request.bundle.documents
    expected_documents = tuple(sorted(item.artifact_id for item in documents))
    expected_chunks = tuple(
        sorted(chunk.chunk_id for item in documents for chunk in item.chunks)
    )
    if draft.analyzed_document_ids != expected_documents:
        raise ValueError(
            "The response did not confirm every released document."
        )
    if draft.analyzed_chunk_ids != expected_chunks:
        raise ValueError("The response did not confirm every released chunk.")
    if not set(draft.limitations).issubset(_allowed_limitations(request)):
        raise ValueError("The response introduced an unsupported limitation.")

    supporting = (
        draft.observed_facts
        + draft.management_claims
        + draft.guidance
        + draft.management_responses
        + draft.jhunjhunwala_style_interpretations
    )
    adverse = draft.analyst_concerns + draft.risks + draft.contradictions
    _validate_review_state(
        draft.supporting_evidence_review,
        supporting,
        missing=draft.missing_evidence,
        label="supporting",
    )
    _validate_review_state(
        draft.adverse_evidence_review,
        adverse,
        missing=draft.missing_evidence,
        label="adverse",
    )
    if not _all_findings(draft):
        raise ValueError("The response contains no grounded filing finding.")
    citations = {
        citation.citation_id: citation
        for document in documents
        for citation in document.citations
    }
    chunks = {
        chunk.chunk_id: chunk
        for document in documents
        for chunk in document.chunks
    }
    for finding in _all_findings(draft):
        citations_released_by_chunks = {
            citation_id
            for chunk_id in finding.chunk_ids
            if chunk_id in chunks
            for citation_id in chunks[chunk_id].citation_ids
        }
        if not set(finding.citation_ids).issubset(
            citations_released_by_chunks
        ):
            raise ValueError(
                "A finding citation is not released by its cited chunk."
            )
        if finding.speaker_turn_ids:
            cited_source_elements = {
                citations[citation_id].source_element_id
                for citation_id in finding.citation_ids
                if citation_id in citations
            }
            if not set(finding.speaker_turn_ids).issubset(
                cited_source_elements
            ):
                raise ValueError(
                    "A speaker-attributed finding lacks that speaker's "
                    "released citation."
                )


def _validate_review_state(
    status: str,
    findings: tuple[CorporateFilingsAnalysisFinding, ...],
    *,
    missing: tuple[CorporateFilingsMissingEvidence, ...],
    label: str,
) -> None:
    identified = status == "identified"
    if identified != bool(findings):
        raise ValueError(
            f"The {label} evidence review status is inconsistent."
        )
    if not identified and not missing:
        raise ValueError(
            f"The absent {label} evidence review requires missing evidence."
        )


def _all_findings(
    draft: _CorporateFilingsAnalysisDraft,
) -> tuple[CorporateFilingsAnalysisFinding, ...]:
    return (
        draft.observed_facts
        + draft.management_claims
        + draft.guidance
        + draft.analyst_concerns
        + draft.management_responses
        + draft.risks
        + draft.contradictions
        + draft.jhunjhunwala_style_interpretations
    )


def _allowed_limitations(
    request: CorporateFilingsAnalysisRequest,
) -> frozenset[str]:
    return _ALLOWED_RUNTIME_LIMITATIONS.union(
        limitation
        for document in request.bundle.documents
        for limitation in document.limitations
    )


def _grounding_feedback(
    request: CorporateFilingsAnalysisRequest,
    failure: str,
) -> str:
    documents = request.bundle.documents
    citation_ids = sorted(
        citation.citation_id
        for document in documents
        for citation in document.citations
    )
    chunk_ids = sorted(
        chunk.chunk_id for document in documents for chunk in document.chunks
    )
    speaker_ids = sorted(
        speaker.speaker_turn_id
        for document in documents
        for speaker in document.speakers
    )
    return (
        f"The previous response was rejected: {failure} "
        f"Use only citation IDs {citation_ids}, chunk IDs {chunk_ids}, "
        f"and speaker-turn IDs {speaker_ids}. Confirm every required "
        "document and chunk and return the assigned schema only."
    )


def _safe_grounding_failure(
    error: ValidationError | ValueError,
) -> tuple[str, CorporateFilingsFailureCategory]:
    message = str(error).lower()
    if any(
        token in message
        for token in (
            "citation",
            "chunk",
            "speaker",
            "document",
            "evidence reference",
        )
    ):
        return (
            "The model response referenced unsupported filing evidence.",
            CorporateFilingsFailureCategory.UNSUPPORTED_CITATION,
        )
    return (
        "The model response violated the grounded analysis contract.",
        CorporateFilingsFailureCategory.INVALID_OUTPUT,
    )
