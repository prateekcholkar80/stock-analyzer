from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
import unittest

from pydantic import ValidationError

from app.agents.corporate_filings_analyst_agent import (
    CorporateFilingsAnalystAgent,
)
from app.exceptions import LLMAuthenticationError
from app.fundamental_framework_skill_loader import (
    LoadedFundamentalFrameworkSkill,
)
from app.llm.gateway import StructuredGeneration
from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisChunk,
    CorporateFilingsAnalysisDocument,
    CorporateFilingsQuarterlyBundle,
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisStatus,
    CorporateFilingsFailureCategory,
    CorporateFilingsFailureStage,
    CorporateFilingsFindingKind,
)
from app.models.fundamental_framework_skill import (
    FundamentalFrameworkSkillContract,
    REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
    REQUIRED_FRAMEWORK_SKILL_CITATIONS,
    REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
)
from app.models.fundamental_frameworks import FundamentalFramework
from tests.unit.test_corporate_filings_analysis import (
    NOW,
    analysis_request,
    completed_result,
    digest,
)


def _framework_skill(
    framework: FundamentalFramework = (
        FundamentalFramework.JHUNJHUNWALA_STYLE
    ),
    *,
    with_doctrine: bool = True,
) -> LoadedFundamentalFrameworkSkill:
    contract_values: dict[str, object] = {
        "skill_id": f"jarvis.fundamental_framework.{framework.value}.v1",
        "framework": framework,
        "version": "1.0.0",
        "purpose": "Explain only released deterministic evidence.",
        "score_policy_id": f"jarvis.framework_score.{framework.value}",
        "score_policy_version": "1.0.0",
        "allowed_evidence": REQUIRED_FRAMEWORK_SKILL_EVIDENCE,
        "required_citations": REQUIRED_FRAMEWORK_SKILL_CITATIONS,
        "abstain_on": REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS,
    }
    doctrine = None
    doctrine_sha256 = None
    if with_doctrine:
        contract_values.update(
            {
                "doctrine_id": (
                    "jarvis.fundamental_framework_doctrine."
                    f"{framework.value}.v1"
                ),
                "doctrine_version": "1.0.0",
            }
        )
        doctrine = "# Verified doctrine\n\nPrefer scale with disciplined risk."
        doctrine_sha256 = digest(doctrine)
    contract = FundamentalFrameworkSkillContract(**contract_values)
    instructions = "# Verified framework skill\n\nUse released evidence only."
    shared_policy = "# Shared policy\n\nNever invent company facts."
    combined = "\n\n".join(
        value
        for value in (shared_policy, doctrine, instructions)
        if value is not None
    )
    return LoadedFundamentalFrameworkSkill(
        contract=contract,
        skill_name=f"{framework.value}-skill",
        description="Verified explanatory framework.",
        framework_instructions=instructions,
        shared_policy=shared_policy,
        doctrine=doctrine,
        skill_content_sha256=digest(instructions),
        shared_policy_content_sha256=digest(shared_policy),
        doctrine_content_sha256=doctrine_sha256,
        prompt_content_sha256=digest(combined),
    )


class _FakeStructuredGateway:
    def __init__(
        self,
        responses: list[dict[str, object]] | None = None,
        *,
        error: Exception | None = None,
        attempt_count: int = 1,
        fingerprint: str | None = None,
    ) -> None:
        self.configuration_fingerprint = fingerprint or digest("gateway")
        self._responses = list(responses or [])
        self._error = error
        self._attempt_count = attempt_count
        self.calls: list[dict[str, object]] = []

    def generate(self, *, system, messages, response_model):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "response_model": response_model,
            }
        )
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("unexpected extra model call")
        value = response_model.model_validate(self._responses.pop(0))
        return StructuredGeneration(
            value=value,
            provider="test-provider",
            model="test-model",
            attempt_count=self._attempt_count,
        )


def _draft_payload(request: CorporateFilingsAnalysisRequest) -> dict[str, object]:
    result = completed_result(request)
    return {
        "analyzed_document_ids": tuple(
            sorted(item.artifact_id for item in request.bundle.documents)
        ),
        "analyzed_chunk_ids": tuple(
            sorted(
                chunk.chunk_id
                for item in request.bundle.documents
                for chunk in item.chunks
            )
        ),
        "supporting_evidence_review": "identified",
        "adverse_evidence_review": "identified",
        "observed_facts": result.observed_facts,
        "management_claims": result.management_claims,
        "guidance": result.guidance,
        "analyst_concerns": result.analyst_concerns,
        "management_responses": result.management_responses,
        "risks": result.risks,
        "contradictions": result.contradictions,
        "missing_evidence": result.missing_evidence,
        "jhunjhunwala_style_interpretations": (
            result.jhunjhunwala_style_interpretations
        ),
        "limitations": result.limitations,
    }


