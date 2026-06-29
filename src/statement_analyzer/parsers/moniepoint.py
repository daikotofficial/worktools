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


DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:?$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
DEFAULT_PAGE_WIDTH = 838.35


@dataclass(frozen=True, slots=True)
class AmountColumnBands:
    debit_center: float
    credit_center: float
    balance_center: float
    left_boundary: float
    debit_credit_boundary: float
    credit_balance_boundary: float

    @classmethod
    def from_centers(
        cls,
        debit_center: float,
        credit_center: float,
        balance_center: float,
    ) -> AmountColumnBands | None:
        if not debit_center < credit_center < balance_center:
            return None
        debit_credit_boundary = midpoint(debit_center, credit_center)
        credit_balance_boundary = midpoint(credit_center, balance_center)
        left_boundary = debit_center - max(10.0, (credit_center - debit_center) / 2)
        return cls(
            debit_center=debit_center,
            credit_center=credit_center,
            balance_center=balance_center,
            left_boundary=left_boundary,
            debit_credit_boundary=debit_credit_boundary,
            credit_balance_boundary=credit_balance_boundary,
        )

    @classmethod
    def from_page_width(cls, page_width: float) -> AmountColumnBands:
        scale = page_width / DEFAULT_PAGE_WIDTH
        return cls(
            debit_center=672.0 * scale,
            credit_center=719.5 * scale,
            balance_center=775.9 * scale,
            left_boundary=648.0 * scale,
            debit_credit_boundary=695.8 * scale,
            credit_balance_boundary=747.7 * scale,
        )

    def semantic_for(self, word: dict) -> str | None:
        x0 = float(word["x0"])
        center = word_center(word)
        if x0 < self.left_boundary - 8:
            return None
        if center < self.left_boundary:
            return None
        if center < self.debit_credit_boundary:
            return "debit"
        if center < self.credit_balance_boundary:
            return "credit"
        return "balance"


@dataclass(frozen=True, slots=True)
class TableLayout:
    page_width: float
    date_right: float
    narration_left: float
    reference_left: float
    reference_right: float
    amount_columns: AmountColumnBands

    @classmethod
    def from_page_width(cls, page_width: float) -> TableLayout:
        amount_columns = AmountColumnBands.from_page_width(page_width)
        return cls(
            page_width=page_width,
            date_right=105.0 * page_width / DEFAULT_PAGE_WIDTH,
            narration_left=105.0 * page_width / DEFAULT_PAGE_WIDTH,
            reference_left=335.0 * page_width / DEFAULT_PAGE_WIDTH,
            reference_right=amount_columns.left_boundary,
            amount_columns=amount_columns,
        )


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
    layout: TableLayout | None = None
    words: list[dict] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)
    amount_top: float | None = None

    def add_line(self, line: WordLine) -> None:
        self.words.extend(line.words)
        self.raw_lines.append(line.text)
        if line_has_amounts(line, self.layout):
            self.amount_top = line.top

    @property
    def has_amounts(self) -> bool:
        return any(word_amount_semantic(word, self.layout) is not None for word in self.words)


