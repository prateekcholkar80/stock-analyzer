"""Deterministic release validation for extracted filing evidence.

The validator does not extract, enrich, summarize, or score filing content. It
checks that an immutable extraction remains bound to the expected issuer,
period, source class, physical pages, source-observed units, and exact citation
lineage before later semantic or LLM processing may consume it.
"""

from calendar import monthrange
from datetime import date
from decimal import Decimal
import re
from typing import Iterable

from app.models.fundamental_filing_evidence import (
    FundamentalFilingCitation,
    FundamentalFilingCitationElementType,
    FundamentalFilingCitedEvidence,
    FundamentalFilingExtractedEvidence,
    FundamentalFilingEvidencePageSpan,
    FundamentalFilingEvidenceValidationResult,
    FundamentalFilingGuidanceCommitment,
    FundamentalFilingGuidanceValueKind,
    FundamentalFilingObservedValue,
    FundamentalFilingPageStatus,
    FundamentalFilingRectangle,
    FundamentalFilingSpeakerRole,
    FundamentalFilingSpeakerTurn,
    FundamentalFilingTableCell,
    FundamentalFilingTextBlock,
    FundamentalFilingUnit,
    FundamentalFilingUnitKind,
    FundamentalFilingValidationCode,
    FundamentalFilingValidationFinding,
    FundamentalFilingValidationStatus,
    FundamentalFilingValueStatus,
)
from app.models.fundamental_filings import (
    FundamentalFilingCategory,
    FundamentalFilingPeriod,
    FundamentalFilingSourceClass,
)
from app.models.fundamentals import FundamentalIssuerIdentity


_POLICY_ID = "filing.evidence.release_validation"
_POLICY_VERSION = "1.4.0"
_WHITESPACE = re.compile(r"\s+")
_NON_IDENTIFIER = re.compile(r"[^a-z0-9]+")
_MAX_UNCLASSIFIED_PUBLICATION_LAG_DAYS = 184
_MONTH_NUMBER = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_NAME = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?"
)
_SOURCE_MONTH_YEAR = re.compile(
    rf"\b(?P<month>{_MONTH_NAME})\s*[,\-/]?\s*(?P<year>20\d{{2}})\b",
    re.I,
)
_REPORTING_PERIOD_PREFIX = (
    r"(?:financial\s+year|fiscal\s+year|year|quarter|"
    r"three\s+months?|six\s+months?|nine\s+months?|twelve\s+months?|"
    r"(?:reporting\s+)?period)\s+ended"
)
_REPORTING_MONTH_FIRST = re.compile(
    rf"\b{_REPORTING_PERIOD_PREFIX}\b.{{0,80}}?"
    rf"\b(?P<month>{_MONTH_NAME})\s+(?P<day>\d{{1,2}})"
    rf"(?:st|nd|rd|th)?\s*,?\s*(?P<year>20\d{{2}})\b",
    re.I,
)
_REPORTING_DAY_FIRST = re.compile(
    rf"\b{_REPORTING_PERIOD_PREFIX}\b.{{0,80}}?"
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+"
    rf"(?P<month>{_MONTH_NAME})\s*,?\s*(?P<year>20\d{{2}})\b",
    re.I,
)
_REPORTING_NUMERIC_DAY_FIRST = re.compile(
    rf"\b{_REPORTING_PERIOD_PREFIX}\b.{{0,80}}?"
    r"(?<!\d)(?P<day>\d{1,2})[./-](?P<month>\d{1,2})"
    r"[./-](?P<year>20\d{2})(?!\d)",
    re.I,
)
_REPORTING_ISO_DATE = re.compile(
    rf"\b{_REPORTING_PERIOD_PREFIX}\b.{{0,80}}?"
    r"(?<!\d)(?P<year>20\d{2})-(?P<month>\d{1,2})-"
    r"(?P<day>\d{1,2})(?!\d)",
    re.I,
)
_FISCAL_QUARTER_THEN_YEAR = re.compile(
    r"\b(?:q\s*(?P<quarter>[1-4])|quarter\s*(?P<quarter_word>[1-4]))\b"
    r".{0,16}?\b(?:fy|fiscal\s+year)\s*['’]?\s*"
    r"(?P<year>20\d{2}|\d{2})\b",
    re.I,
)
_FISCAL_YEAR_THEN_QUARTER = re.compile(
    r"\b(?:fy|fiscal\s+year)\s*['’]?\s*"
    r"(?P<year>20\d{2}|\d{2})\b.{0,16}?"
    r"\b(?:q\s*(?P<quarter>[1-4])|quarter\s*(?P<quarter_word>[1-4]))\b",
    re.I,
)
_SYMBOL_LABEL = re.compile(
    r"\b(?:nse\s+)?symbol\s*[:\-]?\s*(?P<symbol>[A-Z][A-Z0-9&.\-]{1,30})\b",
    re.I,
)
_SOURCE_CLASS_BY_CATEGORY = {
    FundamentalFilingCategory.EARNINGS_RELEASE: (
        FundamentalFilingSourceClass.COMPANY_AUTHORED_EARNINGS_RELEASE
    ),
    FundamentalFilingCategory.CONFERENCE_CALL: (
        FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
    ),
}


