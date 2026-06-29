from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pdfplumber

from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.generic import clean_text, normalized_text, parse_date, parse_decimal_from_cell
from statement_analyzer.parsers.pdf_utils import open_pdf


@dataclass(slots=True)
class WordRow:
    top: float
    words: list[dict]


@dataclass(slots=True)
class LedgerHeaderBounds:
    posting_boundary: float
    value_boundary: float
    description_boundary: float
    outflow_boundary: float
    inflow_boundary: float
    page_width: float
    header_top: float


@dataclass(slots=True)
class ParsedLedgerRow:
    page_number: int
    top: float
    posting_date: str = ""
    value_date: str = ""
    description: str = ""
    outflow: str = ""
    inflow: str = ""
    balance: str = ""

    @property
    def has_dates(self) -> bool:
        return bool(parse_date(self.posting_date) and parse_date(self.value_date))

    @property
    def has_amounts(self) -> bool:
        return any((self.outflow, self.inflow, self.balance))

    @property
    def text(self) -> str:
        return clean_text(
            " ".join(
                part
                for part in (
                    self.posting_date,
                    self.value_date,
                    self.description,
                    self.outflow,
                    self.inflow,
                    self.balance,
                )
                if part
            )
        )

    def is_footer(self) -> bool:
        return bool(re.fullmatch(r"PAGE\s+\d+\s+OF\s+\d+", normalized_text(self.text)))

    def is_continuation(self) -> bool:
        return bool(self.description and not self.has_dates and not self.has_amounts and not self.is_footer())


class PostingValueLedgerStatementParser(StatementParser):
    bank_name = "posting-value-ledger"
    HEADER_SIGNATURE = "POSTING DATE VALUE DATE DESCRIPTION OUTFLOW INFLOW BALANCE"

    def can_parse(self, pdf_path: Path) -> bool:
        if pdf_path.suffix.lower() != ".pdf":
            return False
        with open_pdf(pdf_path) as pdf:
            first_page_text = normalized_text(pdf.pages[0].extract_text() or "")
        return self.HEADER_SIGNATURE in first_page_text and "INFLOW VS OUTFLOW" in first_page_text

    def parse(self, pdf_path: Path) -> list[Transaction]:
        with open_pdf(pdf_path) as pdf:
            pages = list(pdf.pages)
            self.last_metadata = extract_dashboard_metadata(pages[0].extract_text() or "")
            rows = extract_ledger_rows_from_pages(pages)

        inferred_owner_name = infer_owner_name(rows)
        if self.last_metadata.account_name is None and inferred_owner_name:
            self.last_metadata.account_name = inferred_owner_name

        transactions: list[Transaction] = []
        if self.last_metadata.opening_balance is not None:
            transactions.append(
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
                )
            )

        for row in rows:
            if not row.has_dates:
                continue

            description, reference = split_reference_and_description(row.description)
            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.value_date) or parse_date(row.posting_date),
                    description=description or row.description or "Unlabeled Transaction",
                    debit=parse_decimal_from_cell(row.outflow, semantic="debit") or Decimal("0"),
                    credit=parse_decimal_from_cell(row.inflow, semantic="credit") or Decimal("0"),
                    balance=parse_decimal_from_cell(row.balance, semantic="balance"),
                    reference=reference,
                    currency=self.last_metadata.currency or "NGN",
                    raw_text=row.text,
                    source_page=row.page_number,
                    parser_name=self.bank_name,
                )
            )

        if transactions and self.last_metadata.closing_balance is None:
            closing = next((item.balance for item in reversed(transactions) if item.balance is not None), None)
            self.last_metadata.closing_balance = closing

        return transactions


def extract_ledger_rows_from_pages(pages: list[pdfplumber.page.Page]) -> list[ParsedLedgerRow]:
    parsed_rows: list[ParsedLedgerRow] = []
    current_row: ParsedLedgerRow | None = None

    for page_number, page in enumerate(pages, start=1):
        words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
        if not words:
            continue

        rows = group_words_into_rows(words)
        header_bounds = find_header_bounds(rows, float(page.width))
        if header_bounds is None:
            continue

        for row in rows:
            if row.top <= header_bounds.header_top + 4:
                continue

            parsed = split_row(row, page_number, header_bounds)
            if not parsed.text or parsed.is_footer():
                continue
            if parsed.is_continuation():
                if current_row is not None:
                    current_row.description = clean_text(f"{current_row.description} {parsed.description}")
                continue
            if not parsed.has_dates:
                continue

            parsed_rows.append(parsed)
            current_row = parsed

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


