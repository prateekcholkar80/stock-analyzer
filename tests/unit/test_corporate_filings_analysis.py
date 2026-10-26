from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import unittest

from pydantic import ValidationError

from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisChunk,
    CorporateFilingsAnalysisCoverage,
    CorporateFilingsAnalysisDocument,
    CorporateFilingsAnalysisExecution,
    CorporateFilingsAnalysisFailure,
    CorporateFilingsAnalysisFinding,
    CorporateFilingsAnalysisKind,
    CorporateFilingsAnalysisPolicy,
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisResult,
    CorporateFilingsAnalysisSpeakerReference,
    CorporateFilingsAnalysisStatus,
    CorporateFilingsCoverageStatus,
    CorporateFilingsFailureCategory,
    CorporateFilingsFailureStage,
    CorporateFilingsFindingConfidence,
    CorporateFilingsFindingKind,
    CorporateFilingsGroundingStatus,
    CorporateFilingsInterpretationPrinciple,
    CorporateFilingsMissingEvidence,
    CorporateFilingsQuarterlyBundle,
    CorporateFilingsSourceQuality,
    CorporateFilingsTokenUsage,
    validate_corporate_filings_analysis_response,
)
from app.models.fundamental_filing_evidence import (
    FundamentalFilingCitation,
    FundamentalFilingCitationElementType,
    FundamentalFilingParticipantAffiliation,
    FundamentalFilingRectangle,
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
    ProviderConnectionScope,
)


NOW = datetime(2026, 9, 14, 10, tzinfo=timezone.utc)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def filing_citation(
    *,
    artifact_id: str,
    content_sha256: str,
    source_element_id: str,
    excerpt: str,
) -> FundamentalFilingCitation:
    return FundamentalFilingCitation(
        artifact_id=artifact_id,
        content_sha256=content_sha256,
        page_number=2,
        element_type=FundamentalFilingCitationElementType.SPEAKER_TURN,
        source_element_id=source_element_id,
        exact_excerpt=excerpt,
        bounds=FundamentalFilingRectangle(
            x0=10.0,
            y0=20.0,
            x1=200.0,
            y1=40.0,
        ),
    )


def analysis_request(
    *,
    prompt_sha256: str | None = None,
) -> CorporateFilingsAnalysisRequest:
    connection = ProviderConnectionScope(
        tenant_id="tenant-1",
        provider_connection_id="tijori-1",
        provider="tijori",
    )
    issuer = FundamentalIssuerIdentity(
        exchange="NSE",
        symbol="SAGILITY",
        legal_name="Sagility India Limited",
        isin="INE0W2G01015",
        provider_company_id="242",
        provider_slug="sagility-india-limited",
    )
    artifact_fingerprint = digest("artifact")
    artifact_id = f"filing_artifact:{artifact_fingerprint}"
    content_sha256 = digest("pdf")
    management_citation = filing_citation(
        artifact_id=artifact_id,
        content_sha256=content_sha256,
        source_element_id="speaker.turn.1",
        excerpt="CEO: Demand remains resilient.",
    )
    analyst_citation = filing_citation(
        artifact_id=artifact_id,
        content_sha256=content_sha256,
        source_element_id="speaker.turn.2",
        excerpt="Analyst: What could pressure margins?",
    )
    citations = tuple(
        sorted(
            (management_citation, analyst_citation),
            key=lambda item: item.citation_id,
        )
    )
    speakers = (
        CorporateFilingsAnalysisSpeakerReference(
            speaker_turn_id="speaker.turn.1",
            participant_id="participant.ceo",
            speaker_name="Chief Executive Officer",
            role=FundamentalFilingSpeakerRole.MANAGEMENT,
            affiliation=FundamentalFilingParticipantAffiliation.ISSUER,
        ),
        CorporateFilingsAnalysisSpeakerReference(
            speaker_turn_id="speaker.turn.2",
            participant_id="participant.analyst",
            speaker_name="Research Analyst",
            role=FundamentalFilingSpeakerRole.ANALYST,
            affiliation=FundamentalFilingParticipantAffiliation.EXTERNAL,
        ),
    )
    chunk = CorporateFilingsAnalysisChunk(
        sequence=0,
        artifact_id=artifact_id,
        page_numbers=(2,),
        source_element_ids=("speaker.turn.1", "speaker.turn.2"),
        citation_ids=tuple(item.citation_id for item in citations),
        speaker_turn_ids=("speaker.turn.1", "speaker.turn.2"),
        text=(
            "CEO: Demand remains resilient.\n"
            "Analyst: What could pressure margins?"
        ),
    )
    document = CorporateFilingsAnalysisDocument(
        artifact_id=artifact_id,
        content_sha256=content_sha256,
        artifact_fingerprint=artifact_fingerprint,
        extraction_policy_fingerprint=digest("extraction-policy"),
        validation_policy_fingerprint=digest("validation-policy"),
        extraction_fingerprint=digest("extraction"),
        validation_fingerprint=digest("validation"),
        category=FundamentalFilingCategory.CONFERENCE_CALL,
        source_class=FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT,
        source_quality=CorporateFilingsSourceQuality.VERBATIM_TRANSCRIPT,
        extracted_at=NOW,
        citations=citations,
        speakers=speakers,
        chunks=(chunk,),
    )
    package_fingerprint = digest("filing-package")
    bundle = CorporateFilingsQuarterlyBundle(
        connection=connection,
        issuer=issuer,
        filing_package_id=f"filing_package:{package_fingerprint}",
        filing_package_fingerprint=package_fingerprint,
        period=FundamentalFilingPeriod(
            state=FundamentalFilingPeriodState.RESOLVED,
            period_type=FundamentalFilingPeriodType.QUARTERLY,
            source_label="Q3 FY2026",
            fiscal_year=2026,
            fiscal_quarter=3,
            period_start=date(2025, 10, 1),
            period_end=date(2025, 12, 31),
        ),
        analysis_kind=CorporateFilingsAnalysisKind.CONFERENCE_CALL,
        assembled_at=NOW,
        documents=(document,),
    )
    policy = CorporateFilingsAnalysisPolicy(
        policy_id="jarvis.corporate_filings_analysis",
        policy_version="1.0.0",
        prompt_sha256=prompt_sha256 or digest("prompt"),
        skill_id="jarvis.corporate_filings_analyst",
        skill_version="1.0.0",
        skill_sha256=digest("skill"),
        model_configuration_fingerprint=digest("model-config"),
        max_input_tokens=20_000,
        max_output_tokens=4_000,
        max_model_calls=2,
    )
    return CorporateFilingsAnalysisRequest(
        request_id="filing-analysis-1",
        operation_id="operation-1",
        bundle=bundle,
        policy=policy,
        requested_at=NOW,
        as_of=NOW + timedelta(minutes=1),
    )


