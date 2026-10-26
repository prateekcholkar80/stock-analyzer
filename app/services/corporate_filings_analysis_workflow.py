"""Single application entry point for grounded corporate-filings analysis."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import ConfigDict, model_validator

from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisPolicy,
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisResult,
)
from app.models.technical import TechnicalModel
from app.services.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoordinationResult,
    CorporateFilingsAnalysisCoordinator,
)
from app.services.corporate_filings_analysis_assembly import (
    CorporateFilingsAnalysisAssemblyInput,
    CorporateFilingsAnalysisRequestAssembler,
)
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisRepository,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidenceRepository,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepository,
)


@runtime_checkable
class CorporateFilingsAnalysisRuntime(Protocol):
    """One verified runtime that owns policy and performs grounded analysis."""

    def build_analysis_policy(self) -> CorporateFilingsAnalysisPolicy:
        ...

    def analyze(
        self,
        request: CorporateFilingsAnalysisRequest,
    ) -> CorporateFilingsAnalysisResult:
        ...


class CorporateFilingsAnalysisWorkflowResult(TechnicalModel):
    """Immutable audit aggregate for one assembled and coordinated request."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[
        "jarvis.corporate_filings_analysis_workflow.v1"
    ] = "jarvis.corporate_filings_analysis_workflow.v1"
    request: CorporateFilingsAnalysisRequest
    coordination: CorporateFilingsAnalysisCoordinationResult

    @model_validator(mode="after")
    def validate_binding(self) -> "CorporateFilingsAnalysisWorkflowResult":
        if (
            self.coordination.request_fingerprint
            != self.request.request_fingerprint
        ):
            raise ValueError(
                "filing-analysis workflow result changes its request binding"
            )
        return self


class CorporateFilingsAnalysisWorkflow:
    """Assemble released evidence and coordinate one cache-aware analysis."""

    def __init__(
        self,
        *,
        analyst: CorporateFilingsAnalysisRuntime,
        filing_repository: FundamentalFilingRepository,
        evidence_repository: FundamentalFilingEvidenceRepository,
        analysis_repository: CorporateFilingsAnalysisRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(analyst, CorporateFilingsAnalysisRuntime):
            raise TypeError(
                "corporate filings workflow requires one verified analyst"
            )
        self._analyst = analyst
        self._assembler = CorporateFilingsAnalysisRequestAssembler(
            filing_repository=filing_repository,
            evidence_repository=evidence_repository,
            policy_provider=analyst,
        )
        self._coordinator = CorporateFilingsAnalysisCoordinator(
            analyzer=analyst,
            repository=analysis_repository,
            clock=clock,
        )

    def execute(
        self,
        value: CorporateFilingsAnalysisAssemblyInput,
        *,
        refresh_requested: bool = False,
    ) -> CorporateFilingsAnalysisWorkflowResult:
        """Run deterministic assembly followed by cache-aware analysis."""

        request = self._assembler.assemble(value)
        coordination = self._coordinator.coordinate(
            request,
            refresh_requested=refresh_requested,
        )
        return CorporateFilingsAnalysisWorkflowResult(
            request=request,
            coordination=coordination,
        )
