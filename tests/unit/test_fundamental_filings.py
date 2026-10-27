import unittest
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError

from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingCategory,
    FundamentalFilingDiscoverySnapshot,
    FundamentalFilingFailure,
    FundamentalFilingFailureCategory,
    FundamentalFilingFailureStage,
    FundamentalFilingPackage,
    FundamentalFilingPackageManifest,
    FundamentalFilingPeriod,
    FundamentalFilingPeriodState,
    FundamentalFilingPeriodType,
    FundamentalFilingReference,
    FundamentalFilingSourceClass,
)
from app.models.fundamentals import FundamentalValidationStatus
from tests.unit.test_fundamental_gateway import build_connection, build_issuer


IST = timezone(timedelta(hours=5, minutes=30))
REQUESTED_AT = datetime(2026, 9, 14, 10, 0, tzinfo=IST)
DISCOVERED_AT = REQUESTED_AT + timedelta(seconds=1)
DOWNLOADED_AT = REQUESTED_AT + timedelta(seconds=2)
COMMITTED_AT = REQUESTED_AT + timedelta(seconds=3)
RECHECK_AFTER = REQUESTED_AT + timedelta(days=10)
PDF_HASH = "a" * 64
SOURCE_DISCOVERY_HASH = "b" * 64


def period(**updates):
    values = {
        "state": FundamentalFilingPeriodState.RESOLVED,
        "period_type": FundamentalFilingPeriodType.QUARTERLY,
        "source_label": "Q1 FY2027",
        "fiscal_year": 2027,
        "fiscal_quarter": 1,
        "period_start": date(2026, 4, 1),
        "period_end": date(2026, 6, 30),
    }
    values.update(updates)
    return FundamentalFilingPeriod(**values)


def reference(**updates):
    values = {
        "filing_reference_id": "filing.coforge.fy2027.q1.er",
        "connection": build_connection(),
        "issuer": build_issuer(
            symbol="COFORGE-EQ",
            legal_name="Coforge Limited",
            isin="INE591G01017",
            provider_company_id="242",
            provider_slug="niit-technologies-limited",
        ),
        "provider": "tijori",
        "source_discovery_fingerprint": SOURCE_DISCOVERY_HASH,
        "category": FundamentalFilingCategory.EARNINGS_RELEASE,
        "source_class": (
            FundamentalFilingSourceClass.COMPANY_AUTHORED_EARNINGS_RELEASE
        ),
        "period": period(),
        "title": "Coforge Q1 FY2027 Earnings Release",
        "provider_label": "ER-Jun26.pdf",
        "original_filename": "ER-Jun26.pdf",
        "source_page_url": (
            "https://www.tijorifinance.com/company/niit-technologies-limited/"
        ),
        "artifact_url": (
            "https://files.tijorifinance.com/insight/india/242/ER-Jun26.pdf"
        ),
        "provider_published_at": REQUESTED_AT - timedelta(days=1),
        "available_at": REQUESTED_AT - timedelta(hours=12),
        "discovered_at": DISCOVERED_AT,
    }
    values.update(updates)
    return FundamentalFilingReference(**values)


def discovery(*, references=None, **updates):
    filing_references = (reference(),) if references is None else references
    values = {
        "discovery_id": "filing.discovery.coforge.20260914",
        "connection": build_connection(),
        "issuer": reference().issuer,
        "categories": (
            FundamentalFilingCategory.EARNINGS_RELEASE,
            FundamentalFilingCategory.CONFERENCE_CALL,
        ),
        "from_fiscal_year": 2017,
        "through_date": REQUESTED_AT.date(),
        "max_documents": 100,
        "discovered_at": DISCOVERED_AT,
        "references": filing_references,
        "unavailable_period_labels": ("Q4 FY2017 conference call",),
        "validation_status": FundamentalValidationStatus.VALIDATED,
        "limitations": ("Provider did not expose one historical call.",),
    }
    values.update(updates)
    return FundamentalFilingDiscoverySnapshot(**values)


