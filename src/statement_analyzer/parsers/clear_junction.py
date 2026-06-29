from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import open_pdf


DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
PERIOD_RE = re.compile(
    r"Period:\s*(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}\s*-\s*"
    r"(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}",
    flags=re.IGNORECASE,
)


class ClearJunctionStatementParser(StatementParser):
    bank_name = "clear-junction"

    def can_parse(self, pdf_path: Path) -> bool:
        with open_pdf(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages[:2]).upper()

        required_terms = (
            "CLEAR JUNCTION LIMITED",
            "BIC: CLJUGB21",
            "ACCOUNT STATEMENT",
            "OPER. DATE",
            "ORDER NUMBER",
            "PAYER ACCOUNT",
            "PAYEE ACCOUNT",
            "TRANSACTION",
            "FEE",
            "CROSS",
            "SCHEME",
        )
        return all(term in text for term in required_terms)

    def parse(self, pdf_path: Path) -> list[Transaction]:
        metadata = self._extract_metadata(pdf_path)
        transactions: list[Transaction] = []
        running_balance = metadata.opening_balance

        if metadata.opening_balance is not None:
            transactions.append(
                Transaction(
                    transaction_date=None,
                    description="Opening Balance",
                    debit=Decimal("0"),
                    credit=Decimal("0"),
                    balance=metadata.opening_balance,
                    currency=metadata.currency or "EUR",
                    raw_text="Opening Balance",
                    source_page=1,
                    parser_name=self.bank_name,
                )
            )

        with open_pdf(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for table in page.extract_tables():
                    if not table or not self._is_transaction_table(table):
                        continue
                    for row in table[1:]:
                        transaction = self._parse_table_row(
                            row,
                            page_number=page_number,
                            currency=metadata.currency or "EUR",
                            running_balance=running_balance,
                        )
                        if transaction is None:
                            continue
                        transactions.append(transaction)
                        if transaction.balance is not None:
                            running_balance = transaction.balance

        transaction_rows = [
            transaction
            for transaction in transactions
            if transaction.parser_name == self.bank_name and transaction.transaction_date is not None
        ]
        metadata.total_credit = sum((transaction.credit for transaction in transaction_rows), Decimal("0"))
        metadata.total_debit = sum((transaction.debit for transaction in transaction_rows), Decimal("0"))
        if metadata.closing_balance is None:
            metadata.closing_balance = running_balance
        self.last_metadata = metadata
        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
            last_page_text = pdf.pages[-1].extract_text() or ""

        period_match = PERIOD_RE.search(first_page_text)
        return StatementMetadata(
            account_name=extract_regex(first_page_text, r"Company:\s*(.+?)\s+Period:"),
            account_number=extract_regex(first_page_text, r"\(IBAN:\s*([^)]+)\)"),
            currency=extract_regex(first_page_text, r"Currency:\s*([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"Opening balance:\s*([-\d,]+\.\d{2})")),
            closing_balance=parse_decimal(extract_regex(last_page_text, r"Closing balance:\s*([-\d,]+\.\d{2})")),
            period_start=parse_date(period_match.group(1)) if period_match else None,
            period_end=parse_date(period_match.group(2)) if period_match else None,
        )

    def _is_transaction_table(self, table: list[list[str | None]]) -> bool:
        header = " ".join(clean_cell(cell).upper() for cell in table[0])
        required_headers = (
            "OPER. DATE",
            "ORDER NUMBER",
            "PAYER NAME",
            "PAYER ACCOUNT",
            "PAYEE NAME",
            "PAYEE ACCOUNT",
            "DESCRIPTION",
            "AMOUNT",
            "TRANSACTION FEE",
            "CROSS SCHEME",
        )
        return all(header_name in header for header_name in required_headers)

    def _parse_table_row(
        self,
        row: list[str | None],
        *,
        page_number: int,
        currency: str,
        running_balance: Decimal | None,
    ) -> Transaction | None:
        if len(row) < 9:
            return None

        transaction_date = parse_date(clean_cell(row[0]))
        if transaction_date is None:
            return None

        signed_amount = parse_decimal(clean_cell(row[7]))
        if signed_amount is None:
            return None

        transaction_fee = abs(parse_decimal(clean_cell(row[8])) or Decimal("0"))
        debit = abs(signed_amount) if signed_amount < 0 else Decimal("0")
        credit = signed_amount if signed_amount > 0 else Decimal("0")
        balance = None
        if running_balance is not None:
            balance = running_balance + credit - debit - transaction_fee

        payer_name = clean_cell(row[2])
        payee_name = clean_cell(row[4])
        detail = clean_cell(row[6])
        reference = clean_cell(row[1]) or None
        description = build_description(detail, payer_name, payee_name)

        return Transaction(
            transaction_date=transaction_date,
            description=description,
            debit=debit,
            credit=credit,
            balance=balance,
            reference=reference,
            currency=currency,
            transaction_fee=transaction_fee,
            raw_text=clean_cell(" ".join(cell or "" for cell in row)),
            source_page=page_number,
            parser_name=self.bank_name,
        )


def clean_cell(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\n", " ")).strip()


def build_description(detail: str, payer_name: str, payee_name: str) -> str:
    parts = []
    if detail:
        parts.append(detail)
    if payer_name:
        parts.append(f"Payer: {payer_name}")
    if payee_name:
        parts.append(f"Payee: {payee_name}")
    return " | ".join(parts) or "Unlabeled Transaction"


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return clean_cell(match.group(1)) if match else None


def parse_date(value: str | None):
    cleaned = clean_cell(value)
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_decimal(value: str | None) -> Decimal | None:
    cleaned = clean_cell(value).replace(",", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
