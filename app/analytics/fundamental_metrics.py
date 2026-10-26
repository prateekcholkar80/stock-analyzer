"""Deterministic normalization of structured financial-table evidence.

The provider adapter preserves complete source documents.  This module maps a
small, reviewed label allow-list into canonical metric identities and verifies
every released numeric value against its original display text.  It performs
no scoring, inference, fuzzy matching, or cross-document substitution.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata

from app.models.financial_documents import (
    FinancialDocumentCell,
    FinancialDocumentPeriod,
    FinancialDocumentRow,
    FinancialDocumentType,
    FinancialReportingBasis,
    StructuredFinancialDocument,
)
from app.models.fundamental_metrics import (
    FundamentalMetricAvailability,
    FundamentalMetricCalculation,
    FundamentalMetricCitation,
    FundamentalMetricKey,
    FundamentalMetricPeriod,
    FundamentalMetricPeriodKind,
    FundamentalMetricQualityFlag,
    FundamentalMetricSourceBinding,
    FundamentalMetricUnit,
    FundamentalMetricValueStatus,
    NormalizedFundamentalMetric,
)
from app.models.fundamental_package import FundamentalPackageDataset
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalNormalizationMethod,
    FundamentalPeriodType,
    FundamentalValueKind,
)


NORMALIZATION_RULE_VERSION = "1.0.0"

_MISSING_MARKERS = frozenset(
    {"", "-", "--", "---", "–", "—", "na", "n/a", "nil", "null"}
)
_NUMBER_WITHOUT_GROUPING = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_WESTERN_GROUPING = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_INDIAN_GROUPING = re.compile(
    r"^[+-]?\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?$"
)
_AFFIX_PREFIX = re.compile(r"^(?:in\s+)?(?:rs\.?|inr)\s*", re.IGNORECASE)
_AFFIX_SUFFIX = re.compile(
    r"\s*(?:%|x|times?|days?|crores?|crs?\.?|lakhs?|lacs?|"
    r"millions?|billions?|shares?)\s*$",
    re.IGNORECASE,
)
_MONTH_PERIOD_LABEL = re.compile(
    r"^([A-Za-z]{3,9})[ '’\-]*(\d{2}|\d{4})$",
    re.IGNORECASE,
)


class FundamentalMetricNormalizationIssueCode(StrEnum):
    """Fail-closed reasons for source rows not released as canonical facts."""

    UNMAPPED_LABEL = "unmapped_label"
    AMBIGUOUS_LABEL = "ambiguous_label"
    UNSUPPORTED_UNIT = "unsupported_unit"
    SOURCE_VALUE_MISMATCH = "source_value_mismatch"
    VALUE_KIND_MISMATCH = "value_kind_mismatch"
    DUPLICATE_METRIC = "duplicate_metric"


@dataclass(frozen=True, slots=True)
class FundamentalMetricNormalizationIssue:
    """One deterministic, non-sensitive normalization limitation."""

    code: FundamentalMetricNormalizationIssueCode
    row_key: str
    period_key: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class FinancialDocumentNormalizationResult:
    """Normalized metrics plus every row or cell intentionally withheld."""

    document_type: FinancialDocumentType
    reporting_basis: FinancialReportingBasis
    metrics: tuple[NormalizedFundamentalMetric, ...]
    issues: tuple[FundamentalMetricNormalizationIssue, ...]


class DerivedFundamentalMetricIssueCode(StrEnum):
    """Why a deterministic formula was not released."""

    MISSING_INPUT = "missing_input"
    UNAVAILABLE_INPUT = "unavailable_input"
    INCOMPATIBLE_UNIT = "incompatible_unit"
    ZERO_DENOMINATOR = "zero_denominator"
    NON_POSITIVE_GROWTH_BASE = "non_positive_growth_base"
    NON_POSITIVE_CAGR_INPUT = "non_positive_cagr_input"
    INSUFFICIENT_HISTORY = "insufficient_history"
    NON_CONSECUTIVE_PERIOD = "non_consecutive_period"


@dataclass(frozen=True, slots=True)
class DerivedFundamentalMetricIssue:
    """Traceable failure or limitation for one versioned formula."""

    code: DerivedFundamentalMetricIssueCode
    formula_id: str
    reporting_basis: FinancialReportingBasis
    period_key: str | None
    input_metric_keys: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class DerivedFundamentalMetricsResult:
    """Canonical calculated metrics and fail-closed formula outcomes."""

    formula_policy_id: str
    formula_policy_version: str
    metrics: tuple[NormalizedFundamentalMetric, ...]
    issues: tuple[DerivedFundamentalMetricIssue, ...]


class _FormulaOperation(StrEnum):
    PERCENT_RATIO = "percent_ratio"
    RATIO = "ratio"
    MONETARY_DIFFERENCE = "monetary_difference"
    FREE_CASH_FLOW = "free_cash_flow"
    NET_DEBT_TO_EBITDA = "net_debt_to_ebitda"
    FCF_TO_PAT = "fcf_to_pat"
    FCF_MARGIN = "fcf_margin"
    INVERSE_PERCENT = "inverse_percent"
    MONETARY_SUM = "monetary_sum"
    COMPONENT_DEBT_TO_EQUITY = "component_debt_to_equity"
    COMPONENT_NET_DEBT = "component_net_debt"
    COMPONENT_NET_DEBT_TO_EBITDA = "component_net_debt_to_ebitda"


@dataclass(frozen=True, slots=True)
class _FormulaSpec:
    formula_id: str
    output_metric_id: str
    input_metric_ids: tuple[str, ...]
    operation: _FormulaOperation
    limitation: str | None = None


DERIVED_FORMULA_POLICY_ID = "jarvis.fundamental_derived_metrics"
DERIVED_FORMULA_POLICY_VERSION = "1.0.0"

_SAME_PERIOD_FORMULAS = (
    _FormulaSpec(
        "derive.margin.ebitda_pct",
        "derived.margin.ebitda_pct",
        ("income.ebitda", "income.revenue"),
        _FormulaOperation.PERCENT_RATIO,
    ),
    _FormulaSpec(
        "derive.margin.ebit_pct",
        "derived.margin.ebit_pct",
        ("income.ebit", "income.revenue"),
        _FormulaOperation.PERCENT_RATIO,
    ),
    _FormulaSpec(
        "derive.margin.pat_pct",
        "derived.margin.pat_pct",
        ("income.pat", "income.revenue"),
        _FormulaOperation.PERCENT_RATIO,
    ),
    _FormulaSpec(
        "derive.leverage.debt_to_equity",
        "derived.leverage.debt_to_equity",
        ("balance.borrowings", "balance.shareholders_equity"),
        _FormulaOperation.RATIO,
    ),
    _FormulaSpec(
        "derive.leverage.net_debt",
        "derived.leverage.net_debt",
        ("balance.borrowings", "balance.cash_and_equivalents"),
        _FormulaOperation.MONETARY_DIFFERENCE,
    ),
    _FormulaSpec(
        "derive.leverage.net_debt_to_ebitda",
        "derived.leverage.net_debt_to_ebitda",
        (
            "balance.borrowings",
            "balance.cash_and_equivalents",
            "income.ebitda",
        ),
        _FormulaOperation.NET_DEBT_TO_EBITDA,
    ),
    _FormulaSpec(
        "derive.cash_conversion.ocf_to_pat",
        "derived.cash_conversion.ocf_to_pat",
        ("cash_flow.operating", "income.pat"),
        _FormulaOperation.RATIO,
    ),
    _FormulaSpec(
        "derive.cash_conversion.free_cash_flow",
        "derived.cash_conversion.free_cash_flow",
        ("cash_flow.operating", "cash_flow.capex"),
        _FormulaOperation.FREE_CASH_FLOW,
    ),
    _FormulaSpec(
        "derive.cash_conversion.fcf_to_pat",
        "derived.cash_conversion.fcf_to_pat",
        ("cash_flow.operating", "cash_flow.capex", "income.pat"),
        _FormulaOperation.FCF_TO_PAT,
    ),
    _FormulaSpec(
        "derive.cash_conversion.fcf_margin_pct",
        "derived.cash_conversion.fcf_margin_pct",
        (
            "cash_flow.operating",
            "cash_flow.capex",
            "income.revenue",
        ),
        _FormulaOperation.FCF_MARGIN,
    ),
    _FormulaSpec(
        "derive.working_capital.net_working_capital",
        "derived.working_capital.net_working_capital",
        ("balance.current_assets", "balance.current_liabilities"),
        _FormulaOperation.MONETARY_DIFFERENCE,
    ),
    _FormulaSpec(
        "derive.working_capital.current_ratio",
        "derived.working_capital.current_ratio",
        ("balance.current_assets", "balance.current_liabilities"),
        _FormulaOperation.RATIO,
    ),
    _FormulaSpec(
        "derive.working_capital.receivables_to_revenue_pct",
        "derived.working_capital.receivables_to_revenue_pct",
        ("balance.trade_receivables", "income.revenue"),
        _FormulaOperation.PERCENT_RATIO,
    ),
    _FormulaSpec(
        "derive.working_capital.inventory_to_revenue_pct",
        "derived.working_capital.inventory_to_revenue_pct",
        ("balance.inventory", "income.revenue"),
        _FormulaOperation.PERCENT_RATIO,
    ),
    _FormulaSpec(
        "derive.valuation.earnings_yield_pct",
        "derived.valuation.earnings_yield_pct",
        ("valuation.pe",),
        _FormulaOperation.INVERSE_PERCENT,
    ),
    _FormulaSpec(
        "derive.valuation.book_yield_pct",
        "derived.valuation.book_yield_pct",
        ("valuation.pb",),
        _FormulaOperation.INVERSE_PERCENT,
    ),
    _FormulaSpec(
        "derive.valuation.ebitda_yield_pct",
        "derived.valuation.ebitda_yield_pct",
        ("valuation.ev_to_ebitda",),
        _FormulaOperation.INVERSE_PERCENT,
    ),
)

_COMPONENT_DEBT_FORMULAS = (
    _FormulaSpec(
        "derive.leverage.total_debt_from_components",
        "derived.leverage.total_debt_proxy",
        ("balance.secured_debt", "balance.unsecured_debt"),
        _FormulaOperation.MONETARY_SUM,
        "Total debt proxy contains only mapped secured and unsecured debt.",
    ),
    _FormulaSpec(
        "derive.leverage.debt_to_equity_from_components",
        "derived.leverage.debt_to_equity",
        (
            "balance.secured_debt",
            "balance.unsecured_debt",
            "balance.shareholders_equity",
        ),
        _FormulaOperation.COMPONENT_DEBT_TO_EQUITY,
        "Debt contains only mapped secured and unsecured debt.",
    ),
    _FormulaSpec(
        "derive.leverage.net_debt_from_components",
        "derived.leverage.net_debt",
        (
            "balance.secured_debt",
            "balance.unsecured_debt",
            "balance.cash_and_equivalents",
        ),
        _FormulaOperation.COMPONENT_NET_DEBT,
        "Debt contains only mapped secured and unsecured debt.",
    ),
    _FormulaSpec(
        "derive.leverage.net_debt_to_ebitda_from_components",
        "derived.leverage.net_debt_to_ebitda",
        (
            "balance.secured_debt",
            "balance.unsecured_debt",
            "balance.cash_and_equivalents",
            "income.ebitda",
        ),
        _FormulaOperation.COMPONENT_NET_DEBT_TO_EBITDA,
        "Debt contains only mapped secured and unsecured debt.",
    ),
)

_GROWTH_FORMULAS = MappingProxyType(
    {
        "income.revenue": "derived.growth.revenue_yoy_pct",
        "income.ebitda": "derived.growth.ebitda_yoy_pct",
        "income.pat": "derived.growth.pat_yoy_pct",
        "income.eps": "derived.growth.eps_yoy_pct",
        "income.adjusted_eps": "derived.growth.adjusted_eps_yoy_pct",
        "cash_flow.operating": "derived.growth.ocf_yoy_pct",
    }
)

_CAGR_FORMULAS = MappingProxyType(
    {
        metric_id: output_metric_id.replace("_yoy_pct", "_cagr_pct")
        for metric_id, output_metric_id in _GROWTH_FORMULAS.items()
    }
)


# Aliases are intentionally statement-scoped and exact after only cosmetic
# normalization.  Additions require reviewed fixtures; fuzzy matching is
# deliberately prohibited.
_ALIASES: Mapping[
    FinancialDocumentType,
    tuple[tuple[str, tuple[str, ...]], ...],
] = MappingProxyType({
    FinancialDocumentType.GROWTH_TABLE: (
        ("growth.revenue", ("sales growth", "revenue growth", "sales cagr", "revenue cagr")),
        ("growth.ebitda", ("operating profit growth", "ebitda growth", "operating profit cagr", "ebitda cagr")),
        ("growth.pat", ("profit growth", "net profit growth", "pat growth", "profit cagr", "net profit cagr", "pat cagr")),
        ("growth.eps", ("eps growth", "earnings per share growth", "eps cagr", "earnings per share cagr")),
    ),
    FinancialDocumentType.BALANCE_SHEET: (
        ("balance.total_assets", ("assets", "total assets")),
        ("balance.non_current_assets", ("non current assets",)),
        ("balance.current_assets", ("current assets", "current assets crs")),
        ("balance.net_block", ("net block", "fixed assets", "net fixed assets")),
        ("balance.capital_work_in_progress", ("capital work in progress", "capital work in progress cwip", "cwip")),
        ("balance.intangibles_under_development", ("intangible assets under development",)),
        ("balance.long_term_investments", ("long term investments",)),
        ("balance.short_term_investments", ("short term investments",)),
        ("balance.inventory", ("inventory", "inventories")),
        ("balance.trade_receivables", ("accounts receivable", "trade receivables", "receivables", "debtors")),
        ("balance.cash_and_equivalents", ("cash and bank balances", "cash and cash equivalents", "cash equivalents", "cash")),
        ("balance.total_liabilities", ("liabilities", "total liabilities")),
        ("balance.shareholders_equity", ("shareholders funds", "shareholder funds", "shareholders equity", "equity", "net worth")),
        ("balance.share_capital", ("share capital", "equity share capital")),
        ("balance.reserves", ("reserves", "reserves and surplus")),
        ("balance.non_current_liabilities", ("non current liabilities",)),
        ("balance.current_liabilities", ("current liabilities",)),
        ("balance.borrowings", ("borrowings", "total borrowings", "debt", "total debt")),
        ("balance.secured_debt", ("secured loans", "secured borrowings")),
        ("balance.unsecured_debt", ("unsecured loans", "unsecured borrowings")),
    ),
    FinancialDocumentType.PROFIT_AND_LOSS: (
        ("income.revenue", ("sales", "net sales", "revenue", "total revenue", "operating revenue")),
        ("income.other_income", ("other income",)),
        ("income.total_income", ("total income",)),
        ("income.ebitda", ("operating profit", "ebitda")),
        ("margin.ebitda", ("opm", "opm percent", "operating profit margin", "operating margin")),
        ("income.depreciation", ("depreciation", "depreciation and amortisation", "depreciation and amortization")),
        ("income.ebit", ("ebit",)),
        ("income.interest_expense", ("interest", "interest expense", "finance cost", "finance costs")),
        ("income.pbt", ("profit before tax", "pbt")),
        ("income.tax_expense", ("tax", "tax expense")),
        ("income.pat", ("net profit", "profit after tax", "pat", "net income")),
        ("income.eps", ("eps", "earnings per share")),
        ("income.adjusted_eps", ("adjusted eps",)),
        ("income.share_count", ("number of shares", "number of shares crs", "number of shares crore")),
    ),
    FinancialDocumentType.CASH_FLOW: (
        ("cash_flow.operating", ("cash from operating activity", "cash from operating activities", "cash flow from operating activity", "cash flow from operating activities", "cfo")),
        ("cash_flow.working_capital_change", ("working capital changes", "change in working capital")),
        ("cash_flow.capex", ("capital expenditure", "capex")),
        ("cash_flow.free_cash_flow", ("free cash flow", "fcf")),
        ("cash_flow.investing", ("cash from investing activity", "cash from investing activities", "cash flow from investing activities", "cfi")),
        ("cash_flow.financing", ("cash from financing activity", "cash from financing activities", "cash flow from financing activities", "cff")),
        ("cash_flow.dividends", ("dividend", "dividends", "dividend paid")),
        ("cash_flow.net_change_in_cash", ("net cash flow", "net change in cash", "net cash increase")),
    ),
    FinancialDocumentType.RATIOS: (
        ("ratio.current", ("current ratio",)),
        ("ratio.quick", ("quick ratio",)),
        ("ratio.debt_to_equity", ("debt equity ratio", "debt to equity", "debt to equity ratio", "d e ratio")),
        ("ratio.interest_coverage", ("interest coverage", "interest coverage ratio")),
        ("ratio.roe", ("roe", "roe percent", "return on equity", "return on equity percent")),
        ("ratio.roce", ("roce", "roce percent", "return on capital employed", "return on capital employed percent")),
        ("margin.gross", ("gross margin", "gross margin percent")),
        ("margin.ebitda", ("opm", "opm percent", "operating profit margin", "operating profit margin percent")),
        ("margin.net", ("net profit margin", "net profit margin percent", "net margin", "net margin percent")),
        ("working_capital.cash_conversion_cycle_days", ("cash conversion cycle", "cash conversion cycle days")),
        ("working_capital.debtor_days", ("debtor days", "receivable days", "receivables days")),
        ("working_capital.inventory_days", ("inventory days",)),
        ("working_capital.payable_days", ("payable days", "creditor days")),
        ("valuation.pe", ("pe", "p e", "price earnings", "price to earnings")),
        ("valuation.pb", ("pb", "p b", "price book", "price to book")),
        ("valuation.ev_to_ebitda", ("ev ebitda", "ev to ebitda")),
        ("income.eps", ("eps", "earnings per share")),
        ("income.adjusted_eps", ("adjusted eps",)),
    ),
    FinancialDocumentType.QUARTERLY_RESULTS: (
        ("income.revenue", ("sales", "net sales", "revenue", "total revenue", "operating revenue")),
        ("income.ebitda", ("operating profit", "ebitda")),
        ("margin.ebitda", ("opm", "opm percent", "operating profit margin", "operating profit margin percent")),
        ("income.pbt", ("profit before tax", "pbt")),
        ("income.pat", ("net profit", "profit after tax", "pat", "net income")),
        ("income.eps", ("eps", "earnings per share")),
        ("income.adjusted_eps", ("adjusted eps",)),
    ),
})

_EXPECTED_VALUE_KINDS: Mapping[str, frozenset[FundamentalValueKind]] = (
    MappingProxyType(
        {
            **{
                metric_id: frozenset({FundamentalValueKind.PERCENTAGE})
                for metric_id in (
                    "growth.revenue",
                    "growth.ebitda",
                    "growth.pat",
                    "growth.eps",
                    "margin.ebitda",
                    "margin.gross",
                    "margin.net",
                    "ratio.roe",
                    "ratio.roce",
                )
            },
            **{
                metric_id: frozenset({FundamentalValueKind.MONETARY})
                for metric_id in (
                    "balance.total_assets",
                    "balance.non_current_assets",
                    "balance.current_assets",
                    "balance.net_block",
                    "balance.capital_work_in_progress",
                    "balance.intangibles_under_development",
                    "balance.long_term_investments",
                    "balance.short_term_investments",
                    "balance.inventory",
                    "balance.trade_receivables",
                    "balance.cash_and_equivalents",
                    "balance.total_liabilities",
                    "balance.shareholders_equity",
                    "balance.share_capital",
                    "balance.reserves",
                    "balance.non_current_liabilities",
                    "balance.current_liabilities",
                    "balance.borrowings",
                    "balance.secured_debt",
                    "balance.unsecured_debt",
                    "income.revenue",
                    "income.other_income",
                    "income.total_income",
                    "income.ebitda",
                    "income.depreciation",
                    "income.ebit",
                    "income.interest_expense",
                    "income.pbt",
                    "income.tax_expense",
                    "income.pat",
                    "cash_flow.operating",
                    "cash_flow.working_capital_change",
                    "cash_flow.capex",
                    "cash_flow.free_cash_flow",
                    "cash_flow.investing",
                    "cash_flow.financing",
                    "cash_flow.dividends",
                    "cash_flow.net_change_in_cash",
                )
            },
            "income.eps": frozenset({FundamentalValueKind.PER_SHARE}),
            "income.adjusted_eps": frozenset(
                {FundamentalValueKind.PER_SHARE}
            ),
            "income.share_count": frozenset({FundamentalValueKind.COUNT}),
            **{
                metric_id: frozenset({FundamentalValueKind.RATIO})
                for metric_id in (
                    "ratio.current",
                    "ratio.quick",
                    "ratio.debt_to_equity",
                    "ratio.interest_coverage",
                    "valuation.pe",
                    "valuation.pb",
                    "valuation.ev_to_ebitda",
                )
            },
            **{
                metric_id: frozenset({FundamentalValueKind.OTHER})
                for metric_id in (
                    "working_capital.cash_conversion_cycle_days",
                    "working_capital.debtor_days",
                    "working_capital.inventory_days",
                    "working_capital.payable_days",
                )
            },
        }
    )
)


def _label_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("&", " and ").replace("%", " percent ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


def _alias_index() -> Mapping[FinancialDocumentType, Mapping[str, str]]:
    result: dict[FinancialDocumentType, Mapping[str, str]] = {}
    for document_type, rules in _ALIASES.items():
        index: dict[str, str] = {}
        for metric_id, aliases in rules:
            for alias in aliases:
                key = _label_key(alias)
                existing = index.get(key)
                if existing is not None and existing != metric_id:
                    raise RuntimeError(f"ambiguous reviewed financial alias: {alias}")
                index[key] = metric_id
        result[document_type] = MappingProxyType(index)
    return MappingProxyType(result)


_ALIAS_INDEX = _alias_index()


def parse_indian_financial_number(source_value: str) -> Decimal | None:
    """Parse one source display number without applying its unit scale.

    Returns ``None`` only for explicit missing markers.  Malformed or
    unreviewed textual values raise ``ValueError`` so callers cannot silently
    coerce them into zero.
    """

    text = unicodedata.normalize("NFKC", source_value)
    text = text.replace("\u00a0", " ").strip()
    if text.casefold() in _MISSING_MARKERS:
        return None

    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1].strip()
    text = text.replace("₹", "").strip()
    text = _AFFIX_PREFIX.sub("", text).strip()
    while True:
        stripped = _AFFIX_SUFFIX.sub("", text).strip()
        if stripped == text:
            break
        text = stripped

    if not text or not any(
        pattern.fullmatch(text)
        for pattern in (
            _NUMBER_WITHOUT_GROUPING,
            _WESTERN_GROUPING,
            _INDIAN_GROUPING,
        )
    ):
        raise ValueError("financial display value is not a supported number")
    if negative_parentheses and text.startswith(("+", "-")):
        raise ValueError("financial display value has contradictory signs")

    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - guarded by regex
        raise ValueError("financial display value is not decimal") from exc
    if not value.is_finite():
        raise ValueError("financial display value must be finite")
    return -value if negative_parentheses else value


def normalize_financial_document_metrics(
    document: StructuredFinancialDocument,
    source: FundamentalMetricSourceBinding,
) -> FinancialDocumentNormalizationResult:
    """Normalize reviewed facts from one immutable package document."""

    _validate_source_binding(document, source)
    aliases = _ALIAS_INDEX[document.document_type]
    periods = {period.period_key: period for period in document.periods}
    candidates: dict[str, list[NormalizedFundamentalMetric]] = {}
    issues: list[FundamentalMetricNormalizationIssue] = []

    for row in sorted(document.rows, key=lambda item: item.display_order):
        mapped = {
            aliases[label_key]
            for label in (row.original_label, row.standardized_label)
            if label is not None
            and (label_key := _label_key(label)) in aliases
        }
        if not mapped:
            issues.append(
                FundamentalMetricNormalizationIssue(
                    code=FundamentalMetricNormalizationIssueCode.UNMAPPED_LABEL,
                    row_key=row.row_key,
                    period_key=None,
                    detail="No reviewed alias exists for this document row.",
                )
            )
            continue
        if len(mapped) != 1:
            issues.append(
                FundamentalMetricNormalizationIssue(
                    code=FundamentalMetricNormalizationIssueCode.AMBIGUOUS_LABEL,
                    row_key=row.row_key,
                    period_key=None,
                    detail="Provider labels resolve to different canonical metrics.",
                )
            )
            continue
        metric_id = next(iter(mapped))
        if row.value_kind not in _EXPECTED_VALUE_KINDS[metric_id]:
            issues.append(
                FundamentalMetricNormalizationIssue(
                    code=(
                        FundamentalMetricNormalizationIssueCode.VALUE_KIND_MISMATCH
                    ),
                    row_key=row.row_key,
                    period_key=None,
                    detail=(
                        "The provider row value kind contradicts the reviewed "
                        "canonical metric definition."
                    ),
                )
            )
            continue
        unit_hint = _row_unit_hint(row, document)
        for cell in sorted(
            row.cells,
            key=lambda item: periods[item.period_key].display_order,
        ):
            try:
                unit = _normalize_unit(
                    value_kind=row.value_kind,
                    normalized_unit=cell.normalized_unit or unit_hint,
                    currency=document.currency,
                    source_label=row.original_label,
                    document_type=document.document_type,
                )
            except ValueError:
                issues.append(
                    FundamentalMetricNormalizationIssue(
                        code=FundamentalMetricNormalizationIssueCode.UNSUPPORTED_UNIT,
                        row_key=row.row_key,
                        period_key=cell.period_key,
                        detail="The row unit is not in the reviewed unit allow-list.",
                    )
                )
                continue

            normalized, issue = _normalize_cell(
                document=document,
                row=row,
                cell=cell,
                period=periods[cell.period_key],
                metric_id=metric_id,
                unit=unit,
                source=source,
            )
            if issue is not None:
                issues.append(issue)
            if normalized is not None:
                candidates.setdefault(normalized.key.canonical_key, []).append(
                    normalized
                )

    metrics: list[NormalizedFundamentalMetric] = []
    for canonical_key in sorted(candidates):
        values = candidates[canonical_key]
        if len(values) == 1:
            metrics.append(values[0])
            continue
        resolved = _resolve_duplicate(values)
        if resolved is None:
            first = values[0]
            issues.append(
                FundamentalMetricNormalizationIssue(
                    code=FundamentalMetricNormalizationIssueCode.DUPLICATE_METRIC,
                    row_key=first.citations[0].source_path[1],
                    period_key=first.key.period_key,
                    detail="Duplicate canonical observations could not be released safely.",
                )
            )
        else:
            metrics.append(resolved)

    return FinancialDocumentNormalizationResult(
        document_type=document.document_type,
        reporting_basis=document.reporting_basis,
        metrics=tuple(metrics),
        issues=tuple(
            sorted(
                issues,
                key=lambda item: (
                    item.code.value,
                    item.row_key,
                    item.period_key or "",
                    item.detail,
                ),
            )
        ),
    )


def derive_fundamental_metrics(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> DerivedFundamentalMetricsResult:
    """Calculate the initial versioned metric family from normalized facts.

    Formulas never substitute reporting bases, skip missing periods, or infer
    unavailable values.  Inputs may come from multiple structured documents,
    but they must already have unique canonical identities.
    """

    canonical_keys = tuple(metric.key.canonical_key for metric in metrics)
    if len(canonical_keys) != len(set(canonical_keys)):
        raise ValueError("derived metric inputs must have unique canonical keys")

    slots: dict[
        tuple[FinancialReportingBasis, FundamentalMetricPeriodKind, str],
        dict[str, NormalizedFundamentalMetric],
    ] = {}
    for metric in metrics:
        slot_key = _metric_period_slot(metric)
        slot = slots.setdefault(slot_key, {})
        if metric.key.metric_id in slot:
            raise ValueError(
                "derived metric inputs contain duplicate period observations"
            )
        slot[metric.key.metric_id] = metric

    derived: list[NormalizedFundamentalMetric] = []
    issues: list[DerivedFundamentalMetricIssue] = []
    for slot_key in sorted(
        slots,
        key=lambda item: (item[0].value, item[1].value, item[2]),
    ):
        slot = slots[slot_key]
        reported_debt = slot.get("balance.borrowings")
        has_component_debt = (
            (
                reported_debt is None
                or reported_debt.value_status
                not in {
                    FundamentalMetricValueStatus.PRESENT,
                    FundamentalMetricValueStatus.ZERO,
                }
            )
            and "balance.secured_debt" in slot
            and "balance.unsecured_debt" in slot
        )
        formulas = (
            tuple(
                formula
                for formula in _SAME_PERIOD_FORMULAS
                if not (
                    has_component_debt
                    and formula.output_metric_id.startswith(
                        "derived.leverage."
                    )
                )
            )
            + (_COMPONENT_DEBT_FORMULAS if has_component_debt else ())
        )
        for formula in formulas:
            if not any(key in slot for key in formula.input_metric_ids):
                continue
            calculated, issue = _calculate_same_period_formula(
                formula,
                reporting_basis=slot_key[0],
                slot=slot,
            )
            if calculated is not None:
                derived.append(calculated)
            if issue is not None:
                issues.append(issue)

    growth_metrics, growth_issues = _calculate_growth_metrics(metrics)
    derived.extend(growth_metrics)
    issues.extend(growth_issues)

    derived.sort(key=lambda item: item.key.canonical_key)
    derived_keys = tuple(metric.key.canonical_key for metric in derived)
    if len(derived_keys) != len(set(derived_keys)):
        raise ValueError("derived formulas produced duplicate canonical metrics")
    issues.sort(
        key=lambda item: (
            item.formula_id,
            item.reporting_basis.value,
            item.period_key or "",
            item.code.value,
            item.input_metric_keys,
        )
    )
    return DerivedFundamentalMetricsResult(
        formula_policy_id=DERIVED_FORMULA_POLICY_ID,
        formula_policy_version=DERIVED_FORMULA_POLICY_VERSION,
        metrics=tuple(derived),
        issues=tuple(issues),
    )


def _metric_period_slot(
    metric: NormalizedFundamentalMetric,
) -> tuple[FinancialReportingBasis, FundamentalMetricPeriodKind, str]:
    period_identity = (
        metric.period.end_date.isoformat()
        if metric.period.end_date is not None
        else metric.period.period_key
    )
    return (
        metric.key.reporting_basis,
        metric.period.period_kind,
        period_identity,
    )


def _calculate_same_period_formula(
    formula: _FormulaSpec,
    *,
    reporting_basis: FinancialReportingBasis,
    slot: dict[str, NormalizedFundamentalMetric],
) -> tuple[
    NormalizedFundamentalMetric | None,
    DerivedFundamentalMetricIssue | None,
]:
    anchor = next(
        slot[key] for key in formula.input_metric_ids if key in slot
    )
    expected_keys = tuple(
        f"{metric_id}:{reporting_basis.value}:{anchor.period.period_key}"
        for metric_id in formula.input_metric_ids
    )
    missing = tuple(
        metric_id
        for metric_id in formula.input_metric_ids
        if metric_id not in slot
    )
    if missing:
        return None, _derived_issue(
            DerivedFundamentalMetricIssueCode.MISSING_INPUT,
            formula=formula,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=expected_keys,
            detail=f"Required normalized inputs are missing: {', '.join(missing)}.",
        )

    inputs = tuple(slot[key] for key in formula.input_metric_ids)
    unavailable = tuple(
        metric.key.metric_id
        for metric in inputs
        if metric.value_status
        not in {
            FundamentalMetricValueStatus.PRESENT,
            FundamentalMetricValueStatus.ZERO,
        }
    )
    if unavailable:
        return None, _derived_issue(
            DerivedFundamentalMetricIssueCode.UNAVAILABLE_INPUT,
            formula=formula,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=tuple(
                metric.key.canonical_key for metric in inputs
            ),
            detail=(
                "Required normalized inputs are unavailable or conflicting: "
                f"{', '.join(unavailable)}."
            ),
        )
    if not _formula_units_compatible(formula.operation, inputs):
        return None, _derived_issue(
            DerivedFundamentalMetricIssueCode.INCOMPATIBLE_UNIT,
            formula=formula,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=tuple(
                metric.key.canonical_key for metric in inputs
            ),
            detail="Required normalized inputs do not have compatible units.",
        )

    values = tuple(_base_value(metric) for metric in inputs)
    denominator_index = _denominator_index(formula.operation)
    if denominator_index is not None and values[denominator_index] == 0:
        return None, _derived_issue(
            DerivedFundamentalMetricIssueCode.ZERO_DENOMINATOR,
            formula=formula,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=tuple(
                metric.key.canonical_key for metric in inputs
            ),
            detail="The formula denominator is explicitly zero.",
        )

    with localcontext() as context:
        context.prec = 34
        context.rounding = ROUND_HALF_EVEN
        value, unit = _apply_formula(formula.operation, inputs, values)
    return (
        _build_calculated_metric(
            formula_id=formula.formula_id,
            output_metric_id=formula.output_metric_id,
            inputs=inputs,
            anchor=anchor,
            value=value,
            unit=unit,
            limitations=((formula.limitation,) if formula.limitation else ()),
        ),
        None,
    )


def _formula_units_compatible(
    operation: _FormulaOperation,
    inputs: tuple[NormalizedFundamentalMetric, ...],
) -> bool:
    if operation is _FormulaOperation.INVERSE_PERCENT:
        return (
            len(inputs) == 1
            and inputs[0].unit.value_kind is FundamentalValueKind.RATIO
        )
    if any(
        metric.unit.value_kind is not FundamentalValueKind.MONETARY
        for metric in inputs
    ):
        return False
    currencies = {metric.unit.currency for metric in inputs}
    return len(currencies) == 1 and None not in currencies


def _denominator_index(operation: _FormulaOperation) -> int | None:
    return {
        _FormulaOperation.PERCENT_RATIO: 1,
        _FormulaOperation.RATIO: 1,
        _FormulaOperation.NET_DEBT_TO_EBITDA: 2,
        _FormulaOperation.FCF_TO_PAT: 2,
        _FormulaOperation.FCF_MARGIN: 2,
        _FormulaOperation.INVERSE_PERCENT: 0,
        _FormulaOperation.COMPONENT_DEBT_TO_EQUITY: 2,
        _FormulaOperation.COMPONENT_NET_DEBT_TO_EBITDA: 3,
    }.get(operation)


def _apply_formula(
    operation: _FormulaOperation,
    inputs: tuple[NormalizedFundamentalMetric, ...],
    values: tuple[Decimal, ...],
) -> tuple[Decimal, FundamentalMetricUnit]:
    if operation is _FormulaOperation.PERCENT_RATIO:
        return values[0] / values[1] * Decimal("100"), _percent_unit()
    if operation is _FormulaOperation.RATIO:
        return values[0] / values[1], _ratio_unit()
    if operation is _FormulaOperation.MONETARY_DIFFERENCE:
        result = values[0] - values[1]
        return result / inputs[0].unit.scale, inputs[0].unit
    if operation is _FormulaOperation.FREE_CASH_FLOW:
        result = values[0] - abs(values[1])
        return result / inputs[0].unit.scale, inputs[0].unit
    if operation is _FormulaOperation.NET_DEBT_TO_EBITDA:
        return (values[0] - values[1]) / values[2], _ratio_unit()
    if operation is _FormulaOperation.FCF_TO_PAT:
        return (values[0] - abs(values[1])) / values[2], _ratio_unit()
    if operation is _FormulaOperation.FCF_MARGIN:
        return (
            (values[0] - abs(values[1])) / values[2] * Decimal("100"),
            _percent_unit(),
        )
    if operation is _FormulaOperation.INVERSE_PERCENT:
        return Decimal("100") / values[0], _percent_unit()
    if operation is _FormulaOperation.MONETARY_SUM:
        result = values[0] + values[1]
        return result / inputs[0].unit.scale, inputs[0].unit
    if operation is _FormulaOperation.COMPONENT_DEBT_TO_EQUITY:
        return (values[0] + values[1]) / values[2], _ratio_unit()
    if operation is _FormulaOperation.COMPONENT_NET_DEBT:
        result = values[0] + values[1] - values[2]
        return result / inputs[0].unit.scale, inputs[0].unit
    if operation is _FormulaOperation.COMPONENT_NET_DEBT_TO_EBITDA:
        return (
            (values[0] + values[1] - values[2]) / values[3],
            _ratio_unit(),
        )
    raise ValueError("unsupported derived formula operation")


def _calculate_growth_metrics(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> tuple[
    list[NormalizedFundamentalMetric],
    list[DerivedFundamentalMetricIssue],
]:
    series: dict[
        tuple[
            str,
            FinancialReportingBasis,
            FundamentalMetricPeriodKind,
        ],
        list[NormalizedFundamentalMetric],
    ] = {}
    for metric in metrics:
        if metric.key.metric_id not in _GROWTH_FORMULAS:
            continue
        if metric.period.period_kind not in {
            FundamentalMetricPeriodKind.ANNUAL,
            FundamentalMetricPeriodKind.QUARTERLY,
        }:
            continue
        series.setdefault(
            (
                metric.key.metric_id,
                metric.key.reporting_basis,
                metric.period.period_kind,
            ),
            [],
        ).append(metric)

    derived: list[NormalizedFundamentalMetric] = []
    issues: list[DerivedFundamentalMetricIssue] = []
    for (metric_id, basis, period_kind), observations in sorted(
        series.items(),
        key=lambda item: (
            item[0][0],
            item[0][1].value,
            item[0][2].value,
        ),
    ):
        output_metric_id = _GROWTH_FORMULAS[metric_id]
        formula_id = output_metric_id.replace("derived.", "derive.", 1)
        observations.sort(
            key=lambda item: (
                item.period.end_date or date.max,
                item.period.period_key,
            )
        )
        if len(observations) < 2 or any(
            item.period.end_date is None for item in observations
        ):
            issues.append(
                DerivedFundamentalMetricIssue(
                    code=DerivedFundamentalMetricIssueCode.INSUFFICIENT_HISTORY,
                    formula_id=formula_id,
                    reporting_basis=basis,
                    period_key=(
                        observations[-1].period.period_key
                        if observations
                        else None
                    ),
                    input_metric_keys=tuple(
                        item.key.canonical_key for item in observations
                    ),
                    detail=(
                        "At least two dated observations are required for "
                        "consecutive-period growth."
                    ),
                )
            )
            continue

        for prior, current in zip(
            observations,
            observations[1:],
            strict=False,
        ):
            input_keys = (prior.key.canonical_key, current.key.canonical_key)
            if not _periods_are_consecutive(prior.period, current.period):
                issues.append(
                    DerivedFundamentalMetricIssue(
                        code=(
                            DerivedFundamentalMetricIssueCode.NON_CONSECUTIVE_PERIOD
                        ),
                        formula_id=formula_id,
                        reporting_basis=basis,
                        period_key=current.period.period_key,
                        input_metric_keys=input_keys,
                        detail=(
                            "Growth was not calculated across a missing or "
                            "non-consecutive reporting period."
                        ),
                    )
                )
                continue
            if any(
                item.value_status
                not in {
                    FundamentalMetricValueStatus.PRESENT,
                    FundamentalMetricValueStatus.ZERO,
                }
                for item in (prior, current)
            ):
                issues.append(
                    DerivedFundamentalMetricIssue(
                        code=DerivedFundamentalMetricIssueCode.UNAVAILABLE_INPUT,
                        formula_id=formula_id,
                        reporting_basis=basis,
                        period_key=current.period.period_key,
                        input_metric_keys=input_keys,
                        detail="A consecutive growth observation is unavailable.",
                    )
                )
                continue
            if not _growth_units_compatible(prior, current):
                issues.append(
                    DerivedFundamentalMetricIssue(
                        code=DerivedFundamentalMetricIssueCode.INCOMPATIBLE_UNIT,
                        formula_id=formula_id,
                        reporting_basis=basis,
                        period_key=current.period.period_key,
                        input_metric_keys=input_keys,
                        detail="Growth inputs do not have compatible units.",
                    )
                )
                continue
            prior_value = _base_value(prior)
            if prior_value <= 0:
                issues.append(
                    DerivedFundamentalMetricIssue(
                        code=(
                            DerivedFundamentalMetricIssueCode.NON_POSITIVE_GROWTH_BASE
                        ),
                        formula_id=formula_id,
                        reporting_basis=basis,
                        period_key=current.period.period_key,
                        input_metric_keys=input_keys,
                        detail=(
                            "Percentage growth is undefined for a zero or "
                            "negative prior-period base."
                        ),
                    )
                )
                continue
            with localcontext() as context:
                context.prec = 34
                context.rounding = ROUND_HALF_EVEN
                growth = (
                    _base_value(current) / prior_value - Decimal("1")
                ) * Decimal("100")
            derived.append(
                _build_calculated_metric(
                    formula_id=formula_id,
                    output_metric_id=output_metric_id,
                    inputs=(prior, current),
                    anchor=current,
                    value=growth,
                    unit=_percent_unit(),
                )
            )
        if period_kind is FundamentalMetricPeriodKind.ANNUAL:
            cagr_metric, cagr_issue = _calculate_cagr(
                metric_id=metric_id,
                reporting_basis=basis,
                observations=tuple(observations),
            )
            if cagr_metric is not None:
                derived.append(cagr_metric)
            if cagr_issue is not None:
                issues.append(cagr_issue)
    return derived, issues


def _calculate_cagr(
    *,
    metric_id: str,
    reporting_basis: FinancialReportingBasis,
    observations: tuple[NormalizedFundamentalMetric, ...],
) -> tuple[
    NormalizedFundamentalMetric | None,
    DerivedFundamentalMetricIssue | None,
]:
    output_metric_id = _CAGR_FORMULAS[metric_id]
    formula_id = output_metric_id.replace("derived.", "derive.", 1)
    input_keys = tuple(item.key.canonical_key for item in observations)
    anchor = observations[-1]
    if len(observations) < 3:
        return None, DerivedFundamentalMetricIssue(
            code=DerivedFundamentalMetricIssueCode.INSUFFICIENT_HISTORY,
            formula_id=formula_id,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=input_keys,
            detail="CAGR requires at least three consecutive annual observations.",
        )
    if any(
        not _periods_are_consecutive(prior.period, current.period)
        for prior, current in zip(
            observations,
            observations[1:],
            strict=False,
        )
    ):
        return None, DerivedFundamentalMetricIssue(
            code=DerivedFundamentalMetricIssueCode.NON_CONSECUTIVE_PERIOD,
            formula_id=formula_id,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=input_keys,
            detail="CAGR was not calculated across a missing annual period.",
        )
    if any(
        item.value_status
        not in {
            FundamentalMetricValueStatus.PRESENT,
            FundamentalMetricValueStatus.ZERO,
        }
        for item in observations
    ):
        return None, DerivedFundamentalMetricIssue(
            code=DerivedFundamentalMetricIssueCode.UNAVAILABLE_INPUT,
            formula_id=formula_id,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=input_keys,
            detail="CAGR requires every annual observation to be available.",
        )
    if any(
        not _growth_units_compatible(observations[0], item)
        for item in observations[1:]
    ):
        return None, DerivedFundamentalMetricIssue(
            code=DerivedFundamentalMetricIssueCode.INCOMPATIBLE_UNIT,
            formula_id=formula_id,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=input_keys,
            detail="CAGR inputs do not have compatible units.",
        )

    first_value = _base_value(observations[0])
    last_value = _base_value(observations[-1])
    if first_value <= 0 or last_value <= 0:
        return None, DerivedFundamentalMetricIssue(
            code=DerivedFundamentalMetricIssueCode.NON_POSITIVE_CAGR_INPUT,
            formula_id=formula_id,
            reporting_basis=reporting_basis,
            period_key=anchor.period.period_key,
            input_metric_keys=input_keys,
            detail="CAGR is undefined for a non-positive start or end value.",
        )
    years = observations[-1].period.end_date.year - (
        observations[0].period.end_date.year
    )
    with localcontext() as context:
        context.prec = 34
        context.rounding = ROUND_HALF_EVEN
        exponent = Decimal("1") / Decimal(years)
        cagr = (
            context.power(last_value / first_value, exponent) - Decimal("1")
        ) * Decimal("100")
    return (
        _build_calculated_metric(
            formula_id=formula_id,
            output_metric_id=output_metric_id,
            inputs=observations,
            anchor=anchor,
            value=cagr,
            unit=_percent_unit(),
        ),
        None,
    )


def _periods_are_consecutive(
    prior: FundamentalMetricPeriod,
    current: FundamentalMetricPeriod,
) -> bool:
    if prior.end_date is None or current.end_date is None:
        return False
    if prior.period_kind is FundamentalMetricPeriodKind.ANNUAL:
        return (
            current.end_date.year == prior.end_date.year + 1
            and current.end_date.month == prior.end_date.month
            and current.end_date.day == prior.end_date.day
        )
    if prior.period_kind is FundamentalMetricPeriodKind.QUARTERLY:
        prior_month = prior.end_date.year * 12 + prior.end_date.month
        current_month = current.end_date.year * 12 + current.end_date.month
        return current_month - prior_month == 3
    return False


def _growth_units_compatible(
    prior: NormalizedFundamentalMetric,
    current: NormalizedFundamentalMetric,
) -> bool:
    if prior.unit.value_kind is not current.unit.value_kind:
        return False
    currency_bearing = prior.unit.value_kind in {
        FundamentalValueKind.MONETARY,
        FundamentalValueKind.PER_SHARE,
    }
    return not currency_bearing or prior.unit.currency == current.unit.currency


def _base_value(metric: NormalizedFundamentalMetric) -> Decimal:
    if metric.value is None:
        raise ValueError("unavailable metric has no calculable value")
    return metric.value * metric.unit.scale


def _build_calculated_metric(
    *,
    formula_id: str,
    output_metric_id: str,
    inputs: tuple[NormalizedFundamentalMetric, ...],
    anchor: NormalizedFundamentalMetric,
    value: Decimal,
    unit: FundamentalMetricUnit,
    limitations: tuple[str, ...] = (),
) -> NormalizedFundamentalMetric:
    citations_by_key = {
        citation.citation_key: citation
        for metric in inputs
        for citation in metric.citations
    }
    quality_flags = tuple(
        sorted(
            {
                flag
                for metric in inputs
                for flag in metric.quality_flags
            },
            key=lambda item: item.value,
        )
    )
    return NormalizedFundamentalMetric(
        key=FundamentalMetricKey(
            metric_id=output_metric_id,
            reporting_basis=anchor.key.reporting_basis,
            period_key=anchor.period.period_key,
        ),
        period=anchor.period,
        value_status=(
            FundamentalMetricValueStatus.ZERO
            if value == 0
            else FundamentalMetricValueStatus.PRESENT
        ),
        value=value,
        unit=unit,
        source_values=(),
        normalization_method=FundamentalNormalizationMethod.CALCULATED,
        normalization_rule_id=formula_id,
        normalization_rule_version=DERIVED_FORMULA_POLICY_VERSION,
        calculation=FundamentalMetricCalculation(
            formula_id=formula_id,
            formula_version=DERIVED_FORMULA_POLICY_VERSION,
            input_metric_keys=tuple(
                metric.key.canonical_key for metric in inputs
            ),
        ),
        citations=tuple(
            citations_by_key[key] for key in sorted(citations_by_key)
        ),
        quality_flags=quality_flags,
        limitations=limitations,
    )


def _derived_issue(
    code: DerivedFundamentalMetricIssueCode,
    *,
    formula: _FormulaSpec,
    reporting_basis: FinancialReportingBasis,
    period_key: str,
    input_metric_keys: tuple[str, ...],
    detail: str,
) -> DerivedFundamentalMetricIssue:
    return DerivedFundamentalMetricIssue(
        code=code,
        formula_id=formula.formula_id,
        reporting_basis=reporting_basis,
        period_key=period_key,
        input_metric_keys=input_metric_keys,
        detail=detail,
    )


def _percent_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.PERCENTAGE,
        normalized_unit="PERCENT",
        scale=Decimal("0.01"),
    )


def _ratio_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.RATIO,
        normalized_unit="RATIO",
        scale=Decimal("1"),
    )


def _validate_source_binding(
    document: StructuredFinancialDocument,
    source: FundamentalMetricSourceBinding,
) -> None:
    expected_dataset = FundamentalPackageDataset(document.document_type.value)
    if source.package_item_key.dataset is not expected_dataset:
        raise ValueError("metric source dataset does not match the document")
    if source.package_item_key.reporting_basis is not document.reporting_basis:
        raise ValueError("metric source basis does not match the document")
    if source.content_fingerprint != document.document_fingerprint:
        raise ValueError("metric source fingerprint does not match the document")
    if (
        source.retrieved_at != document.retrieved_at
        or source.expires_at != document.expires_at
    ):
        raise ValueError("metric source freshness does not match the document")


def _row_unit_hint(
    row: FinancialDocumentRow,
    document: StructuredFinancialDocument,
) -> str:
    for cell in sorted(row.cells, key=lambda item: item.period_key):
        if cell.normalized_unit is not None:
            return cell.normalized_unit
    return document.source_unit


def _normalize_unit(
    *,
    value_kind: FundamentalValueKind,
    normalized_unit: str,
    currency: str | None,
    source_label: str,
    document_type: FinancialDocumentType,
) -> FundamentalMetricUnit:
    unit_key = _label_key(normalized_unit)
    label_key = _label_key(source_label)

    if value_kind is FundamentalValueKind.MONETARY:
        if currency is None:
            raise ValueError("monetary metric requires document currency")
        monetary_units = {
            "inr": ("INR", Decimal("1")),
            "inr crore": ("INR_CRORE", Decimal("10000000")),
            "rs cr": ("INR_CRORE", Decimal("10000000")),
            "rs crore": ("INR_CRORE", Decimal("10000000")),
            "crore": ("INR_CRORE", Decimal("10000000")),
            "in rs cr": ("INR_CRORE", Decimal("10000000")),
            "inr lakh": ("INR_LAKH", Decimal("100000")),
            "lakh": ("INR_LAKH", Decimal("100000")),
            "lac": ("INR_LAKH", Decimal("100000")),
            "inr million": ("INR_MILLION", Decimal("1000000")),
            "inr billion": ("INR_BILLION", Decimal("1000000000")),
        }
        if unit_key not in monetary_units:
            raise ValueError("unsupported monetary unit")
        canonical, scale = monetary_units[unit_key]
        return FundamentalMetricUnit(
            value_kind=value_kind,
            normalized_unit=canonical,
            scale=scale,
            currency=currency,
        )

    if value_kind is FundamentalValueKind.PER_SHARE:
        if currency is None or unit_key not in {
            "per share",
            "inr per share",
            "rs per share",
        }:
            raise ValueError("unsupported per-share unit")
        return FundamentalMetricUnit(
            value_kind=value_kind,
            normalized_unit=f"{currency}_PER_SHARE",
            scale=Decimal("1"),
            currency=currency,
        )

    if value_kind is FundamentalValueKind.PERCENTAGE and (
        unit_key in {"percent", "percentage"}
        or document_type is FinancialDocumentType.GROWTH_TABLE
    ):
        return FundamentalMetricUnit(
            value_kind=value_kind,
            normalized_unit="PERCENT",
            scale=Decimal("0.01"),
        )
    if value_kind is FundamentalValueKind.BASIS_POINTS and unit_key in {
        "basis points",
        "bps",
    }:
        return FundamentalMetricUnit(
            value_kind=value_kind,
            normalized_unit="BASIS_POINTS",
            scale=Decimal("0.0001"),
        )
    if value_kind is FundamentalValueKind.RATIO and unit_key in {
        "ratio",
        "multiple",
        "times",
    }:
        return FundamentalMetricUnit(
            value_kind=value_kind,
            normalized_unit="RATIO",
            scale=Decimal("1"),
        )
    if value_kind is FundamentalValueKind.COUNT:
        if "crore" in unit_key or "crs" in unit_key or "cr" in label_key:
            canonical, scale = "CRORE_COUNT", Decimal("10000000")
        elif "lakh" in unit_key or "lac" in unit_key:
            canonical, scale = "LAKH_COUNT", Decimal("100000")
        elif unit_key in {"count", "shares"}:
            canonical, scale = "COUNT", Decimal("1")
        else:
            raise ValueError("unsupported count unit")
        return FundamentalMetricUnit(
            value_kind=value_kind,
            normalized_unit=canonical,
            scale=scale,
        )
    if value_kind is FundamentalValueKind.OTHER and unit_key in {
        "day",
        "days",
    }:
        return FundamentalMetricUnit(
            value_kind=value_kind,
            normalized_unit="DAYS",
            scale=Decimal("1"),
        )
    raise ValueError("unsupported metric unit")


def _period_metadata(
    period: FinancialDocumentPeriod,
    *,
    document_type: FinancialDocumentType,
) -> FundamentalMetricPeriod:
    period_kind_by_source = {
        FundamentalPeriodType.ANNUAL: FundamentalMetricPeriodKind.ANNUAL,
        FundamentalPeriodType.QUARTERLY: FundamentalMetricPeriodKind.QUARTERLY,
        FundamentalPeriodType.LTM: FundamentalMetricPeriodKind.LTM,
        FundamentalPeriodType.YTD: FundamentalMetricPeriodKind.YTD,
    }
    forecast_types = {
        FundamentalPeriodType.FORECAST,
        FundamentalPeriodType.BUDGET,
        FundamentalPeriodType.PRO_FORMA,
        FundamentalPeriodType.SCENARIO,
    }
    is_forecast = period.period_type in forecast_types
    if period.period_type is None:
        if document_type is FinancialDocumentType.GROWTH_TABLE:
            period_kind = FundamentalMetricPeriodKind.HORIZON
        elif document_type is FinancialDocumentType.QUARTERLY_RESULTS:
            period_kind = FundamentalMetricPeriodKind.QUARTERLY
        else:
            period_kind = FundamentalMetricPeriodKind.ANNUAL
    else:
        period_kind = period_kind_by_source.get(
            period.period_type,
            FundamentalMetricPeriodKind.UNKNOWN,
        )
    period_namespace = (
        period.period_type.value
        if period.period_type is not None
        else period_kind.value
    )
    start_date, end_date = _period_dates(period, period_kind=period_kind)
    return FundamentalMetricPeriod(
        period_key=f"{period_namespace}:{period.period_key}",
        source_label=period.source_label,
        period_kind=period_kind,
        start_date=start_date,
        end_date=end_date,
        availability=FundamentalMetricAvailability.UNKNOWN,
        available_at=None,
        is_forecast=is_forecast,
    )


def _period_dates(
    period: FinancialDocumentPeriod,
    *,
    period_kind: FundamentalMetricPeriodKind,
) -> tuple[date | None, date | None]:
    if period.end_date is not None:
        return period.start_date, period.end_date
    if period_kind not in {
        FundamentalMetricPeriodKind.ANNUAL,
        FundamentalMetricPeriodKind.QUARTERLY,
    }:
        return period.start_date, period.end_date

    matched = _MONTH_PERIOD_LABEL.fullmatch(period.source_label.strip())
    if matched is None:
        return period.start_date, period.end_date
    month_by_abbreviation = {
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
    month = month_by_abbreviation.get(matched.group(1)[:3].casefold())
    if month is None:
        return period.start_date, period.end_date
    if period_kind is FundamentalMetricPeriodKind.ANNUAL and month != 3:
        return period.start_date, period.end_date
    if (
        period_kind is FundamentalMetricPeriodKind.QUARTERLY
        and month not in {3, 6, 9, 12}
    ):
        return period.start_date, period.end_date
    year_text = matched.group(2)
    year = int(year_text)
    if len(year_text) == 2:
        year += 2000 if year < 70 else 1900
    end_date = date(year, month, monthrange(year, month)[1])
    if period_kind is FundamentalMetricPeriodKind.ANNUAL:
        start_date = date(year - 1, 4, 1)
    else:
        start_month = month - 2
        start_date = date(year, start_month, 1)
    return start_date, end_date


def _citation(
    *,
    row: FinancialDocumentRow,
    cell: FinancialDocumentCell,
    source: FundamentalMetricSourceBinding,
) -> FundamentalMetricCitation:
    return FundamentalMetricCitation(
        package_item_key=source.package_item_key,
        source_reference_id=source.source_reference_id,
        content_fingerprint=source.content_fingerprint,
        source_path=("rows", row.row_key, "cells", cell.period_key),
        source_label=row.original_label,
        period_key=cell.period_key,
    )


def _normalize_cell(
    *,
    document: StructuredFinancialDocument,
    row: FinancialDocumentRow,
    cell: FinancialDocumentCell,
    period: FinancialDocumentPeriod,
    metric_id: str,
    unit: FundamentalMetricUnit,
    source: FundamentalMetricSourceBinding,
) -> tuple[
    NormalizedFundamentalMetric | None,
    FundamentalMetricNormalizationIssue | None,
]:
    citation = _citation(row=row, cell=cell, source=source)
    metric_period = _period_metadata(period, document_type=document.document_type)
    flags = [
        FundamentalMetricQualityFlag.SOURCE_AVAILABILITY_UNKNOWN,
        FundamentalMetricQualityFlag.PROVIDER_STANDARDIZED,
    ]
    if metric_period.is_forecast:
        flags.append(FundamentalMetricQualityFlag.FORECAST)

    source_values = (cell.source_value,) if cell.source_value is not None else ()
    limitation: tuple[str, ...] = ()
    value: Decimal | None = None
    issue: FundamentalMetricNormalizationIssue | None = None
    status: FundamentalMetricValueStatus

    if cell.availability_status is not FundamentalAvailabilityStatus.AVAILABLE:
        status = FundamentalMetricValueStatus.MISSING
        flags.append(FundamentalMetricQualityFlag.SOURCE_VALUE_MISSING)
        limitation = (
            f"Provider availability status: {cell.availability_status.value}.",
        )
    else:
        try:
            parsed = parse_indian_financial_number(cell.source_value or "")
        except ValueError:
            parsed = None
        if parsed is None or parsed != cell.normalized_value:
            status = FundamentalMetricValueStatus.PARSE_ERROR
            flags.append(FundamentalMetricQualityFlag.PARSE_FAILED)
            limitation = (
                "Source display value could not be reproduced exactly from the provider-normalized value.",
            )
            issue = FundamentalMetricNormalizationIssue(
                code=FundamentalMetricNormalizationIssueCode.SOURCE_VALUE_MISMATCH,
                row_key=row.row_key,
                period_key=cell.period_key,
                detail="The display value and provider-normalized decimal differ or are malformed.",
            )
        else:
            value = parsed
            status = (
                FundamentalMetricValueStatus.ZERO
                if parsed == 0
                else FundamentalMetricValueStatus.PRESENT
            )
            issue = None

    metric = NormalizedFundamentalMetric(
        key=FundamentalMetricKey(
            metric_id=metric_id,
            reporting_basis=document.reporting_basis,
            period_key=metric_period.period_key,
        ),
        period=metric_period,
        value_status=status,
        value=value,
        unit=unit,
        source_values=source_values,
        normalization_method=FundamentalNormalizationMethod.MAPPED,
        normalization_rule_id=(
            f"normalize.{document.document_type.value}.{metric_id}"
        ),
        normalization_rule_version=NORMALIZATION_RULE_VERSION,
        citations=(citation,),
        quality_flags=tuple(flags),
        limitations=limitation,
    )
    return metric, issue


def _resolve_duplicate(
    metrics: list[NormalizedFundamentalMetric],
) -> NormalizedFundamentalMetric | None:
    first = metrics[0]
    if any(
        metric.unit != first.unit or metric.period != first.period
        for metric in metrics[1:]
    ):
        return None

    citations = tuple(
        sorted(
            (citation for metric in metrics for citation in metric.citations),
            key=lambda item: item.citation_key,
        )
    )
    statuses_and_values = {
        (metric.value_status, metric.value) for metric in metrics
    }
    source_values = tuple(
        dict.fromkeys(
            source_value
            for metric in metrics
            for source_value in metric.source_values
        )
    )
    if len(statuses_and_values) == 1:
        return _rebuild_metric(
            first,
            citations=citations,
            source_values=source_values,
            limitations=tuple(
                dict.fromkeys(
                    (*first.limitations, "Equivalent duplicate source rows were retained as multiple citations.")
                )
            ),
        )
    if len(source_values) < 2:
        return None
    return _rebuild_metric(
        first,
        value_status=FundamentalMetricValueStatus.CONFLICT,
        value=None,
        source_values=source_values,
        citations=citations,
        quality_flags=tuple(
            dict.fromkeys(
                (*first.quality_flags, FundamentalMetricQualityFlag.DUPLICATE_CONFLICT)
            )
        ),
        limitations=(
            "Multiple source rows mapped to this canonical metric with different values.",
        ),
    )


def _rebuild_metric(
    metric: NormalizedFundamentalMetric,
    **updates: object,
) -> NormalizedFundamentalMetric:
    values = metric.model_dump(exclude_computed_fields=True)
    values.update(updates)
    return NormalizedFundamentalMetric.model_validate(values)