def artifact(**updates):
    values = {
        "reference": reference(),
        "content_sha256": PDF_HASH,
        "safe_relative_path": (
            "tenant/provider/INE591G01017/FY2027/Q1/earnings-release/"
            f"{PDF_HASH}.pdf"
        ),
        "byte_length": 4096,
        "page_count": 5,
        "pdf_version": "1.7",
        "downloaded_at": DOWNLOADED_AT,
        "last_checked_at": DOWNLOADED_AT,
        "recheck_after": RECHECK_AFTER,
    }
    values.update(updates)
    return FundamentalFilingArtifactManifest(**values)


def package(**updates):
    filing_discovery = discovery()
    filing_artifact = artifact()
    filing_manifest = FundamentalFilingPackageManifest(
        operation_id="operation-filing-refresh-1",
        connection=build_connection(),
        issuer=filing_discovery.issuer,
        requested_at=REQUESTED_AT,
        committed_at=COMMITTED_AT,
        discovery_fingerprint=filing_discovery.discovery_fingerprint,
        artifact_ids=(filing_artifact.artifact_id,),
        recheck_after=RECHECK_AFTER,
    )
    values = {
        "manifest": filing_manifest,
        "discovery": filing_discovery,
        "artifacts": (filing_artifact,),
    }
    values.update(updates)
    return FundamentalFilingPackage(**values)


class FundamentalFilingPeriodTests(unittest.TestCase):
    def test_preserves_resolved_quarter_without_inference(self):
        result = period()

        self.assertEqual(result.fiscal_year, 2027)
        self.assertEqual(result.fiscal_quarter, 1)

    def test_rejects_partial_or_inconsistent_periods(self):
        with self.assertRaisesRegex(ValidationError, "dates must be supplied"):
            period(period_end=None)
        with self.assertRaisesRegex(ValidationError, "requires a fiscal quarter"):
            period(fiscal_quarter=None)
        with self.assertRaisesRegex(ValidationError, "cannot release inferred"):
            period(
                state=FundamentalFilingPeriodState.UNCLASSIFIED,
                period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            )

    def test_accepts_explicitly_unclassified_period(self):
        result = period(
            state=FundamentalFilingPeriodState.UNCLASSIFIED,
            period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            fiscal_year=None,
            fiscal_quarter=None,
            period_start=None,
            period_end=None,
            source_label="Provider period unresolved",
        )

        self.assertIsNone(result.fiscal_year)


class FundamentalFilingReferenceTests(unittest.TestCase):
    def test_binds_reference_to_verified_provider_issuer(self):
        result = reference()

        self.assertEqual(result.connection.provider, result.provider)
        self.assertEqual(result.issuer.provider_company_id, "242")
        self.assertEqual(
            result.source_discovery_fingerprint,
            SOURCE_DISCOVERY_HASH,
        )

    def test_rejects_provider_source_class_and_url_mismatches(self):
        with self.assertRaisesRegex(ValidationError, "must match its connection"):
            reference(provider="another-provider")
        with self.assertRaisesRegex(ValidationError, "does not match its category"):
            reference(
                source_class=(
                    FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
                )
            )
        with self.assertRaisesRegex(ValidationError, "credential-free HTTPS"):
            reference(artifact_url="https://user:secret@example.com/file.pdf")
        with self.assertRaisesRegex(ValidationError, "resolved provider issuer"):
            reference(issuer=build_issuer(provider_company_id=None))
        with self.assertRaisesRegex(ValidationError, "string_pattern_mismatch"):
            reference(source_discovery_fingerprint="not-a-sha256")

    def test_rejects_naive_or_impossible_timestamps(self):
        with self.assertRaisesRegex(ValidationError, "timezone"):
            reference(discovered_at=datetime(2026, 9, 14, 10, 0))
        with self.assertRaisesRegex(ValidationError, "follow discovery"):
            reference(provider_published_at=DISCOVERED_AT + timedelta(seconds=1))


class FundamentalFilingDiscoveryTests(unittest.TestCase):
    def test_fingerprint_is_stable_for_semantically_unordered_fields(self):
        result = discovery()
        reordered = discovery(
            categories=tuple(reversed(result.categories)),
            unavailable_period_labels=tuple(
                reversed(result.unavailable_period_labels)
            ),
            limitations=tuple(reversed(result.limitations)),
        )

        self.assertEqual(
            result.discovery_fingerprint,
            reordered.discovery_fingerprint,
        )

    def test_rejects_cross_tenant_duplicate_or_out_of_range_references(self):
        with self.assertRaisesRegex(ValidationError, "must share its scope"):
            discovery(
                references=(
                    reference(connection=build_connection(tenant_id="tenant.other")),
                )
            )
        with self.assertRaisesRegex(ValidationError, "reference IDs must be unique"):
            discovery(references=(reference(), reference()))
        with self.assertRaisesRegex(ValidationError, "predates requested"):
            discovery(
                from_fiscal_year=2028,
                references=(reference(),),
            )


