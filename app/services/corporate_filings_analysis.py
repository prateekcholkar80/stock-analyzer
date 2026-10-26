"""Cache-aware coordination for grounded corporate-filings analysis."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import ConfigDict, model_validator

from app.exceptions import StorageConflictError
from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisResult,
    validate_corporate_filings_analysis_response,
)
from app.models.technical import TechnicalModel
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisPeriodKey,
    CorporateFilingsAnalysisRepository,
    CorporateFilingsAnalysisRepositoryScope,
    StoredCorporateFilingsAnalysis,
)


CorporateFilingsAnalysisCoordinatorClock = Callable[[], datetime]


@runtime_checkable
class CorporateFilingsAnalysisGenerator(Protocol):
    """Provider-neutral boundary implemented by the grounded LLM agent."""

    def analyze(
        self,
        request: CorporateFilingsAnalysisRequest,
    ) -> CorporateFilingsAnalysisResult:
        ...


class CorporateFilingsAnalysisCoordinationStatus(StrEnum):
    EXACT_CACHE_REUSED = "exact_cache_reused"
    ANALYSIS_PUBLISHED = "analysis_published"
    ANALYSIS_FAILED_ACTIVE_PRESERVED = (
        "analysis_failed_active_preserved"
    )
    ANALYSIS_FAILED_NO_ACTIVE = "analysis_failed_no_active"
    PUBLICATION_CONFLICT_ACTIVE_PRESERVED = (
        "publication_conflict_active_preserved"
    )
    PUBLICATION_CONFLICT_NO_ACTIVE = "publication_conflict_no_active"


class CorporateFilingsAnalysisCoordinationResult(TechnicalModel):
    """Immutable audit result separating an attempt from usable evidence."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[
        "jarvis.corporate_filings_analysis_coordination.v1"
    ] = "jarvis.corporate_filings_analysis_coordination.v1"
    status: CorporateFilingsAnalysisCoordinationStatus
    request_fingerprint: str
    scope: CorporateFilingsAnalysisRepositoryScope
    period_key: CorporateFilingsAnalysisPeriodKey
    refresh_requested: bool
    analysis_result: CorporateFilingsAnalysisResult
    selected_analysis: StoredCorporateFilingsAnalysis | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.analysis_result.request_fingerprint != self.request_fingerprint:
            raise ValueError("coordinated analysis changes its request binding")
        selected = self.selected_analysis
        if selected is not None and (
            selected.scope != self.scope
            or selected.period_key != self.period_key
        ):
            raise ValueError("selected analysis escapes its repository scope")

        cache_or_publish = self.status in {
            CorporateFilingsAnalysisCoordinationStatus.EXACT_CACHE_REUSED,
            CorporateFilingsAnalysisCoordinationStatus.ANALYSIS_PUBLISHED,
        }
        failure_preserved = self.status is (
            CorporateFilingsAnalysisCoordinationStatus.
            ANALYSIS_FAILED_ACTIVE_PRESERVED
        )
        failure_without_active = self.status is (
            CorporateFilingsAnalysisCoordinationStatus.
            ANALYSIS_FAILED_NO_ACTIVE
        )
        conflict_preserved = self.status is (
            CorporateFilingsAnalysisCoordinationStatus.
            PUBLICATION_CONFLICT_ACTIVE_PRESERVED
        )
        conflict_without_active = self.status is (
            CorporateFilingsAnalysisCoordinationStatus.
            PUBLICATION_CONFLICT_NO_ACTIVE
        )

        if cache_or_publish:
            if (
                selected is None
                or not self.analysis_result.releaseable
                or selected.result != self.analysis_result
            ):
                raise ValueError(
                    "reused or published analysis is not chain-bound"
                )
        elif failure_preserved or failure_without_active:
            if self.analysis_result.releaseable:
                raise ValueError("failed analysis outcome released a result")
            if failure_preserved != (selected is not None):
                raise ValueError(
                    "failed analysis preservation state is inconsistent"
                )
        elif conflict_preserved or conflict_without_active:
            if not self.analysis_result.releaseable:
                raise ValueError(
                    "publication conflict requires a releaseable candidate"
                )
            if conflict_preserved != (selected is not None):
                raise ValueError(
                    "publication-conflict preservation state is inconsistent"
                )

        if (
            self.status
            is CorporateFilingsAnalysisCoordinationStatus.EXACT_CACHE_REUSED
            and self.refresh_requested
        ):
            raise ValueError("explicit refresh cannot reuse exact cache")
        return self


