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
from statement_analyzer.parsers.pdf_utils import find_regex_in_pages


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    trans_date: str = ""
    reference: str = ""
    details: str = ""
    value_date: str = ""
    withdrawal: str = ""
    deposit: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.trans_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.withdrawal, self.deposit, self.balance))

    def is_header(self) -> bool:
        text = " ".join(
            part for part in (self.trans_date, self.reference, self.details, self.value_date) if part
        ).upper()
        return "TRANS DATE" in text and "REF. NUMBER" in text and "TRANSACTION DETAILS" in text

    def is_opening_balance(self) -> bool:
        text = " ".join(part for part in (self.trans_date, self.reference, self.details) if part).upper()
        return "OPENING BALANCE" in text

    def is_closing_balance(self) -> bool:
        text = " ".join(part for part in (self.trans_date, self.reference, self.details) if part).upper()
        return "CLOSING BALANCE" in text

    def is_description_only(self) -> bool:
        return bool(self.details) and not self.has_date and not self.has_amounts


class FirstBankStatementParser(StatementParser):
    bank_name = "firstbank"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "firstbank_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        attachments = self._build_attachments(rows)
        transactions: list[Transaction] = []
        opening_balance_added = False

        for index, row in enumerate(rows):
            if row.is_header():
                continue

            if row.is_opening_balance() and not opening_balance_added:
                transactions.append(
                    Transaction(
                        transaction_date=None,
                        description="Opening Balance",
                        debit=Decimal("0"),
                        credit=Decimal("0"),
                        balance=parse_decimal(row.balance) or self.last_metadata.opening_balance if self.last_metadata else Decimal("0"),
                        reference=None,
                        currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                        raw_text="Opening Balance",
                        source_page=row.page_number,
                        parser_name=self.bank_name,
                    )
                )
                opening_balance_added = True
                continue

            if row.is_closing_balance() or not row.has_date:
                continue

            attached_rows = sorted(attachments.get(index, []), key=lambda item: item.top)
            detail_parts = [row.details]
            for extra in attached_rows:
                if extra.details:
                    detail_parts.append(extra.details)

            full_details = clean_text(" ".join(part for part in detail_parts if part))
            reference = extract_reference(" ".join(part for part in [row.reference, full_details] if part))
            description = strip_reference_tokens(full_details)

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.trans_date),
                    description=description or full_details,
                    debit=parse_decimal(row.withdrawal) or Decimal("0"),
                    credit=parse_decimal(row.deposit) or Decimal("0"),
                    balance=parse_decimal(row.balance),
                    reference=reference,
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (
                                row.trans_date,
                                row.reference,
                                full_details,
                                row.value_date,
                                row.withdrawal,
                                row.deposit,
                                row.balance,
                            )
                            if part
                        )
                    ),
                    source_page=row.page_number,
                    parser_name=self.bank_name,
                )
            )

        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
            closing_balance = parse_decimal(
                find_regex_in_pages(
                    pdf.pages,
                    r"Closing Balance\s+([0-9,]+\.\d{2})",
                    flags=re.IGNORECASE,
                    reverse=True,
                )
            )

        period_match = re.search(
            r"period:\s*(\d{2}-[A-Za-z]{3}-\d{4})\s*To\s*(\d{2}-[A-Za-z]{3}-\d{4})",
            first_page_text,
            flags=re.IGNORECASE,
        )

        return StatementMetadata(
            account_name=extract_regex(first_page_text, r"Account Name:\s*(.+?)\s+Available Balance:"),
            account_number=extract_regex(first_page_text, r"Account No:\s*(\d+)"),
            currency=extract_regex(first_page_text, r"Currency:\s*([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"Opening Balance\s+([0-9,]+\.\d{2})")),
            total_debit=parse_decimal(extract_regex(first_page_text, r"Total Debit:\s*([0-9,]+\.\d{2})")),
            total_credit=parse_decimal(extract_regex(first_page_text, r"Total Credit:\s*([0-9,]+\.\d{2})")),
            closing_balance=closing_balance,
            period_start=parse_period_date(period_match.group(1)) if period_match else None,
            period_end=parse_period_date(period_match.group(2)) if period_match else None,
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
                "reference": [],
                "details": [],
                "value_date": [],
                "withdrawal": [],
                "deposit": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 96:
                    columns["trans_date"].append(text)
                elif x0 < 150:
                    columns["reference"].append(text)
                elif x0 < 308:
                    columns["details"].append(text)
                elif x0 < 358:
                    columns["value_date"].append(text)
                elif x0 < 423:
                    columns["withdrawal"].append(text)
                elif x0 < 482:
                    columns["deposit"].append(text)
                else:
                    columns["balance"].append(text)

            row.trans_date = " ".join(columns["trans_date"]).strip()
            row.reference = " ".join(columns["reference"]).strip()
            row.details = " ".join(columns["details"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.withdrawal = " ".join(columns["withdrawal"]).strip()
            row.deposit = " ".join(columns["deposit"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part
                for part in (
                    row.trans_date,
                    row.reference,
                    row.details,
                    row.value_date,
                    row.withdrawal,
                    row.deposit,
                    row.balance,
                )
                if part
            ).upper()
            if not text_blob:
                continue
            if any(
                term in text_blob
                for term in (
                    "DEAR ",
                    "PLEASE FIND BELOW YOUR BANK STATEMENT",
                    "ACCOUNT NAME:",
                    "ACCOUNT TYPE:",
                    "GENERATED BY",
                    "PLEASE REPORT ANY DISCREPANCIES",
                    "FOR ENQUIRIES, REQUESTS OR COMPLAINTS",
                    "FIRSTCONTACT",
                    "PAGE ",
                )
            ):
                continue
            rows.append(row)

        return rows

    def _build_attachments(self, rows: list[ParsedRow]) -> dict[int, list[ParsedRow]]:
        attachments: dict[int, list[ParsedRow]] = {}
        anchor_indices = [
            index for index, row in enumerate(rows)
            if row.has_date or row.is_opening_balance() or row.is_closing_balance()
        ]

        for index, row in enumerate(rows):
            if row.is_header() or row.has_date or row.is_opening_balance() or row.is_closing_balance():
                continue
            if not row.is_description_only():
                continue

            prev_anchor = next(
                (
                    candidate
                    for candidate in reversed(anchor_indices)
                    if candidate < index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            next_anchor = next(
                (
                    candidate
                    for candidate in anchor_indices
                    if candidate > index and rows[candidate].page_number == row.page_number
                ),
                None,
            )

            choices: list[tuple[float, int]] = []
            if prev_anchor is not None:
                choices.append((abs(row.top - rows[prev_anchor].top), prev_anchor))
            if next_anchor is not None:
                choices.append((abs(rows[next_anchor].top - row.top), next_anchor))
            if not choices:
                continue

            distance, target = min(choices, key=lambda item: item[0])
            if distance > 36:
                continue

            attachments.setdefault(target, []).append(row)

        return attachments


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else None


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%d-%b-%Y")
        return True
    except ValueError:
        return False


def parse_date(value: str):
    return datetime.strptime(value.strip(), "%d-%b-%Y").date()


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%d-%b-%Y").date()


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


def extract_reference(text: str) -> str | None:
    match = re.search(r"\bRef\s*([A-Z0-9]{6,})\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\b(S\d{6,})\b", text)
    return match.group(1) if match else None


def strip_reference_tokens(text: str) -> str:
    cleaned = re.sub(r"\bRef\s*[A-Z0-9]{6,}\b", "", text, flags=re.IGNORECASE)
    return clean_text(cleaned)