def finding(
    request: CorporateFilingsAnalysisRequest,
    *,
    finding_id: str,
    kind: CorporateFilingsFindingKind,
    speaker_turn_ids: tuple[str, ...] = (),
    citation_index: int = 0,
    principle: CorporateFilingsInterpretationPrinciple | None = None,
) -> CorporateFilingsAnalysisFinding:
    document = request.bundle.documents[0]
    citation_ids = (document.citations[citation_index].citation_id,)
    if kind is CorporateFilingsFindingKind.CONTRADICTION:
        citation_ids = tuple(item.citation_id for item in document.citations)
    return CorporateFilingsAnalysisFinding(
        finding_id=finding_id,
        kind=kind,
        statement=f"Grounded {kind.value} statement.",
        confidence=CorporateFilingsFindingConfidence.HIGH,
        citation_ids=tuple(sorted(citation_ids)),
        chunk_ids=(document.chunks[0].chunk_id,),
        speaker_turn_ids=tuple(sorted(speaker_turn_ids)),
        principle=principle,
    )


def completed_result(
    request: CorporateFilingsAnalysisRequest,
) -> CorporateFilingsAnalysisResult:
    document = request.bundle.documents[0]
    management_citation_index = next(
        index
        for index, item in enumerate(document.citations)
        if item.source_element_id == "speaker.turn.1"
    )
    analyst_citation_index = next(
        index
        for index, item in enumerate(document.citations)
        if item.source_element_id == "speaker.turn.2"
    )
    observed = finding(
        request,
        finding_id="finding.observed.1",
        kind=CorporateFilingsFindingKind.OBSERVED_FACT,
        citation_index=management_citation_index,
    )
    claim = finding(
        request,
        finding_id="finding.claim.1",
        kind=CorporateFilingsFindingKind.MANAGEMENT_CLAIM,
        speaker_turn_ids=("speaker.turn.1",),
        citation_index=management_citation_index,
    )
    concern = finding(
        request,
        finding_id="finding.concern.1",
        kind=CorporateFilingsFindingKind.ANALYST_CONCERN,
        speaker_turn_ids=("speaker.turn.2",),
        citation_index=analyst_citation_index,
    )
    interpretation = finding(
        request,
        finding_id="finding.interpretation.1",
        kind=(
            CorporateFilingsFindingKind.JHUNJHUNWALA_STYLE_INTERPRETATION
        ),
        citation_index=management_citation_index,
        principle=CorporateFilingsInterpretationPrinciple.OPPORTUNITY_AND_RUNWAY,
    )
    used_citations = {
        citation_id
        for item in (observed, claim, concern, interpretation)
        for citation_id in item.citation_ids
    }
    return CorporateFilingsAnalysisResult(
        analysis_id="corporate-filing-analysis-1",
        request_fingerprint=request.request_fingerprint,
        reuse_key_fingerprint=request.reuse_key.reuse_key_fingerprint,
        bundle_fingerprint=request.bundle.bundle_fingerprint,
        policy_fingerprint=request.policy.policy_fingerprint,
        status=CorporateFilingsAnalysisStatus.COMPLETED,
        grounding_status=CorporateFilingsGroundingStatus.GROUNDED,
        coverage=CorporateFilingsAnalysisCoverage(
            status=CorporateFilingsCoverageStatus.COMPLETE,
            expected_documents=1,
            analyzed_documents=1,
            expected_chunks=1,
            analyzed_chunks=1,
            available_citations=len(document.citations),
            cited_citations=len(used_citations),
            unresolved_speaker_turns=0,
        ),
        observed_facts=(observed,),
        management_claims=(claim,),
        analyst_concerns=(concern,),
        jhunjhunwala_style_interpretations=(interpretation,),
        missing_evidence=(
            CorporateFilingsMissingEvidence(
                missing_id="missing.capital_allocation",
                question="No capital-allocation update was identified.",
                searched_chunk_ids=(document.chunks[0].chunk_id,),
            ),
        ),
        limitations=("Management statements are not independently verified.",),
        execution=CorporateFilingsAnalysisExecution(
            model_id="provider/model",
            model_configuration_fingerprint=(
                request.policy.model_configuration_fingerprint
            ),
            started_at=NOW + timedelta(seconds=1),
            completed_at=NOW + timedelta(seconds=3),
            token_usage=CorporateFilingsTokenUsage(
                input_tokens=1_000,
                output_tokens=500,
                model_calls=1,
            ),
        ),
    )


