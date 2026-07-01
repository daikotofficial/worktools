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
from statement_analyzer.parsers.pdf_utils import find_regex_in_pages, open_pdf


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    date: str = ""
    description: str = ""
    reference: str = ""
    value_date: str = ""
    withdrawals: str = ""
    lodgements: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.date)

    @property
    def has_amounts(self) -> bool:
        return any((self.withdrawals, self.lodgements, self.balance))

    def is_header(self) -> bool:
        text = " ".join(
            part for part in (self.date, self.description, self.reference, self.value_date) if part
        ).upper()
        return (
            "DATE" in text
            and "REFERENCE" in text
            and "VALUE DATE" in text
            and ("TRANSACTION DETAILS" in text or "DATE REFERENCE" in text)
        )

    def is_opening_balance(self) -> bool:
        text = " ".join(part for part in (self.date, self.description, self.reference) if part).upper()
        return "OPENING BALANCE" in text

    def is_closing_balance(self) -> bool:
        text = " ".join(
            part
            for part in (self.date, self.description, self.reference, self.value_date, self.withdrawals, self.lodgements)
            if part
        ).upper()
        return "CLOSING BALANCE" in text

    def is_attachment_candidate(self) -> bool:
        return not self.has_date and not self.is_header() and not self.is_closing_balance()


class SummaryDetailsStatementParser(StatementParser):
    bank_name = "summary-details"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "summary_details_unknown"

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
                balance = parse_decimal(self._resolved_balance(row, attachments.get(index, [])))
                transactions.append(
                    Transaction(
                        transaction_date=None,
                        description="Opening Balance",
                        debit=Decimal("0"),
                        credit=Decimal("0"),
                        balance=balance if balance is not None else self.last_metadata.opening_balance,
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

            attached_rows = sorted(attachments.get(index, []), key=lambda item: (item.page_number, item.top))
            description_parts = [row.description]
            reference_parts = [row.reference]
            balance_parts = [row.balance]

            for extra in attached_rows:
                if extra.description:
                    description_parts.append(extra.description)
                if extra.reference:
                    reference_parts.append(extra.reference)
                if extra.balance:
                    balance_parts.append(extra.balance)

            description = clean_text(" ".join(part for part in description_parts if part))
            reference = clean_text(" ".join(part for part in reference_parts if part)) or None
            balance = parse_decimal(" ".join(part for part in balance_parts if part))

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.date),
                    description=description,
                    debit=parse_decimal(row.withdrawals) or Decimal("0"),
                    credit=parse_decimal(row.lodgements) or Decimal("0"),
                    balance=balance,
                    reference=reference,
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (
                                row.date,
                                description,
                                reference or "",
                                row.value_date,
                                row.withdrawals,
                                row.lodgements,
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
                    r"CLOSING BALANCE\s+(-?[0-9,]+\.\d{2})",
                    flags=re.IGNORECASE,
                    reverse=True,
                )
            )

        period_match = re.search(
            r"Summary Statement for\s+(\d{1,2}/\d{1,2}/\d{4})\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M\s+To\s+(\d{1,2}/\d{1,2}/\d{4})",
            first_page_text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        return StatementMetadata(
            account_name=extract_regex(first_page_text, r"TOTAL WITHDRAWALS\s+[0-9,.\-]+\s+(.+?)\s+TOTAL LODGEMENTS"),
            account_number=extract_regex(first_page_text, r"ACCOUNT NO\.\s*(\d+)"),
            currency=extract_regex(first_page_text, r"Currency\s+([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"OPENING BALANCE\s+(-?[0-9,]+\.\d{2})")),
            total_debit=parse_decimal(extract_regex(first_page_text, r"TOTAL WITHDRAWALS\s+([0-9,]+\.\d{2})")),
            total_credit=parse_decimal(extract_regex(first_page_text, r"TOTAL LODGEMENTS\s+([0-9,]+\.\d{2})")),
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
                "description": [],
                "reference": [],
                "value_date": [],
                "withdrawals": [],
                "lodgements": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 190:
                    columns["date"].append(text)
                elif x0 < 410:
                    columns["description"].append(text)
                elif x0 < 480:
                    columns["reference"].append(text)
                elif x0 < 550:
                    columns["value_date"].append(text)
                elif x0 < 620:
                    columns["withdrawals"].append(text)
                elif x0 < 690:
                    columns["lodgements"].append(text)
                else:
                    columns["balance"].append(text)

            row.date = " ".join(columns["date"]).strip()
            row.description = " ".join(columns["description"]).strip()
            row.reference = " ".join(columns["reference"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.withdrawals = " ".join(columns["withdrawals"]).strip()
            row.lodgements = " ".join(columns["lodgements"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part
                for part in (
                    row.date,
                    row.description,
                    row.reference,
                    row.value_date,
                    row.withdrawals,
                    row.lodgements,
                    row.balance,
                )
                if part
            ).upper()
            if not text_blob:
                continue
            if any(
                term in text_blob
                for term in (
                    "GARKI ABUJA,AHMADU BELLO WAY",
                    "ACCOUNT STATEMENT SUMMARY DETAILS",
                    "SUMMARY STATEMENT FOR",
                    "ACCOUNT NO.",
                    "ALT. ACCOUNT NO.",
                    "CURRENCY NGN",
                    "TOTAL WITHDRAWALS",
                    "TOTAL LODGEMENTS",
                    "CLEARED BALANCE",
                    "UNCLEARED BALANCE",
                    "PRIVATE & CONFIDENTIAL",
                    "CURRENT ACC. - CORPORATE",
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
            if not row.is_attachment_candidate():
                continue

            prev_same_page = next(
                (
                    candidate
                    for candidate in reversed(anchor_indices)
                    if candidate < index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            next_same_page = next(
                (
                    candidate
                    for candidate in anchor_indices
                    if candidate > index and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            prev_any_page = next((candidate for candidate in reversed(anchor_indices) if candidate < index), None)

            if prev_same_page is None and prev_any_page is not None and row.top <= 240:
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
            if distance > 36:
                continue
            attachments.setdefault(target, []).append(row)

        return attachments

    def _resolved_balance(self, row: ParsedRow, attached_rows: list[ParsedRow]) -> str:
        parts = [row.balance]
        parts.extend(item.balance for item in attached_rows if item.balance)
        return " ".join(part for part in parts if part)


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else None


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%m/%d/%Y")
        return True
    except ValueError:
        return False


def parse_date(value: str):
    return datetime.strptime(value.strip(), "%m/%d/%Y").date()


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%m/%d/%Y").date()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\x00", "").replace("\n", " ").split())
