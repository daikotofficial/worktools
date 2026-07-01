from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import open_pdf

MONEY_VALUE_PATTERN = re.compile(r"\d[\d,]*\.\d{2}")


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    posted_date: str = ""
    value_date: str = ""
    description: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.posted_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.debit, self.credit, self.balance))

    def is_header(self) -> bool:
        header_text = " ".join(
            part for part in (self.posted_date, self.value_date, self.description) if part
        ).upper()
        return "DATE POSTED" in header_text and "VALUE DATE" in header_text

    def is_opening_balance(self) -> bool:
        return "OPENING BALANCE" in self.description.upper() and not self.has_date

    def is_description_only(self) -> bool:
        return bool(self.description) and not self.has_date and not self.has_amounts


class ZenithStyleParser(StatementParser):
    bank_name = "zenith-style"

    def can_parse(self, pdf_path: Path) -> bool:
        with open_pdf(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
        normalized = first_page_text.upper()
        return (
            "DATE POSTED" in normalized
            and "VALUE DATE" in normalized
            and "BALANCE" in normalized
        )

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        transactions: list[Transaction] = []
        opening_balance_added = False
        attachments = self._build_attachments(rows)

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
                        balance=parse_decimal(row.balance) or Decimal("0"),
                        raw_text=row.description,
                        source_page=row.page_number,
                        parser_name=self.bank_name,
                    )
                )
                opening_balance_added = True
                continue

            if not row.has_date:
                continue

            extras = sorted(attachments.get(index, []), key=lambda item: item[0])
            description_parts = [text for top, text in extras if top < row.top]
            description_parts.append(row.description)
            description_parts.extend(text for top, text in extras if top >= row.top)
            description = clean_description(" ".join(part for part in description_parts if part))

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.posted_date),
                    description=description,
                    debit=parse_decimal(row.debit) or Decimal("0"),
                    credit=parse_decimal(row.credit) or Decimal("0"),
                    balance=parse_decimal(row.balance),
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
        account_line = next((line for line in lines if "Account Number:" in line), "")

        return StatementMetadata(
            account_name=account_line.split("Account Number:")[0].strip() if account_line else None,
            account_number=extract_regex(first_page_text, r"Account Number:\s+CA\s+(\d+)"),
            currency=extract_regex(first_page_text, r"Currency:\s+([A-Z]{3})"),
            opening_balance=parse_decimal(
                extract_regex(first_page_text, r"Opening Balance:\s+([0-9,]+\.\d{2})")
            ),
            total_debit=parse_decimal(
                extract_regex(first_page_text, r"Total Debit:\s+([0-9,]+\.\d{2})")
            ),
            total_credit=parse_decimal(
                extract_regex(first_page_text, r"Total Credit:\s+([0-9,]+\.\d{2})")
            ),
            closing_balance=parse_decimal(
                extract_regex(first_page_text, r"Closing Balance:\s+([0-9,]+\.\d{2})")
            ),
            period_start=parse_period_date(
                extract_regex(first_page_text, r"Period:\s+(\d{2}-[A-Za-z]{3}-\d{4})")
            ),
            period_end=parse_period_date(
                extract_regex(
                    first_page_text,
                    r"Period:\s+\d{2}-[A-Za-z]{3}-\d{4}\s+TO\s+(\d{2}-[A-Za-z]{3}-\d{4})",
                )
            ),
        )

    def _extract_rows(self, pdf_path: Path) -> list[ParsedRow]:
        rows: list[ParsedRow] = []
        with open_pdf(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                rows.extend(self._extract_page_rows(page, page_number))
        return rows

    def _extract_page_rows(
        self,
        page: pdfplumber.page.Page,
        page_number: int,
    ) -> list[ParsedRow]:
        words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
        if not words:
            return []

        grouped: list[list[dict]] = []
        current_group: list[dict] = []
        current_top: float | None = None

        for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
            top = float(word["top"])
            if current_top is None or abs(top - current_top) <= 2.5:
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
                "posted_date": [],
                "value_date": [],
                "description": [],
                "debit": [],
                "credit": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                x1 = float(word["x1"])
                text = word["text"]

                if x0 < 90:
                    columns["posted_date"].append(text)
                elif x0 < 170:
                    columns["value_date"].append(text)
                elif is_amount_token(text) and x0 >= 430:
                    if x1 <= 510:
                        columns["debit"].append(text)
                    elif x1 <= 590:
                        columns["credit"].append(text)
                    else:
                        columns["balance"].append(text)
                elif x0 < 445:
                    columns["description"].append(text)
                elif x0 < 528:
                    columns["debit"].append(text)
                elif x0 < 607:
                    columns["credit"].append(text)
                else:
                    columns["balance"].append(text)

            row.posted_date = " ".join(columns["posted_date"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.description = " ".join(columns["description"]).strip()
            row.debit = " ".join(columns["debit"]).strip()
            row.credit = " ".join(columns["credit"]).strip()
            row.balance = " ".join(columns["balance"]).strip()
            normalize_amount_columns(row)

            if any(
                (row.posted_date, row.value_date, row.description, row.debit, row.credit, row.balance)
            ):
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
            if distance > 20:
                continue

            attachments.setdefault(target, []).append((row.top, row.description))

        return attachments


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%d/%m/%Y")
        return True
    except ValueError:
        return False


def parse_date(value: str):
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%d-%b-%Y").date()


def is_amount_token(value: str) -> bool:
    return bool(MONEY_VALUE_PATTERN.fullmatch(value))


def normalize_amount_columns(row: ParsedRow) -> None:
    if row.balance:
        return

    for field_name in ("credit", "debit"):
        raw_value = getattr(row, field_name)
        amount_values = MONEY_VALUE_PATTERN.findall(raw_value)
        if len(amount_values) < 2:
            continue
        setattr(row, field_name, amount_values[0])
        row.balance = amount_values[-1]
        return


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


def clean_description(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())
