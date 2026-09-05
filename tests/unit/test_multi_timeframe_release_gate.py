import unittest

from pydantic import ValidationError

from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import JarvisJudgeDecision
from app.models.multi_timeframe_evidence import (
    MultiTimeframeEvidencePackage,
    MultiTimeframeEvidenceReview,
    multi_timeframe_evidence_fingerprint,
)
from app.models.timeframes import MultiTimeframeTechnicalAnalysis
from app.models.timeframes import multi_timeframe_technical_fingerprint
from app.orchestration.agent_orchestrator import (
    AgentOrchestrator,
    JarvisSwingJudge,
)
from app.orchestration.timeframe_technical_orchestrator import (
    ParallelTimeframeTechnicalOrchestrator,
)
from app.use_cases.build_multi_timeframe_evidence import (
    BuildMultiTimeframeEvidence,
)
from tests.unit.test_build_multi_timeframe_evidence import (
    _cyclical_timeframes,
)


class _FixedTechnicalRunner:
    def __init__(self, analysis):
        self.analysis = analysis
        self.completed = False

    def execute(self, timeframes):
        self.completed = True
        return self.analysis


class _OrderingEvidenceBuilder:
    def __init__(self, runner, package):
        self.runner = runner
        self.package = package
        self.called_after_completion = False

    def execute(self, analysis):
        self.called_after_completion = self.runner.completed
        return self.package


class _LegacyJudge:
    def review(self, submission, series):
        raise AssertionError("single-timeframe review should not be called")


class MultiTimeframeReleaseGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.timeframes = _cyclical_timeframes()
        cls.analysis = ParallelTimeframeTechnicalOrchestrator().execute(
            cls.timeframes
        )
        cls.package = BuildMultiTimeframeEvidence().execute(cls.analysis)

    def test_existing_judge_releases_only_complete_validated_package(self):
        orchestrator = AgentOrchestrator()

        result = orchestrator.run_multi_timeframe_analysis(self.timeframes)

        self.assertIsInstance(result, MultiTimeframeEvidenceReview)
        self.assertTrue(result.decision.accepted)
        self.assertIs(result.released_evidence, result.evidence_package)
        self.assertEqual(
            result.decision.judge_id,
            "jarvis.swing_judge.v1",
        )
        self.assertEqual(
            result.decision.submission_id,
            result.evidence_package.package_fingerprint,
        )
        self.assertIn(
            "daily_technical_submission_approved",
            result.decision.passed_checks,
        )
        self.assertIn(
            "weekly_technical_submission_approved",
            result.decision.passed_checks,
        )
        self.assertIn(
            "daily_and_weekly_accumulation_verified",
            result.decision.passed_checks,
        )
        self.assertIn(
            "daily_and_weekly_cpr_verified",
            result.decision.passed_checks,
        )

    def test_builder_runs_only_after_parallel_result_is_complete(self):
        runner = _FixedTechnicalRunner(self.analysis)
        builder = _OrderingEvidenceBuilder(runner, self.package)

        result = AgentOrchestrator().run_multi_timeframe_analysis(
            self.timeframes,
            timeframe_orchestrator=runner,
            evidence_builder=builder,
        )

        self.assertTrue(builder.called_after_completion)
        self.assertTrue(result.decision.accepted)

    def test_released_package_preserves_both_submissions_unchanged(self):
        released = AgentOrchestrator().require_released_multi_timeframe_evidence(
            self.timeframes
        )

        self.assertEqual(
            [item.evidence for item in released.daily.evidence],
            released.technical_analysis.daily_submission.profile.snapshot.evidence,
        )
        self.assertEqual(
            [item.evidence for item in released.weekly.evidence],
            released.technical_analysis.weekly_submission.profile.snapshot.evidence,
        )

    def test_cannot_fabricate_release_without_all_gate_checks(self):
        decision = AgentOrchestrator().judge.review_multi_timeframe(
            self.package,
            self.analysis,
        )
        incomplete_decision = JarvisJudgeDecision(
            **(
                decision.model_dump(exclude_computed_fields=True)
                | {"passed_checks": ("multi_timeframe_package_schema_valid",)}
            )
        )

        with self.assertRaisesRegex(ValidationError, "missing gate checks"):
            MultiTimeframeEvidenceReview(
                evidence_package=self.package,
                decision=incomplete_decision,
            )

    def test_cannot_release_package_without_cpr_verification_check(self):
        decision = AgentOrchestrator().judge.review_multi_timeframe(
            self.package,
            self.analysis,
        )
        without_cpr_check = tuple(
            check
            for check in decision.passed_checks
            if check != "daily_and_weekly_cpr_verified"
        )
        incomplete_decision = JarvisJudgeDecision(
            **(
                decision.model_dump(exclude_computed_fields=True)
                | {"passed_checks": without_cpr_check}
            )
        )

        with self.assertRaisesRegex(ValidationError, "missing gate checks"):
            MultiTimeframeEvidenceReview(
                evidence_package=self.package,
                decision=incomplete_decision,
            )

    def test_judge_rejects_package_for_another_assignment(self):
        other_timeframes = _cyclical_timeframes(week_count=81)
        other_analysis = ParallelTimeframeTechnicalOrchestrator().execute(
            other_timeframes
        )
        judge = AgentOrchestrator().judge

        decision = judge.review_multi_timeframe(
            self.package,
            other_analysis,
        )
        review = MultiTimeframeEvidenceReview(
            evidence_package=self.package,
            decision=decision,
        )

        self.assertFalse(decision.accepted)
        self.assertIsNone(review.released_evidence)
        self.assertIn(
            "evidence package does not match the assigned analysis",
            decision.reasons,
        )

    def test_require_release_blocks_a_rejected_assignment(self):
        other_analysis = ParallelTimeframeTechnicalOrchestrator().execute(
            _cyclical_timeframes(week_count=81)
        )
        runner = _FixedTechnicalRunner(other_analysis)
        builder = _OrderingEvidenceBuilder(runner, self.package)

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "does not match the assigned analysis",
        ):
            AgentOrchestrator().require_released_multi_timeframe_evidence(
                self.timeframes,
                timeframe_orchestrator=runner,
                evidence_builder=builder,
            )

    def test_judge_recomputes_validation_instead_of_trusting_receipt(self):
        forged_receipt = self.analysis.daily_validation.model_copy(
            update={"passed_checks": ("forged_check",)}
        )
        forged_analysis = MultiTimeframeTechnicalAnalysis(
            **(
                self.analysis.model_dump(exclude_computed_fields=True)
                | {"daily_validation": forged_receipt}
            )
        )
        original = self.package
        forged_package = MultiTimeframeEvidencePackage(
            technical_analysis=forged_analysis,
            daily=original.daily,
            weekly=original.weekly,
            package_fingerprint=multi_timeframe_evidence_fingerprint(
                forged_analysis,
                original.daily,
                original.weekly,
            ),
        )

        decision = AgentOrchestrator().judge.review_multi_timeframe(
            forged_package,
            forged_analysis,
        )

        self.assertFalse(decision.accepted)
        self.assertIn(
            "daily technical validation receipt is invalid",
            decision.reasons,
        )

    def test_judge_recomputes_accumulation_before_debate_release(self):
        forged_accumulation = self.analysis.weekly_accumulation.model_copy(
            update={"zones": ()}
        )
        forged_analysis = MultiTimeframeTechnicalAnalysis(
            **(
                self.analysis.model_dump(exclude_computed_fields=True)
                | {
                    "weekly_accumulation": forged_accumulation,
                    "combined_fingerprint": multi_timeframe_technical_fingerprint(
                        self.analysis.timeframes,
                        self.analysis.daily_submission,
                        self.analysis.weekly_submission,
                        self.analysis.daily_accumulation,
                        forged_accumulation,
                    ),
                }
            )
        )
        forged_package = BuildMultiTimeframeEvidence().execute(
            forged_analysis
        )

        decision = AgentOrchestrator().judge.review_multi_timeframe(
            forged_package,
            forged_analysis,
        )

        self.assertFalse(decision.accepted)
        self.assertIn(
            "weekly accumulation evidence does not match deterministic "
            "recalculation",
            decision.reasons,
        )

    def test_judge_recomputes_cpr_before_debate_release(self):
        forged_cpr = self.package.daily.cpr.model_copy(
            update={"calculation_fingerprint": "f" * 64}
        )
        forged_daily = self.package.daily.model_copy(
            update={"cpr": forged_cpr}
        )
        forged_package = MultiTimeframeEvidencePackage(
            technical_analysis=self.analysis,
            daily=forged_daily,
            weekly=self.package.weekly,
            package_fingerprint=multi_timeframe_evidence_fingerprint(
                self.analysis,
                forged_daily,
                self.package.weekly,
            ),
        )

        decision = AgentOrchestrator().judge.review_multi_timeframe(
            forged_package,
            self.analysis,
        )

        self.assertFalse(decision.accepted)
        self.assertIn(
            "daily CPR evidence does not match deterministic recalculation",
            decision.reasons,
        )

    def test_rejects_partial_or_invalid_pipeline_contracts(self):
        orchestrator = AgentOrchestrator()
        with self.assertRaisesRegex(ValueError, "swing timeframe series"):
            orchestrator.run_multi_timeframe_analysis(object())
        with self.assertRaisesRegex(ValueError, "provide execute"):
            orchestrator.run_multi_timeframe_analysis(
                self.timeframes,
                timeframe_orchestrator=object(),
            )

        agent = orchestrator.technical_agent
        legacy_orchestrator = AgentOrchestrator(
            technical_agent=agent,
            judge=_LegacyJudge(),
        )
        with self.assertRaisesRegex(
            ValueError,
            "review_multi_timeframe",
        ):
            legacy_orchestrator.run_multi_timeframe_analysis(
                self.timeframes,
            )

    def test_judge_requires_valid_package_and_assignment(self):
        judge = AgentOrchestrator().judge
        self.assertIsInstance(judge, JarvisSwingJudge)
        with self.assertRaisesRegex(ValueError, "evidence package"):
            judge.review_multi_timeframe(object(), self.analysis)
        with self.assertRaisesRegex(ValueError, "assigned analysis"):
            judge.review_multi_timeframe(self.package, object())


if __name__ == "__main__":
    unittest.main()
