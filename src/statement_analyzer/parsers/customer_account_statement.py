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
from statement_analyzer.parsers.generic import clean_text, normalized_text, parse_decimal, parse_decimal_from_cell
from statement_analyzer.parsers.pdf_utils import open_pdf


@dataclass(slots=True)
class WordRow:
    top: float
    words: list[dict]


@dataclass(slots=True)
class HeaderBounds:
    date_boundary: float
    reference_boundary: float
    narration_boundary: float
    debit_boundary: float
    credit_boundary: float
    page_width: float
    header_top: float


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    date_text: str = ""
    reference: str = ""
    narration: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""

    @property
    def text(self) -> str:
        return clean_text(
            " ".join(
                part
                for part in (
                    self.date_text,
                    self.reference,
                    self.narration,
                    self.debit,
                    self.credit,
                    self.balance,
                )
                if part
            )
        )

    @property
    def has_amounts(self) -> bool:
        return any(
            (
                parse_decimal_from_cell(self.debit, semantic="debit") is not None,
                parse_decimal_from_cell(self.credit, semantic="credit") is not None,
                parse_decimal_from_cell(self.balance, semantic="balance") is not None,
            )
        )

    def is_header(self) -> bool:
        upper = normalized_text(self.text)
        return (
            "DATE" in upper
            and "REFERENCE" in upper
            and "NARRATION" in upper
            and "DEBIT" in upper
            and "CREDIT" in upper
            and "BALANCE" in upper
        )

    def is_footer(self) -> bool:
        upper = normalized_text(self.text)
        return (
            upper == "DISCLAIMER"
            or "THIS IS A COMPUTER GENERATED STATEMENT" in upper
            or "THE BANK IMMEDIATELY" in upper
            or "WITHIN 2 WEEKS" in upper
            or bool(re.fullmatch(r"\d+\s+OF\s+\d+", upper))
        )

    def is_noise(self) -> bool:
        upper = normalized_text(self.text)
        if not upper:
            return True
        if self.has_amounts:
            return False
        if any(char.isdigit() for char in upper):
            return False
        return len(upper.replace(" ", "")) <= 2

    def starts_transaction(self) -> bool:
        return self.has_amounts and looks_like_date_fragment(self.date_text, reference=self.reference)

    def is_continuation(self) -> bool:
        return (
            not self.starts_transaction()
            and not self.is_header()
            and not self.is_footer()
            and not self.is_noise()
            and bool(self.date_text or self.reference or self.narration)
        )


@dataclass(slots=True)
class PendingTransaction:
    page_number: int
    top: float
    date_parts: list[str] = field(default_factory=list)
    reference_parts: list[str] = field(default_factory=list)
    narration_parts: list[str] = field(default_factory=list)
    debit: str = ""
    credit: str = ""
    balance: str = ""

    def absorb(self, row: ParsedRow) -> None:
        if row.date_text:
            self.date_parts.append(row.date_text)
        if row.reference:
            self.reference_parts.append(row.reference)
        if row.narration:
            self.narration_parts.append(row.narration)
        if row.debit and not self.debit:
            self.debit = row.debit
        if row.credit and not self.credit:
            self.credit = row.credit
        if row.balance:
            self.balance = row.balance


