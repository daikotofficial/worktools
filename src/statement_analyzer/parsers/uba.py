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
    description: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.trans_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.debit, self.credit, self.balance))

    def is_header(self) -> bool:
        header_text = " ".join(
            part for part in (self.trans_date, self.value_date, self.description) if part
        ).upper()
        return "TRANS DATE" in header_text and "VALUE DATE" in header_text and "NARRATION" in header_text

    def is_description_only(self) -> bool:
        return bool(self.description) and not self.has_date and not self.has_amounts


class UBAStatementParser(StatementParser):
    bank_name = "uba"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "uba_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        attachments = self._build_attachments(rows)
        transactions: list[Transaction] = []

        for index, row in enumerate(rows):
            if row.is_header() or not row.has_date:
                continue

            extras = sorted(attachments.get(index, []), key=lambda item: item[0])
            description_parts = [text for top, text in extras if top < row.top]
            description_parts.append(row.description)
            description_parts.extend(text for top, text in extras if top >= row.top)
            description = clean_text(" ".join(part for part in description_parts if part))

            debit = parse_decimal(row.debit) or Decimal("0")
            credit = parse_decimal(row.credit) or Decimal("0")
            balance = parse_decimal(row.balance)
            transaction_date = parse_date(row.trans_date)

            if "OPENING BALANCE" in description.upper() and debit == 0 and credit == 0:
                description = "Opening Balance"
                transaction_date = None

            transactions.append(
                Transaction(
                    transaction_date=transaction_date,
                    description=description,
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    reference=extract_reference(description),
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=description,
                    source_page=row.page_number,
                    parser_name=self.bank_name,
                )
            )

        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""

        lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]
        account_name = lines[1] if len(lines) > 1 and lines[0].upper() == "BANK STATEMENT" else None
        period_match = re.search(r"(\d{2}-[A-Za-z]{3}-\d{4})\s+to\s+(\d{2}-[A-Za-z]{3}-\d{4})", first_page_text)

        return StatementMetadata(
            account_name=account_name,
            account_number=extract_regex(first_page_text, r"Account Number:\s*(\d+)"),
            currency=extract_regex(first_page_text, r"Currency:\s*([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"Opening Balance:\s*(-?[0-9,]+\.\d{2})")),
            total_debit=parse_decimal(extract_regex(first_page_text, r"Total Debit:\s*([0-9,]+\.\d{2})")),
            total_credit=parse_decimal(extract_regex(first_page_text, r"Total Credit:\s*([0-9,]+\.\d{2})")),
            closing_balance=parse_decimal(extract_regex(first_page_text, r"Closing Balance:\s*(-?[0-9,]+\.\d{2})")),
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
                "trans_date": [],
                "value_date": [],
                "description": [],
                "debit": [],
                "credit": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 82:
                    columns["trans_date"].append(text)
                elif x0 < 140:
                    columns["value_date"].append(text)
                elif x0 < 340:
                    columns["description"].append(text)
                elif x0 < 420:
                    columns["debit"].append(text)
                elif x0 < 495:
                    columns["credit"].append(text)
                else:
                    columns["balance"].append(text)

            row.trans_date = " ".join(columns["trans_date"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.description = " ".join(columns["description"]).strip()
            row.debit = " ".join(columns["debit"]).strip()
            row.credit = " ".join(columns["credit"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part for part in (row.trans_date, row.value_date, row.description, row.debit, row.credit, row.balance) if part
            ).upper()
            if not text_blob:
                continue
            if any(term in text_blob for term in ("DOWNLOAD APP", "HEAD OFFICE:", "PRIVACY POLICY", "AFRICA'S GLOBAL BANK")):
                continue
            rows.append(row)

        return rows

    def _build_attachments(self, rows: list[ParsedRow]) -> dict[int, list[tuple[float, str]]]:
        attachments: dict[int, list[tuple[float, str]]] = {}
        date_indices = [index for index, row in enumerate(rows) if row.has_date]

        for index, row in enumerate(rows):
            if not row.is_description_only():
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
            if distance > 28:
                continue

            attachments.setdefault(target, []).append((row.top, row.description))

        return attachments


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


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
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_text(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def extract_reference(description: str) -> str | None:
    match = re.search(r"\b\d{10,}\b", description)
    return match.group(0) if match else None
