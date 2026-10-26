import unittest
from datetime import datetime

from app.models.fundamental_package import FundamentalEvidencePackage
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepository,
    FundamentalPackageRepositoryScope,
)
from tests.unit.test_fundamental_gateway import build_connection, build_issuer


class CompleteRepository:
    @property
    def adapter_name(self) -> str:
        return "contract_test"

    def commit_fundamental_package(
        self,
        package: FundamentalEvidencePackage,
        *,
        expected_active_package_id: str | None,
    ) -> FundamentalEvidencePackage:
        del expected_active_package_id
        return package

    def get_active_fundamental_package(
        self,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        del scope, as_of
        return None

    def get_fundamental_package(
        self,
        package_id: str,
        *,
        scope: FundamentalPackageRepositoryScope,
        as_of: datetime,
    ) -> FundamentalEvidencePackage | None:
        del package_id, scope, as_of
        return None

    def purge_expired_fundamental_packages(
        self,
        *,
        as_of: datetime,
    ) -> int:
        del as_of
        return 0


class IncompleteRepository:
    @property
    def adapter_name(self) -> str:
        return "incomplete"


class FundamentalPackageRepositoryContractTests(unittest.TestCase):
    def test_runtime_protocol_requires_complete_atomic_repository_surface(self):
        self.assertIsInstance(CompleteRepository(), FundamentalPackageRepository)
        self.assertNotIsInstance(
            IncompleteRepository(),
            FundamentalPackageRepository,
        )

    def test_scope_preserves_connection_and_issuer_identity(self):
        connection = build_connection()
        issuer = build_issuer()
        scope = FundamentalPackageRepositoryScope(
            connection=connection,
            issuer=issuer,
        )

        self.assertEqual(scope.connection, connection)
        self.assertEqual(scope.issuer, issuer)
        with self.assertRaises(Exception):
            scope.issuer = build_issuer(symbol="RELIANCE")


if __name__ == "__main__":
    unittest.main()