class CustomerAccountStatementParser(StatementParser):
    bank_name = "customer-account-statement"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "customer_account_statement_layout"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        with open_pdf(pdf_path) as pdf:
            pages = list(pdf.pages)
            self.last_metadata = self._extract_metadata(pages[0])
            pending_rows = extract_transaction_rows(pages)

        fallback_year = infer_fallback_year(pending_rows)
        transactions = [
            build_transaction(
                pending,
                currency=self.last_metadata.currency or "NGN",
                parser_name=self.bank_name,
                fallback_year=fallback_year,
            )
            for pending in pending_rows
        ]
        transactions = [transaction for transaction in transactions if transaction is not None]

        if len(transactions) >= 2:
            first_dated = next((item for item in transactions if item.transaction_date is not None), None)
            last_dated = next((item for item in reversed(transactions) if item.transaction_date is not None), None)
            if first_dated is not None and last_dated is not None and first_dated.transaction_date > last_dated.transaction_date:
                transactions.reverse()

        if self.last_metadata.opening_balance is not None:
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

        normalize_transaction_sequence(transactions)

        if transactions and self.last_metadata.closing_balance is None:
            self.last_metadata.closing_balance = next(
                (item.balance for item in reversed(transactions) if item.balance is not None),
                None,
            )

        return transactions

    def _extract_metadata(self, first_page: pdfplumber.page.Page) -> StatementMetadata:
        first_page_text = first_page.extract_text() or ""
        normalized = normalized_text(first_page_text)
        lines = [clean_text(line) for line in first_page_text.splitlines() if clean_text(line)]

        debit_summary = re.search(
            r"TOTAL DEBIT COUNT.*?(\d+)\s+NGN\s+([0-9,]+\.\d{2})\s+[^0-9]*([0-9,]+\.\d{2})",
            normalized,
            flags=re.IGNORECASE,
        )
        credit_summary = re.search(
            r"TOTAL CREDIT COUNT.*?(\d+)\s+NGN\s+([0-9,]+\.\d{2})\s+[^0-9]*([0-9,]+\.\d{2})",
            normalized,
            flags=re.IGNORECASE,
        )
        account_number_match = re.search(
            r"ACCOUNT NUMBER.{0,100}?(\d{10})\b",
            normalized,
            flags=re.IGNORECASE,
        )

        account_name = next(
            (
                line
                for line in lines
                if "CUSTOMER ACCOUNT STATEMENT" not in normalized_text(line)
                and "DISCLAIMER" not in normalized_text(line)
                and not re.fullmatch(r"[A-Za-z]", line)
            ),
            None,
        )

        return StatementMetadata(
            account_name=account_name,
            account_number=account_number_match.group(1) if account_number_match else None,
            currency="NGN" if " NGN " in f" {normalized} " else None,
            opening_balance=parse_decimal(debit_summary.group(3)) if debit_summary else None,
            total_debit=parse_decimal(debit_summary.group(2)) if debit_summary else None,
            total_credit=parse_decimal(credit_summary.group(2)) if credit_summary else None,
            closing_balance=parse_decimal(credit_summary.group(3)) if credit_summary else None,
        )


def extract_transaction_rows(pages: list[pdfplumber.page.Page]) -> list[PendingTransaction]:
    pending_transactions: list[PendingTransaction] = []
    current: PendingTransaction | None = None

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
            if parsed.is_header() or parsed.is_footer() or parsed.is_noise():
                continue

            if parsed.starts_transaction():
                if current is not None:
                    pending_transactions.append(current)
                current = PendingTransaction(page_number=page_number, top=parsed.top)
                current.absorb(parsed)
                continue

            if current is not None and parsed.is_continuation():
                current.absorb(parsed)

    if current is not None:
        pending_transactions.append(current)

    return pending_transactions


def build_transaction(
    pending: PendingTransaction,
    *,
    currency: str,
    parser_name: str,
    fallback_year: int | None = None,
) -> Transaction | None:
    transaction_date = parse_statement_date(
        " ".join(pending.date_parts),
        fallback_year=fallback_year,
        month_hint=extract_month_hint(" ".join(pending.reference_parts)),
    )
    if transaction_date is None:
        return None

    description = normalize_description(" ".join(pending.narration_parts))
    reference = normalize_reference(pending.reference_parts)

    return Transaction(
        transaction_date=transaction_date,
        description=description or "Unlabeled Transaction",
        debit=parse_decimal_from_cell(pending.debit, semantic="debit") or Decimal("0"),
        credit=parse_decimal_from_cell(pending.credit, semantic="credit") or Decimal("0"),
        balance=parse_decimal_from_cell(pending.balance, semantic="balance"),
        reference=reference,
        currency=currency,
        raw_text=clean_text(
            " ".join(
                part
                for part in (
                    " ".join(pending.date_parts),
                    " ".join(pending.reference_parts),
                    " ".join(pending.narration_parts),
                    pending.debit,
                    pending.credit,
                    pending.balance,
                )
                if part
            )
        ),
        source_page=pending.page_number,
        parser_name=parser_name,
    )


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
        try:
            date_index = texts.index("DATE")
            reference_index = texts.index("REFERENCE", date_index + 1)
            narration_index = texts.index("NARRATION", reference_index + 1)
            debit_index = texts.index("DEBIT", narration_index + 1)
            credit_index = texts.index("CREDIT", debit_index + 1)
            balance_index = texts.index("BALANCE", credit_index + 1)
        except ValueError:
            continue

        reference_start = float(row.words[reference_index]["x0"])
        narration_start = float(row.words[narration_index]["x0"])
        debit_start = float(row.words[debit_index]["x0"])
        credit_start = float(row.words[credit_index]["x0"])
        balance_start = float(row.words[balance_index]["x0"])

        return HeaderBounds(
            date_boundary=midpoint(float(row.words[date_index]["x1"]), reference_start),
            reference_boundary=midpoint(float(row.words[reference_index]["x1"]), narration_start),
            narration_boundary=debit_start - 6,
            debit_boundary=credit_start - 6,
            credit_boundary=balance_start - 6,
            page_width=page_width,
            header_top=row.top,
        )
    return None


