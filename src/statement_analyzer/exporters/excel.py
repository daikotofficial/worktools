from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from statement_analyzer.classifiers.rules import REVIEW_CONFIDENCE_THRESHOLD, inflow_categories, outflow_categories
from statement_analyzer.models import ClassifiedTransaction, StatementAnalysis, TransactionDirection


class ExcelExporter:
    def export(
        self,
        analysis: StatementAnalysis,
        output_path: Path,
        manual_classifications: dict[int, str] | None = None,
    ) -> None:
        overrides = sanitize_manual_classifications(analysis, manual_classifications)
        workbook = xlsxwriter.Workbook(output_path)

        header_format = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1, "align": "center"})
        money_format = workbook.add_format({"num_format": "#,##0.00"})
        date_format = workbook.add_format({"num_format": "dd/mm/yyyy"})
        if analysis.parser_name == "clear-junction":
            self._write_clear_junction_workbook(workbook, analysis, header_format, money_format, date_format)
            workbook.close()
            return

        label_format = workbook.add_format({"bold": True})
        title_format = workbook.add_format({"bold": True, "font_size": 14})
        good_format = workbook.add_format({"bg_color": "#DBF4E6", "font_color": "#0F5C2B"})
        warn_format = workbook.add_format({"bg_color": "#FFF0D9", "font_color": "#8A4B08"})
        note_format = workbook.add_format({"italic": True, "font_color": "#5F6B73"})

        self._write_main_sheet(workbook, analysis, header_format, money_format, date_format)
        self._write_inflows_sheet(
            workbook,
            analysis,
            overrides,
            header_format,
            money_format,
            date_format,
        )
        self._write_outflows_sheet(
            workbook,
            analysis,
            overrides,
            header_format,
            money_format,
            date_format,
        )
        self._write_analysis_sheet(
            workbook,
            analysis,
            overrides,
            title_format,
            header_format,
            money_format,
            label_format,
            good_format,
            warn_format,
            note_format,
        )
        workbook.close()

    def _write_main_sheet(self, workbook, analysis, header_format, money_format, date_format) -> None:
        sheet = workbook.add_worksheet("Main")
        headers = ["DATE", "DESCRIPTION", "DEBIT", "CREDIT", "BALANCE"]
        self._write_header(sheet, headers, header_format)

        row_index = 1
        metadata = analysis.metadata
        first_transaction = analysis.all_transactions[0] if analysis.all_transactions else None
        first_is_opening_balance = bool(
            first_transaction
            and "OPENING BALANCE" in (first_transaction.description or "").upper()
        )
        if metadata and metadata.opening_balance is not None and not first_is_opening_balance:
            sheet.write(row_index, 1, "Opening Balance")
            sheet.write_number(row_index, 2, 0, money_format)
            sheet.write_number(row_index, 3, 0, money_format)
            sheet.write_number(row_index, 4, float(metadata.opening_balance), money_format)
            row_index += 1

        for transaction in analysis.all_transactions:
            if transaction.transaction_date:
                sheet.write_datetime(row_index, 0, as_datetime(transaction.transaction_date), date_format)
            sheet.write(row_index, 1, transaction.description)
            sheet.write_number(row_index, 2, float(transaction.debit), money_format)
            sheet.write_number(row_index, 3, float(transaction.credit), money_format)
            if transaction.balance is not None:
                sheet.write_number(row_index, 4, float(transaction.balance), money_format)
            row_index += 1

        sheet.freeze_panes(1, 0)
        sheet.set_column("A:A", 14)
        sheet.set_column("B:B", 96)
        sheet.set_column("C:E", 16)

    def _write_clear_junction_workbook(
        self,
        workbook,
        analysis,
        header_format,
        money_format,
        date_format,
    ) -> None:
        transaction_items = [
            item.transaction
            for item in analysis.classified_transactions
            if item.transaction.direction in {TransactionDirection.INFLOW, TransactionDirection.OUTFLOW}
        ]
        inflow_items = [
            transaction
            for transaction in transaction_items
            if transaction.direction == TransactionDirection.INFLOW
        ]
        outflow_items = [
            transaction
            for transaction in transaction_items
            if transaction.direction == TransactionDirection.OUTFLOW
        ]

        self._write_clear_junction_sheet(
            workbook,
            "Transactions",
            transaction_items,
            header_format,
            money_format,
            date_format,
        )
        self._write_clear_junction_sheet(
            workbook,
            "Inflows",
            inflow_items,
            header_format,
            money_format,
            date_format,
        )
        self._write_clear_junction_sheet(
            workbook,
            "Outflows",
            outflow_items,
            header_format,
            money_format,
            date_format,
        )

    def _write_clear_junction_sheet(
        self,
        workbook,
        sheet_name: str,
        transactions,
        header_format,
        money_format,
        date_format,
    ) -> None:
        sheet = workbook.add_worksheet(sheet_name)
        headers = ["DATE", "DESCRIPTION", "INFLOW", "OUTFLOW", "TRANSACTION FEE"]
        self._write_header(sheet, headers, header_format)

        start_row = 2
        for row_index, transaction in enumerate(transactions, start=start_row):
            if transaction.transaction_date:
                sheet.write_datetime(row_index, 0, as_datetime(transaction.transaction_date), date_format)
            sheet.write(row_index, 1, transaction.description)
            sheet.write_number(row_index, 2, float(transaction.credit), money_format)
            sheet.write_number(row_index, 3, float(transaction.debit), money_format)
            sheet.write_number(row_index, 4, float(transaction.transaction_fee), money_format)

        if transactions:
            sheet.write(1, 1, "TOTAL")
            for col_index in range(2, 5):
                self._write_totals_row(sheet, 1, start_row, len(transactions), col_index, money_format)

        sheet.freeze_panes(start_row, 0)
        sheet.set_column("A:A", 14)
        sheet.set_column("B:B", 96)
        sheet.set_column("C:E", 18)

    def _write_inflows_sheet(
        self,
        workbook,
        analysis,
        overrides,
        header_format,
        money_format,
        date_format,
    ) -> None:
        categories = inflow_categories()
        sheet = workbook.add_worksheet("Inflows")
        headers = ["DATE", "DESCRIPTION", "DEBIT", "CREDIT", "CLASSIFICATION", *[category.upper() for category in categories]]
        self._write_header(sheet, headers, header_format)

        items = [
            (index, item)
            for index, item in enumerate(analysis.classified_transactions)
            if item.transaction.direction == TransactionDirection.INFLOW
        ]
        start_row = 2
        for row_index, (index, item) in enumerate(items, start=start_row):
            tx = item.transaction
            category = applied_category(index, item, overrides)
            if tx.transaction_date:
                sheet.write_datetime(row_index, 0, as_datetime(tx.transaction_date), date_format)
            sheet.write(row_index, 1, tx.description)
            sheet.write_number(row_index, 2, float(tx.debit), money_format)
            sheet.write_number(row_index, 3, float(tx.credit), money_format)
            sheet.write(row_index, 4, category)

            for offset, bucket in enumerate(categories, start=5):
                if category == bucket:
                    sheet.write_number(row_index, offset, float(tx.credit), money_format)

        for col_index in range(2, 5 + len(categories)):
            self._write_totals_row(sheet, 1, start_row, len(items), col_index, money_format)

        sheet.freeze_panes(start_row, 0)
        sheet.set_column("A:A", 14)
        sheet.set_column("B:B", 96)
        sheet.set_column("C:D", 16)
        sheet.set_column("E:E", 22)
        sheet.set_column("F:AZ", 20)

    def _write_outflows_sheet(
        self,
        workbook,
        analysis,
        overrides,
        header_format,
        money_format,
        date_format,
    ) -> None:
        categories = outflow_categories()
        sheet = workbook.add_worksheet("Outflows")
        headers = ["DATE", "DESCRIPTION", "DEBIT", "CONFIRM", "DIFF", "CLASSIFICATION", *[category.upper() for category in categories]]
        self._write_header(sheet, headers, header_format)

        items = [
            (index, item)
            for index, item in enumerate(analysis.classified_transactions)
            if item.transaction.direction == TransactionDirection.OUTFLOW
        ]
        start_row = 2
        for row_index, (index, item) in enumerate(items, start=start_row):
            tx = item.transaction
            category = applied_category(index, item, overrides)
            confirmed_amount = tx.debit if category != "Unclassified" else Decimal("0")
            difference = tx.debit - confirmed_amount
            if tx.transaction_date:
                sheet.write_datetime(row_index, 0, as_datetime(tx.transaction_date), date_format)
            sheet.write(row_index, 1, tx.description)
            sheet.write_number(row_index, 2, float(tx.debit), money_format)
            sheet.write_number(row_index, 3, float(confirmed_amount), money_format)
            sheet.write_number(row_index, 4, float(difference), money_format)
            sheet.write(row_index, 5, category)

            for offset, bucket in enumerate(categories, start=6):
                if category == bucket:
                    sheet.write_number(row_index, offset, float(tx.debit), money_format)

        for col_index in range(2, 6 + len(categories)):
            self._write_totals_row(sheet, 1, start_row, len(items), col_index, money_format)

        sheet.freeze_panes(start_row, 0)
        sheet.set_column("A:A", 14)
        sheet.set_column("B:B", 96)
        sheet.set_column("C:E", 16)
        sheet.set_column("F:F", 22)
        sheet.set_column("G:AZ", 20)

    def _write_analysis_sheet(
        self,
        workbook,
        analysis,
        overrides,
        title_format,
        header_format,
        money_format,
        label_format,
        good_format,
        warn_format,
        note_format,
    ) -> None:
        sheet = workbook.add_worksheet("Analysis")
        metadata = analysis.metadata
        row = 0

        sheet.write(row, 0, "Statement Analysis", title_format)
        row += 2
        sheet.write(row, 0, "Account Name", label_format)
        sheet.write(row, 1, metadata.account_name if metadata else "")
        row += 1
        sheet.write(row, 0, "Account Number", label_format)
        sheet.write(row, 1, metadata.account_number if metadata else "")
        row += 1
        sheet.write(row, 0, "Currency", label_format)
        sheet.write(row, 1, metadata.currency if metadata else "")
        row += 1
        sheet.write(row, 0, "Parser", label_format)
        sheet.write(row, 1, analysis.parser_name or "")
        row += 1
        sheet.write(row, 0, "Period", label_format)
        if metadata and metadata.period_start and metadata.period_end:
            sheet.write(row, 1, f"{metadata.period_start.isoformat()} to {metadata.period_end.isoformat()}")
        row += 1
        sheet.write(row, 0, "Classification Coverage", label_format)
        unresolved_count = sum(
            1
            for index, item in enumerate(analysis.classified_transactions)
            if needs_review(item) and index not in overrides
        )
        sheet.write(row, 1, f"{len(analysis.classified_transactions) - unresolved_count}/{len(analysis.classified_transactions)} rows resolved")
        row += 1
        sheet.write(row, 0, "Review Note", label_format)
        sheet.write(row, 1, "Review stays inside the app. This workbook contains the cleaned final output.", note_format)
        row += 2

        checks = [
            self._build_check(
                "Inflows Total",
                metadata.total_credit if metadata else None,
                sum(item.transaction.credit for item in analysis.inflows),
            ),
            self._build_check(
                "Outflows Total",
                metadata.total_debit if metadata else None,
                sum(item.transaction.debit for item in analysis.outflows),
            ),
            self._build_check(
                "Opening Balance",
                metadata.opening_balance if metadata else None,
                analysis.all_transactions[0].balance if analysis.all_transactions else None,
            ),
            self._build_check(
                "Closing Balance",
                metadata.closing_balance if metadata else None,
                next(
                    (transaction.balance for transaction in reversed(analysis.all_transactions) if transaction.balance is not None),
                    None,
                ),
            ),
        ]

        self._write_header(sheet, ["CHECK", "STATUS", "EXPECTED", "ACTUAL", "DIFFERENCE"], header_format, row)
        row += 1
        for check in checks:
            status = "Unavailable"
            status_format = warn_format
            if check["available"]:
                status = "Matched" if check["matched"] else "Check"
                status_format = good_format if check["matched"] else warn_format
            sheet.write(row, 0, check["label"])
            sheet.write(row, 1, status, status_format)
            if check["expected"] is not None:
                sheet.write_number(row, 2, check["expected"], money_format)
            if check["actual"] is not None:
                sheet.write_number(row, 3, check["actual"], money_format)
            if check["difference"] is not None:
                sheet.write_number(row, 4, check["difference"], money_format)
            row += 1

        row += 1
        self._write_header(sheet, ["SECTION", "CATEGORY", "TOTAL"], header_format, row)
        row += 1

        for offset, category in enumerate(inflow_categories(), start=5):
            sheet.write(row, 0, "Inflows")
            sheet.write(row, 1, category)
            sheet.write_formula(row, 2, column_sum_formula("Inflows", offset), money_format)
            row += 1

        for offset, category in enumerate(outflow_categories(), start=6):
            sheet.write(row, 0, "Outflows")
            sheet.write(row, 1, category)
            sheet.write_formula(row, 2, column_sum_formula("Outflows", offset), money_format)
            row += 1

        sheet.set_column("A:A", 18)
        sheet.set_column("B:B", 30)
        sheet.set_column("C:E", 18)

    def _build_check(self, label: str, expected: Decimal | None, actual: Decimal | None) -> dict[str, object]:
        if expected is None or actual is None:
            return {
                "label": label,
                "expected": float(expected) if expected is not None else None,
                "actual": float(actual) if actual is not None else None,
                "difference": None,
                "matched": False,
                "available": False,
            }

        difference = actual - expected
        return {
            "label": label,
            "expected": float(expected),
            "actual": float(actual),
            "difference": float(difference),
            "matched": abs(difference) <= Decimal("0.01"),
            "available": True,
        }

    def _write_header(self, sheet, headers, header_format, row_index: int = 0) -> None:
        for col_index, header in enumerate(headers):
            sheet.write(row_index, col_index, header, header_format)

    def _write_totals_row(
        self,
        sheet,
        totals_row: int,
        data_start_row: int,
        item_count: int,
        column: int,
        money_format,
    ) -> None:
        if item_count <= 0:
            return
        start = data_start_row + 1
        end = data_start_row + item_count
        col_name = xl_col_to_name(column)
        sheet.write_formula(totals_row, column, f"=SUM({col_name}{start}:{col_name}{end})", money_format)


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
        valid_categories = set(category_options_for(item))
        if category in valid_categories or category == "Unclassified":
            sanitized[index] = category
    return sanitized


def category_options_for(item: ClassifiedTransaction) -> list[str]:
    if item.transaction.direction == TransactionDirection.INFLOW:
        return inflow_categories()
    if item.transaction.direction == TransactionDirection.OUTFLOW:
        return outflow_categories()
    return []


def needs_review(item: ClassifiedTransaction) -> bool:
    return (
        item.transaction.direction != TransactionDirection.UNKNOWN
        and (item.classification == "Unclassified" or item.confidence < REVIEW_CONFIDENCE_THRESHOLD)
    )


def applied_category(index: int, item: ClassifiedTransaction, manual_classifications: dict[int, str] | None) -> str:
    if manual_classifications and index in manual_classifications:
        return manual_classifications[index]
    return item.classification


def column_sum_formula(sheet_name: str, column: int) -> str:
    return f"=SUM({sheet_name}!{xl_col_to_name(column)}:{xl_col_to_name(column)})"


def as_datetime(value):
    return datetime.combine(value, datetime.min.time())
