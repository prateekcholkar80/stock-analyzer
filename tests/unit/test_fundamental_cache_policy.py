from datetime import timedelta
import unittest

from app.services.fundamental_cache_policy import (
    FundamentalCachePolicy,
    FundamentalLoadAction,
)
from app.storage.adapters.fundamental_in_memory import (
    InMemoryFundamentalSnapshotRepository,
)
from tests.unit.test_fundamental_in_memory_repository import (
    MutableClock,
    build_entry,
)


class FundamentalCachePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stored = build_entry()
        self.clock = MutableClock(self.stored.stored_at + timedelta(hours=1))
        self.repository = InMemoryFundamentalSnapshotRepository(
            clock=self.clock
        )
        self.policy = FundamentalCachePolicy(
            self.repository,
            clock=self.clock,
        )

    def test_reuses_fresh_scoped_snapshot(self):
        self.repository.save_fundamental_snapshot(self.stored)

        decision = self.policy.decide(self.stored.request)

        self.assertEqual(decision.action, FundamentalLoadAction.USE_CACHE)
        self.assertEqual(decision.reason, "cache_hit")
        self.assertEqual(decision.cached_snapshot, self.stored)

    def test_requires_provider_when_cache_is_missing(self):
        decision = self.policy.decide(self.stored.request)

        self.assertEqual(
            decision.action,
            FundamentalLoadAction.RETRIEVE_PROVIDER,
        )
        self.assertEqual(decision.reason, "cache_miss")
        self.assertIsNone(decision.cached_snapshot)

    def test_requires_provider_and_purges_snapshot_at_inclusive_expiry(self):
        self.repository.save_fundamental_snapshot(self.stored)
        self.clock.now = self.stored.expires_at

        decision = self.policy.decide(self.stored.request)

        self.assertEqual(
            decision.action,
            FundamentalLoadAction.RETRIEVE_PROVIDER,
        )
        self.assertEqual(decision.reason, "cache_miss")
        self.assertIsNone(decision.cached_snapshot)

    def test_explicit_refresh_bypasses_a_fresh_cache_entry(self):
        self.repository.save_fundamental_snapshot(self.stored)

        decision = self.policy.decide(
            self.stored.request,
            refresh_requested=True,
        )

        self.assertEqual(
            decision.action,
            FundamentalLoadAction.RETRIEVE_PROVIDER,
        )
        self.assertEqual(decision.reason, "explicit_refresh")
        self.assertIsNone(decision.cached_snapshot)

    def test_different_request_scope_cannot_reuse_cached_evidence(self):
        self.repository.save_fundamental_snapshot(self.stored)
        other_scope = build_entry(max_periods=8)

        decision = self.policy.decide(other_scope.request)

        self.assertEqual(
            decision.action,
            FundamentalLoadAction.RETRIEVE_PROVIDER,
        )
        self.assertEqual(decision.reason, "cache_miss")

    def test_rejects_invalid_dependencies_and_refresh_flag(self):
        with self.assertRaises(TypeError):
            FundamentalCachePolicy("not-a-repository")
        with self.assertRaises(TypeError):
            self.policy.decide(
                self.stored.request,
                refresh_requested="yes",
            )


if __name__ == "__main__":
    unittest.main()
