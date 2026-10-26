import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.gateways.fundamentals import FundamentalCapability
from app.models.benchmarking_financials_storage import (
    StoredBenchmarkingFinancialsDocument,
)
from app.models.financial_document_storage import (
    StoredStructuredFinancialDocument,
)
from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_package import (
    FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
    FundamentalEvidencePackage,
    FundamentalPackageDataset,
    FundamentalPackageItem,
    FundamentalPackageItemKey,
    FundamentalPackageManifest,
)
from app.models.fundamental_storage import StoredFundamentalSnapshot
from app.models.peer_comparison_storage import StoredPeerComparisonDocument
from tests.unit.test_fundamental_gateway import build_connection, build_issuer


IST = ZoneInfo("Asia/Kolkata")
REQUESTED_AT = datetime(2026, 9, 8, 10, 0, tzinfo=IST)
COMMITTED_AT = REQUESTED_AT + timedelta(minutes=5)

EXPECTED_PACKAGE_KEYS = {
    "company_overview:not_applicable",
    "financial_statements:not_applicable",
    "shareholding_history:not_applicable",
    "growth_table:not_applicable",
    "balance_sheet:consolidated",
    "balance_sheet:standalone",
    "profit_and_loss:consolidated",
    "profit_and_loss:standalone",
    "cash_flow:consolidated",
    "cash_flow:standalone",
    "ratios:consolidated",
    "ratios:standalone",
    "quarterly_results:consolidated",
    "quarterly_results:standalone",
    "peer_comparison:not_applicable",
    "benchmarking_financials:not_applicable",
}


def package_items():
    return tuple(
        FundamentalPackageItem(
            key=key,
            cache_entry_id=f"fundamental_artifact:item-{index}",
            content_fingerprint=f"{index + 1:064x}",
            retrieved_at=REQUESTED_AT + timedelta(seconds=index + 1),
            expires_at=REQUESTED_AT + timedelta(days=10),
        )
        for index, key in enumerate(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS)
    )


def manifest(**updates):
    values = {
        "operation_id": "operation-fundamental-refresh-1",
        "connection": build_connection(),
        "issuer": build_issuer(),
        "requested_at": REQUESTED_AT,
        "committed_at": COMMITTED_AT,
        "items": package_items(),
    }
    values.update(updates)
    return FundamentalPackageManifest(**values)


def _request(**updates):
    values = {
        "connection": build_connection(),
        "issuer": build_issuer(),
        "operation_id": "operation-fundamental-refresh-1",
    }
    values.update(updates)
    return SimpleNamespace(**values)


def evidence_package(**updates):
    artifacts = []
    snapshots = []
    structured_documents = []
    for index, key in enumerate(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS):
        cache_entry_id = f"fundamental_artifact:item-{index}"
        fingerprint = f"{index + 1:064x}"
        common = {
            "cache_key": SimpleNamespace(cache_entry_id=cache_entry_id),
            "retrieved_at": REQUESTED_AT + timedelta(seconds=index + 1),
            "expires_at": REQUESTED_AT + timedelta(days=10),
        }
        if key.dataset in {
            FundamentalPackageDataset.COMPANY_OVERVIEW,
            FundamentalPackageDataset.FINANCIAL_STATEMENTS,
            FundamentalPackageDataset.SHAREHOLDING_HISTORY,
        }:
            snapshot = StoredFundamentalSnapshot.model_construct(
                request=_request(
                    capability=FundamentalCapability(key.dataset.value)
                ),
                snapshot_fingerprint=fingerprint,
                **common,
            )
            snapshots.append(snapshot)
            artifacts.append(snapshot)
        elif key.dataset in {
            FundamentalPackageDataset.PEER_COMPARISON,
            FundamentalPackageDataset.BENCHMARKING_FINANCIALS,
        }:
            document_type = key.dataset.value
            document_class = (
                StoredPeerComparisonDocument
                if key.dataset is FundamentalPackageDataset.PEER_COMPARISON
                else StoredBenchmarkingFinancialsDocument
            )
            document = document_class.model_construct(
                request=_request(document_type=document_type),
                document_fingerprint=fingerprint,
                **common,
            )
            artifacts.append(document)
        else:
            document = StoredStructuredFinancialDocument.model_construct(
                request=_request(
                    document_type=key.dataset,
                    reporting_basis=key.reporting_basis,
                ),
                document_fingerprint=fingerprint,
                **common,
            )
            structured_documents.append(document)
            artifacts.append(document)

    package_manifest = manifest(
        items=tuple(
            FundamentalPackageItem(
                key=key,
                cache_entry_id=artifact.cache_key.cache_entry_id,
                content_fingerprint=(
                    artifact.snapshot_fingerprint
                    if isinstance(artifact, StoredFundamentalSnapshot)
                    else artifact.document_fingerprint
                ),
                retrieved_at=artifact.retrieved_at,
                expires_at=artifact.expires_at,
            )
            for key, artifact in zip(
                FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
                artifacts,
                strict=True,
            )
        )
    )
    values = {
        "manifest": package_manifest,
        "snapshots": tuple(snapshots),
        "structured_documents": tuple(structured_documents),
        "peer_comparison": artifacts[-2],
        "benchmarking_financials": artifacts[-1],
    }
    values.update(updates)
    return FundamentalEvidencePackage(**values)


