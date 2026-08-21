import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from tests.unit._debate_fixtures import (
    build_approved_technical_result,
    build_market_series,
)

from app.exceptions import StorageConflictError
from app.models.debate import BullBearArgument, DebateSide, DebateVerdict
from app.models.signals import SignalDirection, SignalStrength
from app.models.storage import (
    DebateRunQuery,
    StoredDebateRun,
    debate_run_summary,
    debate_signal_signature,
    payload_fingerprint,
    stored_debate_run,
)
from app.orchestration.debate_orchestrator import (
    DebateOrchestrator,
    DebateOrchestratorConfig,
)
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.in_memory import InMemoryJarvisStorage


class _StubSideAgent:
    def __init__(self, agent_id, side, config_fingerprint):
        self.agent_id = agent_id
        self._side = side
        self.configuration_fingerprint = config_fingerprint

    def generate_argument(
        self,
        *,
        profile,
        transcript_so_far,
        round_number,
        technical_submission_id,
        precedent=(),
    ):
        evidence_id = profile.snapshot.evidence[0].evidence_id
        rebuts = (
            f"bull:{round_number}"
            if self._side is DebateSide.BEAR
            else None
        )
        return BullBearArgument(
            argument_id=f"{self._side.value}:{round_number}",
            side=self._side,
            round_number=round_number,
            thesis=f"{self._side.value} thesis {round_number}",
            evidence_citations=(evidence_id,),
            rebuts_argument_id=rebuts,
            model_id="stub-model",
            generated_at=datetime.now(UTC),
        )


class _StubJudgeAgent:
    agent_id = "stub.judge.v1"
    configuration_fingerprint = "b" * 64

    def render_verdict(
        self,
        *,
        profile,
        transcript,
        technical_submission_id,
    ):
        evidence_id = profile.snapshot.evidence[0].evidence_id
        return DebateVerdict(
            verdict_id=f"stub-verdict:{technical_submission_id}",
            judge_model_id="stub-model",
            winner=SignalDirection.BULLISH,
            confidence_percentage=80.0,
            decisive_evidence_ids=(evidence_id,),
            bull_case_summary="Stub bull summary.",
            bear_case_summary="Stub bear summary.",
            rationale="Stub rationale.",
            generated_at=datetime.now(UTC),
        )


def _build_debate_result(market_series=None):
    technical_result = build_approved_technical_result(market_series)
    profile = technical_result.submission.profile
    orchestrator = DebateOrchestrator(
        bull_agent=_StubSideAgent("stub.bull.v1", DebateSide.BULL, "a" * 64),
        bear_agent=_StubSideAgent("stub.bear.v1", DebateSide.BEAR, "c" * 64),
        judge_agent=_StubJudgeAgent(),
        config=DebateOrchestratorConfig(max_rounds=1),
    )
    result = orchestrator.run_debate(technical_result)
    return result, profile


class DebateSignalSignatureTests(unittest.TestCase):
    def test_signature_matches_moderate_or_strong_non_neutral_evidence(self):
        technical_result = build_approved_technical_result()
        profile = technical_result.submission.profile

        expected = tuple(
            sorted(
                {
                    f"{item.category.value}:{item.direction.value}"
                    for item in profile.snapshot.evidence
                    if item.strength is not SignalStrength.WEAK
                    and item.direction is not SignalDirection.NEUTRAL
                }
            )
        )

        self.assertEqual(debate_signal_signature(profile), expected)

    def test_signature_is_deterministic(self):
        technical_result = build_approved_technical_result()
        profile = technical_result.submission.profile

        self.assertEqual(
            debate_signal_signature(profile),
            debate_signal_signature(profile),
        )


class StoredDebateRunTests(unittest.TestCase):
    def test_round_trips_from_result_and_profile(self):
        result, profile = _build_debate_result()

        stored = stored_debate_run(
            result,
            profile,
            stored_at=datetime.now(UTC),
        )

        self.assertEqual(
            stored.technical_fingerprint,
            result.submission.input_fingerprint,
        )
        self.assertEqual(stored.result_fingerprint, payload_fingerprint(result))
        self.assertEqual(stored.signature, debate_signal_signature(profile))
        self.assertEqual(stored.exchange, profile.snapshot.exchange)

    def test_rejects_mismatched_profile(self):
        result, _profile = _build_debate_result()
        other_result, other_profile = _build_debate_result(
            build_market_series(count=90)
        )
        self.assertNotEqual(
            payload_fingerprint(other_profile),
            result.submission.input_fingerprint,
        )

        with self.assertRaisesRegex(ValueError, "must match the profile"):
            stored_debate_run(
                result,
                other_profile,
                stored_at=datetime.now(UTC),
            )

    def test_rejects_tampered_result_fingerprint(self):
        result, profile = _build_debate_result()
        stored = stored_debate_run(result, profile, stored_at=datetime.now(UTC))

        with self.assertRaisesRegex(ValidationError, "fingerprint must match"):
            StoredDebateRun.model_validate(
                stored.model_dump()
                | {"result_fingerprint": "0" * 64}
            )

    def test_debate_run_summary_reflects_verdict_and_identity(self):
        result, profile = _build_debate_result()
        stored = stored_debate_run(result, profile, stored_at=datetime.now(UTC))

        summary = debate_run_summary(stored)

        self.assertEqual(summary.winner, SignalDirection.BULLISH)
        self.assertEqual(summary.confidence_percentage, 80.0)
        self.assertEqual(summary.round_count, 1)
        self.assertEqual(summary.symbol, profile.snapshot.symbol)
        self.assertEqual(summary.signature, stored.signature)