class DeterministicFundamentalFilingEvidenceValidator:
    """Validate extracted evidence without inference or provider access."""

    policy_id = _POLICY_ID
    policy_version = _POLICY_VERSION

    def validate(
        self,
        evidence: FundamentalFilingExtractedEvidence,
        *,
        expected_issuer: FundamentalIssuerIdentity,
        expected_period: FundamentalFilingPeriod,
        expected_source_class: FundamentalFilingSourceClass,
    ) -> FundamentalFilingEvidenceValidationResult:
        findings = (
            self._validate_issuer(evidence, expected_issuer),
            self._validate_period(evidence, expected_period),
            self._validate_page_spans(evidence),
            self._validate_units(evidence),
            self._validate_duplicates(evidence),
            self._validate_source_class(evidence, expected_source_class),
            self._validate_citations(evidence),
        )
        ordered_findings = tuple(sorted(findings, key=lambda item: item.code))
        spans = self._derive_evidence_page_spans(evidence)
        return FundamentalFilingEvidenceValidationResult(
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            extraction_fingerprint=evidence.extraction_fingerprint,
            releaseable=all(
                finding.status is FundamentalFilingValidationStatus.PASS
                for finding in ordered_findings
            ),
            findings=ordered_findings,
            evidence_page_spans=spans,
        )

    @staticmethod
    def _validate_issuer(
        evidence: FundamentalFilingExtractedEvidence,
        expected: FundamentalIssuerIdentity,
    ) -> FundamentalFilingValidationFinding:
        actual = evidence.artifact.reference.issuer
        content = _identity_content(evidence)
        if actual != expected or not _content_matches_issuer(content, expected):
            return _failed(
                FundamentalFilingValidationCode.ISSUER_IDENTITY,
                "Filing metadata or extracted PDF content does not independently "
                "match the verified operation issuer.",
                (evidence.artifact.reference.filing_reference_id,),
            )
        return _passed(
            FundamentalFilingValidationCode.ISSUER_IDENTITY,
            "Filing metadata and extracted PDF content independently match "
            "the verified operation issuer.",
        )

    @staticmethod
    def _validate_period(
        evidence: FundamentalFilingExtractedEvidence,
        expected: FundamentalFilingPeriod,
    ) -> FundamentalFilingValidationFinding:
        actual = evidence.artifact.reference.period
        affected: list[str] = []
        content = _identity_content(evidence, all_pages=True)
        if actual != expected or not _content_matches_period(content, expected):
            affected.append(evidence.artifact.reference.filing_reference_id)
        if actual.period_end is not None:
            for guidance in evidence.guidance_commitments:
                if (
                    guidance.horizon_end is not None
                    and guidance.horizon_end < actual.period_end
                ):
                    affected.append(guidance.evidence_id)
        if affected:
            return _failed(
                FundamentalFilingValidationCode.PERIOD_IDENTITY,
                "Filing metadata or extracted PDF content does not independently "
                "match the expected period, or guidance predates the source period.",
                affected,
            )
        return _passed(
            FundamentalFilingValidationCode.PERIOD_IDENTITY,
            "Filing metadata, extracted PDF period evidence, and explicit "
            "guidance horizons are consistent.",
        )

    @staticmethod
    def _validate_page_spans(
        evidence: FundamentalFilingExtractedEvidence,
    ) -> FundamentalFilingValidationFinding:
        block_pages = {
            block.element_id: page.page_number
            for page in evidence.pages
            for block in page.text_blocks
        }
        affected: list[str] = []
        for turn in evidence.speaker_turns:
            source_pages = tuple(
                block_pages[block_id]
                for block_id in turn.source_block_ids
                if block_id in block_pages
            )
            if not source_pages:
                affected.append(turn.element_id)
                continue
            if source_pages != tuple(sorted(source_pages)):
                affected.append(turn.element_id)
                continue
            unique_pages = tuple(dict.fromkeys(source_pages))
            if unique_pages != tuple(
                range(unique_pages[0], unique_pages[-1] + 1)
            ):
                affected.append(turn.element_id)

        affected.extend(
            limitation.limitation_id for limitation in evidence.limitations
            if limitation.material
        )
        citations = {item.citation_id: item for item in evidence.citations}
        unreadable_pages = {
            page.page_number
            for page in evidence.pages
            if page.status is FundamentalFilingPageStatus.UNREADABLE
        }
        for item in _semantic_evidence(evidence):
            pages = {
                citations[citation_id].page_number
                for citation_id in item.citation_ids
                if citation_id in citations
            }
            if not pages or pages.intersection(unreadable_pages):
                affected.append(item.evidence_id)

        if affected:
            return _failed(
                FundamentalFilingValidationCode.PAGE_SPANS,
                "One or more evidence spans are absent, cross unreadable pages, "
                "contain a non-contiguous speaker turn, or have material extraction limitations.",
                affected,
            )
        return _passed(
            FundamentalFilingValidationCode.PAGE_SPANS,
            "All evidence spans resolve to readable physical pages.",
        )

    @staticmethod
    def _validate_units(
        evidence: FundamentalFilingExtractedEvidence,
    ) -> FundamentalFilingValidationFinding:
        affected: list[str] = []
        citations = {item.citation_id: item for item in evidence.citations}
        source_values = {
            cell.element_id: cell.observed_value
            for page in evidence.pages
            for table in page.tables
            for cell in table.cells
            if cell.observed_value is not None
        }

        for page in evidence.pages:
            for table in page.tables:
                if table.default_unit is not None and not _valid_unit(
                    table.default_unit
                ):
                    affected.append(table.table_id)
                for cell in table.cells:
                    value = cell.observed_value
                    if value is None:
                        continue
                    if value.raw_text != cell.text or not _valid_value(value):
                        affected.append(cell.element_id)
                        continue
                    if (
                        value.status is FundamentalFilingValueStatus.EXACT
                        and value.unit is None
                        and table.default_unit is not None
                    ):
                        affected.append(cell.element_id)

        for item in evidence.observed_facts:
            if item.value is not None and (
                not _valid_value(item.value)
                or not _value_has_cited_source(
                    item.value,
                    item.citation_ids,
                    citations,
                    source_values,
                )
            ):
                affected.append(item.evidence_id)

        for item in evidence.adjusted_metrics:
            if (
                not _valid_value(item.reported_value)
                or not _valid_value(item.adjusted_value)
                or not _compatible_units(
                    item.reported_value.unit,
                    item.adjusted_value.unit,
                )
                or not _value_has_cited_source(
                    item.reported_value,
                    item.citation_ids,
                    citations,
                    source_values,
                )
            ):
                affected.append(item.evidence_id)

        for item in evidence.guidance_commitments:
            if not _guidance_units_are_valid(item):
                affected.append(item.evidence_id)
                continue
            for value in _guidance_values(item):
                if not _value_has_cited_source(
                    value,
                    item.citation_ids,
                    citations,
                    source_values,
                ):
                    affected.append(item.evidence_id)
                    break

        if affected:
            return _failed(
                FundamentalFilingValidationCode.UNIT_INTEGRITY,
                "One or more values lack exact source support or carry "
                "incompatible unit semantics.",
                affected,
            )
        return _passed(
            FundamentalFilingValidationCode.UNIT_INTEGRITY,
            "Source-observed and derived value units are internally consistent.",
        )

    @staticmethod
    def _validate_duplicates(
        evidence: FundamentalFilingExtractedEvidence,
    ) -> FundamentalFilingValidationFinding:
        seen: dict[tuple[object, ...], str] = {}
        affected: list[str] = []
        for item in _semantic_evidence(evidence):
            key = (
                item.evidence_kind.value,
                _canonical_text(item.label),
                _canonical_text(item.statement),
            )
            original = seen.setdefault(key, item.evidence_id)
            if original != item.evidence_id:
                affected.extend((original, item.evidence_id))
        if affected:
            return _failed(
                FundamentalFilingValidationCode.DUPLICATE_CONTENT,
                "Duplicate semantic evidence was released under different IDs.",
                affected,
            )
        return _passed(
            FundamentalFilingValidationCode.DUPLICATE_CONTENT,
            "No duplicate semantic evidence content was detected.",
        )

    @staticmethod
    def _validate_source_class(
        evidence: FundamentalFilingExtractedEvidence,
        expected: FundamentalFilingSourceClass,
    ) -> FundamentalFilingValidationFinding:
        reference = evidence.artifact.reference
        affected: list[str] = []
        if (
            reference.source_class is not expected
            or reference.source_class
            is not _SOURCE_CLASS_BY_CATEGORY[reference.category]
        ):
            affected.append(reference.filing_reference_id)

        turns = {turn.element_id: turn for turn in evidence.speaker_turns}
        citations = {item.citation_id: item for item in evidence.citations}
        for item in (
            *evidence.management_claims,
            *evidence.guidance_commitments,
        ):
            if item.speaker_turn_ids and any(
                turns[turn_id].speaker_role
                is not FundamentalFilingSpeakerRole.MANAGEMENT
                for turn_id in item.speaker_turn_ids
                if turn_id in turns
            ):
                affected.append(item.evidence_id)
            if (
                reference.source_class
                is FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
                and not _has_management_turn_citation(item, citations, turns)
            ):
                affected.append(item.evidence_id)

        for item in evidence.risk_disclosures:
            if (
                reference.source_class
                is FundamentalFilingSourceClass.EARNINGS_CALL_TRANSCRIPT
                and item.issuer_disclosed
                and not _has_management_turn_citation(item, citations, turns)
            ):
                affected.append(item.evidence_id)

        if affected:
            return _failed(
                FundamentalFilingValidationCode.SOURCE_CLASSIFICATION,
                "Evidence source class is mismatched or an issuer-attributed "
                "transcript statement lacks a management-speaker citation.",
                affected,
            )
        return _passed(
            FundamentalFilingValidationCode.SOURCE_CLASSIFICATION,
            "Source class matches the filing category and attribution boundary.",
        )

    @staticmethod
    def _validate_citations(
        evidence: FundamentalFilingExtractedEvidence,
    ) -> FundamentalFilingValidationFinding:
        affected: list[str] = []
        blocks = {
            block.element_id: block
            for page in evidence.pages
            for block in page.text_blocks
        }
        cells = {
            cell.element_id: cell
            for page in evidence.pages
            for table in page.tables
            for cell in table.cells
        }
        turns = {turn.element_id: turn for turn in evidence.speaker_turns}
        citations_by_source: dict[
            tuple[FundamentalFilingCitationElementType, str],
            list[FundamentalFilingCitation],
        ] = {}
        for citation in evidence.citations:
            key = (citation.element_type, citation.source_element_id)
            citations_by_source.setdefault(key, []).append(citation)
            if not _citation_matches_source(citation, blocks, cells, turns):
                affected.append(citation.citation_id)

        for element_type, sources in (
            (FundamentalFilingCitationElementType.TEXT_BLOCK, blocks),
            (FundamentalFilingCitationElementType.TABLE_CELL, cells),
        ):
            for element_id, source in sources.items():
                candidates = citations_by_source.get(
                    (element_type, element_id),
                    (),
                )
                if not any(
                    _is_full_source_citation(item, source.text)
                    for item in candidates
                ):
                    affected.append(element_id)

        for turn_id, turn in turns.items():
            candidates = citations_by_source.get(
                (FundamentalFilingCitationElementType.SPEAKER_TURN, turn_id),
                (),
            )
            if not _turn_citations_are_complete(turn, candidates, blocks):
                affected.append(turn_id)

        available = {citation.citation_id for citation in evidence.citations}
        for item in _semantic_evidence(evidence):
            if not item.citation_ids or any(
                citation_id not in available for citation_id in item.citation_ids
            ):
                affected.append(item.evidence_id)

        if affected:
            return _failed(
                FundamentalFilingValidationCode.CITATION_COMPLETENESS,
                "Citation excerpts, offsets, source elements, or complete "
                "source coverage are inconsistent.",
                affected,
            )
        return _passed(
            FundamentalFilingValidationCode.CITATION_COMPLETENESS,
            "Every extracted source element and semantic record has exact "
            "citation lineage.",
        )

    @staticmethod
    def _derive_evidence_page_spans(
        evidence: FundamentalFilingExtractedEvidence,
    ) -> tuple[FundamentalFilingEvidencePageSpan, ...]:
        citations = {item.citation_id: item for item in evidence.citations}
        spans: list[FundamentalFilingEvidencePageSpan] = []
        for item in _semantic_evidence(evidence):
            pages = tuple(
                sorted(
                    {
                        citations[citation_id].page_number
                        for citation_id in item.citation_ids
                        if citation_id in citations
                    }
                )
            )
            if not pages:
                continue
            spans.append(
                FundamentalFilingEvidencePageSpan(
                    evidence_id=item.evidence_id,
                    first_page=pages[0],
                    last_page=pages[-1],
                    cited_pages=pages,
                )
            )
        return tuple(sorted(spans, key=lambda item: item.evidence_id))


