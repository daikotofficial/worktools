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
class WordRow:
    top: float
    words: list[dict]


@dataclass(slots=True)
class HeaderBounds:
    posting_boundary: float
    value_boundary: float
    description_boundary: float
    deposit_boundary: float
    withdrawal_boundary: float
    header_top: float


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    posting_date: str = ""
    value_date: str = ""
    description: str = ""
    deposit: str = ""
    withdrawal: str = ""
    balance: str = ""

    @property
    def has_posting_date(self) -> bool:
        return parse_date(self.posting_date) is not None

    @property
    def has_value_date(self) -> bool:
        return parse_date(self.value_date) is not None

    @property
    def has_date(self) -> bool:
        return self.has_posting_date or self.has_value_date

    @property
    def has_amounts(self) -> bool:
        return any((self.deposit, self.withdrawal, self.balance))

    @property
    def text(self) -> str:
        return clean_text(
            " ".join(
                part
                for part in (
                    self.posting_date,
                    self.value_date,
                    self.description,
                    self.deposit,
                    self.withdrawal,
                    self.balance,
                )
                if part
            )
        )

    def is_header(self) -> bool:
        upper = normalized_text(self.text)
        return (
            "DESCRIPTION" in upper
            and "DEPOSIT" in upper
            and "WITHDRAWAL" in upper
            and "BALANCE" in upper
            and upper.count("DATE") >= 2
        )

    def is_opening_balance(self) -> bool:
        return "BALANCE BROUGHT FORWARD" in normalized_text(self.description)

    def is_page_footer(self) -> bool:
        upper = normalized_text(self.text)
        return upper.startswith("PAGE ") and " OF " in upper

    def is_footer_noise(self) -> bool:
        upper = normalized_text(self.text)
        return any(
            term in upper
            for term in (
                "THANK YOU FOR BANKING WITH STANDARD CHARTERED BANK",
                "ALTHOUGH UNCLEARED ITEMS RECEIVED FOR CREDIT",
                "THE STATEMENT AND ENCLOSURES WILL BE CONSIDERED CORRECT",
                "WHERE THERE IS NO NOTICE OF DISCREPANCY",
                "DEPOSITS AND PAYMENTS ARE GOVERNED",
            )
        ) or upper.startswith("DATE :")

    def is_continuation(self) -> bool:
        return bool(not self.has_date and not self.is_page_footer() and not self.is_footer_noise() and self.text)


