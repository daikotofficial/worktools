from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber

from statement_analyzer.layouts import detect_layout
from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import find_regex_in_pages, open_pdf


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    date: str = ""
    reference: str = ""
    description: str = ""
    value_date: str = ""
    deposit: str = ""
    withdrawal: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.date)

    @property
    def has_amounts(self) -> bool:
        return any((self.deposit, self.withdrawal, self.balance))

    def is_header(self) -> bool:
        header_text = " ".join(
            part for part in (self.date, self.reference, self.description, self.value_date) if part
        ).upper()
        return (
            "DATE" in header_text
            and "REFERENCE" in header_text
            and ("DESCRIPTION" in header_text or "DESCRIP" in header_text)
        )

    def is_opening_balance(self) -> bool:
        text = " ".join(part for part in (self.date, self.description, self.reference) if part).upper()
        return "OPENING BALANCE" in text


class FCMBStatementParser(StatementParser):
    bank_name = "fcmb"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "fcmb_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        attachments = self._build_attachments(rows)
        transactions: list[Transaction] = []

        for index, row in enumerate(rows):
            if row.is_header():
                continue

            if row.is_opening_balance():
                balance = parse_decimal(self._resolved_balance(row, attachments.get(index, [])))
                transactions.append(
                    Transaction(
                        transaction_date=None,
                        description="Opening Balance",
                        debit=Decimal("0"),
                        credit=Decimal("0"),
                        balance=balance,
                        reference=None,
                        currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                        raw_text=clean_text(row.date),
                        source_page=row.page_number,
                        parser_name=self.bank_name,
                    )
                )
                continue

            if not row.has_date:
                continue

            attached_rows = sorted(attachments.get(index, []), key=lambda item: item.top)
            reference_parts = [row.reference]
            description_parts = [row.description]
            balance_parts = [row.balance]

            for extra in attached_rows:
                if extra.reference:
                    reference_parts.append(extra.reference)
                if extra.description:
                    description_parts.append(extra.description)
                if extra.balance:
                    balance_parts.append(extra.balance)

            description = clean_text(" ".join(part for part in description_parts if part))
            reference = clean_text(" ".join(part for part in reference_parts if part)) or None
            credit = parse_decimal(row.deposit) or Decimal("0")
            debit = parse_decimal(row.withdrawal) or Decimal("0")
            balance = parse_decimal(" ".join(part for part in balance_parts if part))

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.date),
                    description=description,
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    reference=reference,
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (row.date, reference or "", description, row.value_date, row.deposit, row.withdrawal)
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
                    r"Closing Balance:\s*([0-9,]+\.\d{2})",
                    flags=re.IGNORECASE,
                    reverse=True,
                )
            )

        period_match = re.search(
            r"For the Period of:\s*(\d{2}-[A-Za-z]{3}-\d{4})\s*To\s*(\d{2}-[A-Za-z]{3}-\d{4})",
            first_page_text,
            flags=re.IGNORECASE,
        )

        return StatementMetadata(
            account_name=extract_regex(first_page_text, r"Account Name:\s*(.+?)\s+Cleared Balance:"),
            account_number=extract_regex(first_page_text, r"Account No:\s*(\d+)"),
            currency=extract_regex(first_page_text, r"Currency:\s*([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"Opening Balance:\s*([0-9,]+\.\d{2})")),
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
                "date": [],
                "reference": [],
                "description": [],
                "value_date": [],
                "deposit": [],
                "withdrawal": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 92:
                    columns["date"].append(text)
                elif x0 < 146:
                    columns["reference"].append(text)
                elif x0 < 351:
                    columns["description"].append(text)
                elif x0 < 402:
                    columns["value_date"].append(text)
                elif x0 < 454:
                    columns["deposit"].append(text)
                elif x0 < 505:
                    columns["withdrawal"].append(text)
                else:
                    columns["balance"].append(text)

            row.date = " ".join(columns["date"]).strip()
            row.reference = " ".join(columns["reference"]).strip()
            row.description = " ".join(columns["description"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.deposit = " ".join(columns["deposit"]).strip()
            row.withdrawal = " ".join(columns["withdrawal"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part
                for part in (
                    row.date,
                    row.reference,
                    row.description,
                    row.value_date,
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
                    "PRIVATE AND CONFIDENTIAL",
                    "FOR ENQUIRIES, REQUEST OR COMPLAINTS",
                    "ADHOC CUSTOMER STATEMENT GENERATED",
                    "3/27/26, 3:08 PM",
                )
            ):
                continue
            rows.append(row)

        return rows

    def _build_attachments(self, rows: list[ParsedRow]) -> dict[int, list[ParsedRow]]:
        anchors = [index for index, row in enumerate(rows) if row.has_date or row.is_opening_balance()]
        attachments: dict[int, list[ParsedRow]] = {}

        for index, row in enumerate(rows):
            if row.is_header() or row.has_date or row.is_opening_balance():
                continue

            prev_anchor = next(
                (
                    candidate
                    for candidate in reversed(anchors)
                    if candidate < index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            next_anchor = next(
                (
                    candidate
                    for candidate in anchors
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
            if distance > 32:
                continue
            attachments.setdefault(target, []).append(row)

        return attachments

    def _resolved_balance(self, row: ParsedRow, attached_rows: list[ParsedRow]) -> str:
        parts = [row.balance]
        parts.extend(item.balance for item in attached_rows if item.balance)
        return " ".join(part for part in parts if part)


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
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
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if not cleaned:
        return None
    return Decimal(cleaned)


def clean_text(value: str) -> str:
    return " ".join(value.replace("\x00", "").replace("\n", " ").split())
