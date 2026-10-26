from datetime import UTC, datetime, timedelta
from decimal import Decimal
import unittest

from pydantic import ValidationError

from app.models.fundamental_framework_explanation import (
    FundamentalFrameworkExplanationBinding,
    FundamentalFrameworkExplanationCitation,
    FundamentalFrameworkExplanationClaim,
    FundamentalFrameworkExplanationClaimKind,
    FundamentalFrameworkExplanationEvidenceState,
    FundamentalFrameworkExplanationRequest,
    FundamentalFrameworkExplanationResult,
    FundamentalFrameworkExplanationStatus,
)
from app.models.fundamental_framework_skill import (
    FundamentalFrameworkSkillAbstentionReason,
    FundamentalFrameworkSkillCitationNamespace,
)
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicabilityStatus,
)
from app.models.fundamental_scoring import (
    FundamentalScoreGrade,
    FundamentalScoreResultStatus,
    FundamentalScorecardStance,
)


_NOW = datetime(2026, 9, 13, 8, 0, tzinfo=UTC)
_HASH = "1" * 64


def _binding(**overrides: object) -> FundamentalFrameworkExplanationBinding:
    values: dict[str, object] = {
        "package_id": f"fundamental_package:{_HASH}",
        "manifest_fingerprint": _HASH,
        "sector_profile_fingerprint": "2" * 64,
        "mandatory_assessment_fingerprint": "3" * 64,
        "applicability_fingerprint": "4" * 64,
        "scorecard_fingerprint": "5" * 64,
        "framework_result_fingerprint": "6" * 64,
        "framework": FundamentalFramework.JHUNJHUNWALA_STYLE,
        "selected_primary": True,
        "applicability_status": (
            FundamentalFrameworkApplicabilityStatus.ELIGIBLE
        ),
        "score_status": FundamentalScoreResultStatus.CALCULATED,
        "score": Decimal("72.50"),
        "grade": FundamentalScoreGrade.FAVOURABLE,
        "family_coverage_bps": 10_000,
        "stance": FundamentalScorecardStance.HEALTHY,
        "review_required": False,
        "score_policy_id": (
            "jarvis.framework_score.jhunjhunwala_style"
        ),
        "score_policy_version": "1.0.0",
        "framework_skill_id": (
            "jarvis.fundamental_framework.jhunjhunwala_style.v1"
        ),
        "framework_skill_version": "1.0.0",
        "framework_skill_prompt_sha256": "7" * 64,
        "doctrine_id": (
            "jarvis.fundamental_framework_doctrine."
            "jhunjhunwala_style.v1"
        ),
        "doctrine_version": "1.0.0",
    }
    values.update(overrides)
    return FundamentalFrameworkExplanationBinding(**values)


def _citation(
    citation_id: str,
    namespace: FundamentalFrameworkSkillCitationNamespace,
) -> FundamentalFrameworkExplanationCitation:
    return FundamentalFrameworkExplanationCitation(
        citation_id=citation_id,
        namespace=namespace,
        evidence_fingerprint="8" * 64,
    )


_MANDATORY_ID = "fundamental.finding:earnings_quality:cash_conversion"
_METRIC_ID = "fundamental.metric:revenue_cagr"
_MANDATORY_CITATION = _citation(
    _MANDATORY_ID,
    FundamentalFrameworkSkillCitationNamespace.MANDATORY_FINDING,
)
_METRIC_CITATION = _citation(
    _METRIC_ID,
    FundamentalFrameworkSkillCitationNamespace.FUNDAMENTAL_METRIC,
)


def _request(**overrides: object) -> FundamentalFrameworkExplanationRequest:
    values: dict[str, object] = {
        "request_id": "framework-explanation-request-1",
        "binding": _binding(),
        "evidence_state": (
            FundamentalFrameworkExplanationEvidenceState.CURRENT
        ),
        "allowed_citations": (
            _MANDATORY_CITATION,
            _METRIC_CITATION,
        ),
        "limitations": (
            "Management quality is unavailable from current evidence.",
        ),
        "as_of": _NOW,
        "package_expires_at": _NOW + timedelta(days=10),
    }
    values.update(overrides)
    return FundamentalFrameworkExplanationRequest(**values)