def find_header_bounds(rows: list[WordRow], page_width: float) -> LedgerHeaderBounds | None:
    for row in rows:
        texts = [normalized_text(word["text"]).strip() for word in row.words]
        try:
            posting_index = texts.index("POSTING")
            first_date_index = texts.index("DATE", posting_index + 1)
            value_index = texts.index("VALUE", first_date_index + 1)
            second_date_index = texts.index("DATE", value_index + 1)
            description_index = texts.index("DESCRIPTION", second_date_index + 1)
            outflow_index = texts.index("OUTFLOW", description_index + 1)
            inflow_index = texts.index("INFLOW", outflow_index + 1)
            balance_index = texts.index("BALANCE", inflow_index + 1)
        except ValueError:
            continue

        value_start = float(row.words[value_index]["x0"])
        description_start = float(row.words[description_index]["x0"])
        outflow_start = float(row.words[outflow_index]["x0"])
        inflow_start = float(row.words[inflow_index]["x0"])
        balance_start = float(row.words[balance_index]["x0"])

        posting_end = midpoint(float(row.words[first_date_index]["x1"]), value_start)
        value_end = midpoint(float(row.words[second_date_index]["x1"]), description_start)
        description_end = outflow_start - 6
        outflow_end = inflow_start - 6
        inflow_end = balance_start - 6

        return LedgerHeaderBounds(
            posting_boundary=posting_end,
            value_boundary=value_end,
            description_boundary=description_end,
            outflow_boundary=outflow_end,
            inflow_boundary=inflow_end,
            page_width=page_width,
            header_top=row.top,
        )
    return None


def split_row(row: WordRow, page_number: int, bounds: LedgerHeaderBounds) -> ParsedLedgerRow:
    columns = {
        "posting_date": [],
        "value_date": [],
        "description": [],
        "outflow": [],
        "inflow": [],
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
        elif center < bounds.outflow_boundary:
            columns["outflow"].append(text)
        elif center < bounds.inflow_boundary:
            columns["inflow"].append(text)
        else:
            columns["balance"].append(text)

    return ParsedLedgerRow(
        page_number=page_number,
        top=row.top,
        posting_date=clean_text(" ".join(columns["posting_date"])),
        value_date=clean_text(" ".join(columns["value_date"])),
        description=clean_text(" ".join(columns["description"])),
        outflow=clean_text(" ".join(columns["outflow"])),
        inflow=clean_text(" ".join(columns["inflow"])),
        balance=clean_text(" ".join(columns["balance"])),
    )


def extract_dashboard_metadata(first_page_text: str) -> StatementMetadata:
    cleaned = clean_text(first_page_text)
    period_match = re.search(
        r"FROM\s+TO\s+(\d{2}\s+[A-Za-z]{3}\s+\d{4})\s+(\d{2}\s+[A-Za-z]{3}\s+\d{4})",
        cleaned,
        flags=re.IGNORECASE,
    )
    balance_match = re.search(
        r"([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+Opening Balance\s+Closing Balance",
        cleaned,
        flags=re.IGNORECASE,
    )

    opening_balance = (
        parse_decimal_from_cell(balance_match.group(1), semantic="balance")
        if balance_match
        else None
    )
    closing_balance = (
        parse_decimal_from_cell(balance_match.group(2), semantic="balance")
        if balance_match
        else None
    )

    return StatementMetadata(
        currency="NGN",
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        period_start=parse_date(period_match.group(1)) if period_match else None,
        period_end=parse_date(period_match.group(2)) if period_match else None,
    )


def infer_owner_name(rows: list[ParsedLedgerRow]) -> str | None:
    counts: dict[str, int] = {}
    for row in rows:
        candidate = extract_self_candidate(row.description)
        if not candidate:
            continue
        counts[candidate] = counts.get(candidate, 0) + 1

    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], len(item[0])))[0]


def extract_self_candidate(description: str) -> str | None:
    cleaned = clean_text(description)
    if not cleaned:
        return None
    match = re.search(r"(.+?)\s+SELF\b", cleaned, flags=re.IGNORECASE)
    if not match:
        return None
    candidate = clean_text(match.group(1))
    candidate, _ = split_reference_and_description(candidate)
    if len(candidate.split()) < 2:
        return None
    return candidate


def split_reference_and_description(description: str) -> tuple[str, str]:
    cleaned = clean_text(description)
    if not cleaned:
        return "", ""

    tokens = cleaned.split()
    reference = ""
    if tokens and looks_like_reference_token(tokens[0]):
        reference = tokens[0]
        cleaned = " ".join(tokens[1:])
    return clean_text(cleaned), reference


def looks_like_reference_token(token: str) -> bool:
    upper = token.upper()
    if upper.startswith("ORG.") or upper.startswith("ORG"):
        return False
    if len(token) >= 18 and re.fullmatch(r"[A-Z0-9./_-]+", upper):
        return True
    if re.fullmatch(r"S\d{6,}[A-Z0-9-]*", upper):
        return True
    return False


def midpoint(left: float, right: float) -> float:
    return (left + right) / 2
