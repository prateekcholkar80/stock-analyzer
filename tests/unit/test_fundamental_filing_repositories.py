import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingPackage,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingDeleteAuthorization,
    FundamentalFilingDeleteReceipt,
    FundamentalFilingDeleteStatus,
    FundamentalFilingDeleteTarget,
    FundamentalFilingRepository,
    FundamentalFilingRepositoryScope,
)
from tests.unit.test_fundamental_filings import artifact, package
from tests.unit.test_fundamental_gateway import build_issuer


IST = timezone(timedelta(hours=5, minutes=30))
AUTHORIZED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=IST)


def delete_authorization(
    *,
    target_kind=FundamentalFilingDeleteTarget.ARTIFACT,
    target_id=None,
    target_fingerprint=None,
    **updates,
):
    filing_artifact = artifact()
    default_id = filing_artifact.artifact_id
    default_fingerprint = filing_artifact.artifact_fingerprint
    if target_kind is FundamentalFilingDeleteTarget.PACKAGE:
        filing_package = package()
        default_id = filing_package.package_id
        default_fingerprint = filing_package.manifest.manifest_fingerprint
    values = {
        "audit_id": "audit.filing.delete.1",
        "operation_id": "operation-filing-delete-1",
        "authorized_by": "user.prateek",
        "target_kind": target_kind,
        "target_id": target_id or default_id,
        "target_fingerprint": target_fingerprint or default_fingerprint,
        "reason": "Remove this exact retained filing after owner review.",
        "authorized_at": AUTHORIZED_AT,
    }
    values.update(updates)
    return FundamentalFilingDeleteAuthorization(**values)


