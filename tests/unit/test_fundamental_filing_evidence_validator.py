import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.fundamentals.filing_evidence_validator import (
    DeterministicFundamentalFilingEvidenceValidator,
    FundamentalFilingValidationCode,
    FundamentalFilingValidationStatus,
)
from app.fundamentals.filing_extractor import (
    DeterministicFundamentalFilingExtractor,
)
from app.models.fundamental_filing_evidence import (
    FundamentalFilingCitationElementType,
    FundamentalFilingGuidanceCommitment,
    FundamentalFilingGuidanceValueKind,
    FundamentalFilingObservedFact,
    FundamentalFilingObservedValue,
    FundamentalFilingUnit,
    FundamentalFilingUnitKind,
    FundamentalFilingValueStatus,
)
from app.models.fundamental_filings import (
    FundamentalFilingPeriod,
    FundamentalFilingPeriodState,
    FundamentalFilingPeriodType,
    FundamentalFilingSourceClass,
)
from tests.unit.test_fundamental_filing_extractor import (
    _manifest_for,
    _native_filing_pdf,
)
from tests.unit.test_fundamental_filings import COMMITTED_AT


def _percentage_value(value: str) -> FundamentalFilingObservedValue:
    return FundamentalFilingObservedValue(
        raw_text=f"{value}%",
        status=FundamentalFilingValueStatus.EXACT,
        normalized_value=Decimal(value),
        unit=FundamentalFilingUnit(
            kind=FundamentalFilingUnitKind.PERCENTAGE,
            source_label="%",
            multiplier=Decimal("0.01"),
        ),
    )


def _with_identity_content(evidence, text):
    page = evidence.pages[1]
    source_block = page.text_blocks[0]
    updated_block = source_block.model_copy(update={"text": text})
    updated_page = page.model_copy(
        update={
            "text_blocks": (
                updated_block,
                *page.text_blocks[1:],
            )
        }
    )
    updated_citations = []
    for citation in evidence.citations:
        if (
            citation.element_type
            is FundamentalFilingCitationElementType.TEXT_BLOCK
            and citation.source_element_id == source_block.element_id
        ):
            citation = citation.model_copy(
                update={
                    "exact_excerpt": text,
                    "start_offset": 0,
                    "end_offset": len(text),
                }
            )
        updated_citations.append(citation)
    return evidence.model_copy(
        update={
            "pages": (evidence.pages[0], updated_page, *evidence.pages[2:]),
            "citations": tuple(
                sorted(updated_citations, key=lambda item: item.citation_id)
            ),
        }
    )


def _with_period(evidence, period):
    reference = evidence.artifact.reference.model_copy(
        update={"period": period}
    )
    artifact = evidence.artifact.model_copy(update={"reference": reference})
    return evidence.model_copy(update={"artifact": artifact})


