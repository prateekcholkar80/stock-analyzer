import unittest
from datetime import date, timedelta
from decimal import Decimal

from pydantic import ValidationError

from app.models.fundamental_filing_evidence import (
    FundamentalFilingAdjustedMetric,
    FundamentalFilingCitation,
    FundamentalFilingCitationElementType,
    FundamentalFilingCoverageStatus,
    FundamentalFilingExtractedEvidence,
    FundamentalFilingExtractionCoverage,
    FundamentalFilingExtractionLimitation,
    FundamentalFilingExtractionMethod,
    FundamentalFilingExtractionPolicy,
    FundamentalFilingExtractionWarning,
    FundamentalFilingGuidanceCommitment,
    FundamentalFilingGuidanceValueKind,
    FundamentalFilingManagementClaim,
    FundamentalFilingObservedFact,
    FundamentalFilingObservedValue,
    FundamentalFilingPage,
    FundamentalFilingPageStatus,
    FundamentalFilingParticipant,
    FundamentalFilingParticipantAffiliation,
    FundamentalFilingParticipantAttributionConfidence,
    FundamentalFilingParticipantAttributionMethod,
    FundamentalFilingRectangle,
    FundamentalFilingRiskDisclosure,
    FundamentalFilingSpeakerAttribution,
    FundamentalFilingSpeakerRole,
    FundamentalFilingSpeakerTurn,
    FundamentalFilingTable,
    FundamentalFilingTableCell,
    FundamentalFilingTextBlock,
    FundamentalFilingTextBlockKind,
    FundamentalFilingUnit,
    FundamentalFilingUnitKind,
    FundamentalFilingValueStatus,
    FundamentalFilingWarningCode,
    FundamentalFilingWarningSeverity,
)
from tests.unit.test_fundamental_filings import COMMITTED_AT, artifact


def rectangle(
    x0: float = 20.0,
    y0: float = 20.0,
    x1: float = 500.0,
    y1: float = 50.0,
) -> FundamentalFilingRectangle:
    return FundamentalFilingRectangle(x0=x0, y0=y0, x1=x1, y1=y1)


def percentage_unit() -> FundamentalFilingUnit:
    return FundamentalFilingUnit(
        kind=FundamentalFilingUnitKind.PERCENTAGE,
        source_label="%",
        multiplier=Decimal("0.01"),
    )


def currency_unit() -> FundamentalFilingUnit:
    return FundamentalFilingUnit(
        kind=FundamentalFilingUnitKind.CURRENCY,
        source_label="INR crore",
        currency="INR",
        multiplier=Decimal("10000000"),
    )


def exact_value(
    value: str,
    *,
    raw_text: str | None = None,
    unit: FundamentalFilingUnit | None = None,
) -> FundamentalFilingObservedValue:
    return FundamentalFilingObservedValue(
        raw_text=raw_text or value,
        status=FundamentalFilingValueStatus.EXACT,
        normalized_value=Decimal(value),
        unit=unit,
    )


def text_blocks() -> tuple[FundamentalFilingTextBlock, ...]:
    return (
        FundamentalFilingTextBlock(
            element_id="page.1.block.1",
            page_number=1,
            reading_order=0,
            kind=FundamentalFilingTextBlockKind.HEADING,
            text="Financial Highlights",
            bounds=rectangle(),
            method=FundamentalFilingExtractionMethod.EMBEDDED_TEXT,
        ),
        FundamentalFilingTextBlock(
            element_id="page.1.block.2",
            page_number=1,
            reading_order=1,
            kind=FundamentalFilingTextBlockKind.PARAGRAPH,
            text="Management expects constant-currency growth of 13%. Risk remains.",
            bounds=rectangle(y0=60.0, y1=100.0),
            method=FundamentalFilingExtractionMethod.EMBEDDED_TEXT,
        ),
    )