def _semantic_evidence(
    evidence: FundamentalFilingExtractedEvidence,
) -> tuple[FundamentalFilingCitedEvidence, ...]:
    return (
        *evidence.observed_facts,
        *evidence.adjusted_metrics,
        *evidence.management_claims,
        *evidence.guidance_commitments,
        *evidence.risk_disclosures,
    )


def _passed(
    code: FundamentalFilingValidationCode,
    message: str,
) -> FundamentalFilingValidationFinding:
    return FundamentalFilingValidationFinding(
        code=code,
        status=FundamentalFilingValidationStatus.PASS,
        message=message,
    )


def _failed(
    code: FundamentalFilingValidationCode,
    message: str,
    affected_ids: Iterable[str],
) -> FundamentalFilingValidationFinding:
    return FundamentalFilingValidationFinding(
        code=code,
        status=FundamentalFilingValidationStatus.FAIL,
        message=message,
        affected_ids=tuple(sorted(set(affected_ids))),
    )


def _canonical_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().casefold()


def _identity_content(
    evidence: FundamentalFilingExtractedEvidence, *, all_pages: bool = False,
) -> str:
    values: list[str] = []
    for page in evidence.pages if all_pages else evidence.pages[:3]:
        values.extend(block.text for block in page.text_blocks)
        values.extend(
            cell.text for table in page.tables for cell in table.cells
        )
    return _WHITESPACE.sub(" ", " ".join(values)).strip()