class StandardCharteredStatementParser(StatementParser):
    bank_name = "standard-chartered"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "standard_chartered_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)

        transactions: list[Transaction] = []
        opening_balance_added = False

        for row in rows:
            if row.is_opening_balance():
                opening_balance = parse_decimal(row.balance) or (
                    self.last_metadata.opening_balance if self.last_metadata else None
                )
                if opening_balance is not None and not opening_balance_added:
                    if self.last_metadata is not None and self.last_metadata.opening_balance is None:
                        self.last_metadata.opening_balance = opening_balance
                    transactions.append(
                        Transaction(
                            transaction_date=None,
                            description="Opening Balance",
                            debit=Decimal("0"),
                            credit=Decimal("0"),
                            balance=opening_balance,
                            currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                            raw_text=row.text,
                            source_page=row.page_number,
                            parser_name=self.bank_name,
                        )
                    )
                    opening_balance_added = True
                continue

            if not row.has_date:
                continue

            description = row.description or "Unlabeled Transaction"
            reference = extract_reference(description)
            transaction_date = parse_date(row.value_date) or parse_date(row.posting_date)

            transactions.append(
                Transaction(
                    transaction_date=transaction_date,
                    description=description,
                    debit=parse_decimal(row.withdrawal) or Decimal("0"),
                    credit=parse_decimal(row.deposit) or Decimal("0"),
                    balance=parse_decimal(row.balance),
                    reference=reference,
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=row.text,
                    source_page=row.page_number,
                    parser_name=self.bank_name,
                )
            )

        if self.last_metadata is not None:
            if self.last_metadata.opening_balance is None and transactions:
                self.last_metadata.opening_balance = transactions[0].balance
            if self.last_metadata.closing_balance is None:
                self.last_metadata.closing_balance = next(
                    (item.balance for item in reversed(transactions) if item.balance is not None),
                    None,
                )

        if not opening_balance_added and self.last_metadata and self.last_metadata.opening_balance is not None:
            transactions.insert(
                0,
                Transaction(
                    transaction_date=None,
                    description="Opening Balance",
                    debit=Decimal("0"),
                    credit=Decimal("0"),
                    balance=self.last_metadata.opening_balance,
                    currency=self.last_metadata.currency or "NGN",
                    raw_text="Opening Balance",
                    source_page=1,
                    parser_name=self.bank_name,
                ),
            )

        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page = pdf.pages[0]
            first_page_text = first_page.extract_text() or ""
            first_page_layout_text = first_page.extract_text(layout=True) or first_page_text

        period_match = re.search(
            r"STATEMENT DATE\s*:\s*(\d{2}\s+[A-Za-z]{3}\s+\d{4})\s+To\s+(\d{2}\s+[A-Za-z]{3}\s+\d{4})",
            first_page_text,
            flags=re.IGNORECASE,
        )

        return StatementMetadata(
            account_name=extract_account_name(first_page_layout_text),
            account_number=extract_regex(first_page_text, r"ACCOUNT NUMBER\s*:\s*(\d+)"),
            currency=normalize_currency(extract_regex(first_page_text, r"CURRENCY\s*:\s*([A-Z ]+)")),
            opening_balance=parse_decimal(
                extract_regex(first_page_text, r"BALANCE BROUGHT FORWARD\s+(-?[0-9,]+\.\d{2})")
            ),
            closing_balance=None,
            period_start=parse_period_date(period_match.group(1)) if period_match else None,
            period_end=parse_period_date(period_match.group(2)) if period_match else None,
        )

    def _extract_rows(self, pdf_path: Path) -> list[ParsedRow]:
        with open_pdf(pdf_path) as pdf:
            return extract_rows_from_pages(list(pdf.pages))


def extract_rows_from_pages(pages: list[pdfplumber.page.Page]) -> list[ParsedRow]:
    parsed_rows: list[ParsedRow] = []

    for page_number, page in enumerate(pages, start=1):
        words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
        if not words:
            continue

        rows = group_words_into_rows(words)
        bounds = find_header_bounds(rows, float(page.width))
        if bounds is None:
            continue

        current_row: ParsedRow | None = None
        current_posting_date: str | None = None

        for row in rows:
            if row.top <= bounds.header_top + 2:
                continue

            parsed = split_row(row, page_number, bounds)
            if not parsed.text or parsed.is_header():
                continue
            if parsed.is_page_footer():
                break
            if parsed.is_footer_noise():
                continue

            if parsed.has_posting_date:
                current_posting_date = clean_text(parsed.posting_date)
            elif parsed.has_value_date:
                parsed.posting_date = current_posting_date or clean_text(parsed.value_date)

            if parsed.is_opening_balance():
                parsed_rows.append(parsed)
                current_row = None
                continue

            if parsed.has_date:
                parsed_rows.append(parsed)
                current_row = parsed
                continue

            if current_row is None or not parsed.is_continuation():
                continue

            merge_continuation_row(current_row, parsed)

    return parsed_rows


def group_words_into_rows(words: list[dict]) -> list[WordRow]:
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

    return [
        WordRow(
            top=min(float(word["top"]) for word in row_words),
            words=sorted(row_words, key=lambda item: item["x0"]),
        )
        for row_words in grouped
    ]


def find_header_bounds(rows: list[WordRow], page_width: float) -> HeaderBounds | None:
    for row in rows:
        texts = [normalized_text(word["text"]) for word in row.words]
        date_indexes = [index for index, text in enumerate(texts) if text == "DATE"]
        try:
            description_index = texts.index("DESCRIPTION")
            deposit_index = texts.index("DEPOSIT")
            withdrawal_index = texts.index("WITHDRAWAL")
            balance_index = texts.index("BALANCE")
        except ValueError:
            continue

        if len(date_indexes) < 2:
            continue

        first_date_word = row.words[date_indexes[0]]
        second_date_word = row.words[date_indexes[1]]
        deposit_word = row.words[deposit_index]
        withdrawal_word = row.words[withdrawal_index]
        balance_word = row.words[balance_index]

        return HeaderBounds(
            posting_boundary=midpoint(float(first_date_word["x1"]), float(second_date_word["x0"])),
            value_boundary=float(second_date_word["x1"]) + (page_width * 0.03),
            description_boundary=float(deposit_word["x0"]) - 8,
            deposit_boundary=float(withdrawal_word["x0"]) - 8,
            withdrawal_boundary=float(balance_word["x0"]) - 8,
            header_top=row.top,
        )
    return None


