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
    value_date: str = ""
    transaction_date: str = ""
    reference: str = ""
    description: str = ""
    credit: str = ""
    debit: str = ""
    balance: str = ""

    @property
    def has_transaction_date(self) -> bool:
        return is_date(self.transaction_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.credit, self.debit, self.balance))

    def is_header(self) -> bool:
        text = " ".join(
            part
            for part in (
                self.value_date,
                self.transaction_date,
                self.reference,
                self.description,
                self.credit,
                self.debit,
                self.balance,
            )
            if part
        ).upper()
        return (
            "VALUE" in text
            and "TRANSACTION" in text
            and ("REFERENCE" in text or "NUMBER" in text or "DETAILS" in text)
        ) or text == "DATE DATE NUMBER"

    def is_continuation(self) -> bool:
        return (
            not self.has_transaction_date
            and not self.has_amounts
            and not self.is_header()
            and bool(self.value_date or self.description)
        )


class WemaStatementParser(StatementParser):
    bank_name = "wema"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "wema_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        attachments = self._build_attachments(rows)
        transactions: list[Transaction] = []

        for index, row in enumerate(rows):
            if row.is_header() or not row.has_transaction_date:
                continue

            attached_rows = sorted(attachments.get(index, []), key=lambda item: item.top)
            description = clean_text(
                " ".join(
                    part
                    for part in (
                        *(
                            item.description
                            for item in attached_rows
                            if item.top < row.top and item.description
                        ),
                        row.description,
                        *(
                            item.description
                            for item in attached_rows
                            if item.top >= row.top and item.description
                        ),
                    )
                    if part
                )
            )

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.transaction_date),
                    description=description,
                    debit=parse_decimal(row.debit) or Decimal("0"),
                    credit=parse_decimal(row.credit) or Decimal("0"),
                    balance=parse_decimal(row.balance),
                    reference=clean_text(row.reference) or None,
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (
                                row.value_date,
                                row.transaction_date,
                                row.reference,
                                description,
                                row.credit,
                                row.debit,
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
            first_page_text = clean_ocr_text(pdf.pages[0].extract_text() or "")

        account_name = None
        lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if line.upper() == "ACCOUNT NAME" and index + 1 < len(lines):
                account_name = lines[index + 1]
                break

        totals_match = re.search(
            r"Account Number\s+Total Credit\s+Total Debit\s+(\d+)\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})",
            first_page_text,
            flags=re.IGNORECASE,
        )
        summary_match = re.search(
            r"Opening Balance\s+Closing Balance\s+Date Printed\s+Start Date\s+End Date\s+"
            r"\D*([0-9,]+\.\d{2})\s+\D*([0-9,]+\.\d{2})\s+"
            r"\d{2}\s*-\s*[A-Za-z]+\s*-\s*\d{4}\s+"
            r"(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}-[A-Za-z]{3}-\d{4})",
            first_page_text,
            flags=re.IGNORECASE,
        )

        return StatementMetadata(
            account_name=account_name,
            account_number=totals_match.group(1) if totals_match else None,
            currency="NGN",
            opening_balance=parse_decimal(summary_match.group(1)) if summary_match else None,
            total_debit=parse_decimal(totals_match.group(3)) if totals_match else None,
            total_credit=parse_decimal(totals_match.group(2)) if totals_match else None,
            closing_balance=parse_decimal(summary_match.group(2)) if summary_match else None,
            period_start=parse_period_date(summary_match.group(3)) if summary_match else None,
            period_end=parse_period_date(summary_match.group(4)) if summary_match else None,
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
            if current_top is None or abs(top - current_top) <= 2.6:
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
                "value_date": [],
                "transaction_date": [],
                "reference": [],
                "description": [],
                "credit": [],
                "debit": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 55:
                    columns["value_date"].append(text)
                elif x0 < 115:
                    columns["transaction_date"].append(text)
                elif x0 < 170:
                    columns["reference"].append(text)
                elif x0 < 400:
                    columns["description"].append(text)
                elif x0 < 455:
                    columns["credit"].append(text)
                elif x0 < 515:
                    columns["debit"].append(text)
                else:
                    columns["balance"].append(text)

            row.value_date = clean_ocr_text(" ".join(columns["value_date"]).strip())
            row.transaction_date = clean_ocr_text(" ".join(columns["transaction_date"]).strip())
            row.reference = clean_ocr_text(" ".join(columns["reference"]).strip())
            row.description = clean_ocr_text(" ".join(columns["description"]).strip())
            row.credit = clean_ocr_text(" ".join(columns["credit"]).strip())
            row.debit = clean_ocr_text(" ".join(columns["debit"]).strip())
            row.balance = clean_ocr_text(" ".join(columns["balance"]).strip())

            if any(
                (
                    row.value_date,
                    row.transaction_date,
                    row.reference,
                    row.description,
                    row.credit,
                    row.debit,
                    row.balance,
                )
            ):
                rows.append(row)

        return rows

    def _build_attachments(self, rows: list[ParsedRow]) -> dict[int, list[ParsedRow]]:
        date_indices = [index for index, row in enumerate(rows) if row.has_transaction_date]
        attachments: dict[int, list[ParsedRow]] = {}

        for index, row in enumerate(rows):
            if not row.is_continuation():
                continue

            prev_date = next(
                (
                    candidate
                    for candidate in reversed(date_indices)
                    if candidate < index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            next_date = next(
                (
                    candidate
                    for candidate in date_indices
                    if candidate > index and rows[candidate].page_number == row.page_number
                ),
                None,
            )

            choices: list[tuple[float, int]] = []
            if prev_date is not None:
                choices.append((abs(row.top - rows[prev_date].top), prev_date))
            if next_date is not None:
                choices.append((abs(rows[next_date].top - row.top), next_date))
            if not choices:
                continue

            distance, target = min(choices, key=lambda item: item[0])
            if distance > 24:
                continue
            attachments.setdefault(target, []).append(row)

        return attachments


def clean_ocr_text(value: str) -> str:
    return " ".join(collapse_duplicated_token(token) for token in value.replace("\x00", "").split())


def collapse_duplicated_token(token: str) -> str:
    if len(token) < 4 or len(token) % 2:
        return token

    pairs = [token[index : index + 2] for index in range(0, len(token), 2)]
    duplicated_pairs = sum(1 for pair in pairs if len(pair) == 2 and pair[0] == pair[1])
    if duplicated_pairs / len(pairs) < 0.75:
        return token
    return "".join(pair[0] for pair in pairs)


def is_date(value: str) -> bool:
    try:
        datetime.strptime(clean_ocr_text(value).strip(), "%d-%b-%Y")
        return True
    except ValueError:
        return False


def parse_date(value: str):
    return datetime.strptime(clean_ocr_text(value).strip(), "%d-%b-%Y").date()


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(clean_ocr_text(value).strip(), "%d-%b-%Y").date()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-()]", "", clean_ocr_text(value))
    if not cleaned:
        return None
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_text(value: str) -> str:
    return " ".join(clean_ocr_text(value).replace("\n", " ").split())