def _content_matches_issuer(
    content: str,
    expected: FundamentalIssuerIdentity,
) -> bool:
    canonical_content = _canonical_identifier(content)
    expected_name = _canonical_company_name(expected.legal_name)
    legal_name_matches = bool(
        expected_name
        and re.search(
            rf"(?:^| ){re.escape(expected_name)}(?: |$)",
            canonical_content,
        )
    )

    expected_symbol = _canonical_market_symbol(expected.symbol)
    labelled_symbols = {
        _canonical_market_symbol(match.group("symbol"))
        for match in _SYMBOL_LABEL.finditer(content)
    }
    symbol_matches = expected_symbol in labelled_symbols
    symbol_conflicts = bool(
        labelled_symbols and any(value != expected_symbol for value in labelled_symbols)
    )

    expected_isin = expected.isin
    isin_matches = bool(
        expected_isin
        and re.search(
            rf"(?<![A-Z0-9]){re.escape(expected_isin)}(?![A-Z0-9])",
            content.upper(),
        )
    )
    return not symbol_conflicts and (
        legal_name_matches or symbol_matches or isin_matches
    )


def _content_matches_period(
    content: str,
    expected: FundamentalFilingPeriod,
) -> bool:
    canonical = _canonical_text(content)
    if expected.fiscal_year is None:
        return _content_has_compatible_unclassified_period(
            canonical,
            expected.source_label,
        )
    year_values = {
        str(expected.fiscal_year),
        str(expected.fiscal_year)[-2:],
    }
    years = "|".join(sorted(year_values, key=len, reverse=True))

    if expected.fiscal_quarter is not None:
        quarter = expected.fiscal_quarter
        quarter_then_year = re.compile(
            rf"\b(?:q\s*{quarter}|quarter\s*{quarter})\b"
            rf".{{0,32}}?\bfy\s*(?:{years})\b"
        )
        year_then_quarter = re.compile(
            rf"\bfy\s*(?:{years})\b"
            rf".{{0,32}}?\b(?:q\s*{quarter}|quarter\s*{quarter})\b"
        )
        if quarter_then_year.search(canonical) or year_then_quarter.search(
            canonical
        ):
            return True
        return bool(
            expected.period_end
            and _content_has_period_end(
                canonical,
                expected.period_end,
                period_kind="quarter",
            )
        )

    annual_expression = re.compile(
        rf"\b(?:fy\s*(?:{years})|financial\s+year\s+(?:{years}))\b"
    )
    if annual_expression.search(canonical):
        return True
    return bool(
        expected.period_end
        and _content_has_period_end(
            canonical,
            expected.period_end,
            period_kind="year",
        )
    )


