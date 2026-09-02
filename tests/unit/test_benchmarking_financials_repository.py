import unittest

from app.storage.benchmarking_financials_repositories import (
    BenchmarkingFinancialsRepository,
)


class CompleteBenchmarkingFinancialsRepository:
    adapter_name = "memory"

    def save_benchmarking_financials(self, stored):
        return stored

    def replace_benchmarking_financials(self, stored, *, scope):
        return stored

    def get_benchmarking_financials(self, key, *, scope, as_of):
        return None

    def get_benchmarking_financials_by_cache_entry_id(
        self,
        cache_entry_id,
        *,
        scope,
        as_of,
    ):
        return None

    def delete_benchmarking_financials(self, key, *, scope):
        return False

    def purge_expired_benchmarking_financials(self, *, as_of):
        return 0


class BenchmarkingFinancialsRepositoryTests(unittest.TestCase):
    def test_complete_adapter_satisfies_runtime_protocol(self):
        repository = CompleteBenchmarkingFinancialsRepository()

        self.assertIsInstance(repository, BenchmarkingFinancialsRepository)
        self.assertEqual(repository.adapter_name, "memory")

    def test_incomplete_adapter_does_not_satisfy_runtime_protocol(self):
        class IncompleteRepository:
            adapter_name = "incomplete"

            def save_benchmarking_financials(self, stored):
                return stored

        self.assertNotIsInstance(
            IncompleteRepository(),
            BenchmarkingFinancialsRepository,
        )

    def test_protocol_exposes_only_bounded_cache_mutations(self):
        methods = set(dir(BenchmarkingFinancialsRepository))
        expected = {
            "save_benchmarking_financials",
            "replace_benchmarking_financials",
            "get_benchmarking_financials",
            "get_benchmarking_financials_by_cache_entry_id",
            "delete_benchmarking_financials",
            "purge_expired_benchmarking_financials",
        }

        self.assertTrue(expected.issubset(methods))
        self.assertNotIn("execute_sql", methods)
        self.assertNotIn("delete_all", methods)
        self.assertNotIn("list_all_tenants", methods)


if __name__ == "__main__":
    unittest.main()