def _claim(
    claim_id: str,
    kind: FundamentalFrameworkExplanationClaimKind,
    citation_id: str = _METRIC_ID,
) -> FundamentalFrameworkExplanationClaim:
    return FundamentalFrameworkExplanationClaim(
        claim_id=claim_id,
        kind=kind,
        statement="A bounded released observation.",
        citation_ids=(citation_id,),
    )


def _result(
    request: FundamentalFrameworkExplanationRequest,
    **overrides: object,
) -> FundamentalFrameworkExplanationResult:
    values: dict[str, object] = {
        "explanation_id": (
            "fundamental.framework_explanation:"
            f"{request.request_fingerprint}"
        ),
        "request_fingerprint": request.request_fingerprint,
        "binding": request.binding,
        "status": FundamentalFrameworkExplanationStatus.EXPLAINED,
        "claims": (
            _claim(
                "claim-growth",
                FundamentalFrameworkExplanationClaimKind.SCORED_EVIDENCE,
            ),
        ),
        "limitations": request.limitations,
        "model_id": "test-model",
        "generated_at": request.as_of + timedelta(seconds=1),
    }
    values.update(overrides)
    return FundamentalFrameworkExplanationResult(**values)


def _abstained(
    request: FundamentalFrameworkExplanationRequest,
) -> FundamentalFrameworkExplanationResult:
    return _result(
        request,
        status=FundamentalFrameworkExplanationStatus.ABSTAINED,
        abstention_reason=request.abstention_reason,
        claims=(),
        limitations=request.limitations,
        model_id=None,
    )


