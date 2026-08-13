from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from statement_analyzer.layouts import detect_layout
from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import open_pdf


@dataclass(slots=True)
class KeystoneRow:
    page_number: int
    top: float
    date: str = ""
    value_date: str = ""
    narration: str = ""
    reference: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return parse_date(self.date) is not None


class KeystoneStatementParser(StatementParser):
    bank_name = "keystone"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "keystone_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        transactions: list[Transaction] = []
        current: Transaction | None = None

        # Consume rows as they are extracted so long statements do not keep a
        # second full in-memory representation of the document.
        for row in self._extract_rows(pdf_path):
            if row.has_date:
                if current is not None:
                    transactions.append(current)
                debit = parse_decimal(row.debit) or Decimal("0")
                credit = parse_decimal(row.credit) or Decimal("0")
                current = Transaction(
                    transaction_date=parse_date(row.date),
                    description=clean_text(" ".join(part for part in (row.narration, row.reference) if part)),
                    debit=debit,
                    credit=credit,
                    balance=parse_decimal(row.balance),
                    reference=clean_text(row.reference) or None,
                    currency="NGN",
                    raw_text=clean_text(" ".join(part for part in (row.date, row.value_date, row.narration, row.reference, row.debit, row.credit, row.balance) if part)),
                    source_page=row.page_number,
                    parser_name=self.bank_name,
                )
                continue

            if current is None or is_noise_row(row):
                continue
            continuation = clean_text(" ".join(part for part in (row.narration, row.reference) if part))
            if continuation:
                current.description = clean_text(f"{current.description} {continuation}")
                current.raw_text = current.description

        if current is not None:
            transactions.append(current)
        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first = pdf.pages[0].extract_text() or ""
            # The closing balance is on the final summary page. Avoid scanning
            # every page twice on long multi-year statements.
            last = pdf.pages[-1].extract_text() or ""
            closing = parse_decimal(extract(last, r"Closing Balance\s*-?\s*([0-9,]+\.\d{2})"))
        account_name = extract(first, r"\n([A-Z][A-Z0-9 .&'/-]+LIMITED)\s*\nNO10\s+DUTSE")
        return StatementMetadata(
            account_name=account_name,
            account_number=extract(first, r"Account No\.\s*-\s*(\d+)"),
            currency="NGN",
            opening_balance=parse_decimal(extract(first, r"Opening Balance\s*-\s*([0-9,]+\.\d{2})")),
            total_debit=parse_decimal(extract(first, r"Total Debits\s*-\s*([0-9,]+\.\d{2})")),
            total_credit=parse_decimal(extract(first, r"Total Credits\s*-\s*([0-9,]+\.\d{2})")),
            closing_balance=closing,
        )

    def _extract_rows(self, pdf_path: Path) -> Iterator[KeystoneRow]:
        with open_pdf(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
                grouped: list[list[dict]] = []
                current: list[dict] = []
                current_top: float | None = None
                for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
                    top = float(word["top"])
                    if current_top is None or abs(top - current_top) <= 2.6:
                        current.append(word)
                        current_top = top if current_top is None else (current_top + top) / 2
                    else:
                        grouped.append(current)
                        current = [word]
                        current_top = top
                if current:
                    grouped.append(current)

                page_rows: list[KeystoneRow] = []
                for group in grouped:
                    columns = {key: [] for key in ("date", "value_date", "narration", "reference", "debit", "credit", "balance")}
                    for word in sorted(group, key=lambda item: item["x0"]):
                        x0 = float(word["x0"])
                        key = "date" if x0 < 72 else "value_date" if x0 < 111 else "narration" if x0 < 285 else "reference" if x0 < 330 else "debit" if x0 < 420 else "credit" if x0 < 500 else "balance"
                        columns[key].append(word["text"])
                    row = KeystoneRow(page_number, min(float(word["top"]) for word in group), **{key: " ".join(value).strip() for key, value in columns.items()})
                    if any(value for value in columns.values()) and not is_header_row(row):
                        page_rows.append(row)
                # pdfplumber caches layout objects on each page. Release them
                # before moving to the next page in very long statements.
                page.close()
                pdf.flush_cache()
                yield from page_rows


def is_header_row(row: KeystoneRow) -> bool:
    text = clean_text(" ".join((row.date, row.value_date, row.narration, row.reference))).upper()
    return "DATE" in text and "NARRATION" in text and "BALANCE" in text


def is_noise_row(row: KeystoneRow) -> bool:
    text = clean_text(" ".join((row.narration, row.reference, row.balance))).upper()
    return not text or "PAGE " in text and " OF " in text or text.startswith("WUSE ") or "WWW.KEYSTONEBANKNG.COM" in text


def parse_date(value: str):
    try:
        return datetime.strptime(value.strip(), "%d%b%y").date()
    except ValueError:
        return None


def parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    matches = re.findall(r"-?[0-9,]+(?:\.\d{1,2})?", value.replace("NGN", ""))
    if not matches:
        return None
    try:
        return Decimal(matches[-1].replace(",", ""))
    except InvalidOperation:
        return None


def extract(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else None


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").split())