class FundamentalFilingArtifactTests(unittest.TestCase):
    def test_original_download_metadata_is_versioned_and_legacy_safe(self):
        old = artifact()
        self.assertEqual(old.first_downloaded_at, DOWNLOADED_AT)
        self.assertNotIn("original_downloaded_at", old.model_dump(mode="json"))
        later = DOWNLOADED_AT + timedelta(days=2)
        new = artifact(
            schema_version="jarvis.fundamental_filing_artifact.v2",
            original_downloaded_at=DOWNLOADED_AT,
            downloaded_at=later, last_checked_at=later,
            recheck_after=later + timedelta(days=10),
        )
        self.assertEqual(new.first_downloaded_at, DOWNLOADED_AT)
        self.assertEqual(new.downloaded_at, later)
        self.assertNotEqual(new.artifact_id, old.artifact_id)
        self.assertEqual(FundamentalFilingArtifactManifest.model_validate_json(
            new.model_dump_json(exclude_computed_fields=True),
        ), new)
        with self.assertRaisesRegex(ValidationError, "requires artifact v2"):
            artifact(original_downloaded_at=DOWNLOADED_AT)
        with self.assertRaisesRegex(ValidationError, "cannot follow"):
            artifact(
                schema_version="jarvis.fundamental_filing_artifact.v2",
                original_downloaded_at=later,
            )

    def test_is_content_addressed_and_uses_ten_day_recheck(self):
        result = artifact()

        self.assertTrue(result.artifact_id.startswith("filing_artifact:"))
        self.assertFalse(result.is_recheck_due(as_of=COMMITTED_AT))
        self.assertTrue(result.is_recheck_due(as_of=RECHECK_AFTER))

    def test_rejects_unsafe_path_hash_mismatch_and_invalid_freshness(self):
        with self.assertRaisesRegex(ValidationError, "safe relative PDF path"):
            artifact(safe_relative_path=f"../{PDF_HASH}.pdf")
        with self.assertRaisesRegex(ValidationError, "safe relative PDF path"):
            artifact(
                safe_relative_path=(
                    "tenant//provider/INE591G01017/FY2027/Q1/"
                    f"{PDF_HASH}.pdf"
                )
            )
        with self.assertRaisesRegex(ValidationError, "end in its content hash"):
            artifact(
                safe_relative_path=(
                    "tenant/provider/INE591G01017/FY2027/Q1/"
                    f"{'b' * 64}.pdf"
                )
            )
        with self.assertRaisesRegex(ValidationError, "cannot exceed ten days"):
            artifact(recheck_after=DOWNLOADED_AT + timedelta(days=10, seconds=1))
        with self.assertRaisesRegex(ValidationError, "timezone"):
            artifact(last_checked_at=datetime(2026, 9, 14, 10, 0))