def financial_table() -> FundamentalFilingTable:
    return FundamentalFilingTable(
        table_id="page.1.table.1",
        logical_table_id="document.table.financial_highlights",
        page_number=1,
        title="Financial Highlights",
        bounds=rectangle(y0=120.0, y1=210.0),
        row_count=2,
        column_count=1,
        default_unit=currency_unit(),
        cells=(
            FundamentalFilingTableCell(
                element_id="page.1.table.1.cell.0.0",
                page_number=1,
                row_index=0,
                column_index=0,
                text="Revenue growth",
                bounds=rectangle(y0=120.0, y1=150.0),
                is_header=True,
            ),
            FundamentalFilingTableCell(
                element_id="page.1.table.1.cell.1.0",
                page_number=1,
                row_index=1,
                column_index=0,
                text="15.2%",
                bounds=rectangle(y0=151.0, y1=180.0),
                observed_value=exact_value(
                    "15.2",
                    raw_text="15.2%",
                    unit=percentage_unit(),
                ),
            ),
        ),
    )


def pages() -> tuple[FundamentalFilingPage, ...]:
    first = FundamentalFilingPage(
        page_number=1,
        printed_page_label="1",
        width_points=595.0,
        height_points=842.0,
        status=FundamentalFilingPageStatus.EXTRACTED,
        method=FundamentalFilingExtractionMethod.EMBEDDED_TEXT,
        text_blocks=text_blocks(),
        tables=(financial_table(),),
    )
    remaining = tuple(
        FundamentalFilingPage(
            page_number=page_number,
            printed_page_label=str(page_number),
            width_points=595.0,
            height_points=842.0,
            status=FundamentalFilingPageStatus.EXTRACTED,
            method=FundamentalFilingExtractionMethod.EMBEDDED_TEXT,
        )
        for page_number in range(2, artifact().page_count + 1)
    )
    return (first, *remaining)


def speaker_turn() -> FundamentalFilingSpeakerTurn:
    return FundamentalFilingSpeakerTurn(
        element_id="turn.1",
        turn_index=0,
        speaker_name="Chief Executive Officer",
        speaker_role=FundamentalFilingSpeakerRole.MANAGEMENT,
        attribution=(
            FundamentalFilingSpeakerAttribution.EXPLICIT_TRANSCRIPT_LABEL
        ),
        text=text_blocks()[1].text,
        source_block_ids=("page.1.block.2",),
    )


def participant(
    *,
    participant_id: str = "participant.management.ceo",
    name: str = "Jane Doe",
    role: FundamentalFilingSpeakerRole = FundamentalFilingSpeakerRole.MANAGEMENT,
    title: str | None = "Chief Executive Officer",
    organization: str | None = "Example Limited",
    issuer_affiliation: FundamentalFilingParticipantAffiliation = (
        FundamentalFilingParticipantAffiliation.ISSUER
    ),
    attribution_method: FundamentalFilingParticipantAttributionMethod = (
        FundamentalFilingParticipantAttributionMethod.EXPLICIT_ROSTER
    ),
    attribution_confidence: FundamentalFilingParticipantAttributionConfidence = (
        FundamentalFilingParticipantAttributionConfidence.VERIFIED
    ),
) -> FundamentalFilingParticipant:
    return FundamentalFilingParticipant(
        participant_id=participant_id,
        name=name,
        normalized_name=" ".join(
            "".join(
                character if character.isalnum() else " "
                for character in name.casefold()
            ).split()
        ),
        role=role,
        title=title,
        organization=organization,
        issuer_affiliation=issuer_affiliation,
        attribution_method=attribution_method,
        attribution_confidence=attribution_confidence,
        citation_ids=(
            citation_by_type(
                FundamentalFilingCitationElementType.TEXT_BLOCK
            ).citation_id,
        ),
    )


