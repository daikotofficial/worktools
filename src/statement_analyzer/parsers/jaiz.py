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
from statement_analyzer.parsers.pdf_utils import open_pdf


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    trans_date: str = ""
    narration: str = ""
    value_date: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""

    def is_header(self) -> bool:
        text = " ".join(
            part for part in (self.trans_date, self.narration, self.value_date, self.debit, self.credit) if part
        ).upper()
        return "TRANSACTI" in text and "NARRATION" in text and "VALUE" in text and "BALANCE" in text

    def is_transaction_start(self) -> bool:
        return bool(re.fullmatch(r"\d{2}-[A-Za-z]{3,5}-?", clean_text(self.trans_date)))

    def is_year_row(self) -> bool:
        return extract_year(self.trans_date) is not None

    def is_noise(self) -> bool:
        tokens = [clean_text(part) for part in (self.trans_date, self.narration, self.value_date) if clean_text(part)]
        amount_tokens = [clean_text(part) for part in (self.debit, self.credit, self.balance) if clean_text(part)]
        return (
            (bool(tokens) and not amount_tokens and all(len(token) <= 1 for token in tokens))
            or (
                not tokens
                and bool(amount_tokens)
                and all(not re.search(r"\d", token) and len(token) <= 1 for token in amount_tokens)
            )
        )

    def has_any_text(self) -> bool:
        return any(
            clean_text(part)
            for part in (self.trans_date, self.narration, self.value_date, self.debit, self.credit, self.balance)
        )

    def is_footer_total(self) -> bool:
        return (
            not clean_text(self.trans_date)
            and not clean_text(self.narration)
            and not clean_text(self.value_date)
            and not clean_text(self.balance)
            and bool(re.search(r"\d", self.debit))
            and bool(re.search(r"\d", self.credit))
        )


