from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pdfplumber

from statement_analyzer.classifiers.rules import (
    REVIEW_CONFIDENCE_THRESHOLD,
    RuleBasedClassifier,
    add_custom_category,
    inflow_categories,
    learn_rules_from_approved_transactions,
    outflow_categories,
)
from statement_analyzer.exporters.excel import ExcelExporter
from statement_analyzer.parsers.clear_junction import ClearJunctionStatementParser
from statement_analyzer.parsers.fcmb import FCMBStatementParser
from statement_analyzer.parsers.fidelity import FidelityStatementParser
from statement_analyzer.parsers.firstbank import FirstBankStatementParser
from statement_analyzer.parsers.customer_account_statement import CustomerAccountStatementParser
from statement_analyzer.parsers.generic import (
    ADAPTIVE_COLUMN_ROLES,
    AdaptiveDetectedColumn,
    AdaptiveReviewRequired,
    GenericStatementParser,
    build_preview_rows,
)
from statement_analyzer.parsers.globus import GlobusStatementParser
from statement_analyzer.parsers.gtbank import GTBankStatementParser
from statement_analyzer.parsers.jaiz import JaizStatementParser
from statement_analyzer.parsers.lotus import LotusStatementParser
from statement_analyzer.parsers.moniepoint import MoniepointStatementParser
from statement_analyzer.parsers.opay import OPayStatementParser
from statement_analyzer.parsers.posting_value_ledger import PostingValueLedgerStatementParser
from statement_analyzer.parsers.providus import ProvidusStatementParser
from statement_analyzer.parsers.registry import ParserRegistry
from statement_analyzer.parsers.standard_chartered import StandardCharteredStatementParser
from statement_analyzer.parsers.summary_details import SummaryDetailsStatementParser
from statement_analyzer.parsers.taj import TajStatementParser
from statement_analyzer.parsers.uba import UBAStatementParser
from statement_analyzer.parsers.pdf_utils import clear_pdf_password, is_password_error, open_pdf, set_pdf_password
from statement_analyzer.parsers.wema import WemaStatementParser
from statement_analyzer.parsers.wema_treasure import WemaTreasureStatementParser
from statement_analyzer.parsers.zenith import ZenithStyleParser
from statement_analyzer.pipeline import StatementPipeline
from statement_analyzer.models import ClassifiedTransaction, StatementAnalysis, TransactionDirection


@dataclass(slots=True)
class ReconciliationCheck:
    label: str
    expected: float | None
    actual: float | None
    matched: bool
    difference: float | None
    available: bool = True


@dataclass(slots=True)
class ReviewRow:
    transaction_index: int
    transaction_date: str | None
    description: str
    amount: float
    direction: str
    suggested_category: str | None
    selected_category: str | None
    confidence: float
    rule_name: str | None
    category_options: list[str]


@dataclass(slots=True)
class AdaptiveReviewRow:
    transaction_date: str | None
    description: str
    debit: str | None
    credit: str | None
    balance: str | None


@dataclass(slots=True)
class AdaptiveDetectedColumnSummary:
    index: int
    label: str
    semantic: str


@dataclass(slots=True)
class AdaptiveReviewSummary:
    parser_name: str
    page_count: int
    confidence: float
    reasons: list[str]
    detected_columns: list[AdaptiveDetectedColumnSummary]
    column_role_options: list[str]
    preview_rows: list[AdaptiveReviewRow]


@dataclass(slots=True)
class AnalysisSummary:
    parser_name: str
    page_count: int
    account_name: str | None
    account_number: str | None
    currency: str | None
    period_label: str | None
    opening_balance: float | None
    closing_balance: float | None
    net_movement: float
    total_transactions: int
    inflow_count: int
    outflow_count: int
    total_credit: float
    total_debit: float
    classified_inflow_total: float
    classified_outflow_total: float
    unclassified_inflow_total: float
    unclassified_outflow_total: float
    inflow_breakdown: list[tuple[str, float]]
    outflow_breakdown: list[tuple[str, float]]
    reconciliation_checks: list[ReconciliationCheck]
    available_check_count: int
    matched_check_count: int
    review_rows: list[ReviewRow]
    review_total_amount: float


@dataclass(slots=True)
class AnalysisResult:
    excel_path: Path
    summary: AnalysisSummary


