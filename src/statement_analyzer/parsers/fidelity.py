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

FIDELITY_DESCRIPTION_BOUNDARY = 412.0
FIDELITY_PAY_IN_BOUNDARY = 472.0
FIDELITY_PAY_OUT_BOUNDARY = 512.0
AMOUNT_TOKEN_RE = re.compile(r"-?\d[\d,]*\.\d{2}")


@dataclass(slots=True)
class ParsedRow:
    page_number: int
    top: float
    transaction_date: str = ""
    value_date: str = ""
    reference: str = ""
    channel: str = ""
    description: str = ""
    pay_in: str = ""
    pay_out: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.transaction_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.pay_in, self.pay_out, self.balance))

    @property
    def text(self) -> str:
        return clean_text(
            " ".join(
                part
                for part in (
                    self.transaction_date,
                    self.value_date,
                    self.reference,
                    self.channel,
                    self.description,
                    self.pay_in,
                    self.pay_out,
                    self.balance,
                )
                if part
            )
        )

    def is_section_title(self) -> bool:
        return normalized_text(self.text) == "TRANSACTIONS"

    def is_header(self) -> bool:
        upper = normalized_text(self.text)
        if upper in {"TRANSACTION", "DATE"}:
            return True
        return (
            "VALUE DATE" in upper
            and "REFERENCE" in upper
            and "CHANNEL" in upper
            and "DESCRIPTION" in upper
            and "PAY OUT" in upper
            and "BALANCE" in upper
        )

    def is_opening_balance(self) -> bool:
        return "OPENING BALANCE" in normalized_text(self.text)

    def is_closing_balance(self) -> bool:
        return "CLOSING BALANCE" in normalized_text(self.text)

    def is_description_only(self) -> bool:
        return (
            bool(self.reference or self.channel or self.description)
            and not self.has_date
            and not self.has_amounts
            and not self.is_section_title()
            and not self.is_header()
            and not self.is_opening_balance()
            and not self.is_closing_balance()
        )