def split_row(row: WordRow, page_number: int, bounds: HeaderBounds) -> ParsedRow:
    columns = {
        "date_text": [],
        "reference": [],
        "narration": [],
        "debit": [],
        "credit": [],
        "balance": [],
    }

    for word in row.words:
        center = (float(word["x0"]) + float(word["x1"])) / 2
        text = word["text"]
        if center < bounds.date_boundary:
            columns["date_text"].append(text)
        elif center < bounds.reference_boundary:
            columns["reference"].append(text)
        elif center < bounds.narration_boundary:
            columns["narration"].append(text)
        elif center < bounds.debit_boundary:
            columns["debit"].append(text)
        elif center < bounds.credit_boundary:
            columns["credit"].append(text)
        else:
            columns["balance"].append(text)

    return ParsedRow(
        page_number=page_number,
        top=row.top,
        date_text=clean_text(" ".join(columns["date_text"])),
        reference=clean_text(" ".join(columns["reference"])),
        narration=clean_text(" ".join(columns["narration"])),
        debit=clean_text(" ".join(columns["debit"])),
        credit=clean_text(" ".join(columns["credit"])),
        balance=clean_text(" ".join(columns["balance"])),
    )


def midpoint(left: float, right: float) -> float:
    return (left + right) / 2


def looks_like_date_fragment(value: str, *, reference: str = "") -> bool:
    cleaned = normalize_date_fragment(value)
    if not cleaned:
        return False
    if re.fullmatch(r"\d{1,2}", cleaned):
        return extract_month_hint(reference) is not None
    return bool(
        re.fullmatch(r"\d{1,2}\s+[A-Z]{3,9}", cleaned)
        or re.fullmatch(r"\d{1,2}\s+[A-Z]{3,9}\s+\d{4}", cleaned)
    )


def parse_statement_date(
    value: str,
    *,
    fallback_year: int | None = None,
    month_hint: str | None = None,
):
    cleaned = normalize_date_fragment(value)
    if not cleaned:
        return None

    match = re.search(r"\b(\d{1,2})\s+([A-Z]{3,9})\s+(\d{4})\b", cleaned)
    if not match and fallback_year is not None:
        partial_match = re.search(r"\b(\d{1,2})\s+([A-Z]{3,9})\b", cleaned)
        if partial_match:
            candidate = f"{partial_match.group(1)} {partial_match.group(2).title()} {fallback_year}"
            for pattern in ("%d %b %Y", "%d %B %Y"):
                try:
                    return datetime.strptime(candidate, pattern).date()
                except ValueError:
                    continue
        day_only_match = re.search(r"\b(\d{1,2})\b", cleaned)
        if day_only_match and month_hint:
            candidate = f"{day_only_match.group(1)} {month_hint} {fallback_year}"
            for pattern in ("%d %b %Y", "%d %B %Y"):
                try:
                    return datetime.strptime(candidate, pattern).date()
                except ValueError:
                    continue
        return None
    if not match:
        return None

    candidate = f"{match.group(1)} {match.group(2).title()} {match.group(3)}"
    for pattern in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(candidate, pattern).date()
        except ValueError:
            continue
    return None


def normalize_date_fragment(value: str) -> str:
    cleaned = normalized_text(value).replace(",", " ")
    cleaned = re.sub(r"(\d{1,2})(ST|ND|RD|TH)\b", r"\1", cleaned, flags=re.IGNORECASE)
    return clean_text(cleaned)


def normalize_description(value: str) -> str:
    return clean_text(value.replace("|", " "))