class FundamentalPackageManifestTests(unittest.TestCase):
    def test_required_contract_contains_all_sixteen_scenarios(self):
        self.assertEqual(len(FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS), 16)
        self.assertSetEqual(
            {
                item.canonical_key
                for item in FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS
            },
            EXPECTED_PACKAGE_KEYS,
        )

    def test_builds_deterministic_complete_manifest(self):
        result = manifest()
        reordered = manifest(items=tuple(reversed(package_items())))

        self.assertEqual(result.manifest_fingerprint, reordered.manifest_fingerprint)
        self.assertEqual(result.package_id, reordered.package_id)
        self.assertEqual(result.expires_at, REQUESTED_AT + timedelta(days=10))
        self.assertFalse(result.is_expired(as_of=COMMITTED_AT))
        self.assertTrue(result.is_expired(as_of=result.expires_at))

    def test_rejects_missing_or_duplicate_required_scenario(self):
        items = package_items()
        with self.assertRaises(ValidationError):
            manifest(items=items[:-1])

        duplicate = items[:-1] + (
            items[0].model_copy(
                update={"cache_entry_id": "fundamental_artifact:duplicate"}
            ),
        )
        with self.assertRaisesRegex(ValidationError, "item keys must be unique"):
            manifest(items=duplicate)

    def test_rejects_invalid_reporting_basis_for_dataset(self):
        with self.assertRaisesRegex(
            ValidationError,
            "requires not-applicable reporting",
        ):
            FundamentalPackageItemKey(
                dataset=FundamentalPackageDataset.PEER_COMPARISON,
                reporting_basis=FinancialReportingBasis.CONSOLIDATED,
            )
        with self.assertRaisesRegex(
            ValidationError,
            "requires consolidated or standalone",
        ):
            FundamentalPackageItemKey(
                dataset=FundamentalPackageDataset.CASH_FLOW,
                reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
            )

    def test_rejects_non_unique_cache_references_and_invalid_commit_window(self):
        items = package_items()
        reused_reference = items[:-1] + (
            items[-1].model_copy(
                update={"cache_entry_id": items[0].cache_entry_id}
            ),
        )
        with self.assertRaisesRegex(
            ValidationError,
            "cache references must be unique",
        ):
            manifest(items=reused_reference)

        with self.assertRaisesRegex(ValidationError, "expired item"):
            manifest(committed_at=REQUESTED_AT + timedelta(days=10))

    def test_rejects_items_outside_the_refresh_transaction_window(self):
        items = package_items()
        before_request = items[0].model_copy(
            update={"retrieved_at": REQUESTED_AT - timedelta(microseconds=1)}
        )
        with self.assertRaisesRegex(ValidationError, "refresh request"):
            manifest(items=(before_request, *items[1:]))

        after_commit = items[-1].model_copy(
            update={"retrieved_at": COMMITTED_AT + timedelta(microseconds=1)}
        )
        with self.assertRaisesRegex(ValidationError, "item retrieval"):
            manifest(items=(*items[:-1], after_commit))

        with self.assertRaisesRegex(ValidationError, "predate its request"):
            manifest(committed_at=REQUESTED_AT - timedelta(microseconds=1))

    def test_requires_timezone_for_commit_and_expiry_checks(self):
        with self.assertRaisesRegex(ValidationError, "require timezone"):
            manifest(committed_at=COMMITTED_AT.replace(tzinfo=None))
        with self.assertRaisesRegex(ValueError, "requires timezone"):
            manifest().is_expired(as_of=COMMITTED_AT.replace(tzinfo=None))