def _content_has_compatible_unclassified_period(
    canonical_content: str,
    source_label: str,
) -> bool:
    """Release unclassified metadata only with bounded content evidence.

    A provider month is an availability label, not a fiscal-period claim.  It
    therefore cannot resolve or rewrite the immutable manifest.  It can only
    bound an explicit reporting-period end independently observed in the PDF.
    """

    source_month_end = _source_label_month_end(source_label)
    if source_month_end is None:
        return False
    reporting_dates = _reporting_period_end_dates(canonical_content)
    # An older comparative column cannot rescue a source label that predates
    # an explicitly stated completed reporting period in the same document.
    if any(period_end > source_month_end for period_end in reporting_dates):
        return False
    return any(
        0 <= (source_month_end - period_end).days
        <= _MAX_UNCLASSIFIED_PUBLICATION_LAG_DAYS
        for period_end in (
            *reporting_dates,
            *_indian_fiscal_quarter_end_dates(canonical_content),
        )
    )


def _source_label_month_end(source_label: str) -> date | None:
    match = _SOURCE_MONTH_YEAR.search(_canonical_text(source_label))
    if match is None:
        return None
    year = int(match.group("year"))
    month = _MONTH_NUMBER[match.group("month")[:3].casefold()]
    return date(year, month, monthrange(year, month)[1])