class StatementAnalysisService:
    def __init__(self) -> None:
        self.dedicated_parsers = [
            ZenithStyleParser(),
            UBAStatementParser(),
            FCMBStatementParser(),
            ProvidusStatementParser(),
            FirstBankStatementParser(),
            FidelityStatementParser(),
            GTBankStatementParser(),
            WemaStatementParser(),
            OPayStatementParser(),
            GlobusStatementParser(),
            LotusStatementParser(),
            StandardCharteredStatementParser(),
            TajStatementParser(),
            JaizStatementParser(),
            CustomerAccountStatementParser(),
            SummaryDetailsStatementParser(),
            PostingValueLedgerStatementParser(),
            ClearJunctionStatementParser(),
            WemaTreasureStatementParser(),
            MoniepointStatementParser(),
        ]
        self.generic_parser = GenericStatementParser()
        self.registry = ParserRegistry([*self.dedicated_parsers, self.generic_parser])
        self.pipeline = StatementPipeline(
            parser_registry=self.registry,
            classifier=RuleBasedClassifier(),
        )
        self.exporter = ExcelExporter()

    def analyze(
        self,
        pdf_path: Path,
        output_path: Path,
        manual_classifications: dict[int, str] | None = None,
        remember_approvals: bool = False,
        allow_low_confidence_adaptive: bool = False,
        adaptive_column_overrides: dict[int, str] | None = None,
        pdf_password: str | None = None,
        training_bank_name: str | None = None,
    ) -> AnalysisResult:
        set_pdf_password(pdf_path, pdf_password)
        try:
            page_count = get_pdf_page_count(pdf_path)
            if training_bank_name:
                known_parser_name = self._known_parser_name_for(pdf_path)
                if known_parser_name:
                    raise ValueError(
                        f"This statement already matches the dedicated {known_parser_name} parser. "
                        "Use Analyze Statement for supported layouts instead of training a duplicate adaptive layout."
                    )
            try:
                analysis = self.pipeline.run(
                    pdf_path,
                    allow_low_confidence_adaptive=allow_low_confidence_adaptive,
                    adaptive_column_overrides=adaptive_column_overrides,
                    adaptive_save_template=False,
                    adaptive_template_name=training_bank_name,
                    adaptive_rename_existing_template=bool(training_bank_name),
                )
            except AdaptiveReviewRequired:
                raise
            except MemoryError as exc:
                raise ValueError(
                    "This PDF is too memory-intensive to finish in one pass right now. "
                    "Please split the statement into smaller date ranges and upload them separately."
                ) from exc
            manual_classifications = sanitize_manual_classifications(analysis, manual_classifications)
            self._validate_or_request_adaptive_review(analysis)
            if remember_approvals and manual_classifications:
                added_count = self._learn_from_manual_classifications(analysis, manual_classifications)
                if added_count:
                    self.pipeline.classifier = RuleBasedClassifier()
                    try:
                        analysis = self.pipeline.run(
                            pdf_path,
                            allow_low_confidence_adaptive=allow_low_confidence_adaptive,
                            adaptive_column_overrides=adaptive_column_overrides,
                            adaptive_save_template=False,
                            adaptive_template_name=training_bank_name,
                            adaptive_rename_existing_template=bool(training_bank_name),
                        )
                    except AdaptiveReviewRequired:
                        raise
                    except MemoryError as exc:
                        raise ValueError(
                            "The statement was parsed, but re-running after learning exhausted memory. "
                            "Try applying the correction without remembering it, or split the PDF into smaller ranges."
                        ) from exc
                    manual_classifications = sanitize_manual_classifications(analysis, manual_classifications)
                    self._validate_or_request_adaptive_review(analysis)
            self._save_adaptive_template_after_validation(
                analysis,
                training_bank_name=training_bank_name,
            )
            self.exporter.export(analysis, output_path, manual_classifications=manual_classifications)
        except Exception as exc:
            if is_password_error(exc):
                raise ValueError(
                    "This PDF is password-protected. Enter the PDF password and upload it again."
                ) from exc
            raise
        finally:
            clear_pdf_password(pdf_path)

        indexed_items = list(enumerate(analysis.classified_transactions))
        inflow_items = [
            (index, item)
            for index, item in indexed_items
            if item.transaction.direction == TransactionDirection.INFLOW
        ]
        outflow_items = [
            (index, item)
            for index, item in indexed_items
            if item.transaction.direction == TransactionDirection.OUTFLOW
        ]

        total_credit = sum(item.transaction.credit for _, item in inflow_items)
        total_debit = sum(item.transaction.debit for _, item in outflow_items)
        classified_inflow_total = sum(
            item.transaction.credit
            for index, item in inflow_items
            if applied_category(index, item, manual_classifications) != "Unclassified"
        )
        classified_outflow_total = sum(
            item.transaction.debit
            for index, item in outflow_items
            if applied_category(index, item, manual_classifications) != "Unclassified"
        )

        inflow_breakdown = [
            (
                category,
                float(
                    sum(
                        item.transaction.credit
                        for index, item in inflow_items
                        if applied_category(index, item, manual_classifications) == category
                    )
                ),
            )
            for category in inflow_categories()
        ]
        outflow_breakdown = [
            (
                category,
                float(
                    sum(
                        item.transaction.debit
                        for index, item in outflow_items
                        if applied_category(index, item, manual_classifications) == category
                    )
                ),
            )
            for category in outflow_categories()
        ]

        metadata = analysis.metadata
        explicit_opening_balance = next(
            (
                transaction.balance
                for transaction in analysis.all_transactions
                if "OPENING BALANCE" in transaction.description.upper() and transaction.balance is not None
            ),
            None,
        )
        parsed_opening_balance = (
            explicit_opening_balance
            if explicit_opening_balance is not None
            else metadata.opening_balance
            if metadata and metadata.opening_balance is not None
            else analysis.all_transactions[0].balance
            if analysis.all_transactions
            else None
        )
        parsed_closing_balance = next(
            (transaction.balance for transaction in reversed(analysis.all_transactions) if transaction.balance is not None),
            None,
        )
        reconciliation_checks = [
            self._build_check("Inflows Total", metadata.total_credit if metadata else None, total_credit),
            self._build_check("Outflows Total", metadata.total_debit if metadata else None, total_debit),
            self._build_check(
                "Opening Balance",
                metadata.opening_balance if metadata else None,
                parsed_opening_balance,
            ),
            self._build_check(
                "Closing Balance",
                metadata.closing_balance if metadata else None,
                parsed_closing_balance,
            ),
        ]

        review_items = sorted(
            (
                (index, item)
                for index, item in indexed_items
                if analysis.parser_name != "clear-junction"
                and needs_review(item)
                and index not in manual_classifications
            ),
            key=lambda entry: (entry[1].transaction.amount, entry[1].confidence),
            reverse=True,
        )
        review_rows = [
            ReviewRow(
                transaction_index=index,
                transaction_date=item.transaction.transaction_date.isoformat()
                if item.transaction.transaction_date
                else None,
                description=item.transaction.description,
                amount=float(item.transaction.amount),
                direction=item.transaction.direction.value,
                suggested_category=item.classification if item.classification != "Unclassified" else None,
                selected_category=manual_classifications.get(index),
                confidence=item.confidence,
                rule_name=item.rule_name,
                category_options=list(category_options_for(item.transaction.direction)),
            )
            for index, item in review_items
        ]

        available_check_count = sum(1 for check in reconciliation_checks if check.available)
        matched_check_count = sum(1 for check in reconciliation_checks if check.available and check.matched)
        review_total_amount = float(sum(item.transaction.amount for _, item in review_items))

        period_label = None
        if metadata and metadata.period_start and metadata.period_end:
            period_label = f"{metadata.period_start.isoformat()} to {metadata.period_end.isoformat()}"

        summary = AnalysisSummary(
            parser_name=analysis.parser_name or "unknown",
            page_count=page_count,
            account_name=metadata.account_name if metadata else None,
            account_number=metadata.account_number if metadata else None,
            currency=metadata.currency if metadata else None,
            period_label=period_label,
            opening_balance=float(metadata.opening_balance if metadata and metadata.opening_balance is not None else parsed_opening_balance)
            if (metadata and metadata.opening_balance is not None) or parsed_opening_balance is not None
            else None,
            closing_balance=float(metadata.closing_balance if metadata and metadata.closing_balance is not None else parsed_closing_balance)
            if (metadata and metadata.closing_balance is not None) or parsed_closing_balance is not None
            else None,
            net_movement=float(total_credit - total_debit),
            total_transactions=len(analysis.all_transactions),
            inflow_count=len(analysis.inflows),
            outflow_count=len(analysis.outflows),
            total_credit=float(total_credit),
            total_debit=float(total_debit),
            classified_inflow_total=float(classified_inflow_total),
            classified_outflow_total=float(classified_outflow_total),
            unclassified_inflow_total=float(total_credit - classified_inflow_total),
            unclassified_outflow_total=float(total_debit - classified_outflow_total),
            inflow_breakdown=inflow_breakdown,
            outflow_breakdown=outflow_breakdown,
            reconciliation_checks=reconciliation_checks,
            available_check_count=available_check_count,
            matched_check_count=matched_check_count,
            review_rows=review_rows,
            review_total_amount=review_total_amount,
        )
        return AnalysisResult(excel_path=output_path, summary=summary)

    def build_adaptive_review_summary(
        self,
        pdf_path: Path,
        review: AdaptiveReviewRequired,
        *,
        pdf_password: str | None = None,
    ) -> AdaptiveReviewSummary:
        set_pdf_password(pdf_path, pdf_password)
        try:
            page_count = get_pdf_page_count(pdf_path)
        finally:
            clear_pdf_password(pdf_path)
        return AdaptiveReviewSummary(
            parser_name=review.template_name,
            page_count=page_count,
            confidence=review.confidence,
            reasons=list(review.reasons),
            detected_columns=[
                AdaptiveDetectedColumnSummary(
                    index=column.index,
                    label=column.label,
                    semantic=column.semantic,
                )
                for column in review.detected_columns
            ],
            column_role_options=list(ADAPTIVE_COLUMN_ROLES),
            preview_rows=[
                AdaptiveReviewRow(
                    transaction_date=row.transaction_date,
                    description=row.description,
                    debit=row.debit,
                    credit=row.credit,
                    balance=row.balance,
                )
                for row in review.preview_rows
            ],
        )

    def _known_parser_name_for(self, pdf_path: Path) -> str | None:
        for parser in self.dedicated_parsers:
            try:
                if parser.can_parse(pdf_path):
                    return parser.bank_name
            except Exception as exc:
                if is_password_error(exc):
                    raise
                continue
        return None

    def _validate_or_request_adaptive_review(self, analysis: StatementAnalysis) -> None:
        try:
            guard_against_silent_zero_totals(analysis)
            guard_against_total_mismatch(analysis)
        except ValueError as exc:
            adaptive_review = self._adaptive_review_from_validation_failure(analysis, str(exc))
            if adaptive_review is not None:
                raise adaptive_review from exc
            raise

    def _adaptive_review_from_validation_failure(
        self,
        analysis: StatementAnalysis,
        reason: str,
    ) -> AdaptiveReviewRequired | None:
        parser = self.pipeline.last_parser
        if not isinstance(parser, GenericStatementParser) or parser.last_plan is None:
            return None

        assessment = parser.last_assessment
        reasons = [reason]
        if assessment is not None:
            reasons.extend(item for item in assessment.reasons if item not in reasons)

        columns = tuple(
            AdaptiveDetectedColumn(index=index, label=column.label, semantic=column.semantic)
            for index, column in enumerate(sorted(parser.last_plan.columns, key=lambda item: item.center))
        )
        if not columns:
            return None

        return AdaptiveReviewRequired(
            confidence=assessment.score if assessment is not None else 0.0,
            reasons=reasons,
            detected_columns=columns,
            preview_rows=build_preview_rows(analysis.all_transactions),
            template_name=analysis.parser_name or parser.bank_name,
        )

    def _save_adaptive_template_after_validation(
        self,
        analysis: StatementAnalysis,
        *,
        training_bank_name: str | None = None,
    ) -> None:
        parser = self.pipeline.last_parser
        if not isinstance(parser, GenericStatementParser):
            return

        learned_template = parser.save_last_template(
            preferred_name=training_bank_name,
            rename_existing=bool(training_bank_name),
        )
        if learned_template is None:
            return

        analysis.parser_name = learned_template.name
        for transaction in analysis.all_transactions:
            transaction.parser_name = learned_template.name

    def _learn_from_manual_classifications(
        self,
        analysis: StatementAnalysis,
        manual_classifications: dict[int, str],
    ) -> int:
        approved_items: list[tuple] = []
        for index, category in manual_classifications.items():
            if not isinstance(index, int) or not (0 <= index < len(analysis.classified_transactions)):
                continue
            approved_items.append((analysis.classified_transactions[index].transaction, category))
        account_name = analysis.metadata.account_name if analysis.metadata else None
        return learn_rules_from_approved_transactions(approved_items, account_name=account_name)

    def _build_check(
        self,
        label: str,
        expected: Decimal | None,
        actual: Decimal | None,
    ) -> ReconciliationCheck:
        if expected is None or actual is None:
            return ReconciliationCheck(
                label=label,
                expected=float(expected) if expected is not None else None,
                actual=float(actual) if actual is not None else None,
                matched=False,
                difference=None,
                available=False,
            )

        difference = actual - expected
        matched = abs(difference) <= Decimal("0.01")
        return ReconciliationCheck(
            label=label,
            expected=float(expected),
            actual=float(actual),
            matched=matched,
            difference=float(difference),
            available=True,
        )


