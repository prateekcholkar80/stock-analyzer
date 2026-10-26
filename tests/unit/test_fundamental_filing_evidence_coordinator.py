import unittest
from datetime import timedelta

from app.exceptions import StorageConflictError
from app.gateways.fundamentals import (
    FundamentalFilingAcquisitionResult,
    FundamentalFilingDiscoveryResult,
    FundamentalRetrievalStatus,
)
from app.models.fundamental_filing_evidence import (
    FundamentalFilingEvidenceValidationResult,
    FundamentalFilingValidationCode,
    FundamentalFilingValidationFinding,
    FundamentalFilingValidationStatus,
)
from app.services.fundamental_filing_evidence import (
    FundamentalFilingEvidenceCoordinationStatus,
    FundamentalFilingEvidenceCoordinator,
)
from app.storage.adapters.fundamental_filing_evidence_in_memory import (
    InMemoryFundamentalFilingEvidenceRepository,
)
from app.storage.adapters.fundamental_filing_in_memory import (
    InMemoryFundamentalFilingRepository,
)
from app.storage.fundamental_filing_evidence_repositories import (
    StoredFundamentalFilingEvidence,
)
from tests.unit.test_fundamental_filing_evidence import extraction, policy
from tests.unit.test_fundamental_filing_gateway import (
    ADAPTER_FINGERPRINT,
    _discovery_request,
)
from tests.unit.test_fundamental_filings import (
    COMMITTED_AT,
    artifact,
    discovery,
    package,
)


NOW = COMMITTED_AT + timedelta(minutes=5)


def _validation(evidence, *, releaseable=True):
    findings = tuple(
        FundamentalFilingValidationFinding(
            code=code,
            status=(
                FundamentalFilingValidationStatus.PASS
                if releaseable or index > 0
                else FundamentalFilingValidationStatus.FAIL
            ),
            message=(
                "The deterministic validation passed."
                if releaseable or index > 0
                else "The deterministic validation failed."
            ),
            affected_ids=(
                ()
                if releaseable or index > 0
                else (evidence.artifact.reference.filing_reference_id,)
            ),
        )
        for index, code in enumerate(sorted(FundamentalFilingValidationCode))
    )
    return FundamentalFilingEvidenceValidationResult(
        policy_id="filing.evidence.validation",
        policy_version="1.0.0",
        extraction_fingerprint=evidence.extraction_fingerprint,
        releaseable=releaseable,
        findings=findings,
        evidence_page_spans=(),
    )


class StubExtractor:
    def __init__(self):
        self.calls = []
        self._policy = policy()

    @property
    def policy(self):
        return self._policy

    def extract(self, filing_artifact, *, extracted_at):
        self.calls.append(filing_artifact.artifact_id)
        return extraction(
            artifact=filing_artifact,
            policy=self._policy,
            extracted_at=extracted_at,
        )


class StubValidator:
    policy_id = "filing.evidence.validation"
    policy_version = "1.0.0"

    def __init__(self, *, releaseable=True):
        self.releaseable = releaseable
        self.calls = []

    def validate(
        self,
        evidence,
        *,
        expected_issuer,
        expected_period,
        expected_source_class,
    ):
        self.calls.append(evidence.artifact.artifact_id)
        return _validation(evidence, releaseable=self.releaseable)


class StubGateway:
    configuration_fingerprint = "c" * 64

    def __init__(self, *, discovery_status=FundamentalRetrievalStatus.COMPLETED):
        self.discovery_status = discovery_status
        self.discovery_calls = []
        self.acquisition_calls = []

    def discover_filings(self, *, request):
        self.discovery_calls.append(request)
        values = self._response_values(request)
        if self.discovery_status is not FundamentalRetrievalStatus.COMPLETED:
            return FundamentalFilingDiscoveryResult(
                **values,
                issuer=request.issuer,
                status=self.discovery_status,
                limitations=("Provider filing discovery is unavailable.",),
            )
        return FundamentalFilingDiscoveryResult(
            **values,
            issuer=request.issuer,
            status=FundamentalRetrievalStatus.COMPLETED,
            discovery=discovery(),
        )

    def acquire_filing(self, *, request):
        self.acquisition_calls.append(request)
        return FundamentalFilingAcquisitionResult(
            **self._response_values(request),
            issuer=request.issuer,
            status=FundamentalRetrievalStatus.COMPLETED,
            artifact=artifact(reference=request.reference),
        )

    @staticmethod
    def _response_values(request):
        return {
            "request_id": request.request_id,
            "request_fingerprint": request.request_fingerprint,
            "connection": request.connection,
            "requested_at": request.requested_at,
            "started_at": request.requested_at,
            "completed_at": max(request.requested_at, NOW),
            "provider_contract_version": "provider.contract.v1",
            "adapter_fingerprint": ADAPTER_FINGERPRINT,
        }


