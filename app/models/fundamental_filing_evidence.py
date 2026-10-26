"""Deterministic, page-preserving evidence contracts for retained filings.

These models describe extraction output only. They do not parse PDFs, call an
LLM, or decide whether a company is investable. Every released observation is
bound to immutable filing metadata and exact page-level citation lineage.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
from re import fullmatch
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamental_filings import FundamentalFilingArtifactManifest
from app.models.fundamentals import FundamentalModel


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_ARTIFACT_ID_PATTERN = r"^filing_artifact:[a-f0-9]{64}$"
_CITATION_ID_PATTERN = r"^filing_citation:[a-f0-9]{64}$"
_PARTICIPANT_ID_PATTERN = r"^participant\.[A-Za-z0-9][A-Za-z0-9_.:-]{0,167}$"
_VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){0,2}$"
_MAX_PAGE_TEXT = 100_000
_MAX_CITATION_EXCERPT = 10_000


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _require_single_line(value: str, field_name: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must be a single line")
    return value


def _require_unique_canonical(
    values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")
    if values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must use canonical order")
    return values


def _fingerprint(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _normalize_participant_name(value: str) -> str:
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in value.casefold()
        ).split()
    )


class FundamentalFilingExtractionMethod(StrEnum):
    EMBEDDED_TEXT = "embedded_text"
    OCR = "ocr"
    HYBRID = "hybrid"
    NONE = "none"


class FundamentalFilingPageStatus(StrEnum):
    EXTRACTED = "extracted"
    PARTIAL = "partial"
    UNREADABLE = "unreadable"


class FundamentalFilingCoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class FundamentalFilingTextBlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE_CAPTION = "table_caption"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    UNKNOWN = "unknown"


class FundamentalFilingUnitKind(StrEnum):
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    BASIS_POINTS = "basis_points"
    RATIO = "ratio"
    COUNT = "count"
    SHARES = "shares"
    DURATION = "duration"
    TEXT = "text"
    UNKNOWN = "unknown"


class FundamentalFilingValueStatus(StrEnum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class FundamentalFilingSpeakerRole(StrEnum):
    MANAGEMENT = "management"
    ANALYST = "analyst"
    OPERATOR = "operator"
    MODERATOR = "moderator"
    UNKNOWN = "unknown"


class FundamentalFilingSpeakerAttribution(StrEnum):
    EXPLICIT_TRANSCRIPT_LABEL = "explicit_transcript_label"
    EXPLICIT_PROVIDER_LABEL = "explicit_provider_label"
    UNRESOLVED = "unresolved"


class FundamentalFilingParticipantAffiliation(StrEnum):
    ISSUER = "issuer"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class FundamentalFilingParticipantAttributionMethod(StrEnum):
    EXPLICIT_ROSTER = "explicit_roster"
    EXPLICIT_TRANSCRIPT_LABEL = "explicit_transcript_label"
    EXACT_NORMALIZED_NAME_MATCH = "exact_normalized_name_match"


class FundamentalFilingParticipantAttributionConfidence(StrEnum):
    VERIFIED = "verified"
    HIGH = "high"


class FundamentalFilingCitationElementType(StrEnum):
    TEXT_BLOCK = "text_block"
    TABLE_CELL = "table_cell"
    SPEAKER_TURN = "speaker_turn"


class FundamentalFilingWarningSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FundamentalFilingWarningCode(StrEnum):
    EMPTY_PAGE = "empty_page"
    UNREADABLE_PAGE = "unreadable_page"
    OCR_REQUIRED = "ocr_required"
    OCR_FAILED = "ocr_failed"
    TEXT_ORDER_UNCERTAIN = "text_order_uncertain"
    TABLE_TRUNCATED = "table_truncated"
    TABLE_STRUCTURE_UNCERTAIN = "table_structure_uncertain"
    ENCODING_ERROR = "encoding_error"
    SPEAKER_UNRESOLVED = "speaker_unresolved"
    PERIOD_UNRESOLVED = "period_unresolved"


class FundamentalFilingEvidenceKind(StrEnum):
    OBSERVED_FACT = "observed_fact"
    ADJUSTED_METRIC = "adjusted_metric"
    MANAGEMENT_CLAIM = "management_claim"
    GUIDANCE_COMMITMENT = "guidance_commitment"
    RISK_DISCLOSURE = "risk_disclosure"


class FundamentalFilingValidationCode(StrEnum):
    ISSUER_IDENTITY = "issuer_identity"
    PERIOD_IDENTITY = "period_identity"
    PAGE_SPANS = "page_spans"
    UNIT_INTEGRITY = "unit_integrity"
    DUPLICATE_CONTENT = "duplicate_content"
    SOURCE_CLASSIFICATION = "source_classification"
    CITATION_COMPLETENESS = "citation_completeness"


class FundamentalFilingValidationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class FundamentalFilingEvidencePageSpan:
    """Physical pages used by one semantic evidence record."""

    evidence_id: str
    first_page: int
    last_page: int
    cited_pages: tuple[int, ...]

    def __post_init__(self) -> None:
        if fullmatch(_ID_PATTERN, self.evidence_id) is None:
            raise ValueError("filing evidence page span ID is invalid")
        if self.first_page < 1 or self.last_page < self.first_page:
            raise ValueError("filing evidence page span is invalid")
        if (
            not self.cited_pages
            or self.cited_pages != tuple(sorted(set(self.cited_pages)))
            or self.cited_pages[0] != self.first_page
            or self.cited_pages[-1] != self.last_page
        ):
            raise ValueError("filing evidence cited pages are inconsistent")


@dataclass(frozen=True, slots=True)
class FundamentalFilingValidationFinding:
    code: FundamentalFilingValidationCode
    status: FundamentalFilingValidationStatus
    message: str
    affected_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.message or len(self.message) > 500:
            raise ValueError("filing validation message is invalid")
        _require_single_line(self.message, "filing validation message")
        if self.affected_ids != tuple(sorted(set(self.affected_ids))):
            raise ValueError("filing validation affected IDs must be canonical")
        if any(fullmatch(_ID_PATTERN, value) is None for value in self.affected_ids):
            raise ValueError("filing validation affected ID is invalid")
        if (
            self.status is FundamentalFilingValidationStatus.PASS
            and self.affected_ids
        ):
            raise ValueError("passing filing validation cannot affect records")


@dataclass(frozen=True, slots=True)
class FundamentalFilingEvidenceValidationResult:
    """Immutable release decision for one extraction aggregate."""

    policy_id: str
    policy_version: str
    extraction_fingerprint: str
    releaseable: bool
    findings: tuple[FundamentalFilingValidationFinding, ...]
    evidence_page_spans: tuple[FundamentalFilingEvidencePageSpan, ...]

    def __post_init__(self) -> None:
        if fullmatch(_ID_PATTERN, self.policy_id) is None:
            raise ValueError("filing validation policy ID is invalid")
        if fullmatch(_VERSION_PATTERN, self.policy_version) is None:
            raise ValueError("filing validation policy version is invalid")
        if fullmatch(_FINGERPRINT_PATTERN, self.extraction_fingerprint) is None:
            raise ValueError("filing extraction fingerprint is invalid")
        finding_codes = tuple(item.code for item in self.findings)
        expected_codes = tuple(sorted(FundamentalFilingValidationCode))
        if finding_codes != expected_codes:
            raise ValueError(
                "filing validation must contain every finding in canonical order"
            )
        expected_release = all(
            item.status is FundamentalFilingValidationStatus.PASS
            for item in self.findings
        )
        if self.releaseable != expected_release:
            raise ValueError("filing validation release decision is inconsistent")
        span_ids = tuple(item.evidence_id for item in self.evidence_page_spans)
        if span_ids != tuple(sorted(set(span_ids))):
            raise ValueError("filing evidence page spans must be canonical")

    @property
    def validation_fingerprint(self) -> str:
        return _fingerprint(asdict(self))


class FundamentalFilingGuidanceValueKind(StrEnum):
    QUALITATIVE = "qualitative"
    POINT = "point"
    RANGE = "range"


class FundamentalFilingRectangle(FundamentalModel):
    """A rectangle in PDF points using the policy-defined coordinate origin."""

    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(gt=0)
    y1: float = Field(gt=0)

    @field_validator("x0", "y0", "x1", "y1")
    @classmethod
    def require_finite_coordinate(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("filing coordinates must be finite")
        return value

    @model_validator(mode="after")
    def validate_rectangle(self) -> Self:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("filing rectangle must have positive area")
        return self


class FundamentalFilingUnit(FundamentalModel):
    """A source-observed unit; no multiplier is inferred by this contract."""

    kind: FundamentalFilingUnitKind
    source_label: str = Field(min_length=1, max_length=80)
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    multiplier: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_currency(self) -> Self:
        if self.kind is FundamentalFilingUnitKind.CURRENCY:
            if self.currency is None:
                raise ValueError("currency filing unit requires a currency")
        elif self.currency is not None:
            raise ValueError("only currency filing units may carry currency")
        return self


class FundamentalFilingObservedValue(FundamentalModel):
    """Raw source text plus an optional exact deterministic normalization."""

    raw_text: str = Field(min_length=1, max_length=500)
    status: FundamentalFilingValueStatus
    normalized_value: Decimal | None = None
    unit: FundamentalFilingUnit | None = None

    @model_validator(mode="after")
    def validate_normalization(self) -> Self:
        if self.status is FundamentalFilingValueStatus.EXACT:
            if self.normalized_value is None:
                raise ValueError("exact filing value requires normalization")
        elif self.normalized_value is not None:
            raise ValueError(
                "ambiguous or unavailable filing value cannot be normalized"
            )
        return self


class FundamentalFilingTextBlock(FundamentalModel):
    element_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    page_number: int = Field(ge=1, le=5_000)
    reading_order: int = Field(ge=0, le=100_000)
    kind: FundamentalFilingTextBlockKind
    text: str = Field(min_length=1, max_length=_MAX_PAGE_TEXT)
    bounds: FundamentalFilingRectangle
    method: FundamentalFilingExtractionMethod

    @model_validator(mode="after")
    def reject_absent_method(self) -> Self:
        if self.method is FundamentalFilingExtractionMethod.NONE:
            raise ValueError("text block requires an extraction method")
        return self


class FundamentalFilingTableCell(FundamentalModel):
    element_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    page_number: int = Field(ge=1, le=5_000)
    row_index: int = Field(ge=0, le=20_000)
    column_index: int = Field(ge=0, le=2_000)
    row_span: int = Field(default=1, ge=1, le=1_000)
    column_span: int = Field(default=1, ge=1, le=1_000)
    text: str = Field(min_length=1, max_length=10_000)
    bounds: FundamentalFilingRectangle
    is_header: bool = False
    observed_value: FundamentalFilingObservedValue | None = None


class FundamentalFilingTable(FundamentalModel):
    table_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    logical_table_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    page_number: int = Field(ge=1, le=5_000)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    bounds: FundamentalFilingRectangle
    row_count: int = Field(ge=1, le=20_000)
    column_count: int = Field(ge=1, le=2_000)
    default_unit: FundamentalFilingUnit | None = None
    cells: tuple[FundamentalFilingTableCell, ...] = Field(
        min_length=1,
        max_length=200_000,
    )

    @model_validator(mode="after")
    def validate_cells(self) -> Self:
        cell_ids = tuple(cell.element_id for cell in self.cells)
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("filing table cell IDs must be unique")
        positions = tuple(
            (cell.row_index, cell.column_index) for cell in self.cells
        )
        if len(positions) != len(set(positions)):
            raise ValueError("filing table cell positions must be unique")
        if positions != tuple(sorted(positions)):
            raise ValueError("filing table cells must use row-column order")
        for cell in self.cells:
            if cell.page_number != self.page_number:
                raise ValueError("filing table cells must remain on their page")
            if (
                cell.row_index + cell.row_span > self.row_count
                or cell.column_index + cell.column_span > self.column_count
            ):
                raise ValueError("filing table cell exceeds declared dimensions")
            if (
                cell.bounds.x0 < self.bounds.x0
                or cell.bounds.y0 < self.bounds.y0
                or cell.bounds.x1 > self.bounds.x1
                or cell.bounds.y1 > self.bounds.y1
            ):
                raise ValueError("filing table cell exceeds table bounds")
        return self


class FundamentalFilingPage(FundamentalModel):
    page_number: int = Field(ge=1, le=5_000)
    printed_page_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
    )
    width_points: float = Field(gt=0, le=100_000)
    height_points: float = Field(gt=0, le=100_000)
    rotation_degrees: Literal[0, 90, 180, 270] = 0
    status: FundamentalFilingPageStatus
    method: FundamentalFilingExtractionMethod
    text_blocks: tuple[FundamentalFilingTextBlock, ...] = Field(
        default=(),
        max_length=100_000,
    )
    tables: tuple[FundamentalFilingTable, ...] = Field(
        default=(),
        max_length=2_000,
    )

    @field_validator("width_points", "height_points")
    @classmethod
    def require_finite_dimension(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("filing page dimensions must be finite")
        return value

    @model_validator(mode="after")
    def validate_page_content(self) -> Self:
        if self.status is FundamentalFilingPageStatus.UNREADABLE:
            if (
                self.method is not FundamentalFilingExtractionMethod.NONE
                or self.text_blocks
                or self.tables
            ):
                raise ValueError(
                    "unreadable filing page cannot release extracted content"
                )
        elif self.method is FundamentalFilingExtractionMethod.NONE:
            raise ValueError("readable filing page requires an extraction method")

        element_ids: list[str] = []
        for block in self.text_blocks:
            if block.page_number != self.page_number:
                raise ValueError("filing text block must remain on its page")
            self._validate_bounds(block.bounds)
            element_ids.append(block.element_id)
        for table in self.tables:
            if table.page_number != self.page_number:
                raise ValueError("filing table must remain on its page")
            self._validate_bounds(table.bounds)
            for cell in table.cells:
                self._validate_bounds(cell.bounds)
                element_ids.append(cell.element_id)
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("filing page element IDs must be unique")

        reading_order = tuple(
            block.reading_order for block in self.text_blocks
        )
        if reading_order != tuple(sorted(reading_order)):
            raise ValueError("filing text blocks must use reading order")
        if len(reading_order) != len(set(reading_order)):
            raise ValueError("filing text-block reading order must be unique")
        table_order = tuple(
            (table.bounds.y0, table.bounds.x0, table.table_id)
            for table in self.tables
        )
        if table_order != tuple(sorted(table_order)):
            raise ValueError("filing tables must use page reading order")
        return self

    def _validate_bounds(self, bounds: FundamentalFilingRectangle) -> None:
        if bounds.x1 > self.width_points or bounds.y1 > self.height_points:
            raise ValueError("filing element exceeds its page bounds")


class FundamentalFilingParticipant(FundamentalModel):
    """Source-cited participant identity used for deterministic attribution."""

    participant_id: str = Field(pattern=_PARTICIPANT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    normalized_name: str = Field(min_length=1, max_length=200)
    role: FundamentalFilingSpeakerRole
    title: str | None = Field(default=None, min_length=1, max_length=300)
    organization: str | None = Field(default=None, min_length=1, max_length=300)
    issuer_affiliation: FundamentalFilingParticipantAffiliation
    attribution_method: FundamentalFilingParticipantAttributionMethod
    attribution_confidence: FundamentalFilingParticipantAttributionConfidence
    citation_ids: tuple[str, ...] = Field(min_length=1, max_length=100)

    @field_validator("name", "title", "organization")
    @classmethod
    def validate_single_line_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_single_line(value, "filing participant metadata")

    @field_validator("citation_ids")
    @classmethod
    def validate_citation_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if fullmatch(_CITATION_ID_PATTERN, value) is None:
                raise ValueError("filing participant citation ID is invalid")
        return _require_unique_canonical(values, "participant citation IDs")

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected_name = _normalize_participant_name(self.name)
        if not expected_name or self.normalized_name != expected_name:
            raise ValueError(
                "participant normalized name must match its deterministic name"
            )
        if (
            self.role is FundamentalFilingSpeakerRole.MANAGEMENT
            and self.issuer_affiliation
            is not FundamentalFilingParticipantAffiliation.ISSUER
        ):
            raise ValueError("management participant must be issuer affiliated")
        if (
            self.role is FundamentalFilingSpeakerRole.ANALYST
            and self.issuer_affiliation
            is not FundamentalFilingParticipantAffiliation.EXTERNAL
        ):
            raise ValueError("analyst participant must be externally affiliated")
        if self.role is FundamentalFilingSpeakerRole.UNKNOWN:
            raise ValueError("participant roster cannot contain an unknown role")
        if (
            self.attribution_method
            is FundamentalFilingParticipantAttributionMethod.EXPLICIT_ROSTER
            and self.attribution_confidence
            is not FundamentalFilingParticipantAttributionConfidence.VERIFIED
        ):
            raise ValueError("explicit roster attribution must be verified")
        if (
            self.attribution_method
            is FundamentalFilingParticipantAttributionMethod.EXACT_NORMALIZED_NAME_MATCH
            and self.attribution_confidence
            is not FundamentalFilingParticipantAttributionConfidence.HIGH
        ):
            raise ValueError(
                "exact participant-name attribution must be high confidence"
            )
        return self


class FundamentalFilingSpeakerTurn(FundamentalModel):
    element_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    turn_index: int = Field(ge=0, le=100_000)
    speaker_name: str | None = Field(default=None, min_length=1, max_length=200)
    speaker_role: FundamentalFilingSpeakerRole
    attribution: FundamentalFilingSpeakerAttribution
    participant_id: str | None = Field(
        default=None,
        pattern=_PARTICIPANT_ID_PATTERN,
    )
    text: str = Field(min_length=1, max_length=_MAX_PAGE_TEXT)
    source_block_ids: tuple[str, ...] = Field(min_length=1, max_length=1_000)

    @field_validator("source_block_ids")
    @classmethod
    def validate_source_blocks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_canonical(values, "speaker source block IDs")

    @model_validator(mode="after")
    def validate_attribution(self) -> Self:
        if self.attribution is FundamentalFilingSpeakerAttribution.UNRESOLVED:
            if (
                self.speaker_name is not None
                or self.speaker_role is not FundamentalFilingSpeakerRole.UNKNOWN
                or self.participant_id is not None
            ):
                raise ValueError(
                    "unresolved speaker cannot carry inferred identity or role"
                )
        elif self.speaker_name is None:
            raise ValueError("explicit speaker attribution requires a name")
        return self


class FundamentalFilingCitation(FundamentalModel):
    """Exact source span released for deterministic downstream validation."""

    artifact_id: str = Field(pattern=_ARTIFACT_ID_PATTERN)
    content_sha256: str = Field(pattern=_FINGERPRINT_PATTERN)
    page_number: int = Field(ge=1, le=5_000)
    element_type: FundamentalFilingCitationElementType
    source_element_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    exact_excerpt: str = Field(min_length=1, max_length=_MAX_CITATION_EXCERPT)
    bounds: FundamentalFilingRectangle
    start_offset: int | None = Field(default=None, ge=0, le=100_000)
    end_offset: int | None = Field(default=None, ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("filing citation offsets must be supplied together")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("filing citation end must follow its start")
        return self

    @computed_field
    @property
    def citation_id(self) -> str:
        payload = self.model_dump(mode="json", exclude_computed_fields=True)
        return f"filing_citation:{_fingerprint(payload)}"


class FundamentalFilingCitedEvidence(FundamentalModel):
    evidence_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    evidence_kind: FundamentalFilingEvidenceKind
    label: str = Field(min_length=1, max_length=300)
    statement: str = Field(min_length=1, max_length=5_000)
    citation_ids: tuple[str, ...] = Field(min_length=1, max_length=64)

    @field_validator("citation_ids")
    @classmethod
    def validate_citations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if fullmatch(_CITATION_ID_PATTERN, value) is None:
                raise ValueError("filing evidence requires filing citations")
        return _require_unique_canonical(values, "filing evidence citations")


class FundamentalFilingObservedFact(FundamentalFilingCitedEvidence):
    evidence_kind: Literal[FundamentalFilingEvidenceKind.OBSERVED_FACT] = (
        FundamentalFilingEvidenceKind.OBSERVED_FACT
    )
    value: FundamentalFilingObservedValue | None = None


class FundamentalFilingAdjustedMetric(FundamentalFilingCitedEvidence):
    evidence_kind: Literal[FundamentalFilingEvidenceKind.ADJUSTED_METRIC] = (
        FundamentalFilingEvidenceKind.ADJUSTED_METRIC
    )
    metric_label: str = Field(min_length=1, max_length=200)
    reported_value: FundamentalFilingObservedValue
    adjusted_value: FundamentalFilingObservedValue
    adjustment_description: str = Field(min_length=1, max_length=1_000)


class FundamentalFilingManagementClaim(FundamentalFilingCitedEvidence):
    evidence_kind: Literal[FundamentalFilingEvidenceKind.MANAGEMENT_CLAIM] = (
        FundamentalFilingEvidenceKind.MANAGEMENT_CLAIM
    )
    attribution_label: str = Field(min_length=1, max_length=300)
    speaker_turn_ids: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("speaker_turn_ids")
    @classmethod
    def validate_speaker_turns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_canonical(values, "management-claim speaker turns")


class FundamentalFilingGuidanceCommitment(FundamentalFilingCitedEvidence):
    evidence_kind: Literal[FundamentalFilingEvidenceKind.GUIDANCE_COMMITMENT] = (
        FundamentalFilingEvidenceKind.GUIDANCE_COMMITMENT
    )
    metric_label: str = Field(min_length=1, max_length=200)
    value_kind: FundamentalFilingGuidanceValueKind
    point_value: FundamentalFilingObservedValue | None = None
    lower_bound: FundamentalFilingObservedValue | None = None
    upper_bound: FundamentalFilingObservedValue | None = None
    horizon_label: str = Field(min_length=1, max_length=200)
    horizon_end: date | None = None
    conditions: tuple[str, ...] = Field(default=(), max_length=32)
    speaker_turn_ids: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("conditions")
    @classmethod
    def validate_conditions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _require_single_line(value, "guidance condition")
        return _require_unique_canonical(values, "guidance conditions")

    @field_validator("speaker_turn_ids")
    @classmethod
    def validate_speaker_turns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_canonical(values, "guidance speaker turns")

    @model_validator(mode="after")
    def validate_guidance_values(self) -> Self:
        if self.value_kind is FundamentalFilingGuidanceValueKind.QUALITATIVE:
            if any((self.point_value, self.lower_bound, self.upper_bound)):
                raise ValueError("qualitative guidance cannot carry numeric bounds")
        elif self.value_kind is FundamentalFilingGuidanceValueKind.POINT:
            if self.point_value is None or any(
                (self.lower_bound, self.upper_bound)
            ):
                raise ValueError("point guidance requires exactly one point value")
        elif (
            self.lower_bound is None
            or self.upper_bound is None
            or self.point_value is not None
        ):
            raise ValueError("range guidance requires lower and upper bounds")
        elif (
            self.lower_bound.status is FundamentalFilingValueStatus.EXACT
            and self.upper_bound.status is FundamentalFilingValueStatus.EXACT
            and self.lower_bound.unit == self.upper_bound.unit
            and self.lower_bound.normalized_value
            > self.upper_bound.normalized_value
        ):
            raise ValueError("guidance lower bound cannot exceed upper bound")
        return self


class FundamentalFilingRiskDisclosure(FundamentalFilingCitedEvidence):
    evidence_kind: Literal[FundamentalFilingEvidenceKind.RISK_DISCLOSURE] = (
        FundamentalFilingEvidenceKind.RISK_DISCLOSURE
    )
    risk_category: str = Field(min_length=1, max_length=200)
    issuer_disclosed: bool = True


class FundamentalFilingExtractionWarning(FundamentalModel):
    warning_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    code: FundamentalFilingWarningCode
    severity: FundamentalFilingWarningSeverity
    message: str = Field(min_length=1, max_length=500)
    page_number: int | None = Field(default=None, ge=1, le=5_000)
    source_element_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return _require_single_line(value, "filing extraction warning")


class FundamentalFilingExtractionLimitation(FundamentalModel):
    limitation_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=_ID_PATTERN,
    )
    description: str = Field(min_length=1, max_length=500)
    affected_pages: tuple[int, ...] = Field(default=(), max_length=5_000)
    material: bool

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _require_single_line(value, "filing extraction limitation")

    @field_validator("affected_pages")
    @classmethod
    def validate_affected_pages(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(value < 1 or value > 5_000 for value in values):
            raise ValueError("filing limitation page is out of range")
        if len(values) != len(set(values)) or values != tuple(sorted(values)):
            raise ValueError("filing limitation pages must be unique and ordered")
        return values


class FundamentalFilingExtractionCoverage(FundamentalModel):
    status: FundamentalFilingCoverageStatus
    expected_pages: int = Field(ge=1, le=5_000)
    represented_pages: int = Field(ge=1, le=5_000)
    extracted_pages: int = Field(ge=0, le=5_000)
    partial_pages: int = Field(ge=0, le=5_000)
    unreadable_pages: int = Field(ge=0, le=5_000)
    ocr_pages: int = Field(ge=0, le=5_000)
    table_count: int = Field(ge=0, le=100_000)
    speaker_turn_count: int = Field(ge=0, le=100_000)

    @model_validator(mode="after")
    def validate_page_counts(self) -> Self:
        if (
            self.extracted_pages
            + self.partial_pages
            + self.unreadable_pages
            != self.represented_pages
        ):
            raise ValueError("filing coverage page counts are inconsistent")
        if self.represented_pages != self.expected_pages:
            raise ValueError("filing coverage must preserve every physical page")
        expected_status = FundamentalFilingCoverageStatus.COMPLETE
        if self.unreadable_pages == self.expected_pages:
            expected_status = FundamentalFilingCoverageStatus.FAILED
        elif self.partial_pages or self.unreadable_pages:
            expected_status = FundamentalFilingCoverageStatus.PARTIAL
        if self.status is not expected_status:
            raise ValueError("filing coverage status does not match page counts")
        return self


class FundamentalFilingExtractionPolicy(FundamentalModel):
    schema_version: Literal["jarvis.fundamental_filing_extraction_policy.v1"] = (
        "jarvis.fundamental_filing_extraction_policy.v1"
    )
    policy_id: str = Field(min_length=1, max_length=180, pattern=_ID_PATTERN)
    policy_version: str = Field(pattern=_VERSION_PATTERN)
    text_engine: str = Field(min_length=1, max_length=100, pattern=_ID_PATTERN)
    table_engine: str = Field(min_length=1, max_length=100, pattern=_ID_PATTERN)
    ocr_engine: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=_ID_PATTERN,
    )
    ocr_enabled: bool = False
    coordinate_system: Literal["pdf_points_top_left"] = "pdf_points_top_left"

    @model_validator(mode="after")
    def validate_ocr_configuration(self) -> Self:
        if self.ocr_enabled != (self.ocr_engine is not None):
            raise ValueError("OCR enablement and engine must be configured together")
        return self

    @computed_field
    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )


class FundamentalFilingExtractedEvidence(FundamentalModel):
    """Complete deterministic extraction aggregate for one immutable PDF."""

    schema_version: Literal["jarvis.fundamental_filing_evidence.v1"] = (
        "jarvis.fundamental_filing_evidence.v1"
    )
    artifact: FundamentalFilingArtifactManifest
    policy: FundamentalFilingExtractionPolicy
    extracted_at: datetime
    pages: tuple[FundamentalFilingPage, ...] = Field(
        min_length=1,
        max_length=5_000,
    )
    speaker_turns: tuple[FundamentalFilingSpeakerTurn, ...] = Field(
        default=(),
        max_length=100_000,
    )
    participants: tuple[FundamentalFilingParticipant, ...] = Field(
        default=(),
        max_length=10_000,
    )
    citations: tuple[FundamentalFilingCitation, ...] = Field(
        default=(),
        max_length=200_000,
    )
    observed_facts: tuple[FundamentalFilingObservedFact, ...] = Field(
        default=(),
        max_length=100_000,
    )
    adjusted_metrics: tuple[FundamentalFilingAdjustedMetric, ...] = Field(
        default=(),
        max_length=50_000,
    )
    management_claims: tuple[FundamentalFilingManagementClaim, ...] = Field(
        default=(),
        max_length=50_000,
    )
    guidance_commitments: tuple[
        FundamentalFilingGuidanceCommitment,
        ...,
    ] = Field(default=(), max_length=20_000)
    risk_disclosures: tuple[FundamentalFilingRiskDisclosure, ...] = Field(
        default=(),
        max_length=50_000,
    )
    warnings: tuple[FundamentalFilingExtractionWarning, ...] = Field(
        default=(),
        max_length=10_000,
    )
    limitations: tuple[FundamentalFilingExtractionLimitation, ...] = Field(
        default=(),
        max_length=10_000,
    )
    coverage: FundamentalFilingExtractionCoverage

    @field_validator("extracted_at")
    @classmethod
    def validate_extracted_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, "filing extraction timestamp")

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if self.extracted_at < self.artifact.downloaded_at:
            raise ValueError("filing extraction cannot predate its PDF download")

        page_numbers = tuple(page.page_number for page in self.pages)
        expected_pages = tuple(range(1, self.artifact.page_count + 1))
        if page_numbers != expected_pages:
            raise ValueError(
                "filing extraction must preserve every page in physical order"
            )

        block_pages: dict[str, int] = {}
        cell_pages: dict[str, int] = {}
        table_ids: set[str] = set()
        for page in self.pages:
            for block in page.text_blocks:
                if block.element_id in block_pages or block.element_id in cell_pages:
                    raise ValueError("filing source element IDs must be unique")
                block_pages[block.element_id] = page.page_number
            for table in page.tables:
                if table.table_id in table_ids:
                    raise ValueError("filing table IDs must be unique")
                table_ids.add(table.table_id)
                for cell in table.cells:
                    if cell.element_id in block_pages or cell.element_id in cell_pages:
                        raise ValueError("filing source element IDs must be unique")
                    cell_pages[cell.element_id] = page.page_number

        turn_ids = tuple(turn.element_id for turn in self.speaker_turns)
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("filing speaker-turn IDs must be unique")
        turn_indexes = tuple(turn.turn_index for turn in self.speaker_turns)
        if turn_indexes != tuple(sorted(turn_indexes)):
            raise ValueError("filing speaker turns must use transcript order")
        if len(turn_indexes) != len(set(turn_indexes)):
            raise ValueError("filing speaker-turn indexes must be unique")
        for turn in self.speaker_turns:
            if any(block_id not in block_pages for block_id in turn.source_block_ids):
                raise ValueError("speaker turn references an unknown text block")

        participant_ids = tuple(
            participant.participant_id for participant in self.participants
        )
        _require_unique_canonical(participant_ids, "filing participant IDs")
        normalized_participant_names = tuple(
            participant.normalized_name for participant in self.participants
        )
        if len(normalized_participant_names) != len(
            set(normalized_participant_names)
        ):
            raise ValueError(
                "filing participants contain duplicate or conflicting identities"
            )
        participants_by_id = {
            participant.participant_id: participant
            for participant in self.participants
        }
        for turn in self.speaker_turns:
            if turn.participant_id is None:
                continue
            participant = participants_by_id.get(turn.participant_id)
            if participant is None:
                raise ValueError("speaker turn references an unknown participant")
            if turn.speaker_role is not participant.role:
                raise ValueError("speaker turn conflicts with participant role")
            if (
                turn.speaker_name is None
                or _normalize_participant_name(turn.speaker_name)
                != participant.normalized_name
            ):
                raise ValueError("speaker turn conflicts with participant identity")

        turn_pages = {
            turn.element_id: {
                block_pages[block_id] for block_id in turn.source_block_ids
            }
            for turn in self.speaker_turns
        }
        citation_ids = tuple(citation.citation_id for citation in self.citations)
        _require_unique_canonical(citation_ids, "filing citation IDs")
        for citation in self.citations:
            if (
                citation.artifact_id != self.artifact.artifact_id
                or citation.content_sha256 != self.artifact.content_sha256
            ):
                raise ValueError("filing citation must match its immutable PDF")
            if citation.element_type is FundamentalFilingCitationElementType.TEXT_BLOCK:
                source_page = block_pages.get(citation.source_element_id)
                if source_page != citation.page_number:
                    raise ValueError("filing citation references an unknown text block")
            elif citation.element_type is (
                FundamentalFilingCitationElementType.TABLE_CELL
            ):
                source_page = cell_pages.get(citation.source_element_id)
                if source_page != citation.page_number:
                    raise ValueError("filing citation references an unknown table cell")
            elif citation.page_number not in turn_pages.get(
                citation.source_element_id,
                set(),
            ):
                raise ValueError("filing citation references an unknown speaker turn")
            self.pages[citation.page_number - 1]._validate_bounds(
                citation.bounds
            )

        evidence = (
            *self.observed_facts,
            *self.adjusted_metrics,
            *self.management_claims,
            *self.guidance_commitments,
            *self.risk_disclosures,
        )
        evidence_ids = tuple(item.evidence_id for item in evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("filing evidence IDs must be globally unique")
        for collection in (
            self.observed_facts,
            self.adjusted_metrics,
            self.management_claims,
            self.guidance_commitments,
            self.risk_disclosures,
        ):
            collection_ids = tuple(item.evidence_id for item in collection)
            if collection_ids != tuple(sorted(collection_ids)):
                raise ValueError("filing evidence must use canonical order")
        available_citations = set(citation_ids)
        for participant in self.participants:
            if any(
                value not in available_citations
                for value in participant.citation_ids
            ):
                raise ValueError("filing participant references an unknown citation")
        for item in evidence:
            if any(value not in available_citations for value in item.citation_ids):
                raise ValueError("filing evidence references an unknown citation")

        available_turns = set(turn_ids)
        for item in (*self.management_claims, *self.guidance_commitments):
            if any(value not in available_turns for value in item.speaker_turn_ids):
                raise ValueError("filing evidence references an unknown speaker turn")

        available_elements = set(block_pages) | set(cell_pages) | available_turns
        warning_ids = tuple(warning.warning_id for warning in self.warnings)
        if len(warning_ids) != len(set(warning_ids)):
            raise ValueError("filing extraction warning IDs must be unique")
        if warning_ids != tuple(sorted(warning_ids)):
            raise ValueError("filing extraction warnings must use canonical order")
        for warning in self.warnings:
            if (
                warning.page_number is not None
                and warning.page_number not in expected_pages
            ):
                raise ValueError("filing warning references an unknown page")
            if (
                warning.source_element_id is not None
                and warning.source_element_id not in available_elements
            ):
                raise ValueError("filing warning references an unknown element")

        limitation_ids = tuple(item.limitation_id for item in self.limitations)
        if len(limitation_ids) != len(set(limitation_ids)):
            raise ValueError("filing extraction limitation IDs must be unique")
        if limitation_ids != tuple(sorted(limitation_ids)):
            raise ValueError("filing extraction limitations must use canonical order")
        for limitation in self.limitations:
            if any(page not in expected_pages for page in limitation.affected_pages):
                raise ValueError("filing limitation references an unknown page")

        degraded_pages = {
            page.page_number
            for page in self.pages
            if page.status is not FundamentalFilingPageStatus.EXTRACTED
        }
        warned_pages = {
            warning.page_number
            for warning in self.warnings
            if warning.page_number is not None
        }
        if not degraded_pages.issubset(warned_pages):
            raise ValueError("degraded filing pages require extraction warnings")
        unreadable_page_numbers = {
            page.page_number
            for page in self.pages
            if page.status is FundamentalFilingPageStatus.UNREADABLE
        }
        materially_limited_pages = {
            page_number
            for limitation in self.limitations
            if limitation.material
            for page_number in limitation.affected_pages
        }
        if not unreadable_page_numbers.issubset(materially_limited_pages):
            raise ValueError("unreadable filing pages require material limitations")
        if any(
            page.method
            in {
                FundamentalFilingExtractionMethod.OCR,
                FundamentalFilingExtractionMethod.HYBRID,
            }
            for page in self.pages
        ) and not self.policy.ocr_enabled:
            raise ValueError("OCR-derived filing pages require an OCR policy")

        extracted_pages = sum(
            page.status is FundamentalFilingPageStatus.EXTRACTED
            for page in self.pages
        )
        partial_pages = sum(
            page.status is FundamentalFilingPageStatus.PARTIAL
            for page in self.pages
        )
        unreadable_pages = sum(
            page.status is FundamentalFilingPageStatus.UNREADABLE
            for page in self.pages
        )
        ocr_pages = sum(
            page.method
            in {
                FundamentalFilingExtractionMethod.OCR,
                FundamentalFilingExtractionMethod.HYBRID,
            }
            for page in self.pages
        )
        expected_coverage = {
            "expected_pages": self.artifact.page_count,
            "represented_pages": len(self.pages),
            "extracted_pages": extracted_pages,
            "partial_pages": partial_pages,
            "unreadable_pages": unreadable_pages,
            "ocr_pages": ocr_pages,
            "table_count": sum(len(page.tables) for page in self.pages),
            "speaker_turn_count": len(self.speaker_turns),
        }
        for field_name, expected in expected_coverage.items():
            if getattr(self.coverage, field_name) != expected:
                raise ValueError(
                    f"filing coverage {field_name} does not match extraction"
                )
        return self

    @computed_field
    @property
    def extraction_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude_computed_fields=True)
        if not self.participants:
            payload.pop("participants", None)
        return _fingerprint(payload)
