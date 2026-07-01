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
    txn_date: str = ""
    val_date: str = ""
    remarks: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.txn_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.debit, self.credit, self.balance))

    def is_header(self) -> bool:
        text = " ".join(part for part in (self.txn_date, self.val_date, self.remarks) if part).upper()
        return "TXN DATE" in text and "VAL DATE" in text and "REMARKS" in text

    def is_description_only(self) -> bool:
        return bool(self.remarks) and not self.has_date and not self.has_amounts


class ProvidusStatementParser(StatementParser):
    bank_name = "providus"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "txn_date_remarks_layout"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        attachments = self._build_attachments(rows)
        transactions: list[Transaction] = []

        if self.last_metadata and self.last_metadata.opening_balance is not None:
            transactions.append(
                Transaction(
                    transaction_date=None,
                    description="Opening Balance",
                    debit=Decimal("0"),
                    credit=Decimal("0"),
                    balance=self.last_metadata.opening_balance,
                    reference=None,
                    currency=self.last_metadata.currency or "NGN",
                    raw_text="Opening Balance",
                    source_page=1,
                    parser_name=self.bank_name,
                )
            )

        for index, row in enumerate(rows):
            if row.is_header() or not row.has_date:
                continue

            extras = sorted(attachments.get(index, []), key=lambda item: item.top)
            description_parts = [extra.remarks for extra in extras if extra.remarks]
            description_parts.append(row.remarks)
            description = clean_text(" ".join(part for part in description_parts if part))

            debit = parse_decimal(row.debit) or Decimal("0")
            credit = parse_decimal(row.credit) or Decimal("0")
            balance = parse_decimal(row.balance)

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.txn_date),
                    description=description,
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    reference=extract_reference(description),
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part for part in (row.txn_date, row.val_date, description, row.debit, row.credit, row.balance) if part
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

        return StatementMetadata(
            account_name=extract_regex(first_page_text, r"CUST\. NAME\s+(.+?)\s+START DATE"),
            account_number=extract_regex(first_page_text, r"ACC\. NO\.\s+(\d+)"),
            currency=extract_regex(first_page_text, r"CURRENCY\s+([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"OPENING BAL\.\s+([0-9,]+\.\d{2})")),
            closing_balance=parse_decimal(extract_regex(first_page_text, r"CLOSING BAL\.\s+([0-9,]+\.\d{2})")),
            period_start=parse_period_date(extract_regex(first_page_text, r"START DATE\s+(\d{2}-\d{2}-\d{4})")),
            period_end=parse_period_date(extract_regex(first_page_text, r"END DATE\s+(\d{2}-\d{2}-\d{4})")),
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
                "txn_date": [],
                "val_date": [],
                "remarks": [],
                "debit": [],
                "credit": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]
                if x0 < 88:
                    columns["txn_date"].append(text)
                elif x0 < 142:
                    columns["val_date"].append(text)
                elif x0 < 376:
                    columns["remarks"].append(text)
                elif x0 < 434:
                    columns["debit"].append(text)
                elif x0 < 500:
                    columns["credit"].append(text)
                else:
                    columns["balance"].append(text)

            row.txn_date = " ".join(columns["txn_date"]).strip()
            row.val_date = " ".join(columns["val_date"]).strip()
            row.remarks = " ".join(columns["remarks"]).strip()
            row.debit = " ".join(columns["debit"]).strip()
            row.credit = " ".join(columns["credit"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part for part in (row.txn_date, row.val_date, row.remarks, row.debit, row.credit, row.balance) if part
            ).upper()
            if not text_blob:
                continue
            if text_blob in {"1", "2", "3", "4", "5", "6", "7", "8", "9"}:
                continue
            if any(term in text_blob for term in ("STATEMENT OF ACCOUNT", "CUST. NAME", "ACC. NO.", "OPENING BAL.", "CLOSING BAL.", "DATE PRINTED")):
                continue
            rows.append(row)

        return rows

    def _build_attachments(self, rows: list[ParsedRow]) -> dict[int, list[ParsedRow]]:
        attachments: dict[int, list[ParsedRow]] = {}
        date_indices = [index for index, row in enumerate(rows) if row.has_date]

        for index, row in enumerate(rows):
            if not row.is_description_only():
                continue

            next_date = next(
                (
                    candidate
                    for candidate in date_indices
                    if candidate > index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            prev_date = next(
                (
                    candidate
                    for candidate in reversed(date_indices)
                    if candidate < index and rows[candidate].page_number == row.page_number
                ),
                None,
            )

            choices: list[tuple[float, int]] = []
            if next_date is not None:
                choices.append((abs(rows[next_date].top - row.top), next_date))
            if prev_date is not None:
                choices.append((abs(row.top - rows[prev_date].top), prev_date))
            if not choices:
                continue

            distance, target = min(choices, key=lambda item: item[0])
            if distance > 48:
                continue
            attachments.setdefault(target, []).append(row)

        return attachments


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%d-%m-%Y")
        return True
    except ValueError:
        return False


def parse_date(value: str):
    return datetime.strptime(value.strip(), "%d-%m-%Y").date()


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%d-%m-%Y").date()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_text(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def extract_reference(description: str) -> str | None:
    match = re.search(r"\b(?:\.?/?\d{18,}|\d{18,})\b", description)
    return match.group(0) if match else None