def sanitize_manual_classifications(
    analysis: StatementAnalysis,
    manual_classifications: dict[int, str] | None,
) -> dict[int, str]:
    if not manual_classifications:
        return {}

    sanitized: dict[int, str] = {}
    for index, raw_category in manual_classifications.items():
        if not isinstance(index, int) or not (0 <= index < len(analysis.classified_transactions)):
            continue
        category = (raw_category or "").strip()
        if not category:
            continue
        item = analysis.classified_transactions[index]
        valid_categories = set(category_options_for(item.transaction.direction))
        if category in valid_categories or category == "Unclassified":
            sanitized[index] = category
    return sanitized


def needs_review(item: ClassifiedTransaction) -> bool:
    return (
        item.transaction.direction != TransactionDirection.UNKNOWN
        and (item.classification == "Unclassified" or item.confidence < REVIEW_CONFIDENCE_THRESHOLD)
    )


def guard_against_silent_zero_totals(analysis: StatementAnalysis) -> None:
    metadata = analysis.metadata
    if metadata is None:
        return

    actual_credit = sum(
        item.credit
        for item in analysis.all_transactions
        if item.direction == TransactionDirection.INFLOW
    )
    actual_debit = sum(
        item.debit
        for item in analysis.all_transactions
        if item.direction == TransactionDirection.OUTFLOW
    )

    failed_totals: list[str] = []
    if metadata.total_credit is not None and metadata.total_credit > Decimal("0") and actual_credit == Decimal("0"):
        failed_totals.append("credits")
    if metadata.total_debit is not None and metadata.total_debit > Decimal("0") and actual_debit == Decimal("0"):
        failed_totals.append("debits")

    if failed_totals:
        totals = " and ".join(failed_totals)
        raise ValueError(
            f"The statement reports {totals}, but no {totals} were extracted from the transaction table. "
            "No workbook was generated because that would produce misleading zero totals. "
            "Please upload a text-based PDF export or use a supported layout variant."
        )


