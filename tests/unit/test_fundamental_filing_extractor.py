import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pdfplumber.utils.exceptions import PdfminerException

from app.fundamentals.filing_extractor import (
    DEFAULT_FILING_EXTRACTION_POLICY,
    DeterministicFundamentalFilingExtractor,
    FundamentalFilingExtractionError,
)
from app.models.fundamental_filing_evidence import (
    FundamentalFilingCitationElementType,
    FundamentalFilingCoverageStatus,
    FundamentalFilingExtractionMethod,
    FundamentalFilingExtractionPolicy,
    FundamentalFilingPageStatus,
    FundamentalFilingParticipantAffiliation,
    FundamentalFilingSpeakerRole,
    FundamentalFilingTextBlockKind,
    FundamentalFilingUnitKind,
    FundamentalFilingWarningCode,
)
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingCategory,
    FundamentalFilingSourceClass,
)
from tests.unit.test_fundamental_filings import COMMITTED_AT, artifact


def _pdf_bytes(page_streams: tuple[str, ...]) -> bytes:
    """Build a deterministic native-text PDF without a fixture dependency."""

    font_object_id = 3 + len(page_streams) * 2
    page_object_ids = tuple(3 + index * 2 for index in range(len(page_streams)))
    content_object_ids = tuple(value + 1 for value in page_object_ids)
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Count "
            f"{len(page_streams)} /Kids ["
            + " ".join(f"{value} 0 R" for value in page_object_ids)
            + "] >>"
        ).encode("ascii"),
        font_object_id: (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        ),
    }
    for page_id, content_id, stream_text in zip(
        page_object_ids,
        content_object_ids,
        page_streams,
        strict=True,
    ):
        stream = stream_text.encode("ascii")
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_object_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )

    payload = bytearray(b"%PDF-1.4\n")
    offsets = {0: 0}
    for object_id in range(1, font_object_id + 1):
        offsets[object_id] = len(payload)
        payload.extend(f"{object_id} 0 obj\n".encode("ascii"))
        payload.extend(objects[object_id])
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {font_object_id + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for object_id in range(1, font_object_id + 1):
        payload.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {font_object_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def _native_filing_pdf(*, include_blank_page: bool = False) -> bytes:
    first_page = "\n".join(
        (
            "BT /F1 18 Tf 72 740 Td (Financial Highlights) Tj ET",
            "BT /F1 10 Tf 72 700 Td "
            "(Chief Executive Officer: We expect revenue growth of 13%.) Tj ET",
            "BT /F1 10 Tf 72 660 Td (In INR crore) Tj ET",
            "72 620 m 420 620 l S",
            "72 590 m 420 590 l S",
            "72 560 m 420 560 l S",
            "72 560 m 72 620 l S",
            "260 560 m 260 620 l S",
            "420 560 m 420 620 l S",
            "BT /F1 10 Tf 78 600 Td (Revenue) Tj ET",
            "BT /F1 10 Tf 268 600 Td (FY2026) Tj ET",
            "BT /F1 10 Tf 78 570 Td (1,234) Tj ET",
            "BT /F1 10 Tf 268 570 Td (15.2%) Tj ET",
            "BT /F1 10 Tf 300 20 Td (1) Tj ET",
        )
    )
    second_page = "\n".join(
        (
            "BT /F1 16 Tf 72 740 Td (Risk Factors) Tj ET",
            "BT /F1 10 Tf 72 700 Td (Risk remains material.) Tj ET",
            "BT /F1 10 Tf 300 20 Td (2) Tj ET",
        )
    )
    streams = (first_page, second_page)
    if include_blank_page:
        streams = (*streams, "")
    return _pdf_bytes(streams)


def _conference_call_pdf(
    *,
    duplicate_management: bool = False,
    conflicting_identity: bool = False,
) -> bytes:
    roster_lines = [
        "BT /F1 18 Tf 72 740 Td (Q3 FY26 Earnings Webinar) Tj ET",
        "BT /F1 16 Tf 72 710 Td (MANAGEMENT) Tj ET",
        "BT /F1 10 Tf 72 685 Td "
        "(Ramesh Gopalan - Managing Director & Group CEO) Tj ET",
        "BT /F1 10 Tf 72 665 Td "
        "(Abhishek Kayan - Deputy Chief Financial Officer) Tj ET",
    ]
    if duplicate_management:
        roster_lines.append(
            "BT /F1 10 Tf 72 645 Td "
            "(Ramesh Gopalan - Managing Director & Group CEO) Tj ET"
        )
    roster_lines.extend(
        (
            "BT /F1 16 Tf 72 615 Td (MODERATOR) Tj ET",
            "BT /F1 10 Tf 72 590 Td "
            "(Siddharth Rangnekar - CDR India) Tj ET",
            "BT /F1 16 Tf 72 560 Td (QUESTIONERS) Tj ET",
            "BT /F1 10 Tf 72 535 Td "
            "(Manik Taneja - Axis Capital) Tj ET",
        )
    )
    if conflicting_identity:
        roster_lines.append(
            "BT /F1 10 Tf 72 515 Td "
            "(Ramesh Gopalan - Axis Capital) Tj ET"
        )
    transcript_page = "\n".join(
        (
            "BT /F1 10 Tf 72 740 Td "
            "(Siddharth Rangnekar: Welcome to the earnings call.) Tj ET",
            "BT /F1 10 Tf 72 710 Td "
            "(Ramesh Gopalan: We continue to execute our strategy.) Tj ET",
            "BT /F1 10 Tf 72 680 Td "
            "(Abhishek Kayan: Cash conversion remained strong.) Tj ET",
            "BT /F1 10 Tf 72 650 Td "
            "(Manik Taneja: Could you discuss the growth outlook?) Tj ET",
            "BT /F1 10 Tf 72 620 Td "
            "(Unlisted Guest: This identity is not in the roster.) Tj ET",
            "BT /F1 10 Tf 72 590 Td "
            "(Corporate Identification Number: L12345KA2021PLC123456) Tj ET",
        )
    )
    return _pdf_bytes(("\n".join(roster_lines), transcript_page))


def _manifest_for(
    root: Path,
    pdf: bytes,
    *,
    page_count: int,
    category: FundamentalFilingCategory = FundamentalFilingCategory.EARNINGS_RELEASE,
) -> FundamentalFilingArtifactManifest:
    content_hash = hashlib.sha256(pdf).hexdigest()
    relative_path = (
        "tenant/provider/INE591G01017/FY2027/Q1/earnings-release/"
        f"{content_hash}.pdf"
    )
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    for parent in (path.parent, *path.parent.parents):
        if parent == root.parent:
            break
        if parent.is_relative_to(root):
            parent.chmod(0o700)
    path.write_bytes(pdf)
    path.chmod(0o600)
    base = artifact()
    source_class = (
        FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
        if category is FundamentalFilingCategory.CONFERENCE_CALL
        else FundamentalFilingSourceClass.COMPANY_AUTHORED_EARNINGS_RELEASE
    )
    filing_reference = base.reference.model_copy(
        update={
            "category": category,
            "source_class": source_class,
        }
    )
    return FundamentalFilingArtifactManifest(
        reference=filing_reference,
        content_sha256=content_hash,
        safe_relative_path=relative_path,
        byte_length=len(pdf),
        page_count=page_count,
        pdf_version="1.4",
        downloaded_at=base.downloaded_at,
        last_checked_at=base.last_checked_at,
        recheck_after=base.recheck_after,
    )


class _UnreadableNativePage:
    width = 612.0
    height = 792.0
    rotation = 0
    images = ()

    @staticmethod
    def extract_words(**kwargs):
        del kwargs
        raise PdfminerException("synthetic unreadable page")


class _TableFailurePage:
    width = 612.0
    height = 792.0
    rotation = 0
    images = ()

    @staticmethod
    def extract_words(**kwargs):
        del kwargs
        return [
            {
                "text": "Revenue increased.",
                "x0": 72.0,
                "x1": 180.0,
                "top": 72.0,
                "bottom": 84.0,
                "fontname": "Helvetica",
                "size": 10.0,
            }
        ]

    @staticmethod
    def find_tables():
        raise PdfminerException("synthetic table failure")


class _SyntheticTableRow:
    def __init__(self, row_index: int) -> None:
        y0 = 20.0 + row_index * 25.0
        y1 = y0 + 25.0
        self.cells = (
            (20.0, y0, 350.0, y1),
            (350.0, y0, 470.0, y1),
            (470.0, y0, 580.0, y1),
        )


class _SyntheticMixedUnitTable:
    bbox = (20.0, 20.0, 580.0, 120.0)
    rows = tuple(_SyntheticTableRow(index) for index in range(4))

    @staticmethod
    def extract():
        return [
            ["Quarter KPIs", "Q3 FY26", "Y-o-Y %"],
            ["Revenue from Operation (in INR million)", "19,712", "35.7%"],
            ["Total Number of Employees", "48,522", "22.5%"],
            ["Clients contributing more than US$20 million", "8", "14.3%"],
        ]


class _SyntheticMixedUnitPage:
    @staticmethod
    def find_tables():
        return [_SyntheticMixedUnitTable()]


class _SyntheticOffPageTextPage:
    width = 100.0
    height = 100.0
    rotation = 0
    images = ()

    @staticmethod
    def extract_words(**_kwargs):
        return [
            {
                "text": "Visible",
                "x0": 10.0,
                "x1": 50.0,
                "top": 10.0,
                "bottom": 20.0,
                "fontname": "Helvetica",
                "size": 10.0,
            },
            {
                "text": "Clipped",
                "x0": 90.0,
                "x1": 110.0,
                "top": 30.0,
                "bottom": 40.0,
                "fontname": "Helvetica",
                "size": 10.0,
            },
            {
                "text": "Hidden",
                "x0": 110.0,
                "x1": 150.0,
                "top": 50.0,
                "bottom": 60.0,
                "fontname": "Helvetica",
                "size": 10.0,
            },
        ]

    @staticmethod
    def find_tables():
        return []


class DeterministicFundamentalFilingExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.root.chmod(0o700)
        self.extracted_at = COMMITTED_AT + timedelta(minutes=2)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def extractor(self) -> DeterministicFundamentalFilingExtractor:
        return DeterministicFundamentalFilingExtractor(
            artifact_root=self.root
        )

    def test_excludes_hidden_off_page_text_and_clips_intersecting_bounds(self):
        page, warnings, limitations = self.extractor()._extract_page(
            _SyntheticOffPageTextPage(),
            page_number=1,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(
            tuple(block.text for block in page.text_blocks),
            ("Visible", "Clipped"),
        )
        self.assertEqual(page.text_blocks[1].bounds.x1, 100.0)
        self.assertEqual(
            tuple(item.limitation_id for item in limitations),
            ("limitation.page.0001.off_page_text_excluded",),
        )

    def test_extracts_native_pages_text_table_speaker_and_citations(self):
        pdf = _native_filing_pdf()
        manifest = _manifest_for(
            self.root,
            pdf,
            page_count=2,
            category=FundamentalFilingCategory.CONFERENCE_CALL,
        )

        result = self.extractor().extract(
            manifest,
            extracted_at=self.extracted_at,
        )

        self.assertEqual(len(result.pages), 2)
        self.assertEqual(
            result.coverage.status,
            FundamentalFilingCoverageStatus.COMPLETE,
        )
        self.assertEqual(result.coverage.table_count, 1)
        headings = [
            block.text
            for page in result.pages
            for block in page.text_blocks
            if block.kind is FundamentalFilingTextBlockKind.HEADING
        ]
        self.assertIn("Financial Highlights", headings)
        table = result.pages[0].tables[0]
        self.assertEqual(
            table.default_unit.kind,
            FundamentalFilingUnitKind.CURRENCY,
        )
        self.assertEqual(table.default_unit.currency, "INR")
        values = {
            cell.text: cell.observed_value for cell in table.cells
        }
        self.assertEqual(values["1,234"].normalized_value, Decimal("1234"))
        self.assertEqual(
            values["15.2%"].unit.kind,
            FundamentalFilingUnitKind.PERCENTAGE,
        )
        self.assertEqual(len(result.speaker_turns), 1)
        self.assertEqual(
            result.speaker_turns[0].speaker_role,
            FundamentalFilingSpeakerRole.MANAGEMENT,
        )
        citation_types = {item.element_type for item in result.citations}
        self.assertEqual(
            citation_types,
            {
                FundamentalFilingCitationElementType.TEXT_BLOCK,
                FundamentalFilingCitationElementType.TABLE_CELL,
                FundamentalFilingCitationElementType.SPEAKER_TURN,
            },
        )
        self.assertFalse(result.observed_facts)
        self.assertFalse(result.management_claims)

    def test_same_pdf_policy_and_time_produce_same_fingerprint(self):
        pdf = _native_filing_pdf()
        manifest = _manifest_for(
            self.root,
            pdf,
            page_count=2,
            category=FundamentalFilingCategory.CONFERENCE_CALL,
        )
        extractor = self.extractor()

        first = extractor.extract(manifest, extracted_at=self.extracted_at)
        repeated = extractor.extract(manifest, extracted_at=self.extracted_at)

        self.assertEqual(first, repeated)
        self.assertEqual(
            first.extraction_fingerprint,
            repeated.extraction_fingerprint,
        )
        self.assertEqual(first.policy.policy_version, "1.4.0")
        self.assertIn("pdfplumber", first.policy.text_engine)

    def test_distinguishes_basis_point_units_from_business_process_services(self):
        extractor = self.extractor()

        self.assertIsNone(extractor._detect_unit("(Excl. BPS)"))
        self.assertIsNone(extractor._detect_unit("IT + BPS headcount"))
        for value in ("180 Bps", "Change (bps)", "in bps", "basis points"):
            with self.subTest(value=value):
                unit = extractor._detect_unit(value)
                self.assertEqual(
                    unit.kind,
                    FundamentalFilingUnitKind.BASIS_POINTS,
                )
                self.assertEqual(unit.source_label, "bps")
                self.assertEqual(unit.multiplier, Decimal("0.0001"))

    def test_explicit_basis_points_override_a_currency_table_default(self):
        extractor = self.extractor()
        currency = extractor._detect_unit("In INR million")

        value = extractor._observed_value("180 Bps", currency)

        self.assertEqual(value.normalized_value, Decimal("180"))
        self.assertEqual(
            value.unit.kind,
            FundamentalFilingUnitKind.BASIS_POINTS,
        )
        self.assertEqual(value.unit.source_label, "bps")

    def test_propagates_only_explicit_row_and_table_unit_declarations(self):
        extractor = self.extractor()
        table = extractor._tables(
            _SyntheticMixedUnitPage(),
            page_number=1,
            blocks=(),
        )[0]
        cells = {
            (cell.row_index, cell.column_index): cell for cell in table.cells
        }

        revenue = cells[(1, 1)].observed_value
        self.assertEqual(revenue.unit.kind, FundamentalFilingUnitKind.CURRENCY)
        self.assertEqual(revenue.unit.currency, "INR")
        self.assertEqual(revenue.unit.multiplier, Decimal("1000000"))
        self.assertEqual(
            cells[(1, 2)].observed_value.unit.kind,
            FundamentalFilingUnitKind.PERCENTAGE,
        )
        self.assertIsNone(cells[(2, 1)].observed_value.unit)
        self.assertIsNone(cells[(3, 1)].observed_value.unit)
        self.assertIsNone(table.default_unit)

        declared_table_unit = extractor._matrix_table_unit(
            [["In INR million", None], ["Revenue", "19,712"]]
        )
        self.assertEqual(
            declared_table_unit.kind,
            FundamentalFilingUnitKind.CURRENCY,
        )
        self.assertEqual(declared_table_unit.currency, "INR")
        self.assertEqual(declared_table_unit.multiplier, Decimal("1000000"))

    def test_preserves_blank_native_page_with_an_explicit_warning(self):
        pdf = _native_filing_pdf(include_blank_page=True)
        manifest = _manifest_for(self.root, pdf, page_count=3)

        result = self.extractor().extract(
            manifest,
            extracted_at=self.extracted_at,
        )

        self.assertEqual(tuple(page.page_number for page in result.pages), (1, 2, 3))
        self.assertEqual(result.pages[2].status, FundamentalFilingPageStatus.EXTRACTED)
        self.assertEqual(
            result.pages[2].method,
            FundamentalFilingExtractionMethod.EMBEDDED_TEXT,
        )
        self.assertIn(
            FundamentalFilingWarningCode.EMPTY_PAGE,
            {warning.code for warning in result.warnings},
        )

    def test_does_not_infer_an_unlabelled_speaker(self):
        pdf = _native_filing_pdf()
        manifest = _manifest_for(
            self.root,
            pdf,
            page_count=2,
            category=FundamentalFilingCategory.CONFERENCE_CALL,
        )

        result = self.extractor().extract(
            manifest,
            extracted_at=self.extracted_at,
        )

        self.assertEqual(len(result.speaker_turns), 1)
        self.assertNotIn(
            "Risk remains material",
            result.speaker_turns[0].text,
        )

    def test_extracts_roster_and_maps_exact_later_speaker_labels(self):
        pdf = _conference_call_pdf()
        manifest = _manifest_for(
            self.root,
            pdf,
            page_count=2,
            category=FundamentalFilingCategory.CONFERENCE_CALL,
        )

        result = self.extractor().extract(
            manifest,
            extracted_at=self.extracted_at,
        )

        participants = {item.name: item for item in result.participants}
        self.assertEqual(
            set(participants),
            {
                "Abhishek Kayan",
                "Manik Taneja",
                "Ramesh Gopalan",
                "Siddharth Rangnekar",
            },
        )
        self.assertEqual(
            participants["Ramesh Gopalan"].issuer_affiliation,
            FundamentalFilingParticipantAffiliation.ISSUER,
        )
        self.assertEqual(
            participants["Ramesh Gopalan"].organization,
            manifest.reference.issuer.legal_name,
        )
        self.assertEqual(
            participants["Siddharth Rangnekar"].role,
            FundamentalFilingSpeakerRole.MODERATOR,
        )
        self.assertEqual(
            participants["Manik Taneja"].organization,
            "Axis Capital",
        )
        known_citation_ids = {citation.citation_id for citation in result.citations}
        self.assertTrue(
            all(
                set(item.citation_ids).issubset(known_citation_ids)
                for item in result.participants
            )
        )

        turns = {turn.speaker_name: turn for turn in result.speaker_turns}
        for name in participants:
            self.assertEqual(
                turns[name].participant_id,
                participants[name].participant_id,
            )
            self.assertEqual(turns[name].speaker_role, participants[name].role)
        self.assertIsNone(turns["Unlisted Guest"].participant_id)
        self.assertEqual(
            turns["Unlisted Guest"].speaker_role,
            FundamentalFilingSpeakerRole.UNKNOWN,
        )
        self.assertNotIn("Corporate Identification Number", turns)

    def test_merges_duplicate_roster_entries_and_rejects_conflicts(self):
        duplicate_pdf = _conference_call_pdf(duplicate_management=True)
        duplicate_manifest = _manifest_for(
            self.root,
            duplicate_pdf,
            page_count=2,
            category=FundamentalFilingCategory.CONFERENCE_CALL,
        )

        result = self.extractor().extract(
            duplicate_manifest,
            extracted_at=self.extracted_at,
        )

        ramesh = next(
            item for item in result.participants if item.name == "Ramesh Gopalan"
        )
        self.assertEqual(len(ramesh.citation_ids), 2)

        conflict_pdf = _conference_call_pdf(conflicting_identity=True)
        conflict_manifest = _manifest_for(
            self.root,
            conflict_pdf,
            page_count=2,
            category=FundamentalFilingCategory.CONFERENCE_CALL,
        )
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "conflicting identity",
        ):
            self.extractor().extract(
                conflict_manifest,
                extracted_at=self.extracted_at,
            )

    def test_does_not_treat_a_corporate_identifier_as_a_speaker(self):
        pdf = _pdf_bytes(
            (
                "BT /F1 10 Tf 72 740 Td "
                "(Corporate Identification Number: L12345KA2021PLC123456) Tj ET",
            )
        )
        manifest = _manifest_for(
            self.root,
            pdf,
            page_count=1,
            category=FundamentalFilingCategory.EARNINGS_RELEASE,
        )

        result = self.extractor().extract(
            manifest,
            extracted_at=self.extracted_at,
        )

        self.assertFalse(result.participants)
        self.assertFalse(result.speaker_turns)

    def test_rejects_byte_length_hash_and_page_count_mismatches(self):
        pdf = _native_filing_pdf()
        manifest = _manifest_for(
            self.root,
            pdf,
            page_count=2,
            category=FundamentalFilingCategory.CONFERENCE_CALL,
        )
        path = self.root.joinpath(*manifest.safe_relative_path.split("/"))

        wrong_length = manifest.model_copy(
            update={"byte_length": manifest.byte_length + 1}
        )
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "byte length",
        ):
            self.extractor().extract(
                wrong_length,
                extracted_at=self.extracted_at,
            )

        corrupted = bytearray(pdf)
        corrupted[20] = ord("X")
        path.write_bytes(corrupted)
        path.chmod(0o600)
        with self.assertRaisesRegex(FundamentalFilingExtractionError, "hash"):
            self.extractor().extract(
                manifest,
                extracted_at=self.extracted_at,
            )

        path.write_bytes(pdf)
        path.chmod(0o600)
        wrong_pages = manifest.model_copy(update={"page_count": 3})
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "page count",
        ):
            self.extractor().extract(
                wrong_pages,
                extracted_at=self.extracted_at,
            )

    def test_rejects_non_owner_file_and_symbolic_artifact_path(self):
        pdf = _native_filing_pdf()
        manifest = _manifest_for(self.root, pdf, page_count=2)
        path = self.root.joinpath(*manifest.safe_relative_path.split("/"))

        path.parent.chmod(0o755)
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "directory must be owner-only",
        ):
            self.extractor().extract(
                manifest,
                extracted_at=self.extracted_at,
            )
        path.parent.chmod(0o700)

        path.chmod(0o644)
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "owner-only",
        ):
            self.extractor().extract(
                manifest,
                extracted_at=self.extracted_at,
            )

        path.chmod(0o600)
        target = path.with_name("retained-real.pdf")
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "not trusted",
        ):
            self.extractor().extract(
                manifest,
                extracted_at=self.extracted_at,
            )

    def test_rejects_missing_or_malformed_retained_pdf_safely(self):
        pdf = _native_filing_pdf()
        manifest = _manifest_for(self.root, pdf, page_count=2)
        path = self.root.joinpath(*manifest.safe_relative_path.split("/"))
        path.unlink()
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "unavailable",
        ):
            self.extractor().extract(
                manifest,
                extracted_at=self.extracted_at,
            )

        malformed = b"%PDF-1.4\nnot-a-valid-pdf\n%%EOF\n"
        malformed_manifest = _manifest_for(
            self.root,
            malformed,
            page_count=1,
        )
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "parsed safely",
        ):
            self.extractor().extract(
                malformed_manifest,
                extracted_at=self.extracted_at,
            )

    def test_synthetic_page_failures_are_attributable_not_silent(self):
        extractor = self.extractor()
        unreadable, warnings, limitations = extractor._extract_page(
            _UnreadableNativePage(),
            page_number=1,
        )
        self.assertEqual(
            unreadable.status,
            FundamentalFilingPageStatus.UNREADABLE,
        )
        self.assertEqual(
            warnings[0].code,
            FundamentalFilingWarningCode.UNREADABLE_PAGE,
        )
        self.assertTrue(limitations[0].material)

        partial, warnings, limitations = extractor._extract_page(
            _TableFailurePage(),
            page_number=1,
        )
        self.assertEqual(partial.status, FundamentalFilingPageStatus.PARTIAL)
        self.assertEqual(
            warnings[0].code,
            FundamentalFilingWarningCode.TABLE_STRUCTURE_UNCERTAIN,
        )
        self.assertFalse(limitations[0].material)

    def test_ocr_policy_is_rejected_until_a_versioned_adapter_exists(self):
        ocr_policy = FundamentalFilingExtractionPolicy(
            policy_id="filing.extraction.ocr",
            policy_version="1.0.0",
            text_engine=DEFAULT_FILING_EXTRACTION_POLICY.text_engine,
            table_engine=DEFAULT_FILING_EXTRACTION_POLICY.table_engine,
            ocr_engine="tesseract.5",
            ocr_enabled=True,
        )
        with self.assertRaisesRegex(ValueError, "does not execute OCR"):
            DeterministicFundamentalFilingExtractor(
                artifact_root=self.root,
                policy=ocr_policy,
            )

    def test_rejects_naive_extraction_time_and_untrusted_root(self):
        pdf = _native_filing_pdf()
        manifest = _manifest_for(self.root, pdf, page_count=2)
        with self.assertRaisesRegex(
            FundamentalFilingExtractionError,
            "timezone",
        ):
            self.extractor().extract(
                manifest,
                extracted_at=datetime(2026, 9, 14, 12, 0),
            )

        self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            DeterministicFundamentalFilingExtractor(
                artifact_root=self.root
            )


if __name__ == "__main__":
    unittest.main()
