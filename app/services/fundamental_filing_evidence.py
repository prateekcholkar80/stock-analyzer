"""Cache-first acquisition and deterministic validation of company filings.

This service is the application boundary between provider discovery/download,
durable filing metadata, deterministic PDF extraction, and release validation.
It never invokes an LLM and never replaces the active filing package until
every discovered artifact has releaseable evidence under the current policies.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from threading import RLock
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from app.exceptions import (
    FundamentalGatewayAuthenticationError,
    FundamentalGatewayEntitlementError,
    FundamentalGatewayError,
    FundamentalResponseValidationError,
    StorageConflictError,
    StorageError,
)
from app.gateways.fundamentals import (
    FundamentalFilingAcquisitionRequest,
    FundamentalFilingAcquisitionResult,
    FundamentalFilingDiscoveryRequest,
    FundamentalFilingDiscoveryResult,
    FundamentalFilingGateway,
    FundamentalRetrievalStatus,
    validate_fundamental_response_binding,
)
from app.models.fundamental_filing_evidence import (
    FundamentalFilingEvidenceValidationResult,
    FundamentalFilingExtractedEvidence,
    FundamentalFilingExtractionPolicy,
)
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingFailure,
    FundamentalFilingFailureCategory,
    FundamentalFilingFailureStage,
    FundamentalFilingPackage,
    FundamentalFilingPackageManifest,
    FundamentalFilingPeriod,
    FundamentalFilingReference,
    FundamentalFilingSourceClass,
)
from app.models.fundamentals import FundamentalIssuerIdentity, FundamentalModel
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidencePolicyKey,
    FundamentalFilingEvidenceRepository,
    StoredFundamentalFilingEvidence,
    validation_policy_fingerprint,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepository,
    FundamentalFilingRepositoryScope,
)


FundamentalFilingEvidenceClock = Callable[[], datetime]


@runtime_checkable
class FundamentalFilingEvidenceExtractor(Protocol):
    """Deterministic extraction boundary used by the coordinator."""

    @property
    def policy(self) -> FundamentalFilingExtractionPolicy:
        ...

    def extract(
        self,
        artifact: FundamentalFilingArtifactManifest,
        *,
        extracted_at: datetime,
    ) -> FundamentalFilingExtractedEvidence:
        ...


@runtime_checkable
class FundamentalFilingEvidenceValidator(Protocol):
    """Deterministic release-validation boundary used by the coordinator."""

    policy_id: str
    policy_version: str

    def validate(
        self,
        evidence: FundamentalFilingExtractedEvidence,
        *,
        expected_issuer: FundamentalIssuerIdentity,
        expected_period: FundamentalFilingPeriod,
        expected_source_class: FundamentalFilingSourceClass,
    ) -> FundamentalFilingEvidenceValidationResult:
        ...


class FundamentalFilingEvidenceCoordinationStatus(StrEnum):
    CACHE_REUSED = "cache_reused"
    PACKAGE_PUBLISHED = "package_published"
    REFRESH_FAILED_ACTIVE_PRESERVED = "refresh_failed_active_preserved"
    REFRESH_FAILED_NO_ACTIVE = "refresh_failed_no_active"
    PUBLICATION_CONFLICT_ACTIVE_PRESERVED = (
        "publication_conflict_active_preserved"
    )
    PUBLICATION_CONFLICT_NO_ACTIVE = "publication_conflict_no_active"


class FundamentalFilingEvidenceCoordinationResult(FundamentalModel):
    """Immutable audit result separating an attempt from usable evidence."""

    schema_version: Literal[
        "jarvis.fundamental_filing_evidence_coordination.v1"
    ] = "jarvis.fundamental_filing_evidence_coordination.v1"
    status: FundamentalFilingEvidenceCoordinationStatus
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: FundamentalFilingRepositoryScope
    refresh_requested: bool
    provider_called: bool
    started_at: datetime
    completed_at: datetime
    selected_package: FundamentalFilingPackage | None = None
    evidence_records: tuple[StoredFundamentalFilingEvidence, ...] = Field(
        default=(),
        max_length=500,
    )
    failure: FundamentalFilingFailure | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("filing coordination time requires timezone")
        return value

    @model_validator(mode="after")
    def validate_outcome(self) -> "FundamentalFilingEvidenceCoordinationResult":
        if self.completed_at < self.started_at:
            raise ValueError("filing coordination completion predates its start")
        if self.selected_package is not None and (
            FundamentalFilingRepositoryScope.from_package(self.selected_package)
            != self.scope
        ):
            raise ValueError("selected filing package escapes its scope")
        if self.failure is not None and (
            self.failure.connection != self.scope.connection
            or self.failure.issuer != self.scope.issuer
        ):
            raise ValueError("filing failure escapes its coordination scope")

        succeeded = self.status in {
            FundamentalFilingEvidenceCoordinationStatus.CACHE_REUSED,
            FundamentalFilingEvidenceCoordinationStatus.PACKAGE_PUBLISHED,
        }
        if succeeded:
            if self.selected_package is None or self.failure is not None:
                raise ValueError("successful filing coordination is incomplete")
            records_by_artifact = {
                record.artifact_id: record for record in self.evidence_records
            }
            if (
                len(records_by_artifact) != len(self.evidence_records)
                or set(records_by_artifact)
                != set(self.selected_package.manifest.artifact_ids)
                or any(not record.releaseable for record in self.evidence_records)
            ):
                raise ValueError(
                    "successful filing coordination requires released evidence"
                )
        elif self.failure is None or self.evidence_records:
            raise ValueError("failed filing coordination must fail closed")

        preserved = self.status in {
            FundamentalFilingEvidenceCoordinationStatus.
            REFRESH_FAILED_ACTIVE_PRESERVED,
            FundamentalFilingEvidenceCoordinationStatus.
            PUBLICATION_CONFLICT_ACTIVE_PRESERVED,
        }
        no_active = self.status in {
            FundamentalFilingEvidenceCoordinationStatus.
            REFRESH_FAILED_NO_ACTIVE,
            FundamentalFilingEvidenceCoordinationStatus.
            PUBLICATION_CONFLICT_NO_ACTIVE,
        }
        if preserved and self.selected_package is None:
            raise ValueError("filing preservation state is inconsistent")
        if no_active and self.selected_package is not None:
            raise ValueError("no-active filing outcome cannot select a package")
        if self.status is FundamentalFilingEvidenceCoordinationStatus.CACHE_REUSED:
            if self.refresh_requested or self.provider_called:
                raise ValueError("cache reuse cannot perform a provider refresh")
        if (
            self.status
            is FundamentalFilingEvidenceCoordinationStatus.PACKAGE_PUBLISHED
            and not self.provider_called
        ):
            raise ValueError("package publication requires provider retrieval")
        return self


class FundamentalFilingEvidenceCoordinator:
    """Reuse a complete cache or atomically publish a fully validated refresh."""

    def __init__(
        self,
        *,
        gateway: FundamentalFilingGateway,
        filing_repository: FundamentalFilingRepository,
        evidence_repository: FundamentalFilingEvidenceRepository,
        extractor: FundamentalFilingEvidenceExtractor,
        validator: FundamentalFilingEvidenceValidator,
        artifact_verifier: Callable[[FundamentalFilingArtifactManifest], bool] | None = None,
        artifact_reconciler: Callable[[FundamentalFilingArtifactManifest], FundamentalFilingArtifactManifest] | None = None,
        clock: FundamentalFilingEvidenceClock | None = None,
    ) -> None:
        if not isinstance(gateway, FundamentalFilingGateway):
            raise TypeError("filing coordinator requires a filing gateway")
        if not isinstance(filing_repository, FundamentalFilingRepository):
            raise TypeError("filing coordinator requires a filing repository")
        if not isinstance(
            evidence_repository,
            FundamentalFilingEvidenceRepository,
        ):
            raise TypeError("filing coordinator requires an evidence repository")
        if not isinstance(extractor, FundamentalFilingEvidenceExtractor):
            raise TypeError("filing coordinator requires an evidence extractor")
        if not isinstance(validator, FundamentalFilingEvidenceValidator):
            raise TypeError("filing coordinator requires an evidence validator")
        if clock is not None and not callable(clock):
            raise TypeError("filing coordinator clock must be callable")
        if artifact_verifier is not None and not callable(artifact_verifier):
            raise TypeError("filing artifact verifier must be callable")
        if artifact_reconciler is not None and not callable(artifact_reconciler):
            raise TypeError("filing artifact reconciler must be callable")
        self._gateway = gateway
        self._filing_repository = filing_repository
        self._evidence_repository = evidence_repository
        self._extractor = extractor
        self._validator = validator
        self._artifact_verifier = artifact_verifier
        self._artifact_reconciler = artifact_reconciler
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()

    def coordinate(
        self,
        request: FundamentalFilingDiscoveryRequest,
        *,
        refresh_requested: bool = False,
    ) -> FundamentalFilingEvidenceCoordinationResult:
        """Prepare one complete filing package without exposing partial work."""

        value = FundamentalFilingDiscoveryRequest.model_validate(request)
        if value.operation_id is None or len(value.operation_id) > 128:
            raise ValueError(
                "filing coordination requires a bounded operation ID"
            )
        if not isinstance(refresh_requested, bool):
            raise TypeError("filing refresh flag must be boolean")
        scope = FundamentalFilingRepositoryScope(
            connection=value.connection,
            issuer=value.issuer,
        )
        with self._lock:
            return self._coordinate_locked(
                value,
                scope=scope,
                refresh_requested=refresh_requested,
            )

    def _coordinate_locked(
        self,
        request: FundamentalFilingDiscoveryRequest,
        *,
        scope: FundamentalFilingRepositoryScope,
        refresh_requested: bool,
    ) -> FundamentalFilingEvidenceCoordinationResult:
        started_at = self._now()
        try:
            active_before = self._filing_repository.get_active_filing_package(
                scope=scope
            )
        except StorageError:
            return self._failed(
                request=request,
                scope=scope,
                refresh_requested=refresh_requested,
                provider_called=False,
                started_at=started_at,
                active=None,
                stage=FundamentalFilingFailureStage.STORAGE,
                category=FundamentalFilingFailureCategory.STORAGE_UNAVAILABLE,
                retryable=True,
                message="The active filing cache could not be read safely.",
            )

        if (
            active_before is not None
            and not refresh_requested
            and not active_before.manifest.is_recheck_due(as_of=started_at)
            and self._cache_covers_request(active_before, request)
            and (
                self._artifact_verifier is None
                or all(self._artifact_verifier(item) for item in active_before.artifacts)
            )
        ):
            try:
                records = self._released_records(
                    active_before,
                    scope=scope,
                    as_of=started_at,
                    allow_extraction=True,
                )
            except StorageError:
                return self._failed(
                    request=request,
                    scope=scope,
                    refresh_requested=False,
                    provider_called=False,
                    started_at=started_at,
                    active=active_before,
                    stage=FundamentalFilingFailureStage.STORAGE,
                    category=FundamentalFilingFailureCategory.STORAGE_UNAVAILABLE,
                    retryable=True,
                    message="Cached filing evidence could not be read safely.",
                )
            except (TypeError, ValueError):
                return self._failed(
                    request=request,
                    scope=scope,
                    refresh_requested=False,
                    provider_called=False,
                    started_at=started_at,
                    active=active_before,
                    stage=FundamentalFilingFailureStage.VALIDATION,
                    category=FundamentalFilingFailureCategory.INVALID_CONTENT,
                    retryable=False,
                    message=(
                        "The cached filing package does not have releaseable "
                        "evidence under the active policies."
                    ),
                )
            return self._outcome(
                request=request,
                scope=scope,
                status=FundamentalFilingEvidenceCoordinationStatus.CACHE_REUSED,
                refresh_requested=False,
                provider_called=False,
                started_at=started_at,
                selected_package=active_before,
                evidence_records=records,
            )

        try:
            discovery_result = self._gateway.discover_filings(request=request)
            if not isinstance(discovery_result, FundamentalFilingDiscoveryResult):
                raise ValueError("filing gateway returned an invalid discovery")
            validate_fundamental_response_binding(request, discovery_result)
        except FundamentalGatewayError as exc:
            return self._provider_exception_failure(
                request=request,
                scope=scope,
                refresh_requested=refresh_requested,
                started_at=started_at,
                active=active_before,
                stage=FundamentalFilingFailureStage.DISCOVERY,
                exception=exc,
            )
        except (TypeError, ValueError):
            return self._failed(
                request=request,
                scope=scope,
                refresh_requested=refresh_requested,
                provider_called=True,
                started_at=started_at,
                active=active_before,
                stage=FundamentalFilingFailureStage.DISCOVERY,
                category=FundamentalFilingFailureCategory.INVALID_CONTENT,
                retryable=False,
                message="The provider filing discovery response was invalid.",
            )
        if discovery_result.status is not FundamentalRetrievalStatus.COMPLETED:
            category, retryable = self._retrieval_failure(
                discovery_result.status
            )
            return self._failed(
                request=request,
                scope=scope,
                refresh_requested=refresh_requested,
                provider_called=True,
                started_at=started_at,
                active=active_before,
                stage=FundamentalFilingFailureStage.DISCOVERY,
                category=category,
                retryable=retryable,
                message="The provider could not complete filing discovery.",
            )

        discovery = discovery_result.discovery
        if discovery is None:
            return self._failed(
                request=request,
                scope=scope,
                refresh_requested=refresh_requested,
                provider_called=True,
                started_at=started_at,
                active=active_before,
                stage=FundamentalFilingFailureStage.DISCOVERY,
                category=FundamentalFilingFailureCategory.INVALID_CONTENT,
                retryable=False,
                message="The completed discovery did not contain a snapshot.",
            )
        artifacts: list[FundamentalFilingArtifactManifest] = []
        records: list[StoredFundamentalFilingEvidence] = []
        failures: list[FundamentalFilingEvidenceCoordinationResult] = []
        for index, reference in enumerate(
            sorted(
                discovery.references,
                key=lambda item: item.filing_reference_id,
            )
        ):
            try:
                artifact = self._reusable_acquired_artifact(
                    reference,
                    scope=scope,
                    requested_at=request.requested_at,
                    as_of=self._now(),
                )
                newly_acquired = artifact is None
                result_completed_at = self._now()
                if artifact is None:
                    acquisition_request = FundamentalFilingAcquisitionRequest(
                        request_id=self._acquisition_request_id(
                            request.operation_id,
                            discovery.discovery_fingerprint,
                            reference.filing_reference_id,
                            index,
                        ),
                        operation_id=request.operation_id,
                        connection=request.connection,
                        requested_at=max(self._now(), reference.discovered_at),
                        issuer=request.issuer,
                        reference=reference,
                        discovery_fingerprint=discovery.discovery_fingerprint,
                    )
                    result = self._gateway.acquire_filing(
                        request=acquisition_request
                    )
                    if not isinstance(result, FundamentalFilingAcquisitionResult):
                        raise ValueError(
                            "filing gateway returned invalid acquisition"
                        )
                    validate_fundamental_response_binding(
                        acquisition_request,
                        result,
                    )
                    if result.status is not FundamentalRetrievalStatus.COMPLETED:
                        category, retryable = self._retrieval_failure(result.status)
                        failures.append(self._failed(
                            request=request,
                            scope=scope,
                            refresh_requested=refresh_requested,
                            provider_called=True,
                            started_at=started_at,
                            active=active_before,
                            stage=FundamentalFilingFailureStage.DOWNLOAD,
                            category=category,
                            retryable=retryable,
                            message=(
                                "The provider could not acquire a required filing."
                            ),
                            filing_reference_id=reference.filing_reference_id,
                        ))
                        continue
                    artifact = result.artifact
                    if artifact is None:
                        raise ValueError(
                            "completed filing acquisition omitted its artifact"
                        )
                    result_completed_at = result.completed_at
            except FundamentalGatewayError as exc:
                failures.append(self._provider_exception_failure(
                    request=request,
                    scope=scope,
                    refresh_requested=refresh_requested,
                    started_at=started_at,
                    active=active_before,
                    stage=FundamentalFilingFailureStage.DOWNLOAD,
                    exception=exc,
                    filing_reference_id=reference.filing_reference_id,
                ))
                continue
            except StorageError:
                failures.append(self._failed(
                    request=request,
                    scope=scope,
                    refresh_requested=refresh_requested,
                    provider_called=True,
                    started_at=started_at,
                    active=active_before,
                    stage=FundamentalFilingFailureStage.STORAGE,
                    category=FundamentalFilingFailureCategory.STORAGE_UNAVAILABLE,
                    retryable=True,
                    message="Stored filing versions could not be read safely.",
                    filing_reference_id=reference.filing_reference_id,
                ))
                continue
            except (TypeError, ValueError):
                failures.append(self._failed(
                    request=request,
                    scope=scope,
                    refresh_requested=refresh_requested,
                    provider_called=True,
                    started_at=started_at,
                    active=active_before,
                    stage=FundamentalFilingFailureStage.DOWNLOAD,
                    category=FundamentalFilingFailureCategory.INVALID_CONTENT,
                    retryable=False,
                    message="The provider filing artifact response was invalid.",
                    filing_reference_id=reference.filing_reference_id,
                ))
                continue
            try:
                if newly_acquired:
                    artifact = self._preserve_original_download(artifact, scope=scope)
                if self._artifact_reconciler is not None:
                    reconciled = self._artifact_reconciler(artifact)
                    original = artifact.model_dump(exclude_computed_fields=True)
                    corrected = reconciled.model_dump(exclude_computed_fields=True)
                    original.pop("page_count")
                    corrected.pop("page_count")
                    if original != corrected:
                        raise ValueError("filing reconciliation changed download provenance")
                    artifact = reconciled
                stored_artifact = self._filing_repository.save_filing_artifact(
                    artifact
                )
                record = self._released_record(
                    stored_artifact,
                    scope=scope,
                    as_of=max(self._now(), result_completed_at),
                    allow_extraction=True,
                )
            except StorageError:
                failures.append(self._failed(
                    request=request,
                    scope=scope,
                    refresh_requested=refresh_requested,
                    provider_called=True,
                    started_at=started_at,
                    active=active_before,
                    stage=FundamentalFilingFailureStage.STORAGE,
                    category=FundamentalFilingFailureCategory.STORAGE_UNAVAILABLE,
                    retryable=True,
                    message="A filing artifact could not be stored safely.",
                    filing_reference_id=reference.filing_reference_id,
                ))
                continue
            except (TypeError, ValueError):
                failures.append(self._failed(
                    request=request,
                    scope=scope,
                    refresh_requested=refresh_requested,
                    provider_called=True,
                    started_at=started_at,
                    active=active_before,
                    stage=FundamentalFilingFailureStage.VALIDATION,
                    category=FundamentalFilingFailureCategory.INVALID_CONTENT,
                    retryable=False,
                    message=(
                        "A required filing did not pass deterministic extraction "
                        "and validation."
                    ),
                    filing_reference_id=reference.filing_reference_id,
                ))
                continue
            artifacts.append(stored_artifact)
            records.append(record)

        if failures:
            # All independently acquired artifacts/evidence remain durable. The
            # analytical package stays fail-closed; report the first failure.
            return failures[0].model_copy(update={"completed_at": self._now()})

        committed_at = self._now()
        if artifacts:
            committed_at = max(
                committed_at,
                *(artifact.last_checked_at for artifact in artifacts),
            )
            recheck_after = committed_at + timedelta(days=10)
        else:
            recheck_after = committed_at + timedelta(days=10)
        try:
            candidate = FundamentalFilingPackage(
                schema_version="jarvis.fundamental_filing_package.v2",
                manifest=FundamentalFilingPackageManifest(
                    operation_id=request.operation_id,
                    connection=request.connection,
                    issuer=request.issuer,
                    requested_at=request.requested_at,
                    committed_at=committed_at,
                    discovery_fingerprint=discovery.discovery_fingerprint,
                    artifact_ids=tuple(item.artifact_id for item in artifacts),
                    recheck_after=recheck_after,
                ),
                discovery=discovery,
                artifacts=tuple(artifacts),
            )
            published = self._filing_repository.commit_filing_package(
                candidate,
                expected_active_package_id=(
                    active_before.package_id
                    if active_before is not None
                    else None
                ),
            )
        except StorageConflictError:
            active_after = self._safe_active(scope)
            return self._outcome(
                request=request,
                scope=scope,
                status=(
                    FundamentalFilingEvidenceCoordinationStatus.
                    PUBLICATION_CONFLICT_ACTIVE_PRESERVED
                    if active_after is not None
                    else FundamentalFilingEvidenceCoordinationStatus.
                    PUBLICATION_CONFLICT_NO_ACTIVE
                ),
                refresh_requested=refresh_requested,
                provider_called=True,
                started_at=started_at,
                selected_package=active_after,
                failure=self._failure(
                    request=request,
                    stage=FundamentalFilingFailureStage.PACKAGE_PUBLICATION,
                    category=(
                        FundamentalFilingFailureCategory.STORAGE_UNAVAILABLE
                    ),
                    retryable=True,
                    message=(
                        "Another filing refresh changed the active package; "
                        "the candidate was not activated."
                    ),
                ),
            )
        except (StorageError, TypeError, ValueError):
            return self._failed(
                request=request,
                scope=scope,
                refresh_requested=refresh_requested,
                provider_called=True,
                started_at=started_at,
                active=active_before,
                stage=FundamentalFilingFailureStage.PACKAGE_PUBLICATION,
                category=FundamentalFilingFailureCategory.STORAGE_UNAVAILABLE,
                retryable=True,
                message="The complete filing package could not be published.",
            )
        return self._outcome(
            request=request,
            scope=scope,
            status=(
                FundamentalFilingEvidenceCoordinationStatus.PACKAGE_PUBLISHED
            ),
            refresh_requested=refresh_requested,
            provider_called=True,
            started_at=started_at,
            selected_package=published,
            evidence_records=tuple(records),
        )

    def _released_records(
        self,
        package: FundamentalFilingPackage,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
        allow_extraction: bool,
    ) -> tuple[StoredFundamentalFilingEvidence, ...]:
        return tuple(
            self._released_record(
                artifact,
                scope=scope,
                as_of=as_of,
                allow_extraction=allow_extraction,
            )
            for artifact in package.artifacts
        )

    def _preserve_original_download(
        self,
        artifact: FundamentalFilingArtifactManifest,
        *,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingArtifactManifest:
        """Carry source acquisition history into a new immutable download."""
        versions = self._filing_repository.list_filing_artifact_versions(
            artifact.reference.filing_reference_id, scope=scope,
        )
        timestamps = [
            item.first_downloaded_at for item in versions
            if item.reference.same_source_as(artifact.reference)
            and item.downloaded_at <= artifact.downloaded_at
        ]
        if not timestamps:
            return artifact
        original = min(artifact.first_downloaded_at, *timestamps)
        values = artifact.model_dump(exclude_computed_fields=True)
        values.update(
            schema_version="jarvis.fundamental_filing_artifact.v2",
            original_downloaded_at=original,
        )
        return FundamentalFilingArtifactManifest.model_validate(values)

    def _reusable_acquired_artifact(
        self,
        reference: FundamentalFilingReference,
        *,
        scope: FundamentalFilingRepositoryScope,
        requested_at: datetime,
        as_of: datetime,
    ) -> FundamentalFilingArtifactManifest | None:
        """Reuse a scoped source URL only after verifying its retained bytes.

        Rediscovery/refresh does not mean re-download. Preserve the original
        discovery and download metadata, including unresolved period labels.
        """

        versions = self._filing_repository.list_filing_artifact_versions(
            reference.filing_reference_id,
            scope=scope,
        )
        for candidate in reversed(versions):
            if self._artifact_verifier is not None:
                if (
                    candidate.reference.same_source_as(reference)
                    and candidate.downloaded_at <= as_of
                    and self._artifact_verifier(candidate)
                ):
                    return candidate
                continue
            if (
                candidate.reference == reference
                and candidate.downloaded_at >= requested_at
                and not candidate.is_recheck_due(as_of=as_of)
            ):
                return candidate
        return None

    def _released_record(
        self,
        artifact: FundamentalFilingArtifactManifest,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
        allow_extraction: bool,
    ) -> StoredFundamentalFilingEvidence:
        policy_key = FundamentalFilingEvidencePolicyKey(
            artifact_id=artifact.artifact_id,
            artifact_fingerprint=artifact.artifact_fingerprint,
            extraction_policy_fingerprint=self._extractor.policy.policy_fingerprint,
            validation_policy_fingerprint=validation_policy_fingerprint(
                policy_id=self._validator.policy_id,
                policy_version=self._validator.policy_version,
            ),
        )
        existing = self._evidence_repository.find_filing_evidence_by_policy(
            policy_key,
            scope=scope,
            as_of=as_of,
        )
        for record in reversed(existing):
            if record.releaseable:
                return record
        if existing or not allow_extraction:
            raise ValueError("filing evidence is not releaseable")

        extracted_at = max(self._now(), artifact.last_checked_at)
        evidence = self._extractor.extract(
            artifact,
            extracted_at=extracted_at,
        )
        if evidence.artifact != artifact:
            raise ValueError("filing extractor changed its artifact binding")
        validation = self._validator.validate(
            evidence,
            expected_issuer=artifact.reference.issuer,
            expected_period=artifact.reference.period,
            expected_source_class=artifact.reference.source_class,
        )
        if validation.extraction_fingerprint != evidence.extraction_fingerprint:
            raise ValueError("filing validator changed its extraction binding")
        record = StoredFundamentalFilingEvidence.from_validated(
            evidence=evidence,
            validation=validation,
            stored_at=max(self._now(), evidence.extracted_at),
        )
        stored = self._evidence_repository.save_filing_evidence(record)
        if not stored.releaseable:
            raise ValueError("filing evidence did not pass release validation")
        return stored

    @staticmethod
    def _cache_covers_request(
        package: FundamentalFilingPackage,
        request: FundamentalFilingDiscoveryRequest,
    ) -> bool:
        discovery = package.discovery
        return (
            discovery.categories == request.categories
            and discovery.from_fiscal_year == request.from_fiscal_year
            and discovery.max_documents == request.max_documents
        )

    @staticmethod
    def _acquisition_request_id(
        operation_id: str,
        discovery_fingerprint: str,
        filing_reference_id: str,
        index: int,
    ) -> str:
        digest = sha256(
            (
                f"{operation_id}:{discovery_fingerprint}:"
                f"{filing_reference_id}:{index}"
            ).encode("utf-8")
        ).hexdigest()
        return f"filing.acquire.{digest}"

    def _provider_exception_failure(
        self,
        *,
        request: FundamentalFilingDiscoveryRequest,
        scope: FundamentalFilingRepositoryScope,
        refresh_requested: bool,
        started_at: datetime,
        active: FundamentalFilingPackage | None,
        stage: FundamentalFilingFailureStage,
        exception: FundamentalGatewayError,
        filing_reference_id: str | None = None,
    ) -> FundamentalFilingEvidenceCoordinationResult:
        if isinstance(exception, FundamentalGatewayAuthenticationError):
            category = (
                FundamentalFilingFailureCategory.AUTHENTICATION_UNAVAILABLE
            )
        elif isinstance(exception, FundamentalGatewayEntitlementError):
            category = FundamentalFilingFailureCategory.NOT_ENTITLED
        elif isinstance(exception, FundamentalResponseValidationError):
            category = FundamentalFilingFailureCategory.INVALID_CONTENT
        else:
            category = FundamentalFilingFailureCategory.PROVIDER_UNAVAILABLE
        return self._failed(
            request=request,
            scope=scope,
            refresh_requested=refresh_requested,
            provider_called=True,
            started_at=started_at,
            active=active,
            stage=stage,
            category=category,
            retryable=exception.context.retryable,
            message="The filing provider could not complete the request safely.",
            filing_reference_id=filing_reference_id,
        )

    @staticmethod
    def _retrieval_failure(
        status: FundamentalRetrievalStatus,
    ) -> tuple[FundamentalFilingFailureCategory, bool]:
        if status in {
            FundamentalRetrievalStatus.NOT_ENTITLED,
            FundamentalRetrievalStatus.PAYWALLED,
        }:
            return FundamentalFilingFailureCategory.NOT_ENTITLED, False
        if status is FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE:
            return FundamentalFilingFailureCategory.PROVIDER_UNAVAILABLE, True
        if status is FundamentalRetrievalStatus.NOT_FOUND:
            return FundamentalFilingFailureCategory.INVALID_REFERENCE, False
        return FundamentalFilingFailureCategory.INVALID_CONTENT, False

    def _failed(
        self,
        *,
        request: FundamentalFilingDiscoveryRequest,
        scope: FundamentalFilingRepositoryScope,
        refresh_requested: bool,
        provider_called: bool,
        started_at: datetime,
        active: FundamentalFilingPackage | None,
        stage: FundamentalFilingFailureStage,
        category: FundamentalFilingFailureCategory,
        retryable: bool,
        message: str,
        filing_reference_id: str | None = None,
    ) -> FundamentalFilingEvidenceCoordinationResult:
        return self._outcome(
            request=request,
            scope=scope,
            status=(
                FundamentalFilingEvidenceCoordinationStatus.
                REFRESH_FAILED_ACTIVE_PRESERVED
                if active is not None
                else FundamentalFilingEvidenceCoordinationStatus.
                REFRESH_FAILED_NO_ACTIVE
            ),
            refresh_requested=refresh_requested,
            provider_called=provider_called,
            started_at=started_at,
            selected_package=active,
            failure=self._failure(
                request=request,
                stage=stage,
                category=category,
                retryable=retryable,
                message=message,
                filing_reference_id=filing_reference_id,
            ),
        )

    def _failure(
        self,
        *,
        request: FundamentalFilingDiscoveryRequest,
        stage: FundamentalFilingFailureStage,
        category: FundamentalFilingFailureCategory,
        retryable: bool,
        message: str,
        filing_reference_id: str | None = None,
    ) -> FundamentalFilingFailure:
        assert request.operation_id is not None
        return FundamentalFilingFailure(
            operation_id=request.operation_id,
            connection=request.connection,
            issuer=request.issuer,
            filing_reference_id=filing_reference_id,
            stage=stage,
            category=category,
            retryable=retryable,
            occurred_at=self._now(),
            sanitized_message=message,
        )

    def _safe_active(
        self,
        scope: FundamentalFilingRepositoryScope,
    ) -> FundamentalFilingPackage | None:
        try:
            return self._filing_repository.get_active_filing_package(scope=scope)
        except StorageError:
            return None

    def _outcome(
        self,
        *,
        request: FundamentalFilingDiscoveryRequest,
        scope: FundamentalFilingRepositoryScope,
        status: FundamentalFilingEvidenceCoordinationStatus,
        refresh_requested: bool,
        provider_called: bool,
        started_at: datetime,
        selected_package: FundamentalFilingPackage | None,
        evidence_records: tuple[StoredFundamentalFilingEvidence, ...] = (),
        failure: FundamentalFilingFailure | None = None,
    ) -> FundamentalFilingEvidenceCoordinationResult:
        return FundamentalFilingEvidenceCoordinationResult(
            status=status,
            request_fingerprint=request.request_fingerprint,
            scope=scope,
            refresh_requested=refresh_requested,
            provider_called=provider_called,
            started_at=started_at,
            completed_at=max(self._now(), started_at),
            selected_package=selected_package,
            evidence_records=evidence_records,
            failure=failure,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("filing coordinator clock requires timezone")
        return value