class FidelityStatementParser(StatementParser):
    bank_name = "fidelity"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key in {
            "fidelity_statement",
            "fidelity_account_statement_variant",
        }

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        rows = self._extract_rows(pdf_path)
        attachments = self._build_attachments(rows)
        transactions: list[Transaction] = []
        opening_balance_added = False
        running_balance = self.last_metadata.opening_balance if self.last_metadata else None

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
            opening_balance_added = True

        for index, row in enumerate(rows):
            if row.is_section_title() or row.is_header() or row.is_closing_balance():
                continue

            if row.is_opening_balance():
                if not opening_balance_added:
                    opening_balance = parse_decimal(row.balance) or (
                        self.last_metadata.opening_balance if self.last_metadata else None
                    )
                    if opening_balance is not None:
                        transactions.append(
                            Transaction(
                                transaction_date=None,
                                description="Opening Balance",
                                debit=Decimal("0"),
                                credit=Decimal("0"),
                                balance=opening_balance,
                                reference=None,
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

            extras = sorted(attachments.get(index, []), key=lambda item: (item.page_number, item.top))
            row_key = (row.page_number, row.top)
            description_parts = [
                extra.description
                for extra in extras
                if (extra.page_number, extra.top) < row_key and extra.description
            ]
            if row.description:
                description_parts.append(row.description)
            description_parts.extend(
                extra.description
                for extra in extras
                if (extra.page_number, extra.top) >= row_key and extra.description
            )
            description = clean_text(" ".join(part for part in description_parts if part))
            reference = clean_text(row.reference) or None
            balance = parse_decimal(row.balance)
            debit, credit = resolve_transaction_amounts(row, previous_balance=running_balance, current_balance=balance)

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.transaction_date) or parse_date(row.value_date),
                    description=description or clean_text(row.channel) or "Unlabeled Transaction",
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    reference=reference,
                    currency=self.last_metadata.currency or "NGN" if self.last_metadata else "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (
                                row.transaction_date,
                                row.value_date,
                                row.reference,
                                row.channel,
                                description,
                                row.pay_in,
                                row.pay_out,
                                row.balance,
                            )
                            if part
                        )
                    ),
                    source_page=row.page_number,
                    parser_name=self.bank_name,
                )
            )
            if balance is not None:
                running_balance = balance
            elif running_balance is not None:
                running_balance = running_balance + credit - debit

        if self.last_metadata is not None and self.last_metadata.closing_balance is None:
            self.last_metadata.closing_balance = next(
                (item.balance for item in reversed(transactions) if item.balance is not None),
                None,
            )
        self._normalize_rounded_summary_totals(transactions)

        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page = pdf.pages[0]
            first_page_text = first_page.extract_text() or ""
            first_page_layout_text = first_page.extract_text(layout=True) or first_page_text

        period_match = re.search(
            r"([A-Za-z]+ \d{1,2}, \d{4})\s+through\s+([A-Za-z]+ \d{1,2}, \d{4})",
            first_page_text,
            flags=re.IGNORECASE,
        )

        variant = "Opening Balance" in first_page_text and "From " in first_page_text
        return StatementMetadata(
            account_name=(extract_variant_account_name(first_page_text) if variant else extract_account_name(first_page_layout_text)),
            account_number=extract_regex(first_page_text, r"Account:\s*(\d+)"),
            currency=extract_regex(first_page_text, r"Currency:\s*([A-Z]{3})"),
            opening_balance=parse_decimal(extract_regex(first_page_text, r"(?:Beginning|Opening) Balance\s+([0-9,]+\.\d{2})")),
            total_debit=parse_decimal(extract_regex(first_page_text, r"Total\s+\d+\s+[0-9,]+\s+\d+\s+([0-9,]+)")),
            total_credit=parse_decimal(extract_regex(first_page_text, r"Total\s+\d+\s+([0-9,]+)\s+\d+\s+[0-9,]+")),
            closing_balance=parse_decimal(extract_regex(first_page_text, r"(?:Ending|Closing) Balance\s+([0-9,]+\.\d{2})")),
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

        grouped = group_words_into_rows(words)
        transaction_top = find_transaction_section_top(grouped) if page_number == 1 else None

        rows: list[ParsedRow] = []
        for group in grouped:
            row_top = min(float(word["top"]) for word in group)
            if transaction_top is not None and row_top < transaction_top:
                continue

            row = ParsedRow(page_number=page_number, top=row_top)
            columns = {
                "transaction_date": [],
                "value_date": [],
                "reference": [],
                "channel": [],
                "description": [],
                "pay_in": [],
                "pay_out": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 96:
                    columns["transaction_date"].append(text)
                elif x0 < 145:
                    columns["value_date"].append(text)
                elif x0 < 190:
                    columns["reference"].append(text)
                elif x0 < 230:
                    columns["channel"].append(text)
                elif x0 < FIDELITY_DESCRIPTION_BOUNDARY:
                    columns["description"].append(text)
                elif x0 < FIDELITY_PAY_IN_BOUNDARY:
                    columns["pay_in"].append(text)
                elif x0 < FIDELITY_PAY_OUT_BOUNDARY:
                    columns["pay_out"].append(text)
                else:
                    columns["balance"].append(text)

            row.transaction_date = " ".join(columns["transaction_date"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.reference = " ".join(columns["reference"]).strip()
            row.channel = " ".join(columns["channel"]).strip()
            row.description = " ".join(columns["description"]).strip()
            row.pay_in = " ".join(columns["pay_in"]).strip()
            row.pay_out = " ".join(columns["pay_out"]).strip()
            row.balance = " ".join(columns["balance"]).strip()
            normalize_amount_columns(row)

            if not row.text:
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
            if distance > 16:
                continue
            attachments.setdefault(target, []).append(row)

        return attachments

    def _normalize_rounded_summary_totals(self, transactions: list[Transaction]) -> None:
        if self.last_metadata is None:
            return

        parsed_credit_total = sum(transaction.credit for transaction in transactions)
        parsed_debit_total = sum(transaction.debit for transaction in transactions)

        if should_promote_rounded_total(self.last_metadata.total_credit, parsed_credit_total):
            self.last_metadata.total_credit = parsed_credit_total
        if should_promote_rounded_total(self.last_metadata.total_debit, parsed_debit_total):
            self.last_metadata.total_debit = parsed_debit_total


def extract_account_name(layout_text: str) -> str | None:
    capture = False
    name_lines: list[str] = []

    for raw_line in layout_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if "GOODLUCK JONATHAN BYPASS" in line:
            break
        if "Website:" in line:
            capture = True
            continue
        if not capture:
            continue

        left_segment = re.split(r"\s{3,}", line.strip())[0].strip()
        upper = left_segment.upper()
        if not left_segment:
            continue
        if any(term in upper for term in ("CUSTOMER SERVICE", "INTERNATIONAL", "WEBSITE")):
            continue
        if upper == "CUSTOMER SERVICE INFORMATION":
            continue
        if re.fullmatch(r"[0-9 ]+", left_segment):
            continue
        name_lines.append(left_segment)
        if len(name_lines) >= 3:
            break

    if not name_lines:
        return None
    return clean_text(" ".join(name_lines))


def extract_variant_account_name(text: str) -> str | None:
    match = re.search(
        r"Currency:\s*[A-Z]{3}\s+Type:\s*\S+\s+(.*?)\s+Transactions\b",
        clean_text(text),
        flags=re.IGNORECASE,
    )
    return clean_text(match.group(1)) if match else None


def group_words_into_rows(words: list[dict]) -> list[list[dict]]:
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

    return grouped


def find_transaction_section_top(groups: list[list[dict]]) -> float | None:
    for group in groups:
        text = normalized_text(" ".join(word["text"] for word in sorted(group, key=lambda item: item["x0"])))
        if text == "TRANSACTIONS" or (
            "VALUE DATE" in text and "REFERENCE" in text and "CHANNEL" in text and "DESCRIPTION" in text
        ):
            return min(float(word["top"]) for word in group)
    return None


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def normalize_amount_columns(row: ParsedRow) -> None:
    if not row.balance:
        pay_in_tokens = extract_amount_tokens(row.pay_in)
        pay_out_tokens = extract_amount_tokens(row.pay_out)

        if len(pay_in_tokens) >= 2:
            row.pay_in = pay_in_tokens[0]
            row.balance = pay_in_tokens[-1]
        elif len(pay_out_tokens) >= 2:
            row.pay_out = pay_out_tokens[0]
            row.balance = pay_out_tokens[-1]

    if not row.pay_in and not row.balance:
        trailing_amount = extract_trailing_amount(row.description)
        pay_out_tokens = extract_amount_tokens(row.pay_out)
        if trailing_amount and len(pay_out_tokens) == 1:
            row.description = remove_trailing_amount(row.description)
            row.pay_in = trailing_amount
            row.balance = pay_out_tokens[0]
            row.pay_out = ""


def resolve_transaction_amounts(
    row: ParsedRow,
    *,
    previous_balance: Decimal | None,
    current_balance: Decimal | None,
) -> tuple[Decimal, Decimal]:
    parsed_pay_in = parse_decimal(row.pay_in)
    parsed_pay_out = parse_decimal(row.pay_out)
    amount_candidates = [amount for amount in (parsed_pay_in, parsed_pay_out) if amount is not None]

    if len(amount_candidates) == 1:
        amount = amount_candidates[0]
        if previous_balance is not None and current_balance is not None:
            if amounts_match(previous_balance + amount, current_balance):
                return Decimal("0"), amount
            if amounts_match(previous_balance - amount, current_balance):
                return amount, Decimal("0")

        if parsed_pay_out is not None:
            return parsed_pay_out, Decimal("0")
        if parsed_pay_in is not None:
            return Decimal("0"), parsed_pay_in

    return parsed_pay_out or Decimal("0"), parsed_pay_in or Decimal("0")


def amounts_match(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= Decimal("0.01")


def should_promote_rounded_total(expected: Decimal | None, parsed: Decimal) -> bool:
    if expected is None:
        return False
    if expected != expected.quantize(Decimal("1")):
        return False
    return parsed.quantize(Decimal("1")) == expected and abs(parsed - expected) < Decimal("1")


def extract_amount_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return AMOUNT_TOKEN_RE.findall(value)


def extract_trailing_amount(value: str) -> str | None:
    match = re.search(r"(-?\d[\d,]*\.\d{2})\s*$", value)
    return match.group(1) if match else None


def remove_trailing_amount(value: str) -> str:
    return clean_text(re.sub(r"\s*-?\d[\d,]*\.\d{2}\s*$", "", value))


def is_date(value: str) -> bool:
    return parse_date(value) is not None


def parse_date(value: str | None):
    if not value:
        return None
    for fmt in ("%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_period_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value.strip(), "%B %d, %Y").date()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalized_text(value: str) -> str:
    return clean_text(value).upper()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if not cleaned or cleaned in {"-", "--"}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