class InMemoryDebateRunStorageTests(unittest.TestCase):
    def setUp(self):
        self.storage = InMemoryJarvisStorage()
        self.archive = ResearchArchiveService(self.storage)
        self.result, self.profile = _build_debate_result()
        self.stored_at = datetime.now(UTC)

    def test_save_and_get_round_trip(self):
        stored = stored_debate_run(
            self.result,
            self.profile,
            stored_at=self.stored_at,
        )
        saved = self.storage.save_debate_run(stored)
        fetched = self.storage.get_debate_run(saved.run_id)
        self.assertEqual(fetched, saved)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.storage.get_debate_run("does-not-exist"))

    def test_repeated_save_is_idempotent(self):
        stored = stored_debate_run(
            self.result,
            self.profile,
            stored_at=self.stored_at,
        )
        first = self.storage.save_debate_run(stored)
        second = self.storage.save_debate_run(stored)
        self.assertEqual(first, second)

    def test_conflicting_save_rejected(self):
        stored = stored_debate_run(
            self.result,
            self.profile,
            stored_at=self.stored_at,
        )
        self.storage.save_debate_run(stored)
        other_result, other_profile = _build_debate_result(
            build_market_series(count=90)
        )
        conflicting = stored_debate_run(
            other_result,
            other_profile,
            stored_at=self.stored_at,
            run_id=stored.run_id,
        )
        with self.assertRaises(StorageConflictError):
            self.storage.save_debate_run(conflicting)

    def test_delete_reports_existence(self):
        stored = stored_debate_run(
            self.result,
            self.profile,
            stored_at=self.stored_at,
        )
        self.storage.save_debate_run(stored)
        self.assertTrue(self.storage.delete_debate_run(stored.run_id))
        self.assertFalse(self.storage.delete_debate_run(stored.run_id))
        self.assertIsNone(self.storage.get_debate_run(stored.run_id))

    def test_list_filters_by_winner_and_paginates(self):
        first = stored_debate_run(
            self.result,
            self.profile,
            stored_at=self.stored_at,
            run_id="debate-1",
        )
        bearish_submission = self.result.submission.model_copy(
            update={
                "verdict": self.result.submission.verdict.model_copy(
                    update={"winner": SignalDirection.BEARISH}
                )
            }
        )
        bearish_result = self.result.model_copy(
            update={"submission": bearish_submission}
        )
        second = stored_debate_run(
            bearish_result,
            self.profile,
            stored_at=self.stored_at + timedelta(minutes=1),
            run_id="debate-2",
        )
        self.storage.save_debate_run(first)
        self.storage.save_debate_run(second)

        bullish_only = self.storage.list_debate_runs(
            DebateRunQuery(winner=SignalDirection.BULLISH)
        )
        self.assertEqual(len(bullish_only), 1)
        self.assertEqual(bullish_only[0].run_id, "debate-1")

        newest_first = self.storage.list_debate_runs()
        self.assertEqual(
            [summary.run_id for summary in newest_first],
            ["debate-2", "debate-1"],
        )

    def test_find_similar_debate_runs_ranks_by_shared_tokens(self):
        base = stored_debate_run(
            self.result,
            self.profile,
            stored_at=self.stored_at,
            run_id="debate-base",
        )
        high_overlap = base.model_copy(
            update={
                "run_id": "debate-high",
                "signature": ("trend:bullish", "momentum:bullish"),
            }
        )
        low_overlap = base.model_copy(
            update={
                "run_id": "debate-low",
                "signature": ("trend:bullish",),
            }
        )
        no_overlap = base.model_copy(
            update={
                "run_id": "debate-none",
                "signature": ("volume:bearish",),
            }
        )
        for stored in (high_overlap, low_overlap, no_overlap):
            self.storage.save_debate_run(stored)

        results = self.storage.find_similar_debate_runs(
            ("trend:bullish", "momentum:bullish"),
            exclude_run_id=None,
            limit=5,
        )

        self.assertEqual(
            [summary.run_id for summary in results],
            ["debate-high", "debate-low"],
        )

    def test_find_similar_debate_runs_excludes_given_run_id(self):
        stored = stored_debate_run(
            self.result,
            self.profile,
            stored_at=self.stored_at,
            run_id="debate-self",
        ).model_copy(update={"signature": ("trend:bullish",)})
        self.storage.save_debate_run(stored)

        results = self.storage.find_similar_debate_runs(
            ("trend:bullish",),
            exclude_run_id="debate-self",
        )

        self.assertEqual(results, ())


if __name__ == "__main__":
    unittest.main()