class CompleteFilingRepository:
    @property
    def adapter_name(self) -> str:
        return "contract_test"

    def save_filing_artifact(
        self,
        artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest:
        return artifact

    def find_filing_artifacts_by_content_hash(
        self,
        content_sha256,
        *,
        scope,
    ):
        del content_sha256, scope
        return ()

    def get_filing_artifact(self, artifact_id, *, scope):
        del artifact_id, scope
        return None

    def list_filing_artifact_versions(self, filing_reference_id, *, scope):
        del filing_reference_id, scope
        return ()

    def commit_filing_package(
        self,
        package: FundamentalFilingPackage,
        *,
        expected_active_package_id,
    ) -> FundamentalFilingPackage:
        del expected_active_package_id
        return package

    def get_active_filing_package(self, *, scope):
        del scope
        return None

    def get_filing_package(self, package_id, *, scope):
        del package_id, scope
        return None

    def delete_filing_artifact(
        self,
        artifact_id,
        *,
        scope,
        authorization,
    ):
        del artifact_id, scope
        return FundamentalFilingDeleteReceipt(
            authorization=authorization,
            status=FundamentalFilingDeleteStatus.DELETED,
            completed_at=AUTHORIZED_AT,
        )

    def delete_filing_package(
        self,
        package_id,
        *,
        scope,
        authorization,
    ):
        del package_id, scope
        return FundamentalFilingDeleteReceipt(
            authorization=authorization,
            status=FundamentalFilingDeleteStatus.DELETED,
            completed_at=AUTHORIZED_AT,
        )


class IncompleteFilingRepository:
    @property
    def adapter_name(self) -> str:
        return "incomplete"


class FundamentalFilingRepositoryContractTests(unittest.TestCase):
    def test_runtime_protocol_requires_complete_bounded_surface(self):
        self.assertIsInstance(
            CompleteFilingRepository(),
            FundamentalFilingRepository,
        )
        self.assertNotIsInstance(
            IncompleteFilingRepository(),
            FundamentalFilingRepository,
        )

    def test_scope_preserves_artifact_package_and_tenant_identity(self):
        filing_artifact = artifact()
        filing_package = package()
        artifact_scope = FundamentalFilingRepositoryScope.from_artifact(
            filing_artifact
        )
        package_scope = FundamentalFilingRepositoryScope.from_package(
            filing_package
        )

        self.assertEqual(artifact_scope, package_scope)
        self.assertEqual(artifact_scope.issuer, filing_artifact.reference.issuer)
        with self.assertRaises(ValidationError):
            artifact_scope.issuer = build_issuer(symbol="RELIANCE-EQ")

    def test_protocol_has_no_broad_query_or_purge_escape_hatch(self):
        methods = set(dir(FundamentalFilingRepository))
        expected = {
            "save_filing_artifact",
            "find_filing_artifacts_by_content_hash",
            "get_filing_artifact",
            "list_filing_artifact_versions",
            "commit_filing_package",
            "get_active_filing_package",
            "get_filing_package",
            "delete_filing_artifact",
            "delete_filing_package",
        }

        self.assertTrue(expected.issubset(methods))
        self.assertNotIn("execute_sql", methods)
        self.assertNotIn("delete_all", methods)
        self.assertNotIn("purge_expired", methods)
        self.assertNotIn("list_all_tenants", methods)


class FundamentalFilingDeleteContractTests(unittest.TestCase):
    def test_authorization_binds_one_exact_artifact_or_package(self):
        artifact_authorization = delete_authorization()
        package_authorization = delete_authorization(
            target_kind=FundamentalFilingDeleteTarget.PACKAGE
        )

        self.assertTrue(
            artifact_authorization.target_id.startswith("filing_artifact:")
        )
        self.assertTrue(
            package_authorization.target_id.startswith("filing_package:")
        )
        with self.assertRaisesRegex(ValidationError, "does not match its kind"):
            delete_authorization(
                target_kind=FundamentalFilingDeleteTarget.PACKAGE,
                target_id=artifact_authorization.target_id,
                target_fingerprint=(
                    artifact_authorization.target_fingerprint
                ),
            )
        with self.assertRaisesRegex(ValidationError, "exact fingerprint"):
            delete_authorization(target_fingerprint="b" * 64)

    def test_authorization_is_timed_sanitized_and_immutable(self):
        authorization = delete_authorization()

        with self.assertRaises(ValidationError):
            authorization.reason = "changed"
        with self.assertRaisesRegex(ValidationError, "timezone"):
            delete_authorization(authorized_at=datetime(2026, 9, 14, 12, 0))
        with self.assertRaisesRegex(ValidationError, "single line"):
            delete_authorization(reason="Approved reason\nraw trace")
        with self.assertRaises(ValidationError):
            FundamentalFilingDeleteAuthorization(
                **{
                    **authorization.model_dump(),
                    "cookie": "secret",
                }
            )

    def test_receipt_records_success_not_found_and_active_rejection(self):
        authorization = delete_authorization()
        deleted = FundamentalFilingDeleteReceipt(
            authorization=authorization,
            status=FundamentalFilingDeleteStatus.DELETED,
            completed_at=AUTHORIZED_AT + timedelta(seconds=1),
        )
        not_found = FundamentalFilingDeleteReceipt(
            authorization=authorization,
            status=FundamentalFilingDeleteStatus.NOT_FOUND,
            completed_at=AUTHORIZED_AT + timedelta(seconds=1),
        )

        self.assertTrue(deleted.deleted)
        self.assertFalse(not_found.deleted)
        with self.assertRaisesRegex(ValidationError, "safe explanation"):
            FundamentalFilingDeleteReceipt(
                authorization=authorization,
                status=(
                    FundamentalFilingDeleteStatus.REJECTED_ACTIVE_REFERENCE
                ),
                completed_at=AUTHORIZED_AT + timedelta(seconds=1),
            )
        with self.assertRaisesRegex(ValidationError, "predate authorization"):
            FundamentalFilingDeleteReceipt(
                authorization=authorization,
                status=FundamentalFilingDeleteStatus.NOT_FOUND,
                completed_at=AUTHORIZED_AT - timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main()