def citations() -> tuple[FundamentalFilingCitation, ...]:
    filing_artifact = artifact()
    values = (
        FundamentalFilingCitation(
            artifact_id=filing_artifact.artifact_id,
            content_sha256=filing_artifact.content_sha256,
            page_number=1,
            element_type=FundamentalFilingCitationElementType.TEXT_BLOCK,
            source_element_id="page.1.block.2",
            exact_excerpt="Risk remains.",
            bounds=rectangle(x0=390.0, y0=60.0, x1=500.0, y1=100.0),
            start_offset=53,
            end_offset=66,
        ),
        FundamentalFilingCitation(
            artifact_id=filing_artifact.artifact_id,
            content_sha256=filing_artifact.content_sha256,
            page_number=1,
            element_type=FundamentalFilingCitationElementType.TABLE_CELL,
            source_element_id="page.1.table.1.cell.1.0",
            exact_excerpt="15.2%",
            bounds=rectangle(y0=151.0, y1=180.0),
        ),
        FundamentalFilingCitation(
            artifact_id=filing_artifact.artifact_id,
            content_sha256=filing_artifact.content_sha256,
            page_number=1,
            element_type=FundamentalFilingCitationElementType.SPEAKER_TURN,
            source_element_id="turn.1",
            exact_excerpt="expects constant-currency growth of 13%",
            bounds=rectangle(x0=100.0, y0=60.0, x1=390.0, y1=100.0),
            start_offset=11,
            end_offset=50,
        ),
    )
    return tuple(sorted(values, key=lambda value: value.citation_id))


def citation_by_type(
    element_type: FundamentalFilingCitationElementType,
) -> FundamentalFilingCitation:
    return next(
        value for value in citations() if value.element_type is element_type
    )


def policy(**updates) -> FundamentalFilingExtractionPolicy:
    values = {
        "policy_id": "filing.extraction.default",
        "policy_version": "1.0.0",
        "text_engine": "pymupdf",
        "table_engine": "pdfplumber",
        "ocr_enabled": False,
    }
    values.update(updates)
    return FundamentalFilingExtractionPolicy(**values)


def coverage_for(
    filing_pages: tuple[FundamentalFilingPage, ...],
    turns: tuple[FundamentalFilingSpeakerTurn, ...],
) -> FundamentalFilingExtractionCoverage:
    extracted = sum(
        page.status is FundamentalFilingPageStatus.EXTRACTED
        for page in filing_pages
    )
    partial = sum(
        page.status is FundamentalFilingPageStatus.PARTIAL
        for page in filing_pages
    )
    unreadable = sum(
        page.status is FundamentalFilingPageStatus.UNREADABLE
        for page in filing_pages
    )
    status = FundamentalFilingCoverageStatus.COMPLETE
    if unreadable == len(filing_pages):
        status = FundamentalFilingCoverageStatus.FAILED
    elif partial or unreadable:
        status = FundamentalFilingCoverageStatus.PARTIAL
    return FundamentalFilingExtractionCoverage(
        status=status,
        expected_pages=artifact().page_count,
        represented_pages=len(filing_pages),
        extracted_pages=extracted,
        partial_pages=partial,
        unreadable_pages=unreadable,
        ocr_pages=sum(
            page.method
            in {
                FundamentalFilingExtractionMethod.OCR,
                FundamentalFilingExtractionMethod.HYBRID,
            }
            for page in filing_pages
        ),
        table_count=sum(len(page.tables) for page in filing_pages),
        speaker_turn_count=len(turns),
    )


def evidence_items():
    table_citation_id = citation_by_type(
        FundamentalFilingCitationElementType.TABLE_CELL
    ).citation_id
    text_citation_id = citation_by_type(
        FundamentalFilingCitationElementType.TEXT_BLOCK
    ).citation_id
    speaker_citation_id = citation_by_type(
        FundamentalFilingCitationElementType.SPEAKER_TURN
    ).citation_id
    return {
        "observed_facts": (
            FundamentalFilingObservedFact(
                evidence_id="evidence.fact.revenue_growth",
                label="Revenue growth",
                statement="Revenue growth was reported as 15.2%.",
                citation_ids=(table_citation_id,),
                value=exact_value("15.2", unit=percentage_unit()),
            ),
        ),
        "adjusted_metrics": (
            FundamentalFilingAdjustedMetric(
                evidence_id="evidence.metric.adjusted_margin",
                label="Adjusted margin",
                statement="Management presented an adjusted margin measure.",
                citation_ids=(table_citation_id,),
                metric_label="Adjusted EBITDA margin",
                reported_value=exact_value("18", unit=percentage_unit()),
                adjusted_value=exact_value("20", unit=percentage_unit()),
                adjustment_description="Excludes the disclosed acquisition cost.",
            ),
        ),
        "management_claims": (
            FundamentalFilingManagementClaim(
                evidence_id="evidence.claim.growth",
                label="Growth expectation",
                statement="Management expects constant-currency growth.",
                citation_ids=(speaker_citation_id,),
                attribution_label="Chief Executive Officer",
                speaker_turn_ids=("turn.1",),
            ),
        ),
        "guidance_commitments": (
            FundamentalFilingGuidanceCommitment(
                evidence_id="evidence.guidance.cc_growth",
                label="Constant-currency growth guidance",
                statement="Management guided to 13% growth.",
                citation_ids=(speaker_citation_id,),
                metric_label="Constant-currency revenue growth",
                value_kind=FundamentalFilingGuidanceValueKind.POINT,
                point_value=exact_value("13", unit=percentage_unit()),
                horizon_label="FY2027",
                horizon_end=date(2027, 3, 31),
                conditions=("Stable demand environment",),
                speaker_turn_ids=("turn.1",),
            ),
        ),
        "risk_disclosures": (
            FundamentalFilingRiskDisclosure(
                evidence_id="evidence.risk.execution",
                label="Execution risk",
                statement="The document states that execution risk remains.",
                citation_ids=(text_citation_id,),
                risk_category="Execution",
            ),
        ),
    }


