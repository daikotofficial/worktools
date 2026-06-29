from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber

from statement_analyzer.layouts import detect_layout
from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.generic import clean_text, normalized_text, parse_decimal_from_cell
from statement_analyzer.parsers.pdf_utils import open_pdf


DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")


@dataclass(slots=True)
class WordLine:
    page_number: int
    top: float
    words: list[dict]

    @property
    def text(self) -> str:
        return clean_text(" ".join(word["text"] for word in sorted(self.words, key=lambda item: item["x0"])))


@dataclass(slots=True)
class PendingRow:
    page_number: int
    words: list[dict] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)
    amount_top: float | None = None

    def add_line(self, line: WordLine) -> None:
        self.words.extend(line.words)
        self.raw_lines.append(line.text)
        if line_has_amounts(line):
            self.amount_top = line.top

    @property
    def has_amounts(self) -> bool:
        return any(word_amount_semantic(word) is not None for word in self.words)


class WemaTreasureStatementParser(StatementParser):
    bank_name = "wema-treasure"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "wema_treasure_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        pending_rows: list[PendingRow] = []
        current: PendingRow | None = None
        prefix_lines: list[WordLine] = []

        with open_pdf(pdf_path) as pdf:
            pages = list(pdf.pages)
            self.last_metadata = self._extract_metadata(pages[0])
            for page_number, page in enumerate(pages, start=1):
                for line in extract_word_lines(page, page_number):
                    if is_noise_line(line):
                        continue

                    if is_transaction_start(line):
                        if current is not None and current.has_amounts:
                            pending_rows.append(current)
                        current = PendingRow(page_number=page_number)
                        for prefix in prefix_lines:
                            current.add_line(prefix)
                        prefix_lines = []
                        current.add_line(line)
                        continue

                    if current is not None:
                        if current.has_amounts and current.amount_top is not None and line.top - current.amount_top > 16:
                            if is_prefix_candidate(line):
                                prefix_lines.append(line)
                            continue
                        current.add_line(line)
                    elif is_prefix_candidate(line):
                        prefix_lines.append(line)

            if current is not None and current.has_amounts:
                pending_rows.append(current)

        transactions = [build_transaction(row, self.last_metadata) for row in pending_rows]
        return [transaction for transaction in transactions if transaction is not None]

    def _extract_metadata(self, first_page: pdfplumber.page.Page) -> StatementMetadata:
        text = first_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
        normalized = normalized_text(text)
        period_match = re.search(
            r"ACCT NAME:\s+.+?\s+(\d{2}-\d{2}-\d{4})\s+TO\s+(\d{2}-\d{2}-\d{4})",
            normalized,
        )
        totals_match = re.search(
            r"([0-9,]+\.\d{2})\s+TOTAL DEBIT:\s+TOTAL CREDIT:\s+([0-9,]+\.\d{2})",
            normalized,
        )

        return StatementMetadata(
            account_name=extract_regex(text, r"Acct Name:\s+(.+?)\s+\d{2}-\d{2}-\d{4}\s+To"),
            account_number=extract_regex(text, r"Acct No:\s*(\d+)"),
            currency=extract_regex(text, r"Currency:\s*([A-Z]{3})"),
            opening_balance=parse_summary_amount(text, r"Opening Bal:\s*([0-9,]+\.\d{2})"),
            total_debit=parse_decimal_from_cell(totals_match.group(1), semantic="amount") if totals_match else None,
            total_credit=parse_decimal_from_cell(totals_match.group(2), semantic="amount") if totals_match else None,
            closing_balance=parse_summary_amount(text, r"Closing Bal:\s*([0-9,]+\.\d{2})"),
            period_start=parse_period_date(period_match.group(1)) if period_match else None,
            period_end=parse_period_date(period_match.group(2)) if period_match else None,
        )


def extract_word_lines(page: pdfplumber.page.Page, page_number: int) -> list[WordLine]:
    words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
    if not words:
        return []

    grouped: list[list[dict]] = []
    current: list[dict] = []
    current_top: float | None = None

    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        top = float(word["top"])
        if current_top is None or abs(top - current_top) <= 2.5:
            current.append(word)
            current_top = top if current_top is None else (current_top + top) / 2
        else:
            grouped.append(current)
            current = [word]
            current_top = top

    if current:
        grouped.append(current)

    return [
        WordLine(page_number=page_number, top=min(float(word["top"]) for word in group), words=group)
        for group in grouped
    ]