class JaizStatementParser(StatementParser):
    bank_name = "jaiz"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "jaiz_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        rows = self._extract_rows(pdf_path)
        self.last_metadata = self._extract_metadata(pdf_path)
        transactions = [
            Transaction(
                transaction_date=None,
                description="Opening Balance",
                debit=Decimal("0"),
                credit=Decimal("0"),
                balance=self.last_metadata.opening_balance or Decimal("0"),
                reference=None,
                currency=self.last_metadata.currency or "NGN",
                raw_text="Opening Balance",
                source_page=1,
                parser_name=self.bank_name,
            )
        ]

        index = 0
        while index < len(rows):
            row = rows[index]
            if row.is_header() or not row.is_transaction_start():
                index += 1
                continue

            start_index = index
            parts = [rows[index]]
            index += 1

            while index < len(rows):
                candidate = rows[index]
                if candidate.is_header() or candidate.is_transaction_start():
                    break
                parts.append(candidate)
                index += 1

            transaction = build_transaction(
                parts,
                metadata=self.last_metadata,
                parser_name=self.bank_name,
                source_page=rows[start_index].page_number,
            )
            if transaction is not None:
                transactions.append(transaction)

        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
            total_debit, total_credit = extract_totals_from_last_page(pdf.pages[-1])

        period_match = re.search(
            r"PERIOD:\s*(\d{2}-[A-Za-z]{3}-\d{4})\s*to\s*(\d{2}-[A-Za-z]{3}-\d{4})",
            first_page_text,
            flags=re.IGNORECASE,
        )

        return StatementMetadata(
            account_name=extract_regex(first_page_text, r"CUSTOMER NAME:\s*(.+?)\s+ACCOUNT NO:"),
            account_number=extract_regex(first_page_text, r"ACCOUNT NO:\s*(\d+)"),
            currency=extract_regex(first_page_text, r"CURRENCY:\s*([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"OPENING\s+([0-9,]+\.\d{2})")),
            total_debit=total_debit,
            total_credit=total_credit,
            closing_balance=parse_decimal(extract_regex(first_page_text, r"CLOSING\s+([0-9,]+\.\d{2})")),
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
                "narration": [],
                "value_date": [],
                "debit": [],
                "credit": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 80:
                    columns["trans_date"].append(text)
                elif x0 < 260:
                    columns["narration"].append(text)
                elif x0 < 317:
                    columns["value_date"].append(text)
                elif x0 < 401:
                    columns["debit"].append(text)
                elif x0 < 485:
                    columns["credit"].append(text)
                else:
                    columns["balance"].append(text)

            row.trans_date = " ".join(columns["trans_date"]).strip()
            row.narration = " ".join(columns["narration"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.debit = " ".join(columns["debit"]).strip()
            row.credit = " ".join(columns["credit"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part
                for part in (row.trans_date, row.narration, row.value_date, row.debit, row.credit, row.balance)
                if part
            ).upper()
            if not text_blob:
                continue
            if any(
                term in text_blob
                for term in (
                    "CUSTOMER NAME:",
                    "ACCOUNT NO:",
                    "ADDRESS:",
                    "PERIOD:",
                    "UNCLEARED",
                    "OPENING",
                    "BRANCH:",
                    "CURRENCY:",
                    "ACCOUNT TYPE:",
                    "CLOSING",
                    "JC62312",
                    "PAGE ",
                )
            ):
                continue
            if row.is_footer_total():
                continue
            if row.is_noise():
                continue
            rows.append(row)

        return rows


def build_transaction(
    rows: list[ParsedRow],
    *,
    metadata: StatementMetadata,
    parser_name: str,
    source_page: int,
) -> Transaction | None:
    start = rows[0]
    trans_year = None
    value_year = None
    narration_parts: list[str] = []
    debit_parts = [start.debit]
    credit_parts = [start.credit]
    balance_parts = [start.balance]

    for row in rows[1:]:
        if row.is_noise():
            continue
        if row.is_year_row():
            trans_year = extract_year(row.trans_date) or trans_year
            value_year = extract_year(row.value_date) or value_year
        if row.narration:
            narration_parts.append(row.narration)
        if row.debit:
            debit_parts.append(row.debit)
        if row.credit:
            credit_parts.append(row.credit)
        if row.balance:
            balance_parts.append(row.balance)

    transaction_date = parse_compound_date(start.trans_date, trans_year)
    if transaction_date is None:
        return None

    description = clean_text(" ".join(part for part in [start.narration, *narration_parts] if part))
    reference = extract_reference(description)

    return Transaction(
        transaction_date=transaction_date,
        description=description,
        debit=parse_decimal(join_number_parts(debit_parts)) or Decimal("0"),
        credit=parse_decimal(join_number_parts(credit_parts)) or Decimal("0"),
        balance=parse_decimal(join_number_parts(balance_parts)),
        reference=reference,
        currency=metadata.currency or "NGN",
        raw_text=clean_text(
            " ".join(
                part
                for part in (
                    compose_date_text(start.trans_date, trans_year),
                    description,
                    compose_date_text(start.value_date, value_year),
                    join_number_parts(debit_parts),
                    join_number_parts(credit_parts),
                    join_number_parts(balance_parts),
                )
                if part
            )
        ),
        source_page=source_page,
        parser_name=parser_name,
    )


def extract_totals_from_last_page(page: pdfplumber.page.Page) -> tuple[Decimal | None, Decimal | None]:
    words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
    debit_parts: list[str] = []
    credit_parts: list[str] = []

    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        top = float(word["top"])
        x0 = float(word["x0"])
        text = clean_text(word["text"])
        if top < 648 or top > 680 or not re.search(r"\d", text):
            continue
        if 317 <= x0 < 401:
            debit_parts.append(text)
        elif 401 <= x0 < 485:
            credit_parts.append(text)

    return parse_decimal(join_number_parts(debit_parts)), parse_decimal(join_number_parts(credit_parts))


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else None


def parse_compound_date(prefix: str, year: str | None):
    composed = compose_date_text(prefix, year)
    if not composed:
        return None
    return datetime.strptime(composed, "%d-%b-%Y").date()


def compose_date_text(prefix: str, year: str | None) -> str | None:
    cleaned = clean_text(prefix)
    match = re.match(r"(?P<day>\d{2})-(?P<month>[A-Za-z]{3,5})-", cleaned)
    if not match or not year:
        return None
    month = normalize_month(match.group("month"))
    return f"{match.group('day')}-{month}-{year}"


def normalize_month(value: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", value)
    if len(letters) >= 3:
        return letters[-3:].title()
    return letters.title()


def extract_year(value: str) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 4 and digits.startswith("20"):
        return digits
    return None


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%d-%b-%Y").date()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if cleaned.count(".") > 1:
        head, tail = cleaned.split(".", 1)
        cleaned = head + "." + tail.replace(".", "")
    if not cleaned or not any(character.isdigit() for character in cleaned):
        return None
    if cleaned.endswith("."):
        return None
    return Decimal(cleaned)


def join_number_parts(parts: list[str]) -> str:
    filtered = [clean_text(part) for part in parts if clean_text(part)]
    if not filtered:
        return ""
    combined = filtered[0]
    for part in filtered[1:]:
        if re.fullmatch(r"\d{1,2}", part) and combined.endswith("."):
            combined += part
        else:
            combined = clean_text(f"{combined} {part}")
    return combined


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").split())


def extract_reference(description: str) -> str | None:
    match = re.search(r"\bRef:\s*([A-Z0-9]+)\b", description, flags=re.IGNORECASE)
    return match.group(1) if match else None