def extraction(**updates) -> FundamentalFilingExtractedEvidence:
    filing_pages = updates.pop("pages", pages())
    turns = updates.pop("speaker_turns", (speaker_turn(),))
    values = {
        "artifact": artifact(),
        "policy": policy(),
        "extracted_at": COMMITTED_AT + timedelta(minutes=1),
        "pages": filing_pages,
        "speaker_turns": turns,
        "citations": citations(),
        **evidence_items(),
        "coverage": coverage_for(filing_pages, turns),
    }
    values.update(updates)
    return FundamentalFilingExtractedEvidence(**values)


class FundamentalFilingEvidenceContractTests(unittest.TestCase):
    def test_preserves_page_table_speaker_and_citation_lineage(self):
        result = extraction()

        self.assertEqual(len(result.pages), result.artifact.page_count)
        self.assertEqual(result.pages[0].printed_page_label, "1")
        self.assertEqual(
            result.pages[0].text_blocks[0].kind,
            FundamentalFilingTextBlockKind.HEADING,
        )
        table = result.pages[0].tables[0]
        self.assertEqual(table.default_unit.currency, "INR")
        self.assertEqual(
            table.cells[1].observed_value.unit.kind,
            FundamentalFilingUnitKind.PERCENTAGE,
        )
        self.assertEqual(
            result.speaker_turns[0].speaker_name,
            "Chief Executive Officer",
        )
        self.assertEqual(result.citations[0].artifact_id, result.artifact.artifact_id)
        self.assertEqual(
            result.coverage.status,
            FundamentalFilingCoverageStatus.COMPLETE,
        )
        self.assertEqual(len(result.extraction_fingerprint), 64)

    def test_fingerprint_is_reproducible_and_content_sensitive(self):
        first = extraction()
        repeated = extraction()
        changed_block = FundamentalFilingTextBlock.model_validate(
            {
                **first.pages[0].text_blocks[1].model_dump(),
                "text": "Management withdrew its prior guidance.",
            }
        )
        changed_page = FundamentalFilingPage.model_validate(
            {
                **first.pages[0].model_dump(),
                "text_blocks": (first.pages[0].text_blocks[0], changed_block),
            }
        )
        changed = extraction(pages=(changed_page, *first.pages[1:]))

        self.assertEqual(first.extraction_fingerprint, repeated.extraction_fingerprint)
        self.assertNotEqual(
            first.extraction_fingerprint,
            changed.extraction_fingerprint,
        )

    def test_requires_every_physical_page_in_exact_order(self):
        filing_pages = pages()
        with self.assertRaisesRegex(ValidationError, "physical page"):
            extraction(
                pages=(filing_pages[0], *filing_pages[2:]),
                coverage=coverage_for(
                    (filing_pages[0], *filing_pages[2:]),
                    (speaker_turn(),),
                ),
            )
        with self.assertRaisesRegex(ValidationError, "physical order"):
            extraction(pages=(filing_pages[1], filing_pages[0], *filing_pages[2:]))

    def test_unreadable_page_is_retained_with_warning_and_limitation(self):
        filing_pages = list(pages())
        filing_pages[1] = FundamentalFilingPage(
            page_number=2,
            printed_page_label="2",
            width_points=595.0,
            height_points=842.0,
            status=FundamentalFilingPageStatus.UNREADABLE,
            method=FundamentalFilingExtractionMethod.NONE,
        )
        filing_pages_tuple = tuple(filing_pages)
        warning = FundamentalFilingExtractionWarning(
            warning_id="warning.page.2.unreadable",
            code=FundamentalFilingWarningCode.UNREADABLE_PAGE,
            severity=FundamentalFilingWarningSeverity.ERROR,
            message="Page 2 could not be read deterministically.",
            page_number=2,
        )
        limitation = FundamentalFilingExtractionLimitation(
            limitation_id="limitation.page.2.unreadable",
            description="Page 2 content is unavailable for downstream analysis.",
            affected_pages=(2,),
            material=True,
        )

        result = extraction(
            pages=filing_pages_tuple,
            coverage=coverage_for(filing_pages_tuple, (speaker_turn(),)),
            warnings=(warning,),
            limitations=(limitation,),
        )

        self.assertEqual(result.pages[1].status, FundamentalFilingPageStatus.UNREADABLE)
        self.assertEqual(result.coverage.unreadable_pages, 1)
        self.assertEqual(
            result.coverage.status,
            FundamentalFilingCoverageStatus.PARTIAL,
        )

        with self.assertRaisesRegex(ValidationError, "require extraction warnings"):
            extraction(
                pages=filing_pages_tuple,
                coverage=coverage_for(filing_pages_tuple, (speaker_turn(),)),
                limitations=(limitation,),
            )

    def test_citation_cannot_escape_artifact_page_or_source_element(self):
        original = citations()[0]
        wrong_artifact = FundamentalFilingCitation.model_validate(
            {
                **original.model_dump(exclude={"citation_id"}),
                "artifact_id": "filing_artifact:" + "f" * 64,
            }
        )
        with self.assertRaisesRegex(ValidationError, "immutable PDF"):
            extraction(citations=(wrong_artifact,))

        unknown_element = FundamentalFilingCitation.model_validate(
            {
                **original.model_dump(exclude={"citation_id"}),
                "source_element_id": "page.1.block.999",
            }
        )
        with self.assertRaisesRegex(ValidationError, "unknown"):
            extraction(citations=(unknown_element,))

    def test_speaker_attribution_is_explicit_or_unresolved_without_guessing(self):
        unresolved = FundamentalFilingSpeakerTurn(
            element_id="turn.unknown",
            turn_index=0,
            speaker_name=None,
            speaker_role=FundamentalFilingSpeakerRole.UNKNOWN,
            attribution=FundamentalFilingSpeakerAttribution.UNRESOLVED,
            text="We cannot identify this speaker.",
            source_block_ids=("page.1.block.2",),
        )
        self.assertIsNone(unresolved.speaker_name)

        with self.assertRaisesRegex(ValidationError, "inferred identity"):
            FundamentalFilingSpeakerTurn(
                element_id="turn.guessed",
                turn_index=0,
                speaker_name="Probably the CFO",
                speaker_role=FundamentalFilingSpeakerRole.MANAGEMENT,
                attribution=FundamentalFilingSpeakerAttribution.UNRESOLVED,
                text="This must not be attributed by inference.",
                source_block_ids=("page.1.block.2",),
            )

    def test_participant_roster_preserves_management_analyst_and_moderator(self):
        management = participant()
        analyst = participant(
            participant_id="participant.analyst.one",
            name="John Smith",
            role=FundamentalFilingSpeakerRole.ANALYST,
            title="Research Analyst",
            organization="Example Securities",
            issuer_affiliation=FundamentalFilingParticipantAffiliation.EXTERNAL,
        )
        moderator = participant(
            participant_id="participant.moderator.one",
            name="Conference Moderator",
            role=FundamentalFilingSpeakerRole.MODERATOR,
            title="Moderator",
            organization="Call Services",
            issuer_affiliation=FundamentalFilingParticipantAffiliation.EXTERNAL,
        )

        result = extraction(participants=(analyst, management, moderator))

        self.assertEqual(
            result.participants[0].role,
            FundamentalFilingSpeakerRole.ANALYST,
        )
        self.assertEqual(
            result.participants[1].issuer_affiliation,
            FundamentalFilingParticipantAffiliation.ISSUER,
        )
        self.assertEqual(
            result.participants[2].attribution_confidence,
            FundamentalFilingParticipantAttributionConfidence.VERIFIED,
        )

    def test_speaker_turn_can_link_to_a_cited_participant(self):
        roster_entry = participant()
        linked_turn = FundamentalFilingSpeakerTurn.model_validate(
            {
                **speaker_turn().model_dump(),
                "speaker_name": roster_entry.name,
                "participant_id": roster_entry.participant_id,
            }
        )

        result = extraction(
            participants=(roster_entry,),
            speaker_turns=(linked_turn,),
        )

        self.assertEqual(
            result.speaker_turns[0].participant_id,
            roster_entry.participant_id,
        )

    def test_participant_roster_rejects_duplicates_and_conflicting_roles(self):
        original = participant()
        duplicate = participant(
            participant_id="participant.management.duplicate",
            name="Jane-Doe",
        )
        with self.assertRaisesRegex(
            ValidationError,
            "duplicate or conflicting identities",
        ):
            extraction(participants=(original, duplicate))

        analyst_identity = participant(
            participant_id="participant.analyst.conflict",
            name="Jane Doe",
            role=FundamentalFilingSpeakerRole.ANALYST,
            title="Research Analyst",
            organization="Example Securities",
            issuer_affiliation=FundamentalFilingParticipantAffiliation.EXTERNAL,
        )
        with self.assertRaisesRegex(
            ValidationError,
            "duplicate or conflicting identities",
        ):
            extraction(participants=(analyst_identity, original))

    def test_participant_and_turn_attribution_fail_closed(self):
        with self.assertRaisesRegex(ValidationError, "must be issuer affiliated"):
            participant(
                issuer_affiliation=FundamentalFilingParticipantAffiliation.EXTERNAL
            )
        with self.assertRaisesRegex(ValidationError, "externally affiliated"):
            participant(
                role=FundamentalFilingSpeakerRole.ANALYST,
                issuer_affiliation=FundamentalFilingParticipantAffiliation.UNKNOWN,
            )
        with self.assertRaisesRegex(ValidationError, "must be verified"):
            participant(
                attribution_confidence=(
                    FundamentalFilingParticipantAttributionConfidence.HIGH
                )
            )

        roster_entry = participant()
        mismatched_turn = FundamentalFilingSpeakerTurn.model_validate(
            {
                **speaker_turn().model_dump(),
                "speaker_role": FundamentalFilingSpeakerRole.ANALYST,
                "participant_id": roster_entry.participant_id,
            }
        )
        with self.assertRaisesRegex(ValidationError, "conflicts"):
            extraction(
                participants=(roster_entry,),
                speaker_turns=(mismatched_turn,),
            )

        with self.assertRaisesRegex(ValidationError, "unknown participant"):
            extraction(
                speaker_turns=(
                    FundamentalFilingSpeakerTurn.model_validate(
                        {
                            **speaker_turn().model_dump(),
                            "participant_id": "participant.missing",
                        }
                    ),
                )
            )

        conflicting_identity = FundamentalFilingSpeakerTurn.model_validate(
            {
                **speaker_turn().model_dump(),
                "speaker_name": "Someone Else",
                "participant_id": roster_entry.participant_id,
            }
        )
        with self.assertRaisesRegex(ValidationError, "participant identity"):
            extraction(
                participants=(roster_entry,),
                speaker_turns=(conflicting_identity,),
            )

        unknown_citation = FundamentalFilingParticipant.model_validate(
            {
                **roster_entry.model_dump(),
                "citation_ids": ("filing_citation:" + "f" * 64,),
            }
        )
        with self.assertRaisesRegex(ValidationError, "unknown citation"):
            extraction(participants=(unknown_citation,))

    def test_table_contract_preserves_units_and_rejects_invalid_geometry(self):
        table = financial_table()
        self.assertEqual(
            table.cells[1].observed_value.normalized_value,
            Decimal("15.2"),
        )
        self.assertEqual(table.cells[1].observed_value.unit.source_label, "%")

        invalid_cell = FundamentalFilingTableCell.model_validate(
            {
                **table.cells[1].model_dump(),
                "bounds": rectangle(x0=20.0, y0=151.0, x1=550.0, y1=180.0),
            }
        )
        with self.assertRaisesRegex(ValidationError, "exceeds table bounds"):
            FundamentalFilingTable.model_validate(
                {
                    **table.model_dump(),
                    "cells": (table.cells[0], invalid_cell),
                }
            )

    def test_semantic_records_require_released_citations_and_known_turns(self):
        fact = FundamentalFilingObservedFact(
            evidence_id="evidence.fact.invalid",
            label="Invalid fact",
            statement="This fact has no released source.",
            citation_ids=("filing_citation:" + "f" * 64,),
        )
        with self.assertRaisesRegex(ValidationError, "unknown citation"):
            extraction(observed_facts=(fact,))

        claim = evidence_items()["management_claims"][0].model_copy(
            update={"speaker_turn_ids": ("turn.unknown",)}
        )
        with self.assertRaisesRegex(ValidationError, "unknown speaker turn"):
            extraction(management_claims=(claim,))

    def test_guidance_shapes_are_fail_closed(self):
        common = {
            "evidence_id": "evidence.guidance.range",
            "label": "Margin guidance",
            "statement": "Management supplied a margin range.",
            "citation_ids": (
                citation_by_type(
                    FundamentalFilingCitationElementType.SPEAKER_TURN
                ).citation_id,
            ),
            "metric_label": "EBITDA margin",
            "value_kind": FundamentalFilingGuidanceValueKind.RANGE,
            "horizon_label": "FY2027",
        }
        with self.assertRaisesRegex(ValidationError, "lower and upper"):
            FundamentalFilingGuidanceCommitment(
                **common,
                lower_bound=exact_value("18", unit=percentage_unit()),
            )
        with self.assertRaisesRegex(ValidationError, "cannot exceed"):
            FundamentalFilingGuidanceCommitment(
                **common,
                lower_bound=exact_value("22", unit=percentage_unit()),
                upper_bound=exact_value("20", unit=percentage_unit()),
            )

    def test_ocr_lineage_requires_explicit_versioned_policy(self):
        filing_pages = list(pages())
        filing_pages[1] = FundamentalFilingPage(
            page_number=2,
            printed_page_label="2",
            width_points=595.0,
            height_points=842.0,
            status=FundamentalFilingPageStatus.EXTRACTED,
            method=FundamentalFilingExtractionMethod.OCR,
            text_blocks=(
                FundamentalFilingTextBlock(
                    element_id="page.2.block.1",
                    page_number=2,
                    reading_order=0,
                    kind=FundamentalFilingTextBlockKind.PARAGRAPH,
                    text="OCR extracted text.",
                    bounds=rectangle(),
                    method=FundamentalFilingExtractionMethod.OCR,
                ),
            ),
        )
        filing_pages_tuple = tuple(filing_pages)
        with self.assertRaisesRegex(ValidationError, "require an OCR policy"):
            extraction(
                pages=filing_pages_tuple,
                coverage=coverage_for(filing_pages_tuple, (speaker_turn(),)),
            )

        result = extraction(
            pages=filing_pages_tuple,
            coverage=coverage_for(filing_pages_tuple, (speaker_turn(),)),
            policy=policy(ocr_enabled=True, ocr_engine="tesseract"),
        )
        self.assertEqual(result.coverage.ocr_pages, 1)
        self.assertTrue(result.policy.ocr_enabled)

    def test_models_are_strict_immutable_and_do_not_accept_pdf_bytes(self):
        result = extraction()
        with self.assertRaises(ValidationError):
            FundamentalFilingExtractedEvidence.model_validate(
                {
                    **result.model_dump(exclude={"extraction_fingerprint"}),
                    "pdf_bytes": b"%PDF-1.7",
                }
            )
        with self.assertRaises(ValidationError):
            result.pages[0].page_number = 9


if __name__ == "__main__":
    unittest.main()
