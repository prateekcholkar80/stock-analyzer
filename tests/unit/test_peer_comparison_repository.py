import unittest

from app.storage.peer_comparison_repositories import PeerComparisonRepository


class CompletePeerComparisonRepository:
    adapter_name = "memory"

    def save_peer_comparison(self, stored):
        return stored

    def replace_peer_comparison(self, stored, *, scope):
        return stored

    def get_peer_comparison(self, key, *, scope, as_of):
        return None

    def get_peer_comparison_by_cache_entry_id(
        self,
        cache_entry_id,
        *,
        scope,
        as_of,
    ):
        return None

    def delete_peer_comparison(self, key, *, scope):
        return False

    def purge_expired_peer_comparisons(self, *, as_of):
        return 0


class PeerComparisonRepositoryTests(unittest.TestCase):
    def test_complete_adapter_satisfies_runtime_protocol(self):
        repository = CompletePeerComparisonRepository()

        self.assertIsInstance(repository, PeerComparisonRepository)
        self.assertEqual(repository.adapter_name, "memory")

    def test_incomplete_adapter_does_not_satisfy_runtime_protocol(self):
        class IncompleteRepository:
            adapter_name = "incomplete"

            def save_peer_comparison(self, stored):
                return stored

        self.assertNotIsInstance(
            IncompleteRepository(),
            PeerComparisonRepository,
        )

    def test_protocol_exposes_only_bounded_cache_mutations(self):
        methods = set(dir(PeerComparisonRepository))
        expected = {
            "save_peer_comparison",
            "replace_peer_comparison",
            "get_peer_comparison",
            "get_peer_comparison_by_cache_entry_id",
            "delete_peer_comparison",
            "purge_expired_peer_comparisons",
        }

        self.assertTrue(expected.issubset(methods))
        self.assertNotIn("execute_sql", methods)
        self.assertNotIn("delete_all", methods)
        self.assertNotIn("list_all_tenants", methods)


if __name__ == "__main__":
    unittest.main()