def normalize_reference(parts: list[str]) -> str | None:
    cleaned_parts: list[str] = []
    for part in parts:
        cleaned = clean_text(part.replace("|", " "))
        cleaned = re.sub(
            r"^(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*,?\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not cleaned or len(cleaned) == 1:
            continue
        if cleaned not in cleaned_parts:
            cleaned_parts.append(cleaned)
    if not cleaned_parts:
        return None
    return " ".join(cleaned_parts)


def infer_fallback_year(pending_rows: list[PendingTransaction]) -> int | None:
    year_counts: dict[int, int] = {}
    for pending in pending_rows:
        for part in pending.date_parts:
            for match in re.findall(r"\b(20\d{2})\b", part):
                year = int(match)
                year_counts[year] = year_counts.get(year, 0) + 1
    if not year_counts:
        return None
    return max(year_counts.items(), key=lambda item: item[1])[0]


def extract_month_hint(value: str) -> str | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    token = re.split(r"[^A-Za-z]+", cleaned, maxsplit=1)[0].upper()
    if len(token) < 3:
        return None

    month_prefix = token[:3]
    month_map = {
        "JAN": "Jan",
        "FEB": "Feb",
        "MAR": "Mar",
        "APR": "Apr",
        "MAY": "May",
        "JUN": "Jun",
        "JUL": "Jul",
        "AUG": "Aug",
        "SEP": "Sep",
        "OCT": "Oct",
        "NOV": "Nov",
        "DEC": "Dec",
    }
    return month_map.get(month_prefix)


def normalize_transaction_sequence(transactions: list[Transaction]) -> None:
    if len(transactions) < 2:
        return

    for _ in range(6):
        changed = False
        previous_balance: Decimal | None = None

        for index, transaction in enumerate(transactions):
            if transaction.transaction_date is None:
                previous_balance = transaction.balance if transaction.balance is not None else previous_balance
                continue

            if previous_balance is None:
                previous_balance = transaction.balance if transaction.balance is not None else previous_balance
                continue

            if index + 1 < len(transactions):
                candidate = transactions[index + 1]
                if should_swap_adjacent(previous_balance, transaction, candidate):
                    transactions[index], transactions[index + 1] = candidate, transaction
                    transaction = transactions[index]
                    candidate = transactions[index + 1]
                    changed = True

            if flip_direction_if_needed(transaction, previous_balance):
                changed = True

            if align_amount_to_balance_delta(transaction, previous_balance):
                changed = True

            previous_balance = transaction.balance if transaction.balance is not None else previous_balance

        if not changed:
            break


def should_swap_adjacent(
    previous_balance: Decimal,
    current: Transaction,
    following: Transaction,
) -> bool:
    if current.transaction_date is None or following.transaction_date is None:
        return False
    if current.transaction_date != following.transaction_date:
        return False
    if current.balance is None or following.balance is None:
        return False
    if not has_single_sided_amount(current) or not has_single_sided_amount(following):
        return False
    if fits_running_balance(current, previous_balance):
        return False
    if not fits_running_balance(following, previous_balance):
        return False
    return fits_running_balance(current, following.balance)


def flip_direction_if_needed(transaction: Transaction, previous_balance: Decimal) -> bool:
    if transaction.balance is None or not has_single_sided_amount(transaction):
        return False

    if transaction.credit > 0:
        credit_expected = previous_balance + transaction.credit
        debit_expected = previous_balance - transaction.credit
        if abs(credit_expected - transaction.balance) > Decimal("1.00") and abs(debit_expected - transaction.balance) <= Decimal("1.00"):
            transaction.debit = transaction.credit
            transaction.credit = Decimal("0")
            return True
        return False

    debit_expected = previous_balance - transaction.debit
    credit_expected = previous_balance + transaction.debit
    if abs(debit_expected - transaction.balance) > Decimal("1.00") and abs(credit_expected - transaction.balance) <= Decimal("1.00"):
        transaction.credit = transaction.debit
        transaction.debit = Decimal("0")
        return True
    return False


def fits_running_balance(transaction: Transaction, previous_balance: Decimal) -> bool:
    if transaction.balance is None or not has_single_sided_amount(transaction):
        return False
    if transaction.credit > 0:
        return abs((previous_balance + transaction.credit) - transaction.balance) <= Decimal("1.00")
    return abs((previous_balance - transaction.debit) - transaction.balance) <= Decimal("1.00")


def has_single_sided_amount(transaction: Transaction) -> bool:
    return (transaction.debit > 0 and transaction.credit == 0) or (transaction.credit > 0 and transaction.debit == 0)


def align_amount_to_balance_delta(transaction: Transaction, previous_balance: Decimal) -> bool:
    if transaction.balance is None or not has_single_sided_amount(transaction):
        return False

    delta = transaction.balance - previous_balance
    if delta < Decimal("-1.00") and transaction.debit > 0:
        corrected = abs(delta)
        if abs(corrected - transaction.debit) > Decimal("1.00"):
            transaction.debit = corrected
            return True
        return False

    if delta > Decimal("1.00") and transaction.credit > 0:
        corrected = delta
        if abs(corrected - transaction.credit) > Decimal("1.00"):
            transaction.credit = corrected
            return True
        return False

    return False