class CorporateFilingsAnalysisContractTests(unittest.TestCase):
    def test_completed_conference_call_result_is_immutable_and_grounded(self):
        request = analysis_request()
        result = completed_result(request)

        validated = validate_corporate_filings_analysis_response(
            request,
            result,
        )

        self.assertIs(validated, result)
        self.assertTrue(result.releaseable)
        self.assertEqual(result.execution.latency_ms, 2_000)
        self.assertEqual(result.execution.token_usage.total_tokens, 1_500)
        self.assertEqual(len(result.result_fingerprint), 64)
        with self.assertRaises(ValidationError):
            result.status = CorporateFilingsAnalysisStatus.FAILED

    def test_fingerprints_are_reproducible_and_bind_policy_changes(self):
        first = analysis_request()
        repeated = analysis_request()
        changed = analysis_request(prompt_sha256=digest("revised-prompt"))

        self.assertEqual(first.request_fingerprint, repeated.request_fingerprint)
        self.assertEqual(
            first.reuse_key.reuse_key_fingerprint,
            repeated.reuse_key.reuse_key_fingerprint,
        )
        self.assertNotEqual(first.request_fingerprint, changed.request_fingerprint)
        self.assertNotEqual(
            first.reuse_key.reuse_key_fingerprint,
            changed.reuse_key.reuse_key_fingerprint,
        )

    def test_document_rejects_unknown_chunk_citation(self):
        request = analysis_request()
        document = request.bundle.documents[0]
        bad_chunk = document.chunks[0].model_copy(
            update={"citation_ids": (f"filing_citation:{digest('unknown')}",)}
        )

        with self.assertRaisesRegex(
            ValidationError,
            "unknown document evidence",
        ):
            CorporateFilingsAnalysisDocument(
                **document.model_dump(
                    exclude={"chunks"},
                    exclude_computed_fields=True,
                ),
                chunks=(bad_chunk,),
            )

    def test_bundle_requires_exact_document_set_for_analysis_kind(self):
        request = analysis_request()
        bundle = request.bundle

        with self.assertRaisesRegex(ValidationError, "document set is incomplete"):
            CorporateFilingsQuarterlyBundle(
                **bundle.model_dump(
                    exclude={"analysis_kind"},
                    exclude_computed_fields=True,
                ),
                analysis_kind=CorporateFilingsAnalysisKind.EARNINGS_RELEASE,
            )

    def test_response_rejects_unknown_citation_and_chunk(self):
        request = analysis_request()
        result = completed_result(request)
        escaped = result.observed_facts[0].model_copy(
            update={
                "citation_ids": (f"filing_citation:{digest('escaped')}",),
                "chunk_ids": (f"filing_chunk:{digest('escaped')}",),
            }
        )
        invalid = result.model_copy(update={"observed_facts": (escaped,)})

        with self.assertRaisesRegex(ValueError, "unsupported evidence references"):
            validate_corporate_filings_analysis_response(request, invalid)

    def test_response_rejects_management_claim_from_analyst(self):
        request = analysis_request()
        result = completed_result(request)
        invalid_claim = result.management_claims[0].model_copy(
            update={"speaker_turn_ids": ("speaker.turn.2",)}
        )
        invalid = result.model_copy(update={"management_claims": (invalid_claim,)})

        with self.assertRaisesRegex(ValueError, "non-management speaker"):
            validate_corporate_filings_analysis_response(request, invalid)

    def test_response_rejects_uncited_conference_call_management(self):
        request = analysis_request()
        result = completed_result(request)
        invalid_claim = result.management_claims[0].model_copy(
            update={"speaker_turn_ids": ()}
        )
        invalid = result.model_copy(update={"management_claims": (invalid_claim,)})

        with self.assertRaisesRegex(ValueError, "require a speaker"):
            validate_corporate_filings_analysis_response(request, invalid)

    def test_response_rejects_binding_budget_and_coverage_changes(self):
        request = analysis_request()
        result = completed_result(request)

        wrong_binding = result.model_copy(
            update={"request_fingerprint": digest("wrong-request")}
        )
        with self.assertRaisesRegex(ValueError, "does not match its request"):
            validate_corporate_filings_analysis_response(request, wrong_binding)

        excessive_usage = result.execution.model_copy(
            update={
                "token_usage": CorporateFilingsTokenUsage(
                    input_tokens=20_001,
                    output_tokens=500,
                    model_calls=1,
                )
            }
        )
        excessive = result.model_copy(update={"execution": excessive_usage})
        with self.assertRaisesRegex(ValueError, "input-token budget"):
            validate_corporate_filings_analysis_response(request, excessive)

        wrong_coverage = result.model_copy(
            update={
                "coverage": result.coverage.model_copy(
                    update={"available_citations": 1}
                )
            }
        )
        with self.assertRaisesRegex(ValueError, "deterministic counts"):
            validate_corporate_filings_analysis_response(request, wrong_coverage)

    def test_abstained_and_failed_results_cannot_release_findings(self):
        request = analysis_request()
        base = completed_result(request)
        empty_collections = {
            field_name: ()
            for field_name in (
                "observed_facts",
                "management_claims",
                "guidance",
                "analyst_concerns",
                "management_responses",
                "risks",
                "contradictions",
                "jhunjhunwala_style_interpretations",
            )
        }
        failure = CorporateFilingsAnalysisFailure(
            stage=CorporateFilingsFailureStage.PREFLIGHT,
            category=CorporateFilingsFailureCategory.INSUFFICIENT_EVIDENCE,
            retryable=False,
            sanitized_message="Released conference-call evidence is incomplete.",
        )
        abstained = CorporateFilingsAnalysisResult(
            **base.model_dump(
                exclude={
                    *empty_collections,
                    "status",
                    "grounding_status",
                    "failure",
                },
                exclude_computed_fields=True,
            ),
            **empty_collections,
            status=CorporateFilingsAnalysisStatus.ABSTAINED,
            grounding_status=(
                CorporateFilingsGroundingStatus.INSUFFICIENT_EVIDENCE
            ),
            failure=failure,
        )

        self.assertFalse(abstained.releaseable)
        with self.assertRaisesRegex(ValidationError, "cannot contain findings"):
            CorporateFilingsAnalysisResult(
                **abstained.model_dump(
                    exclude={"observed_facts"},
                    exclude_computed_fields=True,
                ),
                observed_facts=base.observed_facts,
            )

    def test_strict_contract_rejects_extra_fields_and_naive_timestamps(self):
        request = analysis_request()
        payload = request.model_dump(exclude_computed_fields=True)
        payload["secret"] = "must-not-cross-boundary"
        with self.assertRaises(ValidationError):
            CorporateFilingsAnalysisRequest.model_validate(payload)

        payload.pop("secret")
        payload["as_of"] = datetime(2026, 9, 14, 10)
        with self.assertRaisesRegex(ValidationError, "timezone"):
            CorporateFilingsAnalysisRequest.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