def split_row(row: WordRow, page_number: int, bounds: HeaderBounds) -> ParsedRow:
    columns = {
        "posting_date": [],
        "value_date": [],
        "description": [],
        "deposit": [],
        "withdrawal": [],
        "balance": [],
    }

    for word in row.words:
        center = (float(word["x0"]) + float(word["x1"])) / 2
        text = word["text"]
        if center < bounds.posting_boundary:
            columns["posting_date"].append(text)
        elif center < bounds.value_boundary:
            columns["value_date"].append(text)
        elif center < bounds.description_boundary:
            columns["description"].append(text)
        elif center < bounds.deposit_boundary:
            columns["deposit"].append(text)
        elif center < bounds.withdrawal_boundary:
            columns["withdrawal"].append(text)
        else:
            columns["balance"].append(text)

    return ParsedRow(
        page_number=page_number,
        top=row.top,
        posting_date=" ".join(columns["posting_date"]).strip(),
        value_date=" ".join(columns["value_date"]).strip(),
        description=" ".join(columns["description"]).strip(),
        deposit=" ".join(columns["deposit"]).strip(),
        withdrawal=" ".join(columns["withdrawal"]).strip(),
        balance=" ".join(columns["balance"]).strip(),
    )


def merge_continuation_row(current_row: ParsedRow, continuation: ParsedRow) -> None:
    if continuation.description:
        current_row.description = clean_text(f"{current_row.description} {continuation.description}")
    if continuation.deposit:
        current_row.deposit = continuation.deposit
    if continuation.withdrawal:
        current_row.withdrawal = continuation.withdrawal
    if continuation.balance:
        current_row.balance = continuation.balance


def extract_account_name(first_page_layout_text: str) -> str | None:
    lines = [clean_text(line) for line in first_page_layout_text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        if normalized_text(line) != "ACCOUNT STATEMENT":
            continue
        for candidate in lines[index + 1 :]:
            if ":" in candidate:
                continue
            upper = normalized_text(candidate)
            if upper in {"VALUE", "DATE"}:
                continue
            return candidate
    return None


def extract_reference(description: str) -> str | None:
    matches: list[str] = []
    for pattern in (
        r"\bNG-\d{3}-\d{6}-\d+-\d+-\d+\b",
        r"\bIL[0-9A-Z]{8,}\b",
        r"\bAUTHCODE:\s*[0-9A-Z]+\b",
        r"\b\d{20,}\b",
    ):
        for match in re.findall(pattern, description, flags=re.IGNORECASE):
            cleaned = clean_text(match)
            if cleaned not in matches:
                matches.append(cleaned)
    return " | ".join(matches) or None


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return clean_text(match.group(1))


def normalize_currency(value: str | None) -> str | None:
    cleaned = clean_text(value or "")
    if not cleaned:
        return None
    upper = cleaned.upper()
    if "NAIRA" in upper or upper == "NGN":
        return "NGN"
    return cleaned


def midpoint(left: float, right: float) -> float:
    return (left + right) / 2


def normalized_text(value: str) -> str:
    return " ".join((value or "").upper().replace("\n", " ").split())


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").split())


def parse_date(value: str):
    cleaned = clean_text(value)
    if not cleaned:
        return None
    match = re.search(
        r"\b\d{1,2}[/-][A-Za-z0-9]{1,3}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b",
        cleaned,
    )
    if match:
        cleaned = match.group(0)
    for pattern in ("%d %b %Y", "%d %b %y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%d %b %Y").date()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = clean_text(value).upper()
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = re.sub(r"\b(?:CR|DR|NGN)\b", "", cleaned)
    match = re.search(r"-?[0-9,]+(?:\.\d{1,2})?", cleaned)
    if not match:
        return None
    try:
        parsed = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    return -parsed if negative else parsed
