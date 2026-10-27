"""Deterministic native-PDF extraction for retained company filings.

The extractor reads only a manifest-bound file below an owner-only artifact
root. It preserves physical pages, source coordinates, native text, ruled
tables, explicit transcript speakers, warnings, and exact source citations.
It does not use OCR, an LLM, or semantic investment inference.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path, PurePosixPath
import os
import re
import stat as stat_module
from statistics import median
from typing import Any

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError

from app.models.fundamental_filing_evidence import (
    FundamentalFilingCitation,
    FundamentalFilingCitationElementType,
    FundamentalFilingCoverageStatus,
    FundamentalFilingExtractedEvidence,
    FundamentalFilingExtractionCoverage,
    FundamentalFilingExtractionLimitation,
    FundamentalFilingExtractionMethod,
    FundamentalFilingExtractionPolicy,
    FundamentalFilingExtractionWarning,
    FundamentalFilingPage,
    FundamentalFilingPageStatus,
    FundamentalFilingParticipant,
    FundamentalFilingParticipantAffiliation,
    FundamentalFilingParticipantAttributionConfidence,
    FundamentalFilingParticipantAttributionMethod,
    FundamentalFilingRectangle,
    FundamentalFilingSpeakerAttribution,
    FundamentalFilingSpeakerRole,
    FundamentalFilingSpeakerTurn,
    FundamentalFilingTable,
    FundamentalFilingTableCell,
    FundamentalFilingTextBlock,
    FundamentalFilingTextBlockKind,
    FundamentalFilingUnit,
    FundamentalFilingUnitKind,
    FundamentalFilingObservedValue,
    FundamentalFilingValueStatus,
    FundamentalFilingWarningCode,
    FundamentalFilingWarningSeverity,
)
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingCategory,
)


_READ_CHUNK_BYTES = 1024 * 1024
_LINE_TOLERANCE_POINTS = 2.0
_HEADING_SIZE_RATIO = 1.3
_MAX_HEADING_WORDS = 18
_TABLE_UNIT_LOOKBACK_POINTS = 100.0
_ENGINE_ID = f"pdfplumber.{pdfplumber.__version__}"
_MISSING_VALUE_PATTERN = re.compile(r"^(?:[-—–]|n/?a|nil|not available)$", re.I)
_NUMBER_PATTERN = re.compile(
    r"^\(?[+-]?(?:\d+(?:,\d{2,3})*|\d*)(?:\.\d+)?\)?$"
)
_PARENTHETICAL_UNIT_PATTERN = re.compile(
    r"\(\s*in\s+(?P<label>[^)]{1,80})\)",
    re.I,
)
_STANDALONE_UNIT_PATTERN = re.compile(
    r"^(?:amounts?\s+)?(?:in\s+)?"
    r"(?:inr|rs\.?|₹|usd|\$)\s*"
    r"(?:crores?|cr\.?|millions?|mn\.?|lakhs?)$",
    re.I,
)
_NUMERIC_BPS_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[+-]?(?:\d+(?:,\d{2,3})*(?:\.\d+)?|\.\d+)"
    r"\s*bps\b",
    re.I,
)
_LABELLED_BPS_UNIT_PATTERN = re.compile(
    r"(?:\bin\s+bps\b|\(\s*bps\s*\)|^\s*bps\s*$)",
    re.I,
)
_SPEAKER_PATTERN = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z .&'()/\-]{1,159})"
    r"\s*:\s*(?P<body>.*)$"
)
_NON_SPEAKER_LABELS = frozenset(
    {
        "balance sheet",
        "cash flow",
        "corporate identification number",
        "financial highlights",
        "guidance",
        "profit and loss",
        "revenue",
        "risk",
        "risks",
        "table",
    }
)
_MANAGEMENT_ROLE_TERMS = (
    "chief ",
    "ceo",
    "cfo",
    "coo",
    "chairman",
    "director",
    "management",
    "president",
    "vice president",
)
_ROSTER_SECTION_PATTERN = re.compile(
    r"^(?P<section>MANAGEMENT|MODERATORS?|QUESTIONERS?|ANALYSTS?|OPERATORS?)"
    r"(?:\s*(?:[:•●]|[-–—])\s*(?P<entry>.+))?$",
    re.I,
)
_ROSTER_ENTRY_SEPARATOR = re.compile(r"\s+[-–—]\s+")
_ROSTER_BULLET_PREFIX = re.compile(r"^[•●*]\s*")
_ROSTER_ROLES = {
    "management": FundamentalFilingSpeakerRole.MANAGEMENT,
    "moderator": FundamentalFilingSpeakerRole.MODERATOR,
    "moderators": FundamentalFilingSpeakerRole.MODERATOR,
    "questioner": FundamentalFilingSpeakerRole.ANALYST,
    "questioners": FundamentalFilingSpeakerRole.ANALYST,
    "analyst": FundamentalFilingSpeakerRole.ANALYST,
    "analysts": FundamentalFilingSpeakerRole.ANALYST,
    "operator": FundamentalFilingSpeakerRole.OPERATOR,
    "operators": FundamentalFilingSpeakerRole.OPERATOR,
}


DEFAULT_FILING_EXTRACTION_POLICY = FundamentalFilingExtractionPolicy(
    policy_id="filing.extraction.native_pdf",
    policy_version="1.5.0",
    text_engine=_ENGINE_ID,
    table_engine=_ENGINE_ID,
    ocr_enabled=False,
)


class FundamentalFilingExtractionError(ValueError):
    """Safe, non-provider-specific failure while extracting a retained PDF."""


class _TableCellLimitError(ValueError):
    """Native text is retained, but the detected table cannot be represented."""


class DeterministicFundamentalFilingExtractor:
    """Extract immutable filing evidence without semantic or LLM inference."""

    extractor_name = "deterministic_native_pdf"

    def __init__(
        self,
        *,
        artifact_root: Path,
        policy: FundamentalFilingExtractionPolicy | None = None,
    ) -> None:
        self._artifact_root = self._validate_artifact_root(artifact_root)
        self._policy = policy or DEFAULT_FILING_EXTRACTION_POLICY
        self._validate_policy(self._policy)

    @property
    def policy(self) -> FundamentalFilingExtractionPolicy:
        return self._policy.model_copy(deep=True)

    def artifact_is_intact(self, artifact: FundamentalFilingArtifactManifest) -> bool:
        """Check retained bytes without parsing pages or validating a period."""
        try:
            manifest = FundamentalFilingArtifactManifest.model_validate(artifact)
            path = self._resolve_artifact_path(manifest)
            self._validate_artifact_file(path, manifest)
        except (OSError, ValueError):
            return False
        return True

    def reconcile_page_count(
        self, artifact: FundamentalFilingArtifactManifest,
    ) -> FundamentalFilingArtifactManifest:
        """Return corrected metadata for identical verified bytes; retain history."""
        manifest = FundamentalFilingArtifactManifest.model_validate(artifact)
        path = self._resolve_artifact_path(manifest)
        self._validate_artifact_file(path, manifest)
        try:
            with pdfplumber.open(path) as document:
                page_count = len(document.pages)
        except (OSError, PDFPasswordIncorrect, PDFSyntaxError, ValueError) as exc:
            raise FundamentalFilingExtractionError(
                "Retained PDF page metadata could not be reconciled"
            ) from exc
        if page_count == manifest.page_count:
            return manifest
        values = manifest.model_dump(exclude_computed_fields=True)
        values["page_count"] = page_count
        return FundamentalFilingArtifactManifest.model_validate(values)

    def extract(
        self,
        artifact: FundamentalFilingArtifactManifest,
        *,
        extracted_at: datetime,
    ) -> FundamentalFilingExtractedEvidence:
        manifest = FundamentalFilingArtifactManifest.model_validate(artifact)
        if extracted_at.tzinfo is None or extracted_at.utcoffset() is None:
            raise FundamentalFilingExtractionError(
                "filing extraction time must include timezone information"
            )
        artifact_path = self._resolve_artifact_path(manifest)
        self._validate_artifact_file(artifact_path, manifest)

        try:
            with pdfplumber.open(artifact_path) as document:
                if len(document.pages) != manifest.page_count:
                    raise FundamentalFilingExtractionError(
                        "retained PDF page count does not match its manifest"
                    )
                pages, warnings, limitations = self._extract_pages(
                    document.pages
                )
        except FundamentalFilingExtractionError:
            raise
        except (
            OSError,
            PDFPasswordIncorrect,
            PDFSyntaxError,
            PdfminerException,
        ) as exc:
            raise FundamentalFilingExtractionError(
                "retained PDF could not be parsed safely"
            ) from exc

        speaker_turns, speaker_sources = self._extract_speaker_turns(pages)
        citations = self._build_citations(
            manifest,
            pages,
            speaker_turns,
            speaker_sources,
        )
        participants: tuple[FundamentalFilingParticipant, ...] = ()
        if manifest.reference.category is FundamentalFilingCategory.CONFERENCE_CALL:
            participants = self._extract_participants(
                pages,
                citations,
                issuer_name=manifest.reference.issuer.legal_name,
            )
            speaker_turns = self._map_speaker_turns_to_participants(
                speaker_turns,
                participants,
            )
        coverage = self._coverage(pages, speaker_turns)
        return FundamentalFilingExtractedEvidence(
            artifact=manifest,
            policy=self._policy,
            extracted_at=extracted_at,
            pages=pages,
            speaker_turns=speaker_turns,
            participants=participants,
            citations=citations,
            warnings=tuple(sorted(warnings, key=lambda item: item.warning_id)),
            limitations=tuple(
                sorted(limitations, key=lambda item: item.limitation_id)
            ),
            coverage=coverage,
        )

    @staticmethod
    def _validate_artifact_root(value: Path) -> Path:
        if not isinstance(value, Path) or not value.is_absolute():
            raise ValueError("filing artifact root must be an absolute path")
        metadata = value.lstat()
        if not stat_module.S_ISDIR(metadata.st_mode) or value.is_symlink():
            raise ValueError("filing artifact root must be a real directory")
        if metadata.st_mode & 0o077:
            raise ValueError("filing artifact root must be owner-only")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ValueError("filing artifact root owner is invalid")
        return value.resolve(strict=True)

    @staticmethod
    def _validate_policy(policy: FundamentalFilingExtractionPolicy) -> None:
        if policy.ocr_enabled:
            raise ValueError(
                "native filing extractor does not execute OCR; use a separately "
                "versioned OCR adapter"
            )
        if (
            policy.text_engine != _ENGINE_ID
            or policy.table_engine != _ENGINE_ID
        ):
            raise ValueError(
                "filing extraction policy does not match the installed engine"
            )

    def _resolve_artifact_path(
        self,
        artifact: FundamentalFilingArtifactManifest,
    ) -> Path:
        relative = PurePosixPath(artifact.safe_relative_path)
        candidate = self._artifact_root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise FundamentalFilingExtractionError(
                "retained filing artifact is unavailable"
            ) from exc
        if resolved != candidate or not resolved.is_relative_to(
            self._artifact_root
        ):
            raise FundamentalFilingExtractionError(
                "retained filing artifact path is not trusted"
            )
        metadata = resolved.lstat()
        if not stat_module.S_ISREG(metadata.st_mode):
            raise FundamentalFilingExtractionError(
                "retained filing artifact must be a regular file"
            )
        if metadata.st_mode & 0o077:
            raise FundamentalFilingExtractionError(
                "retained filing artifact must be owner-only"
            )
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise FundamentalFilingExtractionError(
                "retained filing artifact owner is invalid"
            )
        self._validate_owner_directories(resolved.parent)
        return resolved

    def _validate_owner_directories(self, directory: Path) -> None:
        cursor = directory
        while cursor.is_relative_to(self._artifact_root):
            metadata = cursor.lstat()
            if not stat_module.S_ISDIR(metadata.st_mode) or cursor.is_symlink():
                raise FundamentalFilingExtractionError(
                    "retained filing artifact directory is not trusted"
                )
            if metadata.st_mode & 0o077:
                raise FundamentalFilingExtractionError(
                    "retained filing artifact directory must be owner-only"
                )
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise FundamentalFilingExtractionError(
                    "retained filing artifact directory owner is invalid"
                )
            if cursor == self._artifact_root:
                break
            cursor = cursor.parent

    @staticmethod
    def _validate_artifact_file(
        path: Path,
        artifact: FundamentalFilingArtifactManifest,
    ) -> None:
        digest = sha256()
        byte_length = 0
        try:
            with path.open("rb") as source:
                while chunk := source.read(_READ_CHUNK_BYTES):
                    digest.update(chunk)
                    byte_length += len(chunk)
        except OSError as exc:
            raise FundamentalFilingExtractionError(
                "retained filing artifact could not be read safely"
            ) from exc
        if byte_length != artifact.byte_length:
            raise FundamentalFilingExtractionError(
                "retained PDF byte length does not match its manifest"
            )
        if digest.hexdigest() != artifact.content_sha256:
            raise FundamentalFilingExtractionError(
                "retained PDF hash does not match its manifest"
            )

    def _extract_pages(
        self,
        source_pages: Iterable[Any],
    ) -> tuple[
        tuple[FundamentalFilingPage, ...],
        list[FundamentalFilingExtractionWarning],
        list[FundamentalFilingExtractionLimitation],
    ]:
        pages: list[FundamentalFilingPage] = []
        warnings: list[FundamentalFilingExtractionWarning] = []
        limitations: list[FundamentalFilingExtractionLimitation] = []
        for page_index, source_page in enumerate(source_pages, start=1):
            page, page_warnings, page_limitations = self._extract_page(
                source_page,
                page_number=page_index,
            )
            pages.append(page)
            warnings.extend(page_warnings)
            limitations.extend(page_limitations)
        return tuple(pages), warnings, limitations

    def _extract_page(
        self,
        source_page: Any,
        *,
        page_number: int,
    ) -> tuple[
        FundamentalFilingPage,
        list[FundamentalFilingExtractionWarning],
        list[FundamentalFilingExtractionLimitation],
    ]:
        warnings: list[FundamentalFilingExtractionWarning] = []
        limitations: list[FundamentalFilingExtractionLimitation] = []
        width = float(source_page.width)
        height = float(source_page.height)

        try:
            words = source_page.extract_words(
                use_text_flow=True,
                keep_blank_chars=False,
                extra_attrs=["fontname", "size"],
            )
            words, excluded_word_count = self._bounded_page_words(
                words,
                page_width=width,
                page_height=height,
            )
            if excluded_word_count:
                limitations.append(
                    self._limitation(
                        page_number,
                        "off_page_text_excluded",
                        "Native text wholly outside the physical page bounds "
                        "was excluded from released evidence.",
                        material=False,
                    )
                )
            blocks = self._text_blocks(
                words,
                page_number=page_number,
                page_height=height,
            )
        except (PdfminerException, PDFSyntaxError, UnicodeError) as exc:
            return self._unreadable_page(
                page_number=page_number,
                width=width,
                height=height,
                cause=exc,
            )

        tables: tuple[FundamentalFilingTable, ...] = ()
        table_failed = False
        try:
            tables = self._tables(
                source_page,
                page_number=page_number,
                blocks=blocks,
            )
        except (PdfminerException, PDFSyntaxError, UnicodeError, _TableCellLimitError) as exc:
            table_failed = True
            warnings.append(
                self._warning(
                    page_number,
                    FundamentalFilingWarningCode.TABLE_STRUCTURE_UNCERTAIN,
                    FundamentalFilingWarningSeverity.WARNING,
                    "Native table structure could not be extracted reliably.",
                )
            )
            limitations.append(
                self._limitation(
                    page_number,
                    "table_structure_uncertain",
                    "Native table structure is unavailable on this page.",
                    material=isinstance(exc, _TableCellLimitError),
                )
            )

        if not blocks and not tables:
            if source_page.images:
                warning = self._warning(
                    page_number,
                    FundamentalFilingWarningCode.OCR_REQUIRED,
                    FundamentalFilingWarningSeverity.ERROR,
                    "Page has image content but no native text; OCR is required.",
                )
                limitation = self._limitation(
                    page_number,
                    "ocr_required",
                    "Page content is unavailable because OCR was not executed.",
                    material=True,
                )
                return (
                    FundamentalFilingPage(
                        page_number=page_number,
                        printed_page_label=None,
                        width_points=width,
                        height_points=height,
                        rotation_degrees=self._rotation(source_page),
                        status=FundamentalFilingPageStatus.UNREADABLE,
                        method=FundamentalFilingExtractionMethod.NONE,
                    ),
                    [warning],
                    [limitation],
                )
            warnings.append(
                self._warning(
                    page_number,
                    FundamentalFilingWarningCode.EMPTY_PAGE,
                    FundamentalFilingWarningSeverity.INFO,
                    "Page contains no native text or ruled table content.",
                )
            )

        status = (
            FundamentalFilingPageStatus.PARTIAL
            if table_failed
            else FundamentalFilingPageStatus.EXTRACTED
        )
        return (
            FundamentalFilingPage(
                page_number=page_number,
                printed_page_label=self._printed_page_label(blocks),
                width_points=width,
                height_points=height,
                rotation_degrees=self._rotation(source_page),
                status=status,
                method=FundamentalFilingExtractionMethod.EMBEDDED_TEXT,
                text_blocks=blocks,
                tables=tables,
            ),
            warnings,
            limitations,
        )

    def _unreadable_page(
        self,
        *,
        page_number: int,
        width: float,
        height: float,
        cause: Exception,
    ) -> tuple[
        FundamentalFilingPage,
        list[FundamentalFilingExtractionWarning],
        list[FundamentalFilingExtractionLimitation],
    ]:
        del cause
        return (
            FundamentalFilingPage(
                page_number=page_number,
                printed_page_label=None,
                width_points=width,
                height_points=height,
                status=FundamentalFilingPageStatus.UNREADABLE,
                method=FundamentalFilingExtractionMethod.NONE,
            ),
            [
                self._warning(
                    page_number,
                    FundamentalFilingWarningCode.UNREADABLE_PAGE,
                    FundamentalFilingWarningSeverity.ERROR,
                    "Native page text could not be extracted safely.",
                )
            ],
            [
                self._limitation(
                    page_number,
                    "unreadable",
                    "Page content is unavailable to downstream analysis.",
                    material=True,
                )
            ],
        )

    @staticmethod
    def _rotation(source_page: Any) -> int:
        rotation = int(getattr(source_page, "rotation", 0) or 0) % 360
        return rotation if rotation in {0, 90, 180, 270} else 0

    @staticmethod
    def _bounded_page_words(
        words: list[dict[str, Any]],
        *,
        page_width: float,
        page_height: float,
    ) -> tuple[list[dict[str, Any]], int]:
        """Exclude hidden off-page text and clip intersecting parser bounds."""

        bounded: list[dict[str, Any]] = []
        excluded = 0
        for word in words:
            x0 = float(word["x0"])
            x1 = float(word["x1"])
            top = float(word["top"])
            bottom = float(word["bottom"])
            if x1 <= 0 or bottom <= 0 or x0 >= page_width or top >= page_height:
                excluded += 1
                continue
            clipped = dict(word)
            clipped.update(
                x0=max(0.0, x0),
                x1=min(page_width, x1),
                top=max(0.0, top),
                bottom=min(page_height, bottom),
            )
            if clipped["x1"] <= clipped["x0"] or clipped["bottom"] <= clipped["top"]:
                excluded += 1
                continue
            bounded.append(clipped)
        return bounded, excluded

    def _text_blocks(
        self,
        words: list[dict[str, Any]],
        *,
        page_number: int,
        page_height: float,
    ) -> tuple[FundamentalFilingTextBlock, ...]:
        if not words:
            return ()
        ordered_words = sorted(
            words,
            key=lambda item: (float(item["top"]), float(item["x0"])),
        )
        lines: list[list[dict[str, Any]]] = []
        for word in ordered_words:
            if not str(word.get("text", "")).strip():
                continue
            if (
                not lines
                or abs(float(word["top"]) - float(lines[-1][0]["top"]))
                > _LINE_TOLERANCE_POINTS
            ):
                lines.append([word])
            else:
                lines[-1].append(word)

        sizes = [
            float(word.get("size") or 0.0)
            for word in ordered_words
            if float(word.get("size") or 0.0) > 0
        ]
        body_size = median(sizes) if sizes else 10.0
        blocks: list[FundamentalFilingTextBlock] = []
        for reading_order, line in enumerate(lines):
            line.sort(key=lambda item: float(item["x0"]))
            text = " ".join(str(word["text"]).strip() for word in line)
            bounds = FundamentalFilingRectangle(
                x0=min(float(word["x0"]) for word in line),
                y0=min(float(word["top"]) for word in line),
                x1=max(float(word["x1"]) for word in line),
                y1=max(float(word["bottom"]) for word in line),
            )
            average_size = sum(
                float(word.get("size") or body_size) for word in line
            ) / len(line)
            blocks.append(
                FundamentalFilingTextBlock(
                    element_id=(
                        f"page.{page_number:04d}.block.{reading_order:06d}"
                    ),
                    page_number=page_number,
                    reading_order=reading_order,
                    kind=self._block_kind(
                        text,
                        bounds=bounds,
                        average_size=average_size,
                        body_size=body_size,
                        page_height=page_height,
                    ),
                    text=text,
                    bounds=bounds,
                    method=FundamentalFilingExtractionMethod.EMBEDDED_TEXT,
                )
            )
        return tuple(blocks)

    @staticmethod
    def _block_kind(
        text: str,
        *,
        bounds: FundamentalFilingRectangle,
        average_size: float,
        body_size: float,
        page_height: float,
    ) -> FundamentalFilingTextBlockKind:
        stripped = text.strip()
        if stripped.isdigit() and bounds.y1 >= page_height - 40:
            return FundamentalFilingTextBlockKind.PAGE_NUMBER
        if bounds.y0 <= 25 and average_size <= body_size * 1.1:
            return FundamentalFilingTextBlockKind.HEADER
        if bounds.y1 >= page_height - 25 and average_size <= body_size * 1.1:
            return FundamentalFilingTextBlockKind.FOOTER
        if stripped.startswith(("•", "-", "–", "—")):
            return FundamentalFilingTextBlockKind.LIST_ITEM
        word_count = len(stripped.split())
        if word_count <= _MAX_HEADING_WORDS and (
            average_size >= body_size * _HEADING_SIZE_RATIO
            or (
                stripped.isupper()
                and any(character.isalpha() for character in stripped)
            )
        ):
            return FundamentalFilingTextBlockKind.HEADING
        return FundamentalFilingTextBlockKind.PARAGRAPH

    def _tables(
        self,
        source_page: Any,
        *,
        page_number: int,
        blocks: tuple[FundamentalFilingTextBlock, ...],
    ) -> tuple[FundamentalFilingTable, ...]:
        tables: list[FundamentalFilingTable] = []
        discovered = sorted(
            source_page.find_tables(),
            key=lambda table: (float(table.bbox[1]), float(table.bbox[0])),
        )
        for table_index, source_table in enumerate(discovered):
            matrix = source_table.extract()
            row_count = len(source_table.rows)
            column_count = max(
                (len(row.cells) for row in source_table.rows),
                default=0,
            )
            if row_count < 1 or column_count < 1:
                continue
            table_bounds = self._rectangle(source_table.bbox)
            default_unit = self._table_unit(blocks, table_bounds)
            if default_unit is None:
                default_unit = self._matrix_table_unit(matrix)
            cells: list[FundamentalFilingTableCell] = []
            for row_index, row in enumerate(source_table.rows):
                row_values = matrix[row_index] if row_index < len(matrix) else []
                row_unit = self._row_unit(row_values)
                for column_index, cell_bounds in enumerate(row.cells):
                    if cell_bounds is None:
                        continue
                    raw_text = ""
                    if row_index < len(matrix) and column_index < len(
                        matrix[row_index]
                    ):
                        raw_text = str(matrix[row_index][column_index] or "").strip()
                    if not raw_text:
                        continue
                    if len(raw_text) > 10_000:
                        raise _TableCellLimitError(
                            "Native table cell exceeds the evidence size bound"
                        )
                    observed = self._observed_value(
                        raw_text,
                        row_unit or default_unit,
                    )
                    cells.append(
                        FundamentalFilingTableCell(
                            element_id=(
                                f"page.{page_number:04d}.table."
                                f"{table_index:04d}.cell."
                                f"{row_index:05d}.{column_index:04d}"
                            ),
                            page_number=page_number,
                            row_index=row_index,
                            column_index=column_index,
                            text=raw_text,
                            bounds=self._rectangle(cell_bounds),
                            is_header=row_index == 0,
                            observed_value=observed,
                        )
                    )
            if not cells:
                continue
            table_id = f"page.{page_number:04d}.table.{table_index:04d}"
            tables.append(
                FundamentalFilingTable(
                    table_id=table_id,
                    logical_table_id=table_id,
                    page_number=page_number,
                    title=self._table_title(blocks, table_bounds),
                    bounds=table_bounds,
                    row_count=row_count,
                    column_count=column_count,
                    default_unit=default_unit,
                    cells=tuple(cells),
                )
            )
        return tuple(tables)

    @staticmethod
    def _rectangle(bounds: Iterable[float]) -> FundamentalFilingRectangle:
        x0, y0, x1, y1 = (float(value) for value in bounds)
        return FundamentalFilingRectangle(x0=x0, y0=y0, x1=x1, y1=y1)

    @staticmethod
    def _table_title(
        blocks: tuple[FundamentalFilingTextBlock, ...],
        bounds: FundamentalFilingRectangle,
    ) -> str | None:
        candidates = [
            block
            for block in blocks
            if block.bounds.y1 <= bounds.y0
            and bounds.y0 - block.bounds.y1 <= _TABLE_UNIT_LOOKBACK_POINTS
            and block.kind is FundamentalFilingTextBlockKind.HEADING
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda block: block.bounds.y1).text

    def _table_unit(
        self,
        blocks: tuple[FundamentalFilingTextBlock, ...],
        bounds: FundamentalFilingRectangle,
    ) -> FundamentalFilingUnit | None:
        candidates = [
            block.text
            for block in blocks
            if block.bounds.y1 <= bounds.y0
            and bounds.y0 - block.bounds.y1 <= _TABLE_UNIT_LOOKBACK_POINTS
        ]
        for text in reversed(candidates):
            unit = self._detect_unit(text)
            if unit is not None:
                return unit
        return None

    def _matrix_table_unit(
        self,
        matrix: list[list[str | None]],
    ) -> FundamentalFilingUnit | None:
        for row in matrix[:3]:
            for value in row:
                text = " ".join(str(value or "").split())
                if not text or _STANDALONE_UNIT_PATTERN.fullmatch(text) is None:
                    continue
                unit = self._detect_unit(text)
                if unit is not None:
                    return unit
        return None

    def _row_unit(
        self,
        values: list[str | None],
    ) -> FundamentalFilingUnit | None:
        label = next(
            (" ".join(str(value).split()) for value in values if value),
            "",
        )
        if not label:
            return None
        match = _PARENTHETICAL_UNIT_PATTERN.search(label)
        if match is None:
            return None
        return self._detect_unit(f"in {match.group('label')}")

    @staticmethod
    def _detect_unit(text: str) -> FundamentalFilingUnit | None:
        normalized = " ".join(text.casefold().split())
        if "%" in text or "percent" in normalized:
            return FundamentalFilingUnit(
                kind=FundamentalFilingUnitKind.PERCENTAGE,
                source_label="%",
                multiplier=Decimal("0.01"),
            )
        if (
            "basis point" in normalized
            or _NUMERIC_BPS_UNIT_PATTERN.search(text)
            or _LABELLED_BPS_UNIT_PATTERN.search(text)
        ):
            return FundamentalFilingUnit(
                kind=FundamentalFilingUnitKind.BASIS_POINTS,
                source_label="bps",
                multiplier=Decimal("0.0001"),
            )
        currency = None
        if "inr" in normalized or "rs." in normalized or "₹" in text:
            currency = "INR"
        elif "usd" in normalized or "$" in text:
            currency = "USD"
        if currency is None:
            return None
        multiplier = Decimal("1")
        label = currency
        if "crore" in normalized or re.search(r"\bcr\.?\b", normalized):
            multiplier = Decimal("10000000")
            label = f"{currency} crore"
        elif "million" in normalized or re.search(r"\bmn\.?\b", normalized):
            multiplier = Decimal("1000000")
            label = f"{currency} million"
        elif "lakh" in normalized:
            multiplier = Decimal("100000")
            label = f"{currency} lakh"
        return FundamentalFilingUnit(
            kind=FundamentalFilingUnitKind.CURRENCY,
            source_label=label,
            currency=currency,
            multiplier=multiplier,
        )

    def _observed_value(
        self,
        text: str,
        default_unit: FundamentalFilingUnit | None,
    ) -> FundamentalFilingObservedValue | None:
        stripped = " ".join(text.split())
        explicit_unit = self._detect_unit(stripped)
        unit = explicit_unit or default_unit
        if _MISSING_VALUE_PATTERN.fullmatch(stripped):
            return FundamentalFilingObservedValue(
                raw_text=stripped,
                status=FundamentalFilingValueStatus.UNAVAILABLE,
                unit=unit,
            )

        numeric = stripped
        if numeric.endswith("%"):
            numeric = numeric[:-1].strip()
            unit = self._detect_unit("%")
        numeric = re.sub(
            r"(?i)(?:₹|\$|\b(?:inr|usd|rs\.?|crores?|cr\.?|millions?|"
            r"mn\.?|lakhs?|bps)\b)",
            "",
            numeric,
        ).strip()
        if not _NUMBER_PATTERN.fullmatch(numeric):
            return None
        negative = numeric.startswith("(") and numeric.endswith(")")
        numeric = numeric.strip("()").replace(",", "")
        if numeric in {"", "+", "-", "."}:
            return None
        try:
            value = Decimal(numeric)
        except InvalidOperation:
            return None
        if negative:
            value = -value
        return FundamentalFilingObservedValue(
            raw_text=stripped,
            status=FundamentalFilingValueStatus.EXACT,
            normalized_value=value,
            unit=unit,
        )

    def _extract_speaker_turns(
        self,
        pages: tuple[FundamentalFilingPage, ...],
    ) -> tuple[
        tuple[FundamentalFilingSpeakerTurn, ...],
        dict[str, tuple[tuple[FundamentalFilingTextBlock, str, int, int], ...]],
    ]:
        turns: list[FundamentalFilingSpeakerTurn] = []
        sources: dict[
            str,
            tuple[tuple[FundamentalFilingTextBlock, str, int, int], ...],
        ] = {}
        candidate_blocks = [
            block
            for page in pages
            for block in page.text_blocks
            if block.kind
            in {
                FundamentalFilingTextBlockKind.PARAGRAPH,
                FundamentalFilingTextBlockKind.LIST_ITEM,
            }
        ]
        for block in candidate_blocks:
            match = _SPEAKER_PATTERN.fullmatch(block.text)
            if match is None:
                continue
            label = " ".join(match.group("label").split())
            body = match.group("body").strip()
            if not body or not self._is_explicit_speaker_label(label):
                continue
            role = self._speaker_role(label)
            turn_id = f"speaker.turn.{len(turns):06d}"
            body_start = block.text.index(match.group("body"))
            while body_start < len(block.text) and block.text[body_start].isspace():
                body_start += 1
            body_end = body_start + len(body)
            turn = FundamentalFilingSpeakerTurn(
                element_id=turn_id,
                turn_index=len(turns),
                speaker_name=label,
                speaker_role=role,
                attribution=(
                    FundamentalFilingSpeakerAttribution.EXPLICIT_TRANSCRIPT_LABEL
                ),
                text=body,
                source_block_ids=(block.element_id,),
            )
            turns.append(turn)
            sources[turn_id] = ((block, body, body_start, body_end),)
        return tuple(turns), sources

    def _extract_participants(
        self,
        pages: tuple[FundamentalFilingPage, ...],
        citations: tuple[FundamentalFilingCitation, ...],
        *,
        issuer_name: str,
    ) -> tuple[FundamentalFilingParticipant, ...]:
        citation_ids_by_block = {
            citation.source_element_id: citation.citation_id
            for citation in citations
            if citation.element_type
            is FundamentalFilingCitationElementType.TEXT_BLOCK
        }
        identities: dict[str, dict[str, object]] = {}
        for page in pages:
            active_section: str | None = None
            for block in page.text_blocks:
                roster_match = _ROSTER_SECTION_PATTERN.fullmatch(
                    " ".join(block.text.split())
                )
                entry_text: str | None = None
                if roster_match is not None:
                    active_section = roster_match.group("section").casefold()
                    entry_text = roster_match.group("entry")
                elif active_section is not None:
                    candidate = _ROSTER_BULLET_PREFIX.sub("", block.text).strip()
                    if (
                        block.kind is FundamentalFilingTextBlockKind.LIST_ITEM
                        or _ROSTER_ENTRY_SEPARATOR.search(candidate) is not None
                    ):
                        entry_text = candidate
                    else:
                        active_section = None

                if active_section is None or not entry_text:
                    continue
                parsed = self._parse_roster_entry(entry_text)
                if parsed is None:
                    continue
                name, descriptor = parsed
                role = _ROSTER_ROLES[active_section]
                normalized_name = self._normalize_participant_name(name)
                citation_id = citation_ids_by_block.get(block.element_id)
                if not normalized_name or citation_id is None:
                    continue
                title, organization, affiliation = self._participant_context(
                    role,
                    descriptor=descriptor,
                    issuer_name=issuer_name,
                )
                identity = {
                    "name": name,
                    "normalized_name": normalized_name,
                    "role": role,
                    "title": title,
                    "organization": organization,
                    "issuer_affiliation": affiliation,
                }
                existing = identities.get(normalized_name)
                if existing is not None:
                    comparable = {
                        key: value
                        for key, value in existing.items()
                        if key != "citation_ids"
                    }
                    if comparable != identity:
                        raise FundamentalFilingExtractionError(
                            "conference-call roster contains a conflicting identity"
                        )
                    citation_ids = existing["citation_ids"]
                    if not isinstance(citation_ids, set):
                        raise FundamentalFilingExtractionError(
                            "conference-call roster state is invalid"
                        )
                    citation_ids.add(citation_id)
                    continue
                identities[normalized_name] = {
                    **identity,
                    "citation_ids": {citation_id},
                }

        participants = []
        for normalized_name, identity in identities.items():
            citation_ids = identity.pop("citation_ids")
            if not isinstance(citation_ids, set):
                raise FundamentalFilingExtractionError(
                    "conference-call roster state is invalid"
                )
            participants.append(
                FundamentalFilingParticipant(
                    participant_id=(
                        "participant."
                        + sha256(normalized_name.encode("utf-8")).hexdigest()[:24]
                    ),
                    **identity,
                    attribution_method=(
                        FundamentalFilingParticipantAttributionMethod.EXPLICIT_ROSTER
                    ),
                    attribution_confidence=(
                        FundamentalFilingParticipantAttributionConfidence.VERIFIED
                    ),
                    citation_ids=tuple(sorted(citation_ids)),
                )
            )
        return tuple(sorted(participants, key=lambda item: item.participant_id))

    @staticmethod
    def _parse_roster_entry(value: str) -> tuple[str, str | None] | None:
        candidate = _ROSTER_BULLET_PREFIX.sub("", value).strip()
        if not candidate or ":" in candidate:
            return None
        parts = _ROSTER_ENTRY_SEPARATOR.split(candidate, maxsplit=1)
        name = " ".join(parts[0].split())
        descriptor = " ".join(parts[1].split()) if len(parts) == 2 else None
        tokens = name.split()
        if (
            len(tokens) < 2
            or len(tokens) > 8
            or not all(
                any(character.isalpha() for character in token)
                for token in tokens
            )
        ):
            return None
        return name, descriptor

    @staticmethod
    def _participant_context(
        role: FundamentalFilingSpeakerRole,
        *,
        descriptor: str | None,
        issuer_name: str,
    ) -> tuple[str | None, str | None, FundamentalFilingParticipantAffiliation]:
        if role is FundamentalFilingSpeakerRole.MANAGEMENT:
            return (
                descriptor,
                issuer_name,
                FundamentalFilingParticipantAffiliation.ISSUER,
            )
        title = {
            FundamentalFilingSpeakerRole.ANALYST: "Questioner",
            FundamentalFilingSpeakerRole.MODERATOR: "Moderator",
            FundamentalFilingSpeakerRole.OPERATOR: "Operator",
        }[role]
        return (
            title,
            descriptor,
            FundamentalFilingParticipantAffiliation.EXTERNAL,
        )

    @staticmethod
    def _normalize_participant_name(value: str) -> str:
        return " ".join(
            "".join(
                character if character.isalnum() else " "
                for character in value.casefold()
            ).split()
        )

    def _map_speaker_turns_to_participants(
        self,
        turns: tuple[FundamentalFilingSpeakerTurn, ...],
        participants: tuple[FundamentalFilingParticipant, ...],
    ) -> tuple[FundamentalFilingSpeakerTurn, ...]:
        participants_by_name = {
            participant.normalized_name: participant
            for participant in participants
        }
        mapped = []
        for turn in turns:
            if turn.speaker_name is None:
                mapped.append(turn)
                continue
            participant = participants_by_name.get(
                self._normalize_participant_name(turn.speaker_name)
            )
            if participant is None:
                mapped.append(turn)
                continue
            mapped.append(
                turn.model_copy(
                    update={
                        "speaker_role": participant.role,
                        "participant_id": participant.participant_id,
                    }
                )
            )
        return tuple(mapped)

    @staticmethod
    def _is_explicit_speaker_label(label: str) -> bool:
        normalized = label.casefold().strip()
        if normalized in _NON_SPEAKER_LABELS:
            return False
        if normalized in {"operator", "moderator", "analyst", "management"}:
            return True
        if any(term in normalized for term in _MANAGEMENT_ROLE_TERMS):
            return True
        tokens = [
            token.strip("().'\"")
            for token in label.split()
            if token.strip("().'\"")
        ]
        return 2 <= len(tokens) <= 5 and all(
            token[0].isupper() and token.replace("-", "").isalpha()
            for token in tokens
        )

    @staticmethod
    def _speaker_role(label: str) -> FundamentalFilingSpeakerRole:
        normalized = label.casefold()
        if "operator" in normalized:
            return FundamentalFilingSpeakerRole.OPERATOR
        if "moderator" in normalized:
            return FundamentalFilingSpeakerRole.MODERATOR
        if "analyst" in normalized:
            return FundamentalFilingSpeakerRole.ANALYST
        if any(term in normalized for term in _MANAGEMENT_ROLE_TERMS):
            return FundamentalFilingSpeakerRole.MANAGEMENT
        return FundamentalFilingSpeakerRole.UNKNOWN

    @staticmethod
    def _build_citations(
        artifact: FundamentalFilingArtifactManifest,
        pages: tuple[FundamentalFilingPage, ...],
        speaker_turns: tuple[FundamentalFilingSpeakerTurn, ...],
        speaker_sources: dict[
            str,
            tuple[tuple[FundamentalFilingTextBlock, str, int, int], ...],
        ],
    ) -> tuple[FundamentalFilingCitation, ...]:
        citations: list[FundamentalFilingCitation] = []
        for page in pages:
            for block in page.text_blocks:
                citations.append(
                    FundamentalFilingCitation(
                        artifact_id=artifact.artifact_id,
                        content_sha256=artifact.content_sha256,
                        page_number=page.page_number,
                        element_type=(
                            FundamentalFilingCitationElementType.TEXT_BLOCK
                        ),
                        source_element_id=block.element_id,
                        exact_excerpt=block.text,
                        bounds=block.bounds,
                        start_offset=0,
                        end_offset=len(block.text),
                    )
                )
            for table in page.tables:
                for cell in table.cells:
                    citations.append(
                        FundamentalFilingCitation(
                            artifact_id=artifact.artifact_id,
                            content_sha256=artifact.content_sha256,
                            page_number=page.page_number,
                            element_type=(
                                FundamentalFilingCitationElementType.TABLE_CELL
                            ),
                            source_element_id=cell.element_id,
                            exact_excerpt=cell.text,
                            bounds=cell.bounds,
                            start_offset=0,
                            end_offset=len(cell.text),
                        )
                    )
        turns_by_id = {turn.element_id: turn for turn in speaker_turns}
        for turn_id, source_spans in speaker_sources.items():
            if turn_id not in turns_by_id:
                continue
            for block, excerpt, start_offset, end_offset in source_spans:
                citations.append(
                    FundamentalFilingCitation(
                        artifact_id=artifact.artifact_id,
                        content_sha256=artifact.content_sha256,
                        page_number=block.page_number,
                        element_type=(
                            FundamentalFilingCitationElementType.SPEAKER_TURN
                        ),
                        source_element_id=turn_id,
                        exact_excerpt=excerpt,
                        bounds=block.bounds,
                        start_offset=start_offset,
                        end_offset=end_offset,
                    )
                )
        return tuple(sorted(citations, key=lambda item: item.citation_id))

    @staticmethod
    def _printed_page_label(
        blocks: tuple[FundamentalFilingTextBlock, ...],
    ) -> str | None:
        labels = [
            block.text
            for block in blocks
            if block.kind is FundamentalFilingTextBlockKind.PAGE_NUMBER
        ]
        return labels[-1] if labels else None

    @staticmethod
    def _warning(
        page_number: int,
        code: FundamentalFilingWarningCode,
        severity: FundamentalFilingWarningSeverity,
        message: str,
    ) -> FundamentalFilingExtractionWarning:
        return FundamentalFilingExtractionWarning(
            warning_id=f"warning.page.{page_number:04d}.{code.value}",
            code=code,
            severity=severity,
            message=message,
            page_number=page_number,
        )

    @staticmethod
    def _limitation(
        page_number: int,
        code: str,
        description: str,
        *,
        material: bool,
    ) -> FundamentalFilingExtractionLimitation:
        return FundamentalFilingExtractionLimitation(
            limitation_id=f"limitation.page.{page_number:04d}.{code}",
            description=description,
            affected_pages=(page_number,),
            material=material,
        )

    @staticmethod
    def _coverage(
        pages: tuple[FundamentalFilingPage, ...],
        speaker_turns: tuple[FundamentalFilingSpeakerTurn, ...],
    ) -> FundamentalFilingExtractionCoverage:
        extracted_pages = sum(
            page.status is FundamentalFilingPageStatus.EXTRACTED
            for page in pages
        )
        partial_pages = sum(
            page.status is FundamentalFilingPageStatus.PARTIAL
            for page in pages
        )
        unreadable_pages = sum(
            page.status is FundamentalFilingPageStatus.UNREADABLE
            for page in pages
        )
        status = FundamentalFilingCoverageStatus.COMPLETE
        if unreadable_pages == len(pages):
            status = FundamentalFilingCoverageStatus.FAILED
        elif partial_pages or unreadable_pages:
            status = FundamentalFilingCoverageStatus.PARTIAL
        return FundamentalFilingExtractionCoverage(
            status=status,
            expected_pages=len(pages),
            represented_pages=len(pages),
            extracted_pages=extracted_pages,
            partial_pages=partial_pages,
            unreadable_pages=unreadable_pages,
            ocr_pages=0,
            table_count=sum(len(page.tables) for page in pages),
            speaker_turn_count=len(speaker_turns),
        )