def _reporting_period_end_dates(content: str) -> tuple[date, ...]:
    candidates: set[date] = set()
    for pattern in (_REPORTING_MONTH_FIRST, _REPORTING_DAY_FIRST):
        for match in pattern.finditer(content):
            _add_period_date(
                candidates,
                year=int(match.group("year")),
                month=_MONTH_NUMBER[match.group("month")[:3].casefold()],
                day=int(match.group("day")),
            )
    for pattern in (_REPORTING_NUMERIC_DAY_FIRST, _REPORTING_ISO_DATE):
        for match in pattern.finditer(content):
            _add_period_date(
                candidates,
                year=int(match.group("year")),
                month=int(match.group("month")),
                day=int(match.group("day")),
            )
    return tuple(sorted(candidates))


def _indian_fiscal_quarter_end_dates(content: str) -> tuple[date, ...]:
    """Resolve explicit Qn/FY labels under India's April-March fiscal year."""

    candidates: set[date] = set()
    quarter_months = {1: 6, 2: 9, 3: 12, 4: 3}
    for pattern in (
        _FISCAL_QUARTER_THEN_YEAR,
        _FISCAL_YEAR_THEN_QUARTER,
    ):
        for match in pattern.finditer(content):
            quarter = int(
                match.group("quarter") or match.group("quarter_word")
            )
            raw_year = int(match.group("year"))
            fiscal_year = raw_year if raw_year >= 2000 else 2000 + raw_year
            calendar_year = fiscal_year if quarter == 4 else fiscal_year - 1
            month = quarter_months[quarter]
            candidates.add(
                date(
                    calendar_year,
                    month,
                    monthrange(calendar_year, month)[1],
                )
            )
    return tuple(sorted(candidates))