def _bound_request(
    agent: CorporateFilingsAnalystAgent,
) -> CorporateFilingsAnalysisRequest:
    base = analysis_request()
    return CorporateFilingsAnalysisRequest(
        **base.model_dump(
            exclude={"policy"},
            exclude_computed_fields=True,
        ),
        policy=agent.build_analysis_policy(),
    )


class CorporateFilingsAnalystAgentTests(unittest.TestCase):
    def _agent(
        self,
        gateway: _FakeStructuredGateway,
        skill: LoadedFundamentalFrameworkSkill | None = None,
        *,
        max_input_tokens: int = 64_000,
        max_output_tokens: int = 4_000,
        max_model_calls: int = 2,
    ) -> tuple[CorporateFilingsAnalystAgent, LoadedFundamentalFrameworkSkill]:
        selected = skill or _framework_skill()
        return (
            CorporateFilingsAnalystAgent(
                gateway,
                framework_skill=selected,
                clock=lambda: NOW + timedelta(minutes=2),
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
                max_model_calls=max_model_calls,
            ),
            selected,
        )

    def test_requires_loader_verified_jhunjhunwala_skill_and_doctrine(self):
        gateway = _FakeStructuredGateway()

        with self.assertRaisesRegex(ValueError, "loader verified"):
            CorporateFilingsAnalystAgent(
                gateway,
                framework_skill=object(),
            )
        with self.assertRaisesRegex(ValueError, "Jhunjhunwala-style"):
            CorporateFilingsAnalystAgent(
                gateway,
                framework_skill=_framework_skill(FundamentalFramework.BUFFETT),
            )
        with self.assertRaisesRegex(ValueError, "verified framework doctrine"):
            CorporateFilingsAnalystAgent(
                gateway,
                framework_skill=_framework_skill(with_doctrine=False),
            )

    def test_rejects_tampered_loaded_skill_artifact(self):
        gateway = _FakeStructuredGateway()
        verified = _framework_skill()
        tampered = replace(
            verified,
            doctrine=f"{verified.doctrine}\nUnsupported policy mutation.",
        )

        with self.assertRaisesRegex(ValueError, "skill hashes are invalid"):
            CorporateFilingsAnalystAgent(
                gateway,
                framework_skill=tampered,
            )

    def test_builds_exact_immutable_policy_from_verified_runtime(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway)

        first = agent.build_analysis_policy()
        second = agent.build_analysis_policy()

        self.assertEqual(first, second)
        self.assertEqual(first.policy_id, "jarvis.corporate_filings_analysis")
        self.assertEqual(first.policy_version, "1.0.0")
        self.assertEqual(first.prompt_sha256, agent.prompt_sha256)
        self.assertEqual(first.skill_id, skill.contract.skill_id)
        self.assertEqual(first.skill_version, skill.contract.version)
        self.assertEqual(first.skill_sha256, skill.skill_content_sha256)
        self.assertEqual(
            first.model_configuration_fingerprint,
            gateway.configuration_fingerprint,
        )
        self.assertEqual(first.max_input_tokens, 64_000)
        self.assertEqual(first.max_output_tokens, 4_000)
        self.assertEqual(first.max_model_calls, 2)
        with self.assertRaises(ValidationError):
            first.max_model_calls = 99

    def test_policy_budget_is_validated_during_agent_construction(self):
        gateway = _FakeStructuredGateway()
        for overrides in (
            {"max_input_tokens": 0},
            {"max_output_tokens": 100_001},
            {"max_model_calls": 0},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, "policy budget"):
                    CorporateFilingsAnalystAgent(
                        gateway,
                        framework_skill=_framework_skill(),
                        **overrides,
                    )

    def test_success_is_bound_to_skill_prompt_model_and_released_context(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway)
        request = _bound_request(agent)
        gateway._responses.append(_draft_payload(request))

        result = agent.analyze(request)

        self.assertTrue(result.releaseable)
        self.assertEqual(result.status, CorporateFilingsAnalysisStatus.COMPLETED)
        self.assertEqual(result.execution.model_id, "test-model")
        self.assertEqual(result.execution.token_usage.model_calls, 1)
        self.assertEqual(len(agent.configuration_fingerprint), 64)
        call = gateway.calls[0]
        self.assertIn(skill.doctrine, call["system"])
        self.assertIn("Corporate Filings Analyst Runtime", call["system"])
        context = call["messages"][0]["content"]
        document = request.bundle.documents[0]
        self.assertIn(document.chunks[0].chunk_id, context)
        self.assertIn(document.citations[0].citation_id, context)
        self.assertIn(document.citations[0].exact_excerpt, context)
        self.assertNotIn(request.bundle.connection.tenant_id, context)
        self.assertNotIn(
            request.bundle.connection.provider_connection_id,
            context,
        )
        serialized = json.loads(context.split("# Released evidence\n", 1)[1].split(
            "\n\n# Validation feedback", 1
        )[0])
        self.assertNotIn("connection", serialized)

    def test_preflight_binding_mismatch_fails_without_calling_model(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway)
        request = _bound_request(agent)
        invalid_policy = request.policy.model_copy(
            update={"prompt_sha256": digest("different-prompt")}
        )
        request = request.model_copy(update={"policy": invalid_policy})

        result = agent.analyze(request)

        self.assertEqual(result.status, CorporateFilingsAnalysisStatus.FAILED)
        self.assertEqual(result.failure.stage, CorporateFilingsFailureStage.PREFLIGHT)
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.INVALID_INPUT,
        )
        self.assertEqual(gateway.calls, [])

    def test_preflight_rejects_caller_authored_budget_changes(self):
        gateway = _FakeStructuredGateway()
        agent, _ = self._agent(gateway)
        request = _bound_request(agent)

        for updates in (
            {"max_input_tokens": 63_999},
            {"max_output_tokens": 3_999},
            {"max_model_calls": 1},
            {"permit_trade_decision": True},
        ):
            with self.subTest(updates=updates):
                altered = request.model_copy(
                    update={
                        "policy": request.policy.model_copy(update=updates)
                    }
                )

                result = agent.analyze(altered)

                self.assertFalse(result.releaseable)
                self.assertEqual(
                    result.failure.stage,
                    CorporateFilingsFailureStage.PREFLIGHT,
                )
        self.assertEqual(gateway.calls, [])

    def test_unknown_citation_is_retried_then_corrected(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway)
        request = _bound_request(agent)
        invalid = _draft_payload(request)
        bad_finding = invalid["observed_facts"][0].model_copy(
            update={"citation_ids": (f"filing_citation:{digest('unknown')}",)}
        )
        invalid["observed_facts"] = (bad_finding,)
        gateway._responses.extend((invalid, _draft_payload(request)))

        result = agent.analyze(request)

        self.assertTrue(result.releaseable)
        self.assertEqual(len(gateway.calls), 2)
        feedback = gateway.calls[1]["messages"][0]["content"]
        self.assertIn("unsupported filing evidence", feedback)
        self.assertIn(request.bundle.documents[0].chunks[0].chunk_id, feedback)

    def test_repeated_unsupported_citation_fails_closed(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway)
        request = _bound_request(agent)
        invalid = _draft_payload(request)
        escaped = invalid["risks"] = (
            invalid["observed_facts"][0].model_copy(
                update={
                    "finding_id": "finding.risk.escaped",
                    "kind": CorporateFilingsFindingKind.RISK,
                    "citation_ids": (
                        f"filing_citation:{digest('escaped')}",
                    ),
                }
            ),
        )
        self.assertTrue(escaped)
        gateway._responses.extend((invalid, invalid))

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.stage,
            CorporateFilingsFailureStage.GROUNDING_VALIDATION,
        )
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.UNSUPPORTED_CITATION,
        )
        self.assertEqual(result.risks, ())

    def test_incomplete_chunk_coverage_never_releases_partial_analysis(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway, max_model_calls=1)
        request = _bound_request(agent)
        invalid = _draft_payload(request)
        invalid["analyzed_chunk_ids"] = ()
        gateway._responses.append(invalid)

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(result.coverage.analyzed_chunks, 0)
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.INVALID_OUTPUT,
        )

    def test_adverse_absence_requires_explicit_missing_evidence(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway, max_model_calls=1)
        request = _bound_request(agent)
        invalid = _draft_payload(request)
        invalid["adverse_evidence_review"] = "not_identified"
        invalid["analyst_concerns"] = ()
        invalid["risks"] = ()
        invalid["contradictions"] = ()
        invalid["missing_evidence"] = ()
        gateway._responses.append(invalid)

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.INVALID_OUTPUT,
        )

    def test_management_claim_cannot_be_attributed_to_analyst(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway, max_model_calls=1)
        request = _bound_request(agent)
        invalid = _draft_payload(request)
        invalid_claim = invalid["management_claims"][0].model_copy(
            update={"speaker_turn_ids": ("speaker.turn.2",)}
        )
        invalid["management_claims"] = (invalid_claim,)
        gateway._responses.append(invalid)

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.UNSUPPORTED_CITATION,
        )

    def test_management_claim_must_cite_its_released_speaker_turn(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway, max_model_calls=1)
        request = _bound_request(agent)
        invalid = _draft_payload(request)
        document = request.bundle.documents[0]
        analyst_citation = next(
            item
            for item in document.citations
            if item.source_element_id == "speaker.turn.2"
        )
        invalid_claim = invalid["management_claims"][0].model_copy(
            update={"citation_ids": (analyst_citation.citation_id,)}
        )
        invalid["management_claims"] = (invalid_claim,)
        gateway._responses.append(invalid)

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.UNSUPPORTED_CITATION,
        )

    def test_finding_citation_must_belong_to_its_released_chunk(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway, max_model_calls=1)
        request = _bound_request(agent)
        document = request.bundle.documents[0]
        citations = {
            item.source_element_id: item for item in document.citations
        }
        chunks = tuple(
            CorporateFilingsAnalysisChunk(
                sequence=sequence,
                artifact_id=document.artifact_id,
                page_numbers=(citation.page_number,),
                source_element_ids=(source_element_id,),
                citation_ids=(citation.citation_id,),
                speaker_turn_ids=(source_element_id,),
                text=citation.exact_excerpt,
            )
            for sequence, (source_element_id, citation) in enumerate(
                sorted(citations.items())
            )
        )
        split_document = CorporateFilingsAnalysisDocument(
            **document.model_dump(
                exclude={"chunks"},
                exclude_computed_fields=True,
            ),
            chunks=chunks,
        )
        split_bundle = CorporateFilingsQuarterlyBundle(
            **request.bundle.model_dump(
                exclude={"documents"},
                exclude_computed_fields=True,
            ),
            documents=(split_document,),
        )
        request = CorporateFilingsAnalysisRequest(
            **request.model_dump(
                exclude={"bundle", "policy"},
                exclude_computed_fields=True,
            ),
            bundle=split_bundle,
            policy=request.policy,
        )
        invalid = _draft_payload(request)
        gateway._responses.append(invalid)

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.UNSUPPORTED_CITATION,
        )

    def test_trade_or_score_fields_are_rejected_by_structured_boundary(self):
        gateway = _FakeStructuredGateway()
        agent, skill = self._agent(gateway)
        request = _bound_request(agent)
        invalid = _draft_payload(request)
        invalid["trade_decision"] = "BUY"
        invalid["framework_score"] = 99
        gateway._responses.append(invalid)

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.stage,
            CorporateFilingsFailureStage.OUTPUT_VALIDATION,
        )
        self.assertEqual(len(gateway.calls), 1)

    def test_provider_authentication_failure_is_sanitized(self):
        gateway = _FakeStructuredGateway(
            error=LLMAuthenticationError(
                "credential sk-sensitive-value was rejected",
                provider="test-provider",
            )
        )
        agent, skill = self._agent(gateway)
        request = _bound_request(agent)

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.category,
            CorporateFilingsFailureCategory.PROVIDER_REJECTED,
        )
        self.assertNotIn("sk-sensitive-value", result.failure.sanitized_message)

    def test_gateway_attempt_count_cannot_exceed_request_budget(self):
        gateway = _FakeStructuredGateway(attempt_count=2)
        agent, skill = self._agent(gateway, max_model_calls=1)
        request = _bound_request(agent)
        gateway._responses.append(_draft_payload(request))

        result = agent.analyze(request)

        self.assertFalse(result.releaseable)
        self.assertEqual(
            result.failure.stage,
            CorporateFilingsFailureStage.LLM_INVOCATION,
        )
        self.assertIn("model-call budget", result.failure.sanitized_message)


if __name__ == "__main__":
    unittest.main()