class MoniepointStatementParser(StatementParser):
    bank_name = "moniepoint"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "moniepoint_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        pending_rows: list[PendingRow] = []
        current: PendingRow | None = None
        prefix_lines: list[WordLine] = []

        with open_pdf(pdf_path) as pdf:
            pages = list(pdf.pages)
            self.last_metadata = self._extract_metadata(pages[0])
            layout = infer_table_layout(pages)
            for page_number, page in enumerate(pages, start=1):
                for line in extract_word_lines(page, page_number):
                    if is_noise_line(line):
                        continue

                    if is_transaction_start(line, layout):
                        if current is not None and current.has_amounts:
                            pending_rows.append(current)
                        current = PendingRow(page_number=page_number, layout=layout)
                        for prefix in prefix_lines:
                            current.add_line(prefix)
                        prefix_lines = []
                        current.add_line(line)
                        continue

                    if current is not None:
                        if current.has_amounts and current.amount_top is not None and line.top - current.amount_top > 16:
                            if is_prefix_candidate(line, layout):
                                prefix_lines.append(line)
                            continue
                        current.add_line(line)
                    elif is_prefix_candidate(line, layout):
                        prefix_lines.append(line)

            if current is not None and current.has_amounts:
                pending_rows.append(current)

        transactions = [build_transaction(row, self.last_metadata) for row in pending_rows]
        transactions = [transaction for transaction in transactions if transaction is not None]

        if self.last_metadata and self.last_metadata.opening_balance is not None:
            transactions.insert(
                0,
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
                ),
            )

        return transactions

    def _extract_metadata(self, first_page: pdfplumber.page.Page) -> StatementMetadata:
        text = first_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
        normalized = normalized_text(text)
        period_match = re.search(
            r"\b(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})\b",
            normalized,
        )

        return StatementMetadata(
            account_name=extract_regex(text, r"Business Name\s+(.+?)\s+Account Number"),
            account_number=extract_regex(text, r"Account Number\s+(\d+)"),
            currency=extract_regex(text, r"Currency\s+([A-Z]{3})"),
            opening_balance=parse_summary_amount(text, r"Opening\s+([0-9,]+\.\d{2})\s+Currency"),
            total_debit=parse_summary_amount(text, r"Total Debits\s+([0-9,]+\.\d{2})"),
            total_credit=parse_summary_amount(text, r"Total Credits\s+([0-9,]+\.\d{2})"),
            closing_balance=parse_summary_amount(text, r"Closing\s+.*?([0-9,]+\.\d{2})\s+Balance"),
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


def infer_table_layout(pages: list[pdfplumber.page.Page]) -> TableLayout | None:
    if not pages:
        return None

    for page_number, page in enumerate(pages[: min(3, len(pages))], start=1):
        for line in extract_word_lines(page, page_number):
            layout = infer_table_layout_from_header(line, float(page.width))
            if layout is not None:
                return layout

    return TableLayout.from_page_width(float(pages[0].width))


def infer_table_layout_from_header(line: WordLine, page_width: float) -> TableLayout | None:
    labels: dict[str, dict] = {}
    for word in line.words:
        label = normalized_text(word["text"])
        if label == "DATE":
            labels["date"] = word
        elif label == "NARRATION":
            labels["narration"] = word
        elif label == "REFERENCE":
            labels["reference"] = word
        elif label in {"DEBIT", "DEBITS"}:
            labels["debit"] = word
        elif label in {"CREDIT", "CREDITS"}:
            labels["credit"] = word
        elif label == "BALANCE":
            labels["balance"] = word

    required_labels = {"date", "narration", "reference", "debit", "credit", "balance"}
    if set(labels) < required_labels:
        return None

    amount_columns = AmountColumnBands.from_centers(
        word_center(labels["debit"]),
        word_center(labels["credit"]),
        word_center(labels["balance"]),
    )
    if amount_columns is None:
        return None

    narration_left = float(labels["narration"]["x0"])
    reference_left = float(labels["reference"]["x0"])
    if not narration_left < reference_left < amount_columns.left_boundary:
        return None

    return TableLayout(
        page_width=page_width,
        date_right=max(
            float(labels["date"]["x1"]),
            narration_left - max(6.0, page_width * 0.01),
        ),
        narration_left=narration_left,
        reference_left=reference_left,
        reference_right=amount_columns.left_boundary,
        amount_columns=amount_columns,
    )


def midpoint(left: float, right: float) -> float:
    return (left + right) / 2


def word_center(word: dict) -> float:
    return (float(word["x0"]) + float(word["x1"])) / 2


def is_transaction_start(line: WordLine, layout: TableLayout | None = None) -> bool:
    date_right = layout.date_right if layout is not None else 105
    return any(float(word["x0"]) < date_right and DATE_PREFIX_RE.match(word["text"]) for word in line.words)


def is_prefix_candidate(line: WordLine, layout: TableLayout | None = None) -> bool:
    if line_has_amounts(line, layout) or is_transaction_start(line, layout):
        return False
    text = normalized_text(line.text)
    if not text or is_metadata_text(text):
        return False
    narration_left = layout.narration_left if layout is not None else 105
    reference_left = layout.reference_left if layout is not None else 335
    return any(narration_left <= float(word["x0"]) < reference_left for word in line.words)


def is_noise_line(line: WordLine) -> bool:
    text = normalized_text(line.text)
    if not text:
        return True
    if len(line.words) == 1 and re.fullmatch(r"\d+", text):
        return True
    if is_metadata_text(text):
        return True
    return False


def is_metadata_text(text: str) -> bool:
    return (
        "ACCOUNT STATEMENT" in text
        or "ACCOUNT SUMMARY" in text
        or text.startswith("BUSINESS NAME")
        or text.startswith("ACCOUNT NUMBER")
        or text.startswith("CURRENCY")
        or text.startswith("DATE ")
        or text.startswith("ADDRESS")
        or text in {"OPENING", "BALANCE", "CLOSING"}
        or text.startswith("TOTAL DEBITS")
        or text.startswith("TOTAL CREDITS")
    )


def line_has_amounts(line: WordLine, layout: TableLayout | None = None) -> bool:
    return any(word_amount_semantic(word, layout) is not None for word in line.words)


def word_amount_semantic(word: dict, layout: TableLayout | None = None) -> str | None:
    x0 = float(word["x0"])
    if parse_decimal_from_cell(word["text"], semantic="amount") is None:
        return None

    if layout is not None:
        return layout.amount_columns.semantic_for(word)

    center = word_center(word)
    if x0 < 630:
        return None
    if center < 700:
        return "debit"
    if center < 750:
        return "credit"
    return "balance"


def build_transaction(row: PendingRow, metadata: StatementMetadata | None) -> Transaction | None:
    transaction_date = parse_transaction_date(row.words, row.layout)
    debit = extract_amount(row.words, "debit", row.layout) or Decimal("0")
    credit = extract_amount(row.words, "credit", row.layout) or Decimal("0")
    balance = extract_amount(row.words, "balance", row.layout)
    if transaction_date is None or balance is None:
        return None

    narration_words: list[str] = []
    reference_words: list[str] = []
    for word in sorted(row.words, key=lambda item: (item["top"], item["x0"])):
        semantic = word_amount_semantic(word, row.layout)
        if semantic is not None:
            continue

        text = word["text"]
        x0 = float(word["x0"])
        date_right = row.layout.date_right if row.layout is not None else 105
        reference_left = row.layout.reference_left if row.layout is not None else 335
        reference_right = row.layout.reference_right if row.layout is not None else 630

        if x0 < date_right:
            if DATE_PREFIX_RE.match(text) or TIME_RE.match(text):
                continue
            if "|" in text or text.startswith("/"):
                reference_words.append(text)
            continue
        if x0 < reference_left:
            narration_words.append(text)
        elif x0 < reference_right:
            reference_words.append(text)

    description = clean_text(" ".join(narration_words))
    reference = clean_text(" ".join(reference_words)) or None

    return Transaction(
        transaction_date=transaction_date,
        description=description or reference or "Moniepoint transaction",
        debit=debit,
        credit=credit,
        balance=balance,
        reference=reference,
        currency=metadata.currency or "NGN" if metadata else "NGN",
        raw_text=clean_text(" ".join(row.raw_lines)),
        source_page=row.page_number,
        parser_name=MoniepointStatementParser.bank_name,
    )


def parse_transaction_date(words: list[dict], layout: TableLayout | None = None):
    date_right = layout.date_right if layout is not None else 105
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if float(word["x0"]) < date_right and DATE_PREFIX_RE.match(word["text"]):
            return datetime.strptime(word["text"][:10], "%Y-%m-%d").date()
    return None


def extract_amount(words: list[dict], semantic: str, layout: TableLayout | None = None) -> Decimal | None:
    matches = [
        word
        for word in words
        if word_amount_semantic(word, layout) == semantic
    ]
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
    return datetime.strptime(value, "%d/%m/%Y").date()
