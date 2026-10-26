"""Deterministic assembly of grounded quarterly filing-analysis requests."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, ValidationError, field_validator, model_validator

from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisChunk,
    CorporateFilingsAnalysisDocument,
    CorporateFilingsAnalysisKind,
    CorporateFilingsAnalysisPolicy,
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisSpeakerReference,
    CorporateFilingsQuarterlyBundle,
    CorporateFilingsSourceQuality,
)
from app.models.fundamental_filing_evidence import (
    FundamentalFilingCitation,
    FundamentalFilingParticipantAffiliation,
    FundamentalFilingSpeakerRole,
)
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingCategory,
    FundamentalFilingPackage,
    FundamentalFilingPeriod,
    FundamentalFilingPeriodState,
    FundamentalFilingPeriodType,
)
from app.models.fundamentals import FundamentalModel
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidenceRepository,
    StoredFundamentalFilingEvidence,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepository,
    FundamentalFilingRepositoryScope,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_DEFAULT_MAX_CHUNK_CHARACTERS = 40_000
_MAX_CHUNK_CHARACTERS = 60_000
_MAX_CITATIONS_PER_CHUNK = 2_000

_REQUIRED_CATEGORIES = {
    CorporateFilingsAnalysisKind.CONFERENCE_CALL: (
        FundamentalFilingCategory.CONFERENCE_CALL,
    ),
    CorporateFilingsAnalysisKind.EARNINGS_RELEASE: (
        FundamentalFilingCategory.EARNINGS_RELEASE,
    ),
    CorporateFilingsAnalysisKind.QUARTERLY_COMBINED: (
        FundamentalFilingCategory.CONFERENCE_CALL,
        FundamentalFilingCategory.EARNINGS_RELEASE,
    ),
}

_SOURCE_QUALITY = {
    FundamentalFilingCategory.CONFERENCE_CALL: (
        CorporateFilingsSourceQuality.VERBATIM_TRANSCRIPT
    ),
    FundamentalFilingCategory.EARNINGS_RELEASE: (
        CorporateFilingsSourceQuality.PRIMARY_COMPANY_DISCLOSURE
    ),
}


class CorporateFilingsAnalysisAssemblyError(ValueError):
    """Safe fail-closed outcome for unavailable or ambiguous released input."""


@runtime_checkable
class CorporateFilingsAnalysisPolicyProvider(Protocol):
    """Trusted runtime boundary for the exact filing-analysis policy."""

    def build_analysis_policy(self) -> CorporateFilingsAnalysisPolicy:
        ...


class CorporateFilingsAnalysisAssemblyInput(FundamentalModel):
    """Immutable point-in-time identity for one assembly attempt."""

    schema_version: Literal[
        "jarvis.corporate_filings_analysis_assembly_input.v1"
    ] = "jarvis.corporate_filings_analysis_assembly_input.v1"
    request_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    operation_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    scope: FundamentalFilingRepositoryScope
    period: FundamentalFilingPeriod
    analysis_kind: CorporateFilingsAnalysisKind
    requested_at: datetime
    assembled_at: datetime
    as_of: datetime

    @field_validator("requested_at", "assembled_at", "as_of")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("filing analysis assembly timestamp requires timezone")
        return value

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if (
            self.period.state is not FundamentalFilingPeriodState.RESOLVED
            or self.period.period_type
            is not FundamentalFilingPeriodType.QUARTERLY
            or self.period.fiscal_year is None
            or self.period.fiscal_quarter is None
        ):
            raise ValueError("filing analysis assembly requires a resolved quarter")
        if not self.requested_at <= self.assembled_at <= self.as_of:
            raise ValueError("filing analysis assembly timeline is inconsistent")
        return self


class CorporateFilingsAnalysisRequestAssembler:
    """Read one active package and release only validated quarterly evidence."""

    def __init__(
        self,
        *,
        filing_repository: FundamentalFilingRepository,
        evidence_repository: FundamentalFilingEvidenceRepository,
        policy_provider: CorporateFilingsAnalysisPolicyProvider,
        max_chunk_characters: int = _DEFAULT_MAX_CHUNK_CHARACTERS,
    ) -> None:
        if not isinstance(filing_repository, FundamentalFilingRepository):
            raise TypeError("filing analysis assembler requires a filing repository")
        if not isinstance(
            evidence_repository,
            FundamentalFilingEvidenceRepository,
        ):
            raise TypeError(
                "filing analysis assembler requires a filing evidence repository"
            )
        if not isinstance(
            policy_provider,
            CorporateFilingsAnalysisPolicyProvider,
        ):
            raise TypeError(
                "filing analysis assembler requires a verified policy provider"
            )
        if (
            not isinstance(max_chunk_characters, int)
            or isinstance(max_chunk_characters, bool)
            or max_chunk_characters < 1_000
            or max_chunk_characters > _MAX_CHUNK_CHARACTERS
        ):
            raise ValueError("filing analysis chunk limit is invalid")
        self._filing_repository = filing_repository
        self._evidence_repository = evidence_repository
        self._policy_provider = policy_provider
        self._max_chunk_characters = max_chunk_characters

    def assemble(
        self,
        value: CorporateFilingsAnalysisAssemblyInput,
    ) -> CorporateFilingsAnalysisRequest:
        assembly = CorporateFilingsAnalysisAssemblyInput.model_validate(value)
        package = self._filing_repository.get_active_filing_package(
            scope=assembly.scope
        )
        if package is None:
            raise CorporateFilingsAnalysisAssemblyError(
                "No active filing package is available for this issuer."
            )
        package = FundamentalFilingPackage.model_validate(package)
        if FundamentalFilingRepositoryScope.from_package(package) != assembly.scope:
            raise CorporateFilingsAnalysisAssemblyError(
                "The active filing package does not match the requested scope."
            )
        if package.manifest.committed_at > assembly.assembled_at:
            raise CorporateFilingsAnalysisAssemblyError(
                "The active filing package was not available at the requested time."
            )

        artifacts = self._select_artifacts(
            package,
            period=assembly.period,
            analysis_kind=assembly.analysis_kind,
        )
        documents = tuple(
            self._build_document(
                artifact,
                scope=assembly.scope,
                as_of=assembly.assembled_at,
            )
            for artifact in artifacts
        )
        bundle = CorporateFilingsQuarterlyBundle(
            connection=assembly.scope.connection,
            issuer=assembly.scope.issuer,
            filing_package_id=package.package_id,
            filing_package_fingerprint=package.manifest.manifest_fingerprint,
            period=assembly.period,
            analysis_kind=assembly.analysis_kind,
            assembled_at=assembly.assembled_at,
            documents=documents,
        )
        try:
            policy = CorporateFilingsAnalysisPolicy.model_validate(
                self._policy_provider.build_analysis_policy()
            )
        except (TypeError, ValidationError, ValueError) as exc:
            raise CorporateFilingsAnalysisAssemblyError(
                "The verified filing-analysis policy is unavailable."
            ) from exc
        return CorporateFilingsAnalysisRequest(
            request_id=assembly.request_id,
            operation_id=assembly.operation_id,
            bundle=bundle,
            policy=policy,
            requested_at=assembly.requested_at,
            as_of=assembly.as_of,
        )

    @staticmethod
    def _select_artifacts(
        package: FundamentalFilingPackage,
        *,
        period: FundamentalFilingPeriod,
        analysis_kind: CorporateFilingsAnalysisKind,
    ) -> tuple[FundamentalFilingArtifactManifest, ...]:
        selected: list[FundamentalFilingArtifactManifest] = []
        for category in _REQUIRED_CATEGORIES[analysis_kind]:
            candidates = tuple(
                artifact
                for artifact in package.artifacts
                if artifact.reference.category is category
                and _same_quarter(artifact.reference.period, period)
            )
            superseded_ids = {
                artifact.supersedes_artifact_id
                for artifact in candidates
                if artifact.supersedes_artifact_id is not None
            }
            effective = tuple(
                artifact
                for artifact in candidates
                if artifact.duplicate_of_artifact_id is None
                and artifact.artifact_id not in superseded_ids
            )
            if not effective:
                raise CorporateFilingsAnalysisAssemblyError(
                    f"No effective {category.value} is available for this quarter."
                )
            if len(effective) != 1:
                raise CorporateFilingsAnalysisAssemblyError(
                    f"The {category.value} artifact is ambiguous for this quarter."
                )
            selected.append(effective[0])
        return tuple(sorted(selected, key=lambda item: item.reference.category.value))

    def _build_document(
        self,
        artifact: FundamentalFilingArtifactManifest,
        *,
        scope: FundamentalFilingRepositoryScope,
        as_of: datetime,
    ) -> CorporateFilingsAnalysisDocument:
        versions = self._evidence_repository.list_filing_evidence_for_artifact(
            artifact.artifact_id,
            scope=scope,
            as_of=as_of,
        )
        releaseable = tuple(
            record
            for record in versions
            if record.releaseable
            and record.stored_at <= as_of
            and record.evidence.artifact == artifact
        )
        if not releaseable:
            raise CorporateFilingsAnalysisAssemblyError(
                "No releaseable extracted evidence is available for a required "
                f"{artifact.reference.category.value} artifact."
            )
        record = max(
            releaseable,
            key=lambda item: (
                item.evidence.extracted_at,
                item.stored_at,
                item.record_id,
            ),
        )
        evidence = record.evidence
        citations = tuple(sorted(evidence.citations, key=lambda item: item.citation_id))
        if not citations:
            raise CorporateFilingsAnalysisAssemblyError(
                "Releaseable filing evidence contains no source citations."
            )
        speakers = _speaker_references(record)
        chunks = _build_chunks(
            citations,
            speaker_ids={item.speaker_turn_id for item in speakers},
            max_characters=self._max_chunk_characters,
        )
        limitations = tuple(
            sorted(
                {
                    *(item.description for item in evidence.limitations),
                    *(
                        "Extraction warning "
                        f"{item.code.value}: {item.message}"
                        for item in evidence.warnings
                    ),
                }
            )
        )
        return CorporateFilingsAnalysisDocument(
            artifact_id=artifact.artifact_id,
            content_sha256=artifact.content_sha256,
            artifact_fingerprint=artifact.artifact_fingerprint,
            extraction_policy_fingerprint=(
                record.extraction_policy_fingerprint
            ),
            validation_policy_fingerprint=(
                record.validation_policy_fingerprint
            ),
            extraction_fingerprint=record.extraction_fingerprint,
            validation_fingerprint=record.validation_fingerprint,
            category=artifact.reference.category,
            source_class=artifact.reference.source_class,
            source_quality=_SOURCE_QUALITY[artifact.reference.category],
            extracted_at=evidence.extracted_at,
            citations=citations,
            speakers=speakers,
            chunks=chunks,
            limitations=limitations,
        )


def _same_quarter(
    candidate: FundamentalFilingPeriod,
    requested: FundamentalFilingPeriod,
) -> bool:
    return (
        candidate.state is FundamentalFilingPeriodState.RESOLVED
        and candidate.period_type is FundamentalFilingPeriodType.QUARTERLY
        and candidate.fiscal_year == requested.fiscal_year
        and candidate.fiscal_quarter == requested.fiscal_quarter
        and (
            candidate.period_start is None
            or requested.period_start is None
            or candidate.period_start == requested.period_start
        )
        and (
            candidate.period_end is None
            or requested.period_end is None
            or candidate.period_end == requested.period_end
        )
    )


def _speaker_references(
    record: StoredFundamentalFilingEvidence,
) -> tuple[CorporateFilingsAnalysisSpeakerReference, ...]:
    evidence = record.evidence
    participants = {item.participant_id: item for item in evidence.participants}
    values = []
    for turn in evidence.speaker_turns:
        participant = (
            participants.get(turn.participant_id)
            if turn.participant_id is not None
            else None
        )
        if participant is not None:
            participant_id = participant.participant_id
            speaker_name = participant.name
            role = participant.role
            affiliation = participant.issuer_affiliation
        elif turn.speaker_role is FundamentalFilingSpeakerRole.UNKNOWN:
            participant_id = None
            speaker_name = None
            role = FundamentalFilingSpeakerRole.UNKNOWN
            affiliation = FundamentalFilingParticipantAffiliation.UNKNOWN
        else:
            participant_id = None
            speaker_name = turn.speaker_name
            role = turn.speaker_role
            affiliation = {
                FundamentalFilingSpeakerRole.MANAGEMENT: (
                    FundamentalFilingParticipantAffiliation.ISSUER
                ),
                FundamentalFilingSpeakerRole.ANALYST: (
                    FundamentalFilingParticipantAffiliation.EXTERNAL
                ),
            }.get(role, FundamentalFilingParticipantAffiliation.UNKNOWN)
        values.append(
            CorporateFilingsAnalysisSpeakerReference(
                speaker_turn_id=turn.element_id,
                participant_id=participant_id,
                speaker_name=speaker_name,
                role=role,
                affiliation=affiliation,
            )
        )
    return tuple(sorted(values, key=lambda item: item.speaker_turn_id))


def _build_chunks(
    citations: Iterable[FundamentalFilingCitation],
    *,
    speaker_ids: set[str],
    max_characters: int,
) -> tuple[CorporateFilingsAnalysisChunk, ...]:
    ordered = sorted(
        citations,
        key=lambda item: (
            item.page_number,
            item.bounds.y0,
            item.bounds.x0,
            item.source_element_id,
            item.citation_id,
        ),
    )
    groups: list[list[FundamentalFilingCitation]] = []
    current: list[FundamentalFilingCitation] = []
    for citation in ordered:
        candidate = [*current, citation]
        if current and (
            len(candidate) > _MAX_CITATIONS_PER_CHUNK
            or len({item.page_number for item in candidate}) > 100
            or len(_serialize_citations(candidate)) > max_characters
        ):
            groups.append(current)
            current = [citation]
        else:
            current = candidate
        if len(_serialize_citations(current)) > max_characters:
            raise CorporateFilingsAnalysisAssemblyError(
                "One filing citation exceeds the configured chunk boundary."
            )
    if current:
        groups.append(current)
    if len(groups) > 10_000:
        raise CorporateFilingsAnalysisAssemblyError(
            "Filing evidence exceeds the supported chunk count."
        )

    chunks = []
    for sequence, group in enumerate(groups):
        chunks.append(
            CorporateFilingsAnalysisChunk(
                sequence=sequence,
                artifact_id=group[0].artifact_id,
                page_numbers=tuple(sorted({item.page_number for item in group})),
                source_element_ids=tuple(
                    sorted({item.source_element_id for item in group})
                ),
                citation_ids=tuple(sorted(item.citation_id for item in group)),
                speaker_turn_ids=tuple(
                    sorted(
                        {
                            item.source_element_id
                            for item in group
                            if item.source_element_id in speaker_ids
                        }
                    )
                ),
                text=_serialize_citations(group),
            )
        )
    return tuple(chunks)


def _serialize_citations(
    citations: Iterable[FundamentalFilingCitation],
) -> str:
    return json.dumps(
        {
            "released_citations": [
                {
                    "citation_id": item.citation_id,
                    "page_number": item.page_number,
                    "element_type": item.element_type.value,
                    "source_element_id": item.source_element_id,
                    "exact_excerpt": item.exact_excerpt,
                }
                for item in citations
            ]
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