class DeterministicFundamentalFilingEvidenceValidatorTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name).resolve()
        root.chmod(0o700)
        pdf = _native_filing_pdf()
        self.manifest = _manifest_for(root, pdf, page_count=2)
        self.evidence = DeterministicFundamentalFilingExtractor(
            artifact_root=root
        ).extract(
            self.manifest,
            extracted_at=COMMITTED_AT + timedelta(minutes=2),
        )
        self.evidence = _with_identity_content(
            self.evidence,
            "Coforge Limited | Symbol: COFORGE | Q1 FY2027 | "
            "quarter ended June 30, 2026",
        )
        self.validator = DeterministicFundamentalFilingEvidenceValidator()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def validate(self, evidence=None, **overrides):
        values = {
            "expected_issuer": self.manifest.reference.issuer,
            "expected_period": self.manifest.reference.period,
            "expected_source_class": self.manifest.reference.source_class,
        }
        values.update(overrides)
        return self.validator.validate(evidence or self.evidence, **values)

    @staticmethod
    def finding(result, code):
        return next(item for item in result.findings if item.code is code)

    def citation(self, element_type, *, page_number=None):
        return next(
            citation
            for citation in self.evidence.citations
            if citation.element_type is element_type
            and (
                page_number is None
                or citation.page_number == page_number
            )
        )

    def test_validates_native_extraction_and_is_reproducible_immutable(self):
        first = self.validate()
        repeated = self.validate()

        self.assertTrue(first.releaseable)
        self.assertEqual(first.policy_version, "1.3.0")
        self.assertEqual(len(first.findings), 7)
        self.assertTrue(
            all(
                item.status is FundamentalFilingValidationStatus.PASS
                for item in first.findings
            )
        )
        self.assertEqual(
            first.validation_fingerprint,
            repeated.validation_fingerprint,
        )
        with self.assertRaises(FrozenInstanceError):
            first.releaseable = False

    def test_rejects_operation_issuer_mismatch_without_guessing(self):
        expected = self.manifest.reference.issuer.model_copy(
            update={"symbol": "ANOTHER-EQ"}
        )

        result = self.validate(expected_issuer=expected)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.ISSUER_IDENTITY,
        )
        self.assertFalse(result.releaseable)
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )

    def test_rejects_manifest_issuer_when_pdf_content_does_not_match(self):
        altered = _with_identity_content(
            self.evidence,
            "Another Company Limited | Symbol: ANOTHER | Q1 FY2027",
        )

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.ISSUER_IDENTITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )
        self.assertIn(
            self.manifest.reference.filing_reference_id,
            finding.affected_ids,
        )

    def test_rejects_conflicting_symbol_even_when_legal_name_matches(self):
        altered = _with_identity_content(
            self.evidence,
            "Coforge Ltd. | Symbol: ANOTHER | Q1 FY2027",
        )

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.ISSUER_IDENTITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )

    def test_rejects_manifest_period_when_pdf_content_does_not_match(self):
        altered = _with_identity_content(
            self.evidence,
            "Coforge Limited | Symbol: COFORGE | Q2 FY2027",
        )

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.PERIOD_IDENTITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )
        self.assertIn(
            self.manifest.reference.filing_reference_id,
            finding.affected_ids,
        )

    def test_accepts_explicit_matching_quarter_end_without_fy_label(self):
        altered = _with_identity_content(
            self.evidence,
            "Coforge Limited | Symbol: COFORGE | "
            "quarter ended June 30, 2026",
        )

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.PERIOD_IDENTITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.PASS,
        )

    def test_accepts_unclassified_provider_month_with_reporting_period_anchor(self):
        period = FundamentalFilingPeriod(
            state=FundamentalFilingPeriodState.UNCLASSIFIED,
            period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            source_label="May 2025",
        )
        altered = _with_period(
            _with_identity_content(
                self.evidence,
                "Coforge Limited | Audited financial results for the quarter "
                "and year ended March 31, 2025",
            ),
            period,
        )

        result = self.validate(altered, expected_period=period)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.PERIOD_IDENTITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.PASS,
        )

    def test_accepts_format_variants_for_unclassified_reporting_period(self):
        period = FundamentalFilingPeriod(
            state=FundamentalFilingPeriodState.UNCLASSIFIED,
            period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            source_label="May 2025",
        )
        variants = (
            "year ended 31 March 2025",
            "financial year ended 31st March, 2025",
            "twelve months ended 31/03/2025",
            "reporting period ended 2025-03-31",
        )
        for value in variants:
            with self.subTest(value=value):
                altered = _with_period(
                    _with_identity_content(
                        self.evidence,
                        f"Coforge Limited | Results for the {value}",
                    ),
                    period,
                )

                result = self.validate(altered, expected_period=period)

                self.assertEqual(
                    self.finding(
                        result,
                        FundamentalFilingValidationCode.PERIOD_IDENTITY,
                    ).status,
                    FundamentalFilingValidationStatus.PASS,
                )

    def test_accepts_indian_fiscal_quarter_shorthand_for_provider_month(self):
        period = FundamentalFilingPeriod(
            state=FundamentalFilingPeriodState.UNCLASSIFIED,
            period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            source_label="Jun 2023",
        )
        variants = (
            "Q1 FY '24 Earnings Conference Call",
            "Q1 FY’24 earnings call",
            "FY 2024 quarter 1 investor call",
        )
        for value in variants:
            with self.subTest(value=value):
                altered = _with_period(
                    _with_identity_content(
                        self.evidence,
                        f"Coforge Limited | {value}",
                    ),
                    period,
                )

                result = self.validate(altered, expected_period=period)

                self.assertEqual(
                    self.finding(
                        result,
                        FundamentalFilingValidationCode.PERIOD_IDENTITY,
                    ).status,
                    FundamentalFilingValidationStatus.PASS,
                )

    def test_rejects_conflicting_fiscal_quarter_shorthand(self):
        period = FundamentalFilingPeriod(
            state=FundamentalFilingPeriodState.UNCLASSIFIED,
            period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            source_label="Jun 2023",
        )
        for value in ("Q2 FY24 earnings call", "Q1 FY23 earnings call"):
            with self.subTest(value=value):
                altered = _with_period(
                    _with_identity_content(
                        self.evidence,
                        f"Coforge Limited | {value}",
                    ),
                    period,
                )

                result = self.validate(altered, expected_period=period)

                self.assertEqual(
                    self.finding(
                        result,
                        FundamentalFilingValidationCode.PERIOD_IDENTITY,
                    ).status,
                    FundamentalFilingValidationStatus.FAIL,
                )

    def test_rejects_unclassified_publication_date_without_period_anchor(self):
        period = FundamentalFilingPeriod(
            state=FundamentalFilingPeriodState.UNCLASSIFIED,
            period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            source_label="May 2025",
        )
        altered = _with_period(
            _with_identity_content(
                self.evidence,
                "Coforge Limited | Earnings release published May 5, 2025",
            ),
            period,
        )

        result = self.validate(altered, expected_period=period)

        self.assertEqual(
            self.finding(
                result,
                FundamentalFilingValidationCode.PERIOD_IDENTITY,
            ).status,
            FundamentalFilingValidationStatus.FAIL,
        )

    def test_rejects_unclassified_stale_or_future_reporting_period(self):
        period = FundamentalFilingPeriod(
            state=FundamentalFilingPeriodState.UNCLASSIFIED,
            period_type=FundamentalFilingPeriodType.UNCLASSIFIED,
            source_label="May 2025",
        )
        for value in (
            "year ended March 31, 2023",
            "quarter ended June 30, 2025",
        ):
            with self.subTest(value=value):
                altered = _with_period(
                    _with_identity_content(
                        self.evidence,
                        f"Coforge Limited | Results for the {value}",
                    ),
                    period,
                )

                result = self.validate(altered, expected_period=period)

                self.assertEqual(
                    self.finding(
                        result,
                        FundamentalFilingValidationCode.PERIOD_IDENTITY,
                    ).status,
                    FundamentalFilingValidationStatus.FAIL,
                )

    def test_rejects_period_mismatch_and_past_guidance_horizon(self):
        expected = self.manifest.reference.period.model_copy(
            update={"fiscal_quarter": 2}
        )
        speaker_citation = self.citation(
            FundamentalFilingCitationElementType.SPEAKER_TURN
        )
        guidance = FundamentalFilingGuidanceCommitment(
            evidence_id="evidence.guidance.invalid_horizon",
            label="Growth guidance",
            statement="Management expects revenue growth.",
            citation_ids=(speaker_citation.citation_id,),
            metric_label="Revenue growth",
            value_kind=FundamentalFilingGuidanceValueKind.QUALITATIVE,
            horizon_label="Earlier period",
            horizon_end=date(2026, 3, 31),
            speaker_turn_ids=(self.evidence.speaker_turns[0].element_id,),
        )
        altered = self.evidence.model_copy(
            update={"guidance_commitments": (guidance,)}
        )

        result = self.validate(altered, expected_period=expected)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.PERIOD_IDENTITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )
        self.assertIn(guidance.evidence_id, finding.affected_ids)

    def test_derives_exact_multi_page_semantic_evidence_span(self):
        page_one = self.citation(
            FundamentalFilingCitationElementType.TEXT_BLOCK,
            page_number=1,
        )
        page_two = self.citation(
            FundamentalFilingCitationElementType.TEXT_BLOCK,
            page_number=2,
        )
        fact = FundamentalFilingObservedFact(
            evidence_id="evidence.fact.cross_page",
            label="Cross-page source",
            statement="The evidence uses two exact source pages.",
            citation_ids=tuple(
                sorted((page_one.citation_id, page_two.citation_id))
            ),
        )
        altered = self.evidence.model_copy(
            update={"observed_facts": (fact,)}
        )

        result = self.validate(altered)

        self.assertTrue(result.releaseable)
        self.assertEqual(len(result.evidence_page_spans), 1)
        span = result.evidence_page_spans[0]
        self.assertEqual(span.first_page, 1)
        self.assertEqual(span.last_page, 2)
        self.assertEqual(span.cited_pages, (1, 2))

    def test_rejects_non_monotonic_speaker_page_lineage(self):
        first_page_block = self.evidence.pages[0].text_blocks[0]
        second_page_block = self.evidence.pages[1].text_blocks[0]
        turn = self.evidence.speaker_turns[0].model_copy(
            update={
                "source_block_ids": (
                    second_page_block.element_id,
                    first_page_block.element_id,
                )
            }
        )
        altered = self.evidence.model_copy(update={"speaker_turns": (turn,)})

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.PAGE_SPANS,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )
        self.assertIn(turn.element_id, finding.affected_ids)

    def test_rejects_value_not_present_in_its_cited_source(self):
        table_citation = self.citation(
            FundamentalFilingCitationElementType.TABLE_CELL
        )
        fact = FundamentalFilingObservedFact(
            evidence_id="evidence.fact.unsupported_value",
            label="Unsupported value",
            statement="The value is not in the source cell.",
            citation_ids=(table_citation.citation_id,),
            value=_percentage_value("99"),
        )
        altered = self.evidence.model_copy(
            update={"observed_facts": (fact,)}
        )

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.UNIT_INTEGRITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )
        self.assertIn(fact.evidence_id, finding.affected_ids)

    def test_rejects_invalid_percentage_multiplier(self):
        page = self.evidence.pages[0]
        table = page.tables[0]
        value_cell = next(
            cell
            for cell in table.cells
            if cell.observed_value is not None
            and cell.observed_value.unit is not None
            and cell.observed_value.unit.kind
            is FundamentalFilingUnitKind.PERCENTAGE
        )
        invalid_unit = value_cell.observed_value.unit.model_copy(
            update={"multiplier": Decimal("1")}
        )
        invalid_value = value_cell.observed_value.model_copy(
            update={"unit": invalid_unit}
        )
        invalid_cell = value_cell.model_copy(
            update={"observed_value": invalid_value}
        )
        cells = tuple(
            invalid_cell if cell.element_id == invalid_cell.element_id else cell
            for cell in table.cells
        )
        invalid_table = table.model_copy(update={"cells": cells})
        invalid_page = page.model_copy(update={"tables": (invalid_table,)})
        altered = self.evidence.model_copy(
            update={"pages": (invalid_page, *self.evidence.pages[1:])}
        )

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.UNIT_INTEGRITY,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )

    def test_rejects_duplicate_semantic_content_under_different_ids(self):
        citation = self.citation(
            FundamentalFilingCitationElementType.TABLE_CELL
        )
        common = {
            "label": "Revenue growth",
            "statement": "Revenue growth was 15.2%.",
            "citation_ids": (citation.citation_id,),
            "value": _percentage_value("15.2"),
        }
        first = FundamentalFilingObservedFact(
            evidence_id="evidence.fact.revenue_growth.1",
            **common,
        )
        duplicate = FundamentalFilingObservedFact(
            evidence_id="evidence.fact.revenue_growth.2",
            **common,
        )
        altered = self.evidence.model_copy(
            update={"observed_facts": (first, duplicate)}
        )

        result = self.validate(altered)

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.DUPLICATE_CONTENT,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )
        self.assertEqual(
            finding.affected_ids,
            (first.evidence_id, duplicate.evidence_id),
        )

    def test_rejects_expected_source_class_mismatch(self):
        result = self.validate(
            expected_source_class=(
                FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
            )
        )

        finding = self.finding(
            result,
            FundamentalFilingValidationCode.SOURCE_CLASSIFICATION,
        )
        self.assertEqual(
            finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )

    def test_rejects_missing_or_corrupted_complete_source_citation(self):
        removed = self.evidence.citations[0]
        missing = self.evidence.model_copy(
            update={
                "citations": tuple(
                    citation
                    for citation in self.evidence.citations
                    if citation.citation_id != removed.citation_id
                )
            }
        )
        missing_result = self.validate(missing)
        missing_finding = self.finding(
            missing_result,
            FundamentalFilingValidationCode.CITATION_COMPLETENESS,
        )
        self.assertEqual(
            missing_finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )

        original = self.citation(
            FundamentalFilingCitationElementType.TEXT_BLOCK
        )
        corrupted = original.model_copy(update={"exact_excerpt": "Invented"})
        altered_citations = tuple(
            corrupted if item.citation_id == original.citation_id else item
            for item in self.evidence.citations
        )
        corrupted_evidence = self.evidence.model_copy(
            update={"citations": altered_citations}
        )
        corrupted_result = self.validate(corrupted_evidence)
        corrupted_finding = self.finding(
            corrupted_result,
            FundamentalFilingValidationCode.CITATION_COMPLETENESS,
        )
        self.assertEqual(
            corrupted_finding.status,
            FundamentalFilingValidationStatus.FAIL,
        )


if __name__ == "__main__":
    unittest.main()