class FundamentalFilingPackageTests(unittest.TestCase):
    def test_v2_reuses_original_download_across_rediscovery(self):
        old = package()
        later = COMMITTED_AT + timedelta(days=12)
        new_reference = reference(
            discovered_at=later,
            source_discovery_fingerprint="c" * 64,
            title="Rediscovered filing",
        )
        found = discovery(references=(new_reference,), discovered_at=later)
        manifest = FundamentalFilingPackageManifest(
            operation_id="operation-rediscovery",
            connection=old.manifest.connection,
            issuer=old.manifest.issuer,
            requested_at=later,
            committed_at=later,
            discovery_fingerprint=found.discovery_fingerprint,
            artifact_ids=old.manifest.artifact_ids,
            recheck_after=later + timedelta(days=10),
        )
        current = FundamentalFilingPackage(
            schema_version="jarvis.fundamental_filing_package.v2",
            manifest=manifest, discovery=found, artifacts=old.artifacts,
        )
        self.assertEqual(current.artifacts, old.artifacts)
        with self.assertRaises(ValidationError):
            FundamentalFilingPackage(
                manifest=manifest, discovery=found, artifacts=old.artifacts,
            )
        changed = discovery(references=(reference(
            discovered_at=later, artifact_url="https://example.com/other.pdf",
        ),), discovered_at=later)
        changed_manifest = manifest.model_copy(update={
            "discovery_fingerprint": changed.discovery_fingerprint,
        })
        with self.assertRaisesRegex(ValidationError, "discovered reference"):
            FundamentalFilingPackage(
                schema_version="jarvis.fundamental_filing_package.v2",
                manifest=changed_manifest, discovery=changed,
                artifacts=old.artifacts,
            )

    def test_releases_complete_atomic_package_and_defensive_dump(self):
        result = package()
        dumped = result.model_dump(mode="json")
        dumped["artifacts"][0]["reference"]["title"] = "Changed outside model"

        self.assertEqual(result.package_id, result.manifest.package_id)
        self.assertEqual(
            result.artifacts[0].reference.title,
            "Coforge Q1 FY2027 Earnings Release",
        )

    def test_rejects_partial_unvalidated_or_cross_scope_package(self):
        result = package()
        with self.assertRaisesRegex(ValidationError, "every discovered"):
            package(
                manifest=result.manifest.model_copy(update={"artifact_ids": ()}),
                artifacts=(),
            )
        with self.assertRaisesRegex(ValidationError, "requires validated discovery"):
            package(
                discovery=result.discovery.model_copy(
                    update={
                        "validation_status": FundamentalValidationStatus.PARTIAL,
                    }
                )
            )
        with self.assertRaisesRegex(ValidationError, "scope must match discovery"):
            package(
                manifest=result.manifest.model_copy(
                    update={
                        "connection": build_connection(tenant_id="tenant.other"),
                    }
                )
            )
        changed_reference_artifact = result.artifacts[0].model_copy(
            update={
                "reference": result.artifacts[0].reference.model_copy(
                    update={"title": "Changed after discovery"}
                )
            }
        )
        with self.assertRaisesRegex(ValidationError, "exactly match"):
            package(
                manifest=result.manifest.model_copy(
                    update={
                        "artifact_ids": (changed_reference_artifact.artifact_id,),
                    }
                ),
                artifacts=(changed_reference_artifact,),
            )

    def test_accepts_validated_empty_discovery_without_fabricating_documents(self):
        empty_discovery = discovery(references=())
        empty_manifest = FundamentalFilingPackageManifest(
            operation_id="operation-filing-refresh-empty",
            connection=empty_discovery.connection,
            issuer=empty_discovery.issuer,
            requested_at=REQUESTED_AT,
            committed_at=COMMITTED_AT,
            discovery_fingerprint=empty_discovery.discovery_fingerprint,
            artifact_ids=(),
            recheck_after=RECHECK_AFTER,
        )

        result = FundamentalFilingPackage(
            manifest=empty_manifest,
            discovery=empty_discovery,
            artifacts=(),
        )

        self.assertEqual(result.artifacts, ())


class FundamentalFilingFailureTests(unittest.TestCase):
    def test_failure_is_scoped_sanitized_and_immutable(self):
        result = FundamentalFilingFailure(
            operation_id="operation-filing-refresh-1",
            connection=build_connection(),
            issuer=reference().issuer,
            filing_reference_id=reference().filing_reference_id,
            stage=FundamentalFilingFailureStage.DOWNLOAD,
            category=FundamentalFilingFailureCategory.TIMEOUT,
            retryable=True,
            occurred_at=DOWNLOADED_AT,
            sanitized_message="Provider download timed out.",
        )

        with self.assertRaises(ValidationError):
            result.operation_id = "changed"
        with self.assertRaisesRegex(ValidationError, "single line"):
            FundamentalFilingFailure(
                **{
                    **result.model_dump(),
                    "sanitized_message": "raw\ntrace",
                }
            )
        with self.assertRaises(ValidationError):
            FundamentalFilingFailure(
                **{
                    **result.model_dump(),
                    "cookie": "secret",
                }
            )


if __name__ == "__main__":
    unittest.main()
