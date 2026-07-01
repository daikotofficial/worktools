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
    reference: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""
    branch: str = ""
    remarks: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.trans_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.debit, self.credit, self.balance))

    def is_header(self) -> bool:
        text = " ".join(
            part
            for part in (
                self.trans_date,
                self.value_date,
                self.reference,
                self.branch,
                self.remarks,
            )
            if part
        ).upper()
        return "TRANS. DATE" in text and "VALUE. DATE" in text and "REMARKS" in text

    def is_description_only(self) -> bool:
        return bool(self.remarks) and not self.has_date and not self.has_amounts


class GTBankStatementParser(StatementParser):
    bank_name = "gtbank"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "gtbank_statement"

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

            extras = sorted(attachments.get(index, []), key=lambda item: (item.page_number, item.top))
            description_parts = [extra.remarks for extra in extras if extra.top < row.top]
            description_parts.append(row.remarks)
            description_parts.extend(extra.remarks for extra in extras if extra.top >= row.top)
            description = clean_text(" ".join(part for part in description_parts if part))

            reference_parts = [clean_reference(row.reference)]
            reference_parts.extend(
                clean_text(extra.remarks)
                for extra in extras
                if extra.remarks.upper().startswith("REF:")
            )
            reference = clean_text(" ".join(part for part in reference_parts if part)) or None

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.trans_date),
                    description=description,
                    debit=parse_decimal(row.debit) or Decimal("0"),
                    credit=parse_decimal(row.credit) or Decimal("0"),
                    balance=parse_decimal(row.balance),
                    reference=reference,
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (
                                row.trans_date,
                                row.value_date,
                                row.reference,
                                row.branch,
                                description,
                                row.debit,
                                row.credit,
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

        period_match = re.search(
            r"Statement Period\s*:(\d{2}-[A-Za-z]{3}-\d{4})\s*to\s*(\d{2}-[A-Za-z]{3}-\d{4})",
            first_page_text,
            flags=re.IGNORECASE,
        )
        account_name_match = re.search(
            r"CUSTOMER STATEMENT\s+(.+?)\s+Trans\. Date",
            first_page_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        currency_text = extract_regex(first_page_text, r"Currency\s+([A-Za-z ]+)")

        return StatementMetadata(
            account_name=clean_text(account_name_match.group(1)) if account_name_match else None,
            account_number=extract_regex(first_page_text, r"Account No\s+(\d+)"),
            currency=normalize_currency(currency_text),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"Opening Balance\s+(-?[0-9,]+(?:\.\d{1,2})?)")),
            total_debit=parse_decimal(extract_regex(first_page_text, r"Total Debit\s+(-?[0-9,]+(?:\.\d{1,2})?)")),
            total_credit=parse_decimal(extract_regex(first_page_text, r"Total Credit\s+(-?[0-9,]+(?:\.\d{1,2})?)")),
            closing_balance=parse_decimal(extract_regex(first_page_text, r"Closing Balance\s+(-?[0-9,]+(?:\.\d{1,2})?)")),
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
                "value_date": [],
                "reference": [],
                "debit": [],
                "credit": [],
                "balance": [],
                "branch": [],
                "remarks": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 108:
                    columns["trans_date"].append(text)
                elif x0 < 176:
                    columns["value_date"].append(text)
                elif x0 < 255:
                    columns["reference"].append(text)
                elif x0 < 337:
                    columns["debit"].append(text)
                elif x0 < 415:
                    columns["credit"].append(text)
                elif x0 < 495:
                    columns["balance"].append(text)
                elif x0 < 593:
                    columns["branch"].append(text)
                else:
                    columns["remarks"].append(text)

            row.trans_date = " ".join(columns["trans_date"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.reference = " ".join(columns["reference"]).strip()
            row.debit = " ".join(columns["debit"]).strip()
            row.credit = " ".join(columns["credit"]).strip()
            row.balance = " ".join(columns["balance"]).strip()
            row.branch = " ".join(columns["branch"]).strip()
            row.remarks = " ".join(columns["remarks"]).strip()

            text_blob = " ".join(
                part
                for part in (
                    row.trans_date,
                    row.value_date,
                    row.reference,
                    row.debit,
                    row.credit,
                    row.balance,
                    row.branch,
                    row.remarks,
                )
                if part
            ).upper()
            if not text_blob:
                continue
            if any(
                term in text_blob
                for term in (
                    "THIS IS A COMPUTER GENERATED EMAIL",
                    "FAX 01-2694276",
                    "CUSTOMER INFORMATION UNIT",
                    "STATEMENT PERIOD",
                    "INTERNAL REFERENCE",
                    "USABLE BALANCE",
                )
            ):
                continue
            if text_blob in {"-", ".", "1", "2", "3"}:
                continue
            rows.append(row)

        return rows

    def _build_attachments(self, rows: list[ParsedRow]) -> dict[int, list[ParsedRow]]:
        attachments: dict[int, list[ParsedRow]] = {}
        date_indices = [index for index, row in enumerate(rows) if row.has_date]

        for index, row in enumerate(rows):
            if not row.is_description_only():
                continue

            prev_same_page = next(
                (
                    candidate
                    for candidate in reversed(date_indices)
                    if candidate < index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            next_same_page = next(
                (
                    candidate
                    for candidate in date_indices
                    if candidate > index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            prev_any_page = next((candidate for candidate in reversed(date_indices) if candidate < index), None)

            if prev_same_page is None and prev_any_page is not None and row.top <= 90:
                attachments.setdefault(prev_any_page, []).append(row)
                continue

            choices: list[tuple[float, int]] = []
            if prev_same_page is not None:
                choices.append((abs(row.top - rows[prev_same_page].top), prev_same_page))
            if next_same_page is not None:
                choices.append((abs(rows[next_same_page].top - row.top), next_same_page))
            if not choices:
                continue

            distance, target = min(choices, key=lambda item: item[0])
            if distance > 44:
                continue
            attachments.setdefault(target, []).append(row)

        return attachments


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = clean_text(value).upper()
    mapping = {
        "NAIRA": "NGN",
        "US DOLLAR": "USD",
    }
    return mapping.get(cleaned, cleaned)


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
    if cleaned.startswith("."):
        cleaned = f"0{cleaned}"
    if cleaned.startswith("-."):
        cleaned = cleaned.replace("-.", "-0.", 1)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").replace("\x00", " ").split())


def clean_reference(value: str) -> str:
    cleaned = clean_text(value)
    if re.fullmatch(r"'\w+", cleaned):
        return cleaned
    if cleaned in {"'0", "'00", "'10", "'20"}:
        return cleaned
    return cleaned