def is_transaction_start(line: WordLine) -> bool:
    left_dates = [word for word in line.words if float(word["x0"]) < 100 and DATE_RE.match(word["text"])]
    value_dates = [word for word in line.words if 100 <= float(word["x0"]) < 190 and DATE_RE.match(word["text"])]
    return bool(left_dates and value_dates)


def is_prefix_candidate(line: WordLine) -> bool:
    if line_has_amounts(line) or is_transaction_start(line):
        return False
    text = normalized_text(line.text)
    if not text or is_metadata_text(text):
        return False
    return any(190 <= float(word["x0"]) < 420 for word in line.words)


def is_noise_line(line: WordLine) -> bool:
    text = normalized_text(line.text)
    if not text:
        return True
    if len(line.words) == 1 and re.fullmatch(r"\d+", text):
        return True
    return is_metadata_text(text)


def is_metadata_text(text: str) -> bool:
    return (
        text.startswith("STATEMENT PERIOD")
        or text.startswith("ACCT NAME")
        or text.startswith("ACCT NO")
        or text.startswith("OPENING BAL")
        or text.startswith("CURRENCY")
        or text.startswith("CURRENT BAL")
        or text.startswith("ACCT TYPE")
        or text.startswith("TOTAL DEBIT")
        or text.startswith("ADDRESS")
        or text.startswith("DATE PRINTED")
        or text.startswith("TRAN DATE")
    )


def line_has_amounts(line: WordLine) -> bool:
    return any(word_amount_semantic(word) is not None for word in line.words)


def word_amount_semantic(word: dict) -> str | None:
    center = (float(word["x0"]) + float(word["x1"])) / 2
    if center < 560:
        return None
    if parse_decimal_from_cell(word["text"], semantic="amount") is None:
        return None
    if center < 670:
        return "debit"
    if center < 780:
        return "credit"
    return "balance"


def build_transaction(row: PendingRow, metadata: StatementMetadata | None) -> Transaction | None:
    transaction_date = parse_transaction_date(row.words)
    debit = extract_amount(row.words, "debit") or Decimal("0")
    credit = extract_amount(row.words, "credit") or Decimal("0")
    balance = extract_amount(row.words, "balance")
    if transaction_date is None or balance is None:
        return None

    narration_words: list[str] = []
    reference_words: list[str] = []
    for word in sorted(row.words, key=lambda item: (item["top"], item["x0"])):
        semantic = word_amount_semantic(word)
        if semantic is not None:
            continue

        text = word["text"]
        x0 = float(word["x0"])
        if DATE_RE.match(text) and x0 < 190:
            continue
        if 190 <= x0 < 420:
            narration_words.append(text)
        elif 420 <= x0 < 560:
            reference_words.append(text)

    description = clean_text(" ".join(narration_words))
    reference = clean_text(" ".join(reference_words)) or None

    return Transaction(
        transaction_date=transaction_date,
        description=description or "Wema Treasure transaction",
        debit=debit,
        credit=credit,
        balance=balance,
        reference=reference,
        currency=metadata.currency or "NGN" if metadata else "NGN",
        raw_text=clean_text(" ".join(row.raw_lines)),
        source_page=row.page_number,
        parser_name=WemaTreasureStatementParser.bank_name,
    )


def parse_transaction_date(words: list[dict]):
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if float(word["x0"]) < 100 and DATE_RE.match(word["text"]):
            return datetime.strptime(word["text"], "%d-%m-%Y").date()
    return None


def extract_amount(words: list[dict], semantic: str) -> Decimal | None:
    matches = [word for word in words if word_amount_semantic(word) == semantic]
    if not matches:
        return None
    matches = sorted(matches, key=lambda item: (item["top"], item["x0"]))
    return parse_decimal_from_cell(matches[-1]["text"], semantic=semantic)


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else None


def parse_summary_amount(text: str, pattern: str) -> Decimal | None:
    matched = extract_regex(text, pattern)
    return parse_decimal_from_cell(matched, semantic="amount") if matched else None


def parse_period_date(value: str):
    return datetime.strptime(value, "%d-%m-%Y").date()