def _add_period_date(
    candidates: set[date],
    *,
    year: int,
    month: int,
    day: int,
) -> None:
    try:
        candidates.add(date(year, month, day))
    except ValueError:
        return


def _content_has_period_end(
    canonical_content: str,
    period_end: date,
    *,
    period_kind: str,
) -> bool:
    month_name = period_end.strftime("%B").casefold()
    month_abbreviation = period_end.strftime("%b").casefold()
    day = str(period_end.day)
    year = str(period_end.year)
    date_patterns = (
        rf"(?:{month_name}|{month_abbreviation})\s+{day}(?:st|nd|rd|th)?\s*,?\s*{year}",
        rf"{day}(?:st|nd|rd|th)?\s+(?:{month_name}|{month_abbreviation})\s+{year}",
    )
    return any(
        re.search(
            rf"\b{period_kind}\s+ended\b.{{0,24}}?{pattern}\b",
            canonical_content,
        )
        for pattern in date_patterns
    )


def _canonical_identifier(value: str) -> str:
    return _NON_IDENTIFIER.sub(" ", value.casefold()).strip()


def _canonical_company_name(value: str) -> str:
    normalized = _canonical_identifier(value)
    tokens = normalized.split()
    suffixes = {"limited", "ltd", "private", "pvt"}
    while len(tokens) > 1 and tokens[-1] in suffixes:
        tokens.pop()
    return " ".join(tokens)


def _canonical_market_symbol(value: str) -> str:
    normalized = value.upper()
    if normalized.endswith("-EQ"):
        normalized = normalized[:-3]
    return re.sub(r"[^A-Z0-9&]", "", normalized)


def _valid_unit(unit: FundamentalFilingUnit | None) -> bool:
    if unit is None:
        return True
    if unit.kind is FundamentalFilingUnitKind.PERCENTAGE:
        return unit.multiplier == Decimal("0.01") and "%" in unit.source_label
    if unit.kind is FundamentalFilingUnitKind.BASIS_POINTS:
        return (
            unit.multiplier == Decimal("0.0001")
            and "bps" in unit.source_label.casefold()
        )
    if unit.kind is FundamentalFilingUnitKind.CURRENCY:
        return (
            unit.currency is not None
            and unit.multiplier is not None
            and unit.currency.casefold() in unit.source_label.casefold()
        )
    return True


def _valid_value(value: FundamentalFilingObservedValue) -> bool:
    if value.status is FundamentalFilingValueStatus.EXACT:
        if value.normalized_value is None:
            return False
    elif value.normalized_value is not None:
        return False
    return value.unit is None or _valid_unit(value.unit)


def _compatible_units(
    first: FundamentalFilingUnit | None,
    second: FundamentalFilingUnit | None,
) -> bool:
    return first == second


def _value_has_cited_source(
    value: FundamentalFilingObservedValue,
    citation_ids: tuple[str, ...],
    citations: dict[str, FundamentalFilingCitation],
    source_values: dict[str, FundamentalFilingObservedValue | None],
) -> bool:
    for citation_id in citation_ids:
        citation = citations.get(citation_id)
        if citation is None:
            continue
        source_value = source_values.get(citation.source_element_id)
        if source_value is not None:
            return (
                source_value.raw_text == value.raw_text
                and source_value.normalized_value == value.normalized_value
                and source_value.unit == value.unit
            )
        if _canonical_text(value.raw_text) in _canonical_text(
            citation.exact_excerpt
        ):
            return True
    return False


def _guidance_values(
    item: FundamentalFilingGuidanceCommitment,
) -> tuple[FundamentalFilingObservedValue, ...]:
    if item.value_kind is FundamentalFilingGuidanceValueKind.QUALITATIVE:
        return ()
    if item.value_kind is FundamentalFilingGuidanceValueKind.POINT:
        return (item.point_value,) if item.point_value is not None else ()
    return tuple(
        value
        for value in (item.lower_bound, item.upper_bound)
        if value is not None
    )