class CorporateFilingsAnalysisCoordinator:
    """Reuse exact analysis or atomically publish one grounded LLM result."""

    def __init__(
        self,
        *,
        analyzer: CorporateFilingsAnalysisGenerator,
        repository: CorporateFilingsAnalysisRepository,
        clock: CorporateFilingsAnalysisCoordinatorClock | None = None,
    ) -> None:
        if not isinstance(analyzer, CorporateFilingsAnalysisGenerator):
            raise TypeError(
                "corporate filings coordinator requires an analysis generator"
            )
        if not isinstance(repository, CorporateFilingsAnalysisRepository):
            raise TypeError(
                "corporate filings coordinator requires its repository"
            )
        if clock is not None and not callable(clock):
            raise TypeError("corporate filings coordinator clock must be callable")
        self._analyzer = analyzer
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def coordinate(
        self,
        request: CorporateFilingsAnalysisRequest,
        *,
        refresh_requested: bool = False,
    ) -> CorporateFilingsAnalysisCoordinationResult:
        value = CorporateFilingsAnalysisRequest.model_validate(request)
        if not isinstance(refresh_requested, bool):
            raise TypeError(
                "corporate filings analysis refresh flag must be boolean"
            )
        scope = CorporateFilingsAnalysisRepositoryScope.from_request(value)
        period_key = CorporateFilingsAnalysisPeriodKey.from_request(value)
        started_at = self._now()
        cached = self._repository.find_corporate_filings_analysis_by_reuse_key(
            value.reuse_key,
            scope=scope,
            as_of=started_at,
        )
        if cached is not None and not refresh_requested:
            return self._outcome(
                request=value,
                status=(
                    CorporateFilingsAnalysisCoordinationStatus.
                    EXACT_CACHE_REUSED
                ),
                refresh_requested=False,
                analysis_result=cached.result,
                selected_analysis=cached,
            )

        active_before = self._repository.get_active_corporate_filings_analysis(
            period_key,
            scope=scope,
            as_of=started_at,
        )
        analysis_result = self._analyzer.analyze(value)
        if not isinstance(analysis_result, CorporateFilingsAnalysisResult):
            raise ValueError(
                "corporate filings analyzer returned an invalid result"
            )
        validate_corporate_filings_analysis_response(value, analysis_result)
        if not analysis_result.releaseable:
            active_after = self._active_at_completion(
                period_key=period_key,
                scope=scope,
            )
            return self._outcome(
                request=value,
                status=(
                    CorporateFilingsAnalysisCoordinationStatus.
                    ANALYSIS_FAILED_ACTIVE_PRESERVED
                    if active_after is not None
                    else CorporateFilingsAnalysisCoordinationStatus.
                    ANALYSIS_FAILED_NO_ACTIVE
                ),
                refresh_requested=refresh_requested,
                analysis_result=analysis_result,
                selected_analysis=active_after,
            )

        candidate = (
            cached
            if cached is not None and cached.result == analysis_result
            else StoredCorporateFilingsAnalysis.from_validated(
                request=value,
                result=analysis_result,
                stored_at=self._now(),
            )
        )
        try:
            persisted = self._repository.commit_corporate_filings_analysis(
                candidate,
                expected_active_record_id=(
                    active_before.record_id
                    if active_before is not None
                    else None
                ),
            )
        except StorageConflictError:
            active_after = self._active_at_completion(
                period_key=period_key,
                scope=scope,
            )
            return self._outcome(
                request=value,
                status=(
                    CorporateFilingsAnalysisCoordinationStatus.
                    PUBLICATION_CONFLICT_ACTIVE_PRESERVED
                    if active_after is not None
                    else CorporateFilingsAnalysisCoordinationStatus.
                    PUBLICATION_CONFLICT_NO_ACTIVE
                ),
                refresh_requested=refresh_requested,
                analysis_result=analysis_result,
                selected_analysis=active_after,
            )
        return self._outcome(
            request=value,
            status=(
                CorporateFilingsAnalysisCoordinationStatus.ANALYSIS_PUBLISHED
            ),
            refresh_requested=refresh_requested,
            analysis_result=analysis_result,
            selected_analysis=persisted,
        )

    def _active_at_completion(
        self,
        *,
        period_key: CorporateFilingsAnalysisPeriodKey,
        scope: CorporateFilingsAnalysisRepositoryScope,
    ) -> StoredCorporateFilingsAnalysis | None:
        return self._repository.get_active_corporate_filings_analysis(
            period_key,
            scope=scope,
            as_of=self._now(),
        )

    def _outcome(
        self,
        *,
        request: CorporateFilingsAnalysisRequest,
        status: CorporateFilingsAnalysisCoordinationStatus,
        refresh_requested: bool,
        analysis_result: CorporateFilingsAnalysisResult,
        selected_analysis: StoredCorporateFilingsAnalysis | None,
    ) -> CorporateFilingsAnalysisCoordinationResult:
        return CorporateFilingsAnalysisCoordinationResult(
            status=status,
            request_fingerprint=request.request_fingerprint,
            scope=CorporateFilingsAnalysisRepositoryScope.from_request(
                request
            ),
            period_key=CorporateFilingsAnalysisPeriodKey.from_request(request),
            refresh_requested=refresh_requested,
            analysis_result=analysis_result,
            selected_analysis=selected_analysis,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "corporate filings coordinator clock requires timezone"
            )
        return value