def guard_against_total_mismatch(analysis: StatementAnalysis, *, tolerance: Decimal = Decimal("0.01")) -> None:
    metadata = analysis.metadata
    if metadata is None:
        return

    actual_credit = sum(
        item.credit
        for item in analysis.all_transactions
        if item.direction == TransactionDirection.INFLOW
    )
    actual_debit = sum(
        item.debit
        for item in analysis.all_transactions
        if item.direction == TransactionDirection.OUTFLOW
    )

    failed_totals: list[str] = []
    if (
        metadata.total_credit is not None
        and metadata.total_credit > Decimal("0")
        and abs(actual_credit - metadata.total_credit) > tolerance
    ):
        failed_totals.append(
            f"credits expected {metadata.total_credit:,.2f} but extracted {actual_credit:,.2f}"
        )
    if (
        metadata.total_debit is not None
        and metadata.total_debit > Decimal("0")
        and abs(actual_debit - metadata.total_debit) > tolerance
    ):
        failed_totals.append(
            f"debits expected {metadata.total_debit:,.2f} but extracted {actual_debit:,.2f}"
        )

    if failed_totals:
        raise ValueError(
            "The extracted transaction totals do not match the statement summary: "
            + "; ".join(failed_totals)
            + ". No workbook was generated because that would produce misleading totals."
        )


def category_options_for(direction: TransactionDirection) -> list[str]:
    if direction == TransactionDirection.INFLOW:
        return inflow_categories()
    if direction == TransactionDirection.OUTFLOW:
        return outflow_categories()
    return []


def save_custom_category_option(direction: str, category_name: str) -> str | None:
    return add_custom_category(direction, category_name)


def applied_category(
    index: int,
    item: ClassifiedTransaction,
    manual_classifications: dict[int, str] | None,
) -> str:
    if manual_classifications and index in manual_classifications:
        return manual_classifications[index]
    return item.classification


def get_pdf_page_count(pdf_path: Path) -> int:
    with open_pdf(pdf_path) as pdf:
        return len(pdf.pages)