def _guidance_units_are_valid(
    item: FundamentalFilingGuidanceCommitment,
) -> bool:
    values = _guidance_values(item)
    if not all(_valid_value(value) for value in values):
        return False
    if len(values) == 2 and not _compatible_units(
        values[0].unit,
        values[1].unit,
    ):
        return False
    return True


def _has_management_turn_citation(
    item: FundamentalFilingCitedEvidence,
    citations: dict[str, FundamentalFilingCitation],
    turns: dict[str, FundamentalFilingSpeakerTurn],
) -> bool:
    for citation_id in item.citation_ids:
        citation = citations.get(citation_id)
        if (
            citation is None
            or citation.element_type
            is not FundamentalFilingCitationElementType.SPEAKER_TURN
        ):
            continue
        turn = turns.get(citation.source_element_id)
        if (
            turn is not None
            and turn.speaker_role is FundamentalFilingSpeakerRole.MANAGEMENT
        ):
            return True
    return False


def _citation_matches_source(
    citation: FundamentalFilingCitation,
    blocks: dict[str, FundamentalFilingTextBlock],
    cells: dict[str, FundamentalFilingTableCell],
    turns: dict[str, FundamentalFilingSpeakerTurn],
) -> bool:
    if citation.element_type is FundamentalFilingCitationElementType.TEXT_BLOCK:
        source = blocks.get(citation.source_element_id)
        if source is None or source.page_number != citation.page_number:
            return False
        return _bounds_contain(source.bounds, citation.bounds) and (
            _citation_excerpt_matches(citation, source.text)
        )
    if citation.element_type is FundamentalFilingCitationElementType.TABLE_CELL:
        source = cells.get(citation.source_element_id)
        if source is None or source.page_number != citation.page_number:
            return False
        return _bounds_contain(source.bounds, citation.bounds) and (
            _citation_excerpt_matches(citation, source.text)
        )

    turn = turns.get(citation.source_element_id)
    if turn is None or citation.exact_excerpt not in turn.text:
        return False
    source_blocks = (
        blocks[block_id]
        for block_id in turn.source_block_ids
        if block_id in blocks
        and blocks[block_id].page_number == citation.page_number
    )
    return any(
        _bounds_contain(block.bounds, citation.bounds)
        and _citation_excerpt_matches(citation, block.text)
        for block in source_blocks
    )


def _turn_citations_are_complete(
    turn: FundamentalFilingSpeakerTurn,
    citations: Iterable[FundamentalFilingCitation],
    blocks: dict[str, FundamentalFilingTextBlock],
) -> bool:
    remaining = list(citations)
    ordered_excerpts: list[str] = []
    for block_id in turn.source_block_ids:
        block = blocks.get(block_id)
        if block is None:
            return False
        matches = [
            citation
            for citation in remaining
            if citation.page_number == block.page_number
            and _citation_excerpt_matches(citation, block.text)
        ]
        matches.sort(
            key=lambda item: (
                item.start_offset if item.start_offset is not None else -1,
                item.end_offset if item.end_offset is not None else -1,
                item.citation_id,
            )
        )
        ordered_excerpts.extend(item.exact_excerpt for item in matches)
        for match in matches:
            remaining.remove(match)
    return _canonical_text(" ".join(ordered_excerpts)) == _canonical_text(
        turn.text
    )


def _citation_excerpt_matches(
    citation: FundamentalFilingCitation,
    source_text: str,
) -> bool:
    if citation.start_offset is None:
        return citation.exact_excerpt in source_text
    assert citation.end_offset is not None
    return source_text[citation.start_offset : citation.end_offset] == (
        citation.exact_excerpt
    )


def _is_full_source_citation(
    citation: FundamentalFilingCitation,
    source_text: str,
) -> bool:
    if citation.exact_excerpt != source_text:
        return False
    if citation.start_offset is None:
        return True
    return citation.start_offset == 0 and citation.end_offset == len(source_text)


def _bounds_contain(
    outer: FundamentalFilingRectangle,
    inner: FundamentalFilingRectangle,
) -> bool:
    return (
        outer.x0 <= inner.x0
        and outer.y0 <= inner.y0
        and outer.x1 >= inner.x1
        and outer.y1 >= inner.y1
    )