class FundamentalEvidencePackageTests(unittest.TestCase):
    def test_builds_order_independent_immutable_complete_aggregate(self):
        result = evidence_package()
        reordered = evidence_package(
            snapshots=tuple(reversed(result.snapshots)),
            structured_documents=tuple(reversed(result.structured_documents)),
        )

        self.assertEqual(result.package_id, result.manifest.package_id)
        self.assertEqual(
            result.aggregate_fingerprint,
            reordered.aggregate_fingerprint,
        )
        self.assertFalse(result.is_expired(as_of=COMMITTED_AT))
        with self.assertRaises(ValidationError):
            result.snapshots = ()

    def test_rejects_artifact_that_does_not_match_manifest(self):
        result = evidence_package()
        changed = result.structured_documents[0].model_copy(
            update={"document_fingerprint": "f" * 64}
        )

        with self.assertRaisesRegex(
            ValidationError,
            "exactly match the manifest",
        ):
            evidence_package(
                structured_documents=(
                    changed,
                    *result.structured_documents[1:],
                )
            )

    def test_rejects_artifact_metadata_that_does_not_match_manifest(self):
        result = evidence_package()
        original = result.structured_documents[0]
        cases = (
            original.model_copy(
                update={
                    "cache_key": SimpleNamespace(
                        cache_entry_id="fundamental_artifact:replacement"
                    )
                }
            ),
            original.model_copy(
                update={
                    "retrieved_at": original.retrieved_at
                    + timedelta(microseconds=1)
                }
            ),
            original.model_copy(
                update={
                    "expires_at": original.expires_at
                    + timedelta(microseconds=1)
                }
            ),
        )

        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(
                    ValidationError,
                    "exactly match the manifest",
                ):
                    evidence_package(
                        structured_documents=(
                            changed,
                            *result.structured_documents[1:],
                        )
                    )

    def test_rejects_mixed_issuer_connection_or_operation(self):
        result = evidence_package()
        cases = (
            _request(
                capability=FundamentalCapability.COMPANY_OVERVIEW,
                issuer=build_issuer(symbol="RELIANCE"),
            ),
            _request(
                capability=FundamentalCapability.COMPANY_OVERVIEW,
                connection=build_connection(tenant_id="tenant.other"),
            ),
            _request(
                capability=FundamentalCapability.COMPANY_OVERVIEW,
                operation_id="operation-other",
            ),
        )
        messages = ("issuer", "connection", "operation")

        for changed_request, message in zip(cases, messages, strict=True):
            changed = result.snapshots[0].model_copy(
                update={"request": changed_request}
            )
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValidationError, message):
                    FundamentalEvidencePackage(
                        manifest=result.manifest,
                        snapshots=(changed, *result.snapshots[1:]),
                        structured_documents=result.structured_documents,
                        peer_comparison=result.peer_comparison,
                        benchmarking_financials=(
                            result.benchmarking_financials
                        ),
                    )

    def test_rejects_missing_or_duplicate_artifact_scenario(self):
        result = evidence_package()
        with self.assertRaises(ValidationError):
            FundamentalEvidencePackage(
                manifest=result.manifest,
                snapshots=result.snapshots,
                structured_documents=result.structured_documents[:-1],
                peer_comparison=result.peer_comparison,
                benchmarking_financials=result.benchmarking_financials,
            )

        duplicate = result.structured_documents[0].model_copy()
        with self.assertRaisesRegex(
            ValidationError,
            "artifact scenarios must be unique",
        ):
            FundamentalEvidencePackage(
                manifest=result.manifest,
                snapshots=result.snapshots,
                structured_documents=(
                    duplicate,
                    *result.structured_documents[:-1],
                ),
                peer_comparison=result.peer_comparison,
                benchmarking_financials=result.benchmarking_financials,
            )


if __name__ == "__main__":
    unittest.main()