class FundamentalFrameworkExplanationTests(unittest.TestCase):
    def test_current_selected_cited_jhunjhunwala_request_can_explain(
        self,
    ) -> None:
        request = _request()
        result = _result(request)

        self.assertIs(request.can_invoke_llm, True)
        self.assertIsNone(request.abstention_reason)
        self.assertIs(result.validate_against(request), result)
        self.assertEqual(len(request.request_fingerprint), 64)
        self.assertEqual(len(result.result_fingerprint), 64)

    def test_request_and_result_are_immutable(self) -> None:
        request = _request()
        result = _result(request)
        with self.assertRaisesRegex(ValidationError, "frozen"):
            request.evidence_state = (
                FundamentalFrameworkExplanationEvidenceState.INVALID
            )
        with self.assertRaisesRegex(ValidationError, "frozen"):
            result.model_id = "another-model"

    def test_jhunjhunwala_preflight_states_abstain_without_llm(self) -> None:
        cases = (
            (
                FundamentalFrameworkExplanationEvidenceState.MISSING,
                FundamentalFrameworkSkillAbstentionReason.MISSING_EVIDENCE,
            ),
            (
                FundamentalFrameworkExplanationEvidenceState.INVALID,
                FundamentalFrameworkSkillAbstentionReason.INVALID_EVIDENCE,
            ),
            (
                FundamentalFrameworkExplanationEvidenceState.PACKAGE_MISMATCH,
                FundamentalFrameworkSkillAbstentionReason.PACKAGE_MISMATCH,
            ),
            (
                FundamentalFrameworkExplanationEvidenceState.POLICY_MISMATCH,
                FundamentalFrameworkSkillAbstentionReason.POLICY_MISMATCH,
            ),
            (
                FundamentalFrameworkExplanationEvidenceState.MISSING_CITATION,
                FundamentalFrameworkSkillAbstentionReason.MISSING_CITATION,
            ),
            (
                FundamentalFrameworkExplanationEvidenceState.UNSUPPORTED_CLAIM,
                FundamentalFrameworkSkillAbstentionReason.UNSUPPORTED_CLAIM,
            ),
        )
        for state, reason in cases:
            with self.subTest(state=state):
                request = _request(
                    evidence_state=state,
                    limitations=("The requested evidence is unavailable.",),
                )
                result = _abstained(request)
                self.assertIs(request.can_invoke_llm, False)
                self.assertIs(request.abstention_reason, reason)
                self.assertIs(result.validate_against(request), result)

    def test_stale_package_abstains_before_model_invocation(self) -> None:
        request = _request(package_expires_at=_NOW)
        result = _abstained(request)

        self.assertIs(
            request.abstention_reason,
            FundamentalFrameworkSkillAbstentionReason.STALE_EVIDENCE,
        )
        self.assertIs(result.validate_against(request), result)

    def test_unselected_framework_abstains(self) -> None:
        request = _request(binding=_binding(selected_primary=False))
        self.assertIs(
            request.abstention_reason,
            FundamentalFrameworkSkillAbstentionReason.FRAMEWORK_NOT_SELECTED,
        )

    def test_inapplicable_or_incomplete_framework_abstains(self) -> None:
        cases = (
            (
                _binding(
                    applicability_status=(
                        FundamentalFrameworkApplicabilityStatus.INELIGIBLE
                    ),
                    score_status=FundamentalScoreResultStatus.NOT_APPLICABLE,
                    score=None,
                    grade=None,
                    family_coverage_bps=0,
                    stance=(
                        FundamentalScorecardStance.INSUFFICIENT_EVIDENCE
                    ),
                ),
                FundamentalFrameworkSkillAbstentionReason.
                FRAMEWORK_INAPPLICABLE,
            ),
            (
                _binding(
                    applicability_status=(
                        FundamentalFrameworkApplicabilityStatus.PARTIAL
                    ),
                    score_status=FundamentalScoreResultStatus.WITHHELD,
                    score=None,
                    grade=None,
                    family_coverage_bps=6_000,
                    stance=(
                        FundamentalScorecardStance.INSUFFICIENT_EVIDENCE
                    ),
                ),
                FundamentalFrameworkSkillAbstentionReason.
                INSUFFICIENT_COVERAGE,
            ),
        )
        for binding, reason in cases:
            with self.subTest(reason=reason):
                request = _request(binding=binding)
                self.assertIs(request.abstention_reason, reason)

    def test_empty_citation_allowlist_abstains(self) -> None:
        request = _request(allowed_citations=())
        self.assertIs(
            request.abstention_reason,
            FundamentalFrameworkSkillAbstentionReason.MISSING_CITATION,
        )

    def test_mandatory_failure_must_be_cited_before_scored_evidence(
        self,
    ) -> None:
        request = _request(
            binding=_binding(
                stance=FundamentalScorecardStance.WEAK,
                review_required=True,
            ),
            mandatory_failure_citation_ids=(_MANDATORY_ID,),
        )
        scored_first = _result(
            request,
            claims=(
                _claim(
                    "claim-growth",
                    FundamentalFrameworkExplanationClaimKind.SCORED_EVIDENCE,
                ),
                _claim(
                    "claim-cash-conversion",
                    FundamentalFrameworkExplanationClaimKind.MANDATORY_FINDING,
                    _MANDATORY_ID,
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "must precede"):
            scored_first.validate_against(request)

        mandatory_first = _result(
            request,
            claims=tuple(reversed(scored_first.claims)),
        )
        self.assertIs(mandatory_first.validate_against(request), mandatory_first)

    def test_mandatory_warning_cannot_be_omitted(self) -> None:
        request = _request(
            binding=_binding(
                stance=FundamentalScorecardStance.CAUTION,
                review_required=True,
            ),
            mandatory_warning_citation_ids=(_MANDATORY_ID,),
        )
        with self.assertRaisesRegex(ValueError, "omitted a mandatory finding"):
            _result(request).validate_against(request)

    def test_claim_cannot_escape_citation_allowlist_or_namespace(self) -> None:
        unknown = _result(
            _request(),
            claims=(
                _claim(
                    "claim-unknown",
                    FundamentalFrameworkExplanationClaimKind.SCORED_EVIDENCE,
                    "fundamental.metric:unknown",
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "unreleased evidence"):
            unknown.validate_against(_request())

        wrong_namespace = _result(
            _request(),
            claims=(
                _claim(
                    "claim-wrong-namespace",
                    FundamentalFrameworkExplanationClaimKind.SCORED_EVIDENCE,
                    _MANDATORY_ID,
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "wrong citation namespace"):
            wrong_namespace.validate_against(_request())

    def test_unreleased_limitation_is_rejected(self) -> None:
        result = _result(
            _request(),
            limitations=("Invented qualitative conclusion.",),
        )
        with self.assertRaisesRegex(ValueError, "unreleased limitation"):
            result.validate_against(_request())

    def test_result_cannot_change_skill_policy_or_package_binding(self) -> None:
        request = _request()
        changed = _result(
            request,
            binding=_binding(framework_result_fingerprint="9" * 64),
        )
        with self.assertRaisesRegex(ValueError, "immutable request"):
            changed.validate_against(request)

    def test_score_grade_coverage_and_stance_are_immutable_binding(self) -> None:
        request = _request()
        changed = _result(
            request,
            binding=_binding(
                score=Decimal("90.00"),
                grade=FundamentalScoreGrade.STRONG,
            ),
        )
        with self.assertRaisesRegex(ValueError, "immutable request"):
            changed.validate_against(request)

    def test_unreleased_score_cannot_carry_numeric_result(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "cannot carry score or grade",
        ):
            _binding(score_status=FundamentalScoreResultStatus.WITHHELD)

    def test_mandatory_failures_cover_known_jhunjhunwala_risk_patterns(
        self,
    ) -> None:
        finding_ids = (
            "fundamental.finding:earnings_quality:cash_conversion",
            "fundamental.finding:earnings_quality:working_capital_absorption",
            "fundamental.finding:financial_health:promoter_pledge",
            "fundamental.finding:forensic_risk:auditor_going_concern",
        )
        for finding_id in finding_ids:
            with self.subTest(finding_id=finding_id):
                citation = _citation(
                    finding_id,
                    FundamentalFrameworkSkillCitationNamespace.
                    MANDATORY_FINDING,
                )
                request = _request(
                    binding=_binding(
                        stance=FundamentalScorecardStance.WEAK,
                        review_required=True,
                    ),
                    allowed_citations=tuple(
                        sorted(
                            (_METRIC_CITATION, citation),
                            key=lambda item: item.citation_id,
                        )
                    ),
                    mandatory_failure_citation_ids=(finding_id,),
                )
                result = _result(
                    request,
                    claims=(
                        _claim(
                            "claim-mandatory-risk",
                            FundamentalFrameworkExplanationClaimKind.
                            MANDATORY_FINDING,
                            finding_id,
                        ),
                        _claim(
                            "claim-growth",
                            FundamentalFrameworkExplanationClaimKind.
                            SCORED_EVIDENCE,
                        ),
                    ),
                )
                self.assertIs(result.validate_against(request), result)

    def test_binding_rejects_cross_framework_and_incomplete_doctrine(self) -> None:
        cases = (
            (
                {"score_policy_id": "jarvis.framework_score.lynch"},
                "score policy",
            ),
            (
                {
                    "framework_skill_id": (
                        "jarvis.fundamental_framework.lynch.v1"
                    )
                },
                "skill does not match",
            ),
            ({"doctrine_version": None}, "doctrine identity is incomplete"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValidationError, message):
                    _binding(**changes)

    def test_abstention_cannot_claim_model_invocation_or_company_claims(
        self,
    ) -> None:
        request = _request(
            evidence_state=(
                FundamentalFrameworkExplanationEvidenceState.UNSUPPORTED_CLAIM
            )
        )
        with self.assertRaises(ValidationError):
            _result(
                request,
                status=FundamentalFrameworkExplanationStatus.ABSTAINED,
                abstention_reason=request.abstention_reason,
                model_id="model-should-not-run",
            )


if __name__ == "__main__":
    unittest.main()
