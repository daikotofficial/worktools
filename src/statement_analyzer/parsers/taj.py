from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_analyzer.layouts import detect_layout
from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import open_pdf


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    trans_date: str = ""
    value_date: str = ""
    branch: str = ""
    details: str = ""
    reference: str = ""
    deposit: str = ""
    withdrawal: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.trans_date)

    def is_header(self) -> bool:
        text = " ".join(
            part for part in (self.trans_date, self.value_date, self.branch, self.details) if part
        ).upper()
        return "TRANS DATE" in text and "VALUE DATE" in text and "TRANSACTION DETAILS" in text

    def is_opening_balance(self) -> bool:
        text = " ".join(part for part in (self.trans_date, self.value_date, self.details) if part).upper()
        return "BALANCE BROUGHT FORWARD" in text

    def is_end_marker(self) -> bool:
        text = " ".join(part for part in (self.trans_date, self.value_date, self.details) if part).upper()
        return "END OF STATEMENT" in text


class TajStatementParser(StatementParser):
    bank_name = "taj"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "taj_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        transactions: list[Transaction] = []
        opening_balance_added = False
        previous_balance = self.last_metadata.opening_balance if self.last_metadata else None

        for row in rows:
            if row.is_header():
                continue

            if row.is_opening_balance() and not opening_balance_added:
                opening_balance = parse_decimal(row.balance)
                if opening_balance is None:
                    if self.last_metadata and self.last_metadata.opening_balance is not None:
                        opening_balance = self.last_metadata.opening_balance
                    else:
                        opening_balance = Decimal("0")
                transactions.append(
                    Transaction(
                        transaction_date=None,
                        description="Opening Balance",
                        debit=Decimal("0"),
                        credit=Decimal("0"),
                        balance=opening_balance,
                        reference=None,
                        currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                        raw_text="Opening Balance",
                        source_page=row.page_number,
                        parser_name=self.bank_name,
                    )
                )
                opening_balance_added = True
                previous_balance = opening_balance
                continue

            if row.is_end_marker() or not row.has_date:
                continue

            description = clean_text(row.details)
            balance = parse_decimal(row.balance)
            reference = clean_text(row.reference) or extract_reference(description)
            debit, credit, reference = resolve_amounts(
                previous_balance=previous_balance,
                balance=balance,
                reference=reference,
                deposit_text=row.deposit,
                withdrawal_text=row.withdrawal,
            )

            transaction = Transaction(
                transaction_date=parse_date(row.trans_date),
                description=description,
                debit=debit,
                credit=credit,
                balance=balance,
                reference=reference or None,
                currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                raw_text=clean_text(
                    " ".join(
                        part
                        for part in (
                            row.trans_date,
                            row.value_date,
                            row.branch,
                            description,
                            reference or "",
                            row.deposit,
                            row.withdrawal,
                            row.balance,
                        )
                        if part
                    )
                ),
                source_page=row.page_number,
                parser_name=self.bank_name,
            )
            transactions.append(transaction)
            if transaction.balance is not None:
                previous_balance = transaction.balance

        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""

        cycle_match = re.search(
            r"Statement Cycle:\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
            first_page_text,
            flags=re.IGNORECASE,
        )
        opening_balance = parse_decimal(extract_regex(first_page_text, r"Opening Balance\s+([0-9,]+\.\d{2})"))
        total_debit = parse_decimal(extract_regex(first_page_text, r"Total Debit\s+([0-9,]+\.\d{2})"))
        total_credit = parse_decimal(extract_regex(first_page_text, r"Total Credit\s+([0-9,]+\.\d{2})"))
        closing_balance = parse_decimal(extract_regex(first_page_text, r"Trans Balance\s+([0-9,]+\.\d{2})"))

        # TAJ's statement summary rolls the opening balance into "Total Credit".
        # Normalize it back to transaction-only inflows so reconciliation stays comparable across banks.
        if (
            opening_balance is not None
            and total_credit is not None
            and total_debit is not None
            and closing_balance is not None
            and abs((total_credit - total_debit) - closing_balance) <= Decimal("0.01")
        ):
            total_credit -= opening_balance

        return StatementMetadata(
            account_name=extract_regex(first_page_text, r"^(.*?)\s+Account Number\s+\d+"),
            account_number=extract_regex(first_page_text, r"Account Number\s+(\d+)"),
            currency=extract_regex(first_page_text, r"Currency\s+([A-Z]{3})"),
            opening_balance=opening_balance,
            total_debit=total_debit,
            total_credit=total_credit,
            closing_balance=closing_balance,
            period_start=parse_period_date(cycle_match.group(1)) if cycle_match else None,
            period_end=parse_period_date(cycle_match.group(2)) if cycle_match else None,
        )

    def _extract_rows(self, pdf_path: Path) -> list[ParsedRow]:
        rows: list[ParsedRow] = []
        with open_pdf(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                rows.extend(self._extract_page_rows(page, page_number))
        return rows

    def _extract_page_rows(self, page: pdfplumber.page.Page, page_number: int) -> list[ParsedRow]:
        words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
        if not words:
            return []

        grouped: list[list[dict]] = []
        current_group: list[dict] = []
        current_top: float | None = None

        for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
            top = float(word["top"])
            if current_top is None or abs(top - current_top) <= 2.8:
                current_group.append(word)
                current_top = top if current_top is None else (current_top + top) / 2
            else:
                grouped.append(current_group)
                current_group = [word]
                current_top = top

        if current_group:
            grouped.append(current_group)

        rows: list[ParsedRow] = []
        for group in grouped:
            row = ParsedRow(page_number=page_number, top=min(float(word["top"]) for word in group))
            columns = {
                "trans_date": [],
                "value_date": [],
                "branch": [],
                "details": [],
                "reference": [],
                "deposit": [],
                "withdrawal": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 70:
                    columns["trans_date"].append(text)
                elif x0 < 126:
                    columns["value_date"].append(text)
                elif x0 < 170:
                    columns["branch"].append(text)
                elif x0 < 342:
                    columns["details"].append(text)
                elif x0 < 399:
                    columns["reference"].append(text)
                elif x0 < 456:
                    columns["deposit"].append(text)
                elif x0 < 513:
                    columns["withdrawal"].append(text)
                else:
                    columns["balance"].append(text)

            row.trans_date = " ".join(columns["trans_date"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.branch = " ".join(columns["branch"]).strip()
            row.details = " ".join(columns["details"]).strip()
            row.reference = " ".join(columns["reference"]).strip()
            row.deposit = " ".join(columns["deposit"]).strip()
            row.withdrawal = " ".join(columns["withdrawal"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part
                for part in (
                    row.trans_date,
                    row.value_date,
                    row.branch,
                    row.details,
                    row.reference,
                    row.deposit,
                    row.withdrawal,
                    row.balance,
                )
                if part
            ).upper()
            if not text_blob:
                continue
            if any(
                term in text_blob
                for term in (
                    "TAJ CORP CURRENT",
                    "STATEMENT OF ACCOUNT -",
                    "AVAILABLE BALANCE",
                    "YOU MUST ADVISE TAJBANK",
                    "TERMS AND CONDITIONS",
                    "PAGE ",
                )
            ):
                continue
            rows.append(row)

        return rows


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return clean_text(match.group(1)) if match else None


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def parse_date(value: str):
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if not cleaned or not any(character.isdigit() for character in cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").split())


def extract_reference(description: str) -> str | None:
    match = re.search(r"\b(?:PHUB|FT|REF|REFA)\d[A-Z0-9-]*\b", description, flags=re.IGNORECASE)
    return match.group(0) if match else None


def resolve_amounts(
    *,
    previous_balance: Decimal | None,
    balance: Decimal | None,
    reference: str | None,
    deposit_text: str,
    withdrawal_text: str,
) -> tuple[Decimal, Decimal, str | None]:
    debit = parse_decimal(withdrawal_text) or Decimal("0")
    credit = parse_decimal(deposit_text) or Decimal("0")
    cleaned_reference = clean_text(reference or "") or None

    if debit > 0 and credit > 0:
        if previous_balance is not None and balance is not None:
            expected_credit_balance = previous_balance + credit
            expected_debit_balance = previous_balance - debit
            credit_matches = abs(expected_credit_balance - balance) <= Decimal("0.01")
            debit_matches = abs(expected_debit_balance - balance) <= Decimal("0.01")

            if debit_matches and not credit_matches:
                return debit, Decimal("0"), merge_reference_token(cleaned_reference, deposit_text)
            if credit_matches and not debit_matches:
                return Decimal("0"), credit, merge_reference_token(cleaned_reference, withdrawal_text)

        if looks_like_spill_token(deposit_text):
            return debit, Decimal("0"), merge_reference_token(cleaned_reference, deposit_text)
        if looks_like_spill_token(withdrawal_text):
            return Decimal("0"), credit, merge_reference_token(cleaned_reference, withdrawal_text)

    return debit, credit, cleaned_reference


def merge_reference_token(reference: str | None, token: str) -> str | None:
    cleaned_token = clean_text(token)
    if not cleaned_token:
        return reference
    return clean_text(" ".join(part for part in (reference or "", cleaned_token) if part)) or None


def looks_like_spill_token(value: str) -> bool:
    cleaned = clean_text(value)
    return bool(re.fullmatch(r"\d{1,4}", cleaned))
