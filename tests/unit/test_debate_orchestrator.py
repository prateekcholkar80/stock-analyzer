import unittest
from datetime import UTC, datetime

from tests.unit._debate_fixtures import (
    build_approved_technical_result,
    build_precedent_summary,
)

from app.exceptions import AgentSubmissionRejectedError, LLMResponseValidationError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
)
from app.models.debate import (
    BullBearArgument,
    DebateSide,
    DebateTerminationReason,
    DebateVerdict,
)
from app.models.signals import SignalDirection
from app.orchestration.debate_orchestrator import (
    SUBMISSION_AGENT_ID,
    DebateOrchestrator,
    DebateOrchestratorConfig,
    JarvisDebateJudge,
)


class StubSideAgent:
    def __init__(self, agent_id, side, citation_fn, config_fingerprint):
        self.agent_id = agent_id
        self._side = side
        self._citation_fn = citation_fn
        self._config_fingerprint = config_fingerprint
        self.received_precedent = []

    @property
    def configuration_fingerprint(self):
        return self._config_fingerprint

    def generate_argument(
        self,
        *,
        profile,
        transcript_so_far,
        round_number,
        technical_submission_id,
        precedent=(),
    ):
        self.received_precedent.append(precedent)
        rebuts = f"bull:{round_number}" if self._side is DebateSide.BEAR else None
        return BullBearArgument(
            argument_id=f"{self._side.value}:{round_number}",
            side=self._side,
            round_number=round_number,
            thesis=f"{self._side.value} thesis round {round_number}",
            evidence_citations=self._citation_fn(round_number),
            rebuts_argument_id=rebuts,
            model_id="stub-model",
            generated_at=datetime.now(UTC),
        )


class StubArchive:
    def __init__(self, precedent=()):
        self.precedent = precedent
        self.find_similar_calls = []
        self.archived_results = []

    def find_similar_debate_runs(self, profile, *, exclude_run_id=None, limit=5):
        self.find_similar_calls.append((profile, exclude_run_id, limit))
        return self.precedent

    def archive_debate_run(self, result, profile, *, run_id=None, stored_at=None):
        self.archived_results.append(result)
        return None


class FailingBullAgent(StubSideAgent):
    def __init__(self, citation_fn, config_fingerprint, fail_from_round):
        super().__init__(
            "stub.failing_bull.v1",
            DebateSide.BULL,
            citation_fn,
            config_fingerprint,
        )
        self._fail_from_round = fail_from_round

    def generate_argument(self, *, round_number, **kwargs):
        if round_number >= self._fail_from_round:
            raise LLMResponseValidationError("stub bull failure")
        return super().generate_argument(round_number=round_number, **kwargs)


class StubJudgeAgent:
    agent_id = "stub.judge.v1"

    def __init__(self, config_fingerprint, *, winner=None, confidence=50.0):
        self._config_fingerprint = config_fingerprint
        self._winner = winner or SignalDirection.NEUTRAL
        self._confidence = confidence

    @property
    def configuration_fingerprint(self):
        return self._config_fingerprint

    def render_verdict(
        self,
        *,
        profile,
        transcript,
        technical_submission_id,
    ):
        flat_arguments = [
            argument
            for debate_round in transcript.rounds
            for argument in (
                debate_round.bull_argument,
                debate_round.bear_argument,
            )
        ]
        cited = flat_arguments[-1].evidence_citations[0]
        return DebateVerdict(
            verdict_id=f"stub-verdict:{technical_submission_id}",
            judge_model_id="stub-model",
            winner=self._winner,
            confidence_percentage=self._confidence,
            decisive_evidence_ids=(cited,),
            bull_case_summary="Stub bull summary.",
            bear_case_summary="Stub bear summary.",
            rationale="Stub verdict.",
            generated_at=datetime.now(UTC),
        )


class InvalidAgent:
    pass


def _rejected_technical_result():
    result = build_approved_technical_result()
    rejected_decision = JarvisJudgeDecision(
        decision_id=result.decision.decision_id,
        judge_id=result.decision.judge_id,
        submission_id=result.submission.submission_id,
        verdict=JarvisJudgeVerdict.REJECTED,
        decided_at=result.decision.decided_at,
        passed_checks=result.decision.passed_checks,
        reasons=("test rejection",),
    )
    return AgenticSwingAnalysisResult(
        orchestrator_id=result.orchestrator_id,
        submission=result.submission,
        decision=rejected_decision,
    )


class DebateOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.technical_result = build_approved_technical_result()
        self.evidence_ids = [
            item.evidence_id
            for item in self.technical_result.submission.profile.snapshot.evidence
        ]

    def _agents(
        self,
        *,
        bull_citation_fn,
        bear_citation_fn=None,
        judge_winner=None,
        judge_confidence=50.0,
    ):
        bear_citation_fn = bear_citation_fn or bull_citation_fn
        bull_agent = StubSideAgent(
            "stub.bull.v1",
            DebateSide.BULL,
            bull_citation_fn,
            "a" * 64,
        )
        bear_agent = StubSideAgent(
            "stub.bear.v1",
            DebateSide.BEAR,
            bear_citation_fn,
            "c" * 64,
        )
        judge_agent = StubJudgeAgent(
            "b" * 64,
            winner=judge_winner,
            confidence=judge_confidence,
        )
        return bull_agent, bear_agent, judge_agent

    def test_completes_all_rounds_reaches_max_rounds_reached(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=2),
        )

        result = orchestrator.run_debate(self.technical_result)

        self.assertTrue(result.decision.accepted)
        self.assertEqual(
            result.submission.transcript.termination_reason,
            DebateTerminationReason.MAX_ROUNDS_REACHED,
        )
        self.assertEqual(result.submission.transcript.round_count, 2)

    def test_stall_detected_before_max_rounds(self):
        constant = lambda round_number: (self.evidence_ids[0],)
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=constant,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=5),
        )

        result = orchestrator.run_debate(self.technical_result)

        self.assertEqual(
            result.submission.transcript.termination_reason,
            DebateTerminationReason.STALL_DETECTED,
        )
        self.assertEqual(result.submission.transcript.round_count, 2)

    def test_stall_forces_indecisive_verdict_even_if_judge_picked_a_side(self):
        constant = lambda round_number: (self.evidence_ids[0],)
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=constant,
            judge_winner=SignalDirection.BULLISH,
            judge_confidence=90.0,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=5),
        )

        result = orchestrator.run_debate(self.technical_result)

        verdict = result.submission.verdict
        self.assertEqual(
            result.submission.transcript.termination_reason,
            DebateTerminationReason.STALL_DETECTED,
        )
        self.assertEqual(verdict.winner, SignalDirection.NEUTRAL)
        self.assertEqual(verdict.confidence_percentage, 0.0)
        self.assertIn("Forced indecisive", verdict.rationale)

    def test_low_confidence_verdict_forced_indecisive_at_max_rounds(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
            judge_winner=SignalDirection.BEARISH,
            judge_confidence=40.0,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(
                max_rounds=2,
                indecisive_confidence_threshold=55.0,
            ),
        )

        result = orchestrator.run_debate(self.technical_result)

        verdict = result.submission.verdict
        self.assertEqual(
            result.submission.transcript.termination_reason,
            DebateTerminationReason.MAX_ROUNDS_REACHED,
        )
        self.assertEqual(verdict.winner, SignalDirection.NEUTRAL)
        self.assertEqual(verdict.confidence_percentage, 40.0)
        self.assertIn("Forced indecisive", verdict.rationale)

    def test_high_confidence_directional_verdict_preserved_at_max_rounds(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
            judge_winner=SignalDirection.BULLISH,
            judge_confidence=80.0,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(
                max_rounds=2,
                indecisive_confidence_threshold=55.0,
            ),
        )

        result = orchestrator.run_debate(self.technical_result)

        verdict = result.submission.verdict
        self.assertEqual(verdict.winner, SignalDirection.BULLISH)
        self.assertEqual(verdict.confidence_percentage, 80.0)
        self.assertNotIn("Forced indecisive", verdict.rationale)

    def test_agent_failure_after_one_round_terminates_debate(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent = FailingBullAgent(
            varying,
            "a" * 64,
            fail_from_round=2,
        )
        _, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
            judge_winner=SignalDirection.BEARISH,
            judge_confidence=90.0,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=5),
        )

        result = orchestrator.run_debate(self.technical_result)

        verdict = result.submission.verdict
        self.assertEqual(verdict.winner, SignalDirection.NEUTRAL)
        self.assertEqual(verdict.confidence_percentage, 0.0)
        self.assertIn("Forced indecisive", verdict.rationale)
        self.assertEqual(
            result.submission.transcript.termination_reason,
            DebateTerminationReason.AGENT_FAILURE,
        )
        self.assertEqual(result.submission.transcript.round_count, 1)

    def test_agent_failure_before_any_round_raises(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent = FailingBullAgent(
            varying,
            "a" * 64,
            fail_from_round=1,
        )
        _, bear_agent, judge_agent = self._agents(bull_citation_fn=varying)
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=5),
        )

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "could not complete a single round",
        ):
            orchestrator.run_debate(self.technical_result)

    def test_rejects_unapproved_technical_result(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
        )

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "Jarvis-approved technical submission",
        ):
            orchestrator.run_debate(_rejected_technical_result())

    def test_structural_judge_rejects_mismatched_configuration(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
        )
        mismatched_judge = JarvisDebateJudge(
            expected_agent_id=SUBMISSION_AGENT_ID,
            expected_configuration_fingerprint="f" * 64,
            max_rounds=2,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            structural_judge=mismatched_judge,
            config=DebateOrchestratorConfig(max_rounds=2),
        )

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "Jarvis rejected debate submission",
        ):
            orchestrator.require_approved_debate_verdict(self.technical_result)

    def test_contract_validation_rejects_invalid_bull_agent(self):
        with self.assertRaisesRegex(ValueError, "generate_argument"):
            DebateOrchestrator(bull_agent=InvalidAgent())

    def test_contract_validation_rejects_invalid_judge_agent(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, _ = self._agents(bull_citation_fn=varying)
        with self.assertRaisesRegex(ValueError, "render_verdict"):
            DebateOrchestrator(
                bull_agent=bull_agent,
                bear_agent=bear_agent,
                judge_agent=InvalidAgent(),
            )

    def test_precedent_fetched_and_passed_to_agents(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
        )
        precedent = (build_precedent_summary(),)
        archive = StubArchive(precedent=precedent)
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=2),
            archive=archive,
        )

        orchestrator.run_debate(self.technical_result)

        self.assertEqual(len(archive.find_similar_calls), 1)
        self.assertEqual(
            archive.find_similar_calls[0][0],
            self.technical_result.submission.profile,
        )
        self.assertTrue(
            all(
                received == precedent
                for received in bull_agent.received_precedent
            )
        )
        self.assertTrue(
            all(
                received == precedent
                for received in bear_agent.received_precedent
            )
        )

    def test_accepted_result_is_archived(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
        )
        archive = StubArchive()
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            config=DebateOrchestratorConfig(max_rounds=2),
            archive=archive,
        )

        result = orchestrator.run_debate(self.technical_result)

        self.assertTrue(result.decision.accepted)
        self.assertEqual(len(archive.archived_results), 1)
        self.assertEqual(archive.archived_results[0], result)

    def test_rejected_result_is_not_archived(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
        )
        archive = StubArchive()
        mismatched_judge = JarvisDebateJudge(
            expected_agent_id=SUBMISSION_AGENT_ID,
            expected_configuration_fingerprint="f" * 64,
            max_rounds=2,
        )
        orchestrator = DebateOrchestrator(
            bull_agent=bull_agent,
            bear_agent=bear_agent,
            judge_agent=judge_agent,
            structural_judge=mismatched_judge,
            config=DebateOrchestratorConfig(max_rounds=2),
            archive=archive,
        )

        result = orchestrator.run_debate(self.technical_result)

        self.assertFalse(result.decision.accepted)
        self.assertEqual(archive.archived_results, [])

    def test_archive_rejects_object_without_debate_archive_methods(self):
        varying = lambda round_number: (
            self.evidence_ids[(round_number - 1) % len(self.evidence_ids)],
        )
        bull_agent, bear_agent, judge_agent = self._agents(
            bull_citation_fn=varying,
        )
        with self.assertRaisesRegex(ValueError, "DebateArchive"):
            DebateOrchestrator(
                bull_agent=bull_agent,
                bear_agent=bear_agent,
                judge_agent=judge_agent,
                archive=object(),
            )


if __name__ == "__main__":
    unittest.main()