class ConflictingFilingRepository(InMemoryFundamentalFilingRepository):
    def __init__(self):
        super().__init__(clock=lambda: NOW)
        self.commit_filing_package(
            package(),
            expected_active_package_id=None,
        )
        self.reject_publication = True

    def commit_filing_package(self, candidate, *, expected_active_package_id):
        if getattr(self, "reject_publication", False):
            raise StorageConflictError("simulated package publication race")
        return super().commit_filing_package(
            candidate,
            expected_active_package_id=expected_active_package_id,
        )


def _stored_record(*, filing_artifact=None):
    evidence = extraction(
        artifact=filing_artifact or artifact(),
        extracted_at=COMMITTED_AT + timedelta(minutes=1),
    )
    return StoredFundamentalFilingEvidence.from_validated(
        evidence=evidence,
        validation=_validation(evidence),
        stored_at=COMMITTED_AT + timedelta(minutes=2),
    )


class FundamentalFilingEvidenceCoordinatorTests(unittest.TestCase):
    def _coordinator(
        self,
        *,
        gateway=None,
        filing_repository=None,
        evidence_repository=None,
        extractor=None,
        validator=None,
    ):
        return FundamentalFilingEvidenceCoordinator(
            gateway=gateway or StubGateway(),
            filing_repository=(
                filing_repository
                or InMemoryFundamentalFilingRepository(clock=lambda: NOW)
            ),
            evidence_repository=(
                evidence_repository
                or InMemoryFundamentalFilingEvidenceRepository(clock=lambda: NOW)
            ),
            extractor=extractor or StubExtractor(),
            validator=validator or StubValidator(),
            clock=lambda: NOW,
        )

    def test_reuses_fresh_complete_package_without_provider_or_extraction(self):
        gateway = StubGateway()
        extractor = StubExtractor()
        filing_repository = InMemoryFundamentalFilingRepository(
            clock=lambda: NOW,
            initial_packages=(package(),),
        )
        evidence_repository = InMemoryFundamentalFilingEvidenceRepository(
            clock=lambda: NOW,
            initial_records=(_stored_record(),),
        )
        coordinator = self._coordinator(
            gateway=gateway,
            filing_repository=filing_repository,
            evidence_repository=evidence_repository,
            extractor=extractor,
        )

        result = coordinator.coordinate(_discovery_request())

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.CACHE_REUSED,
        )
        self.assertFalse(result.provider_called)
        self.assertEqual(gateway.discovery_calls, [])
        self.assertEqual(extractor.calls, [])
        self.assertEqual(len(result.evidence_records), 1)

    def test_extracts_missing_policy_evidence_from_cache_without_provider(self):
        gateway = StubGateway()
        extractor = StubExtractor()
        evidence_repository = InMemoryFundamentalFilingEvidenceRepository(
            clock=lambda: NOW
        )
        coordinator = self._coordinator(
            gateway=gateway,
            filing_repository=InMemoryFundamentalFilingRepository(
                clock=lambda: NOW,
                initial_packages=(package(),),
            ),
            evidence_repository=evidence_repository,
            extractor=extractor,
        )

        result = coordinator.coordinate(_discovery_request())

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.CACHE_REUSED,
        )
        self.assertEqual(len(extractor.calls), 1)
        self.assertEqual(gateway.discovery_calls, [])
        self.assertTrue(result.evidence_records[0].releaseable)

    def test_cache_miss_publishes_only_after_every_artifact_is_releaseable(self):
        gateway = StubGateway()
        filing_repository = InMemoryFundamentalFilingRepository(clock=lambda: NOW)
        coordinator = self._coordinator(
            gateway=gateway,
            filing_repository=filing_repository,
        )

        result = coordinator.coordinate(_discovery_request())

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.PACKAGE_PUBLISHED,
        )
        self.assertTrue(result.provider_called)
        self.assertEqual(len(gateway.discovery_calls), 1)
        self.assertEqual(len(gateway.acquisition_calls), 1)
        self.assertEqual(len(result.evidence_records), 1)
        self.assertEqual(
            filing_repository.get_active_filing_package(scope=result.scope),
            result.selected_package,
        )

    def test_retry_reuses_exact_current_artifact_without_duplicate_download(self):
        gateway = StubGateway()
        filing_artifact = artifact()
        coordinator = self._coordinator(
            gateway=gateway,
            filing_repository=InMemoryFundamentalFilingRepository(
                clock=lambda: NOW,
                initial_artifacts=(filing_artifact,),
            ),
            evidence_repository=InMemoryFundamentalFilingEvidenceRepository(
                clock=lambda: NOW,
                initial_records=(
                    _stored_record(filing_artifact=filing_artifact),
                ),
            ),
        )

        result = coordinator.coordinate(_discovery_request())

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.PACKAGE_PUBLISHED,
        )
        self.assertEqual(len(gateway.discovery_calls), 1)
        self.assertEqual(gateway.acquisition_calls, [])

    def test_explicit_refresh_bypasses_fresh_active_cache(self):
        gateway = StubGateway()
        coordinator = self._coordinator(
            gateway=gateway,
            filing_repository=InMemoryFundamentalFilingRepository(
                clock=lambda: NOW,
                initial_packages=(package(),),
            ),
            evidence_repository=InMemoryFundamentalFilingEvidenceRepository(
                clock=lambda: NOW,
                initial_records=(_stored_record(),),
            ),
        )

        result = coordinator.coordinate(
            _discovery_request(),
            refresh_requested=True,
        )

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.PACKAGE_PUBLISHED,
        )
        self.assertTrue(result.refresh_requested)
        self.assertEqual(len(gateway.discovery_calls), 1)

    def test_provider_failure_preserves_the_active_package(self):
        active = package()
        repository = InMemoryFundamentalFilingRepository(
            clock=lambda: NOW,
            initial_packages=(active,),
        )
        coordinator = self._coordinator(
            gateway=StubGateway(
                discovery_status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE
            ),
            filing_repository=repository,
        )

        result = coordinator.coordinate(
            _discovery_request(),
            refresh_requested=True,
        )

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.
            REFRESH_FAILED_ACTIVE_PRESERVED,
        )
        self.assertEqual(result.selected_package, active)
        self.assertTrue(result.failure.retryable)
        self.assertEqual(
            repository.get_active_filing_package(scope=result.scope),
            active,
        )

    def test_failed_release_validation_never_activates_partial_package(self):
        filing_repository = InMemoryFundamentalFilingRepository(clock=lambda: NOW)
        evidence_repository = InMemoryFundamentalFilingEvidenceRepository(
            clock=lambda: NOW
        )
        coordinator = self._coordinator(
            filing_repository=filing_repository,
            evidence_repository=evidence_repository,
            validator=StubValidator(releaseable=False),
        )

        result = coordinator.coordinate(_discovery_request())

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.REFRESH_FAILED_NO_ACTIVE,
        )
        self.assertIsNone(result.selected_package)
        self.assertIsNone(
            filing_repository.get_active_filing_package(scope=result.scope)
        )
        stored = evidence_repository.list_filing_evidence_for_artifact(
            artifact().artifact_id,
            scope=result.scope,
            as_of=NOW,
        )
        self.assertEqual(len(stored), 1)
        self.assertFalse(stored[0].releaseable)

    def test_publication_conflict_keeps_the_concurrently_active_package(self):
        filing_repository = ConflictingFilingRepository()
        active = package()
        coordinator = self._coordinator(
            filing_repository=filing_repository,
            evidence_repository=InMemoryFundamentalFilingEvidenceRepository(
                clock=lambda: NOW,
                initial_records=(_stored_record(),),
            ),
        )

        result = coordinator.coordinate(
            _discovery_request(),
            refresh_requested=True,
        )

        self.assertEqual(
            result.status,
            FundamentalFilingEvidenceCoordinationStatus.
            PUBLICATION_CONFLICT_ACTIVE_PRESERVED,
        )
        self.assertEqual(result.selected_package, active)


if __name__ == "__main__":
    unittest.main()
