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
class SegmentInfo:
    index: int
    start_page: int
    end_page: int
    period_start: datetime.date | None
    period_end: datetime.date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    ending_balance: Decimal | None
    account_name: str | None
    account_number: str | None
    currency: str | None


@dataclass(slots=True)
class ParsedRow:
    segment_index: int
    page_number: int
    top: float
    book_date: str = ""
    reference: str = ""
    description: str = ""
    value_date: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""

    @property
    def has_date(self) -> bool:
        return is_date(self.book_date)

    @property
    def has_amounts(self) -> bool:
        return any((self.debit, self.credit, self.balance))

    def is_header(self) -> bool:
        text = " ".join(
            part for part in (self.book_date, self.reference, self.description, self.value_date) if part
        ).upper()
        return "BOOK DATE" in text and "REFERENCE" in text and "DESCRIPTION" in text

    def is_balance_marker(self) -> bool:
        text = " ".join(
            part for part in (self.book_date, self.reference, self.description, self.value_date) if part
        ).upper()
        return "BALANCE AT PERIOD" in text

    def is_attachment_candidate(self) -> bool:
        return not self.has_date and any((self.reference, self.description, self.balance))


class LotusStatementParser(StatementParser):
    bank_name = "lotus"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "lotus_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        with open_pdf(pdf_path) as pdf:
            segments = build_segments(pdf)
            self.last_metadata = build_overall_metadata(segments)
            rows = self._extract_rows(pdf, segments)

        attachments = self._build_attachments(rows)
        transactions: list[Transaction] = []
        opening_balance_added = False

        for index, row in enumerate(rows):
            if row.is_header() or row.is_balance_marker() or not row.has_date:
                continue

            attached_rows = sorted(attachments.get(index, []), key=lambda item: (item.page_number, item.top))
            reference_parts = [row.reference]
            description_parts = [row.description]
            balance_parts = [row.balance]

            for extra in attached_rows:
                if extra.reference:
                    reference_parts.append(extra.reference)
                if extra.description:
                    description_parts.append(extra.description)
                if extra.balance:
                    balance_parts.append(extra.balance)

            reference = clean_text(" ".join(part for part in reference_parts if part)) or None
            description = clean_text(" ".join(part for part in description_parts if part))
            balance = parse_decimal("".join(part.strip() for part in balance_parts if part))
            segment = segments[row.segment_index]

            if not opening_balance_added and segment.opening_balance is not None:
                transactions.append(
                    Transaction(
                        transaction_date=None,
                        description="Opening Balance",
                        debit=Decimal("0"),
                        credit=Decimal("0"),
                        balance=segment.opening_balance,
                        reference=None,
                        currency=segment.currency or "NGN",
                        raw_text="Opening Balance",
                        source_page=segment.start_page,
                        parser_name=self.bank_name,
                    )
                )
                opening_balance_added = True

            transactions.append(
                Transaction(
                    transaction_date=parse_date(row.book_date),
                    description=description,
                    debit=parse_decimal(row.debit) or Decimal("0"),
                    credit=parse_decimal(row.credit) or Decimal("0"),
                    balance=balance,
                    reference=reference,
                    currency=segment.currency or "NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (
                                row.book_date,
                                reference or "",
                                description,
                                row.value_date,
                                row.debit,
                                row.credit,
                                "".join(part.strip() for part in balance_parts if part),
                            )
                            if part
                        )
                    ),
                    source_page=row.page_number,
                    parser_name=self.bank_name,
                )
            )

        final_segment = segments[-1] if segments else None
        if final_segment and final_segment.ending_balance is not None:
            last_balance = next((tx.balance for tx in reversed(transactions) if tx.balance is not None), None)
            if last_balance != final_segment.ending_balance:
                transactions.append(
                    Transaction(
                        transaction_date=None,
                        description="Closing Balance",
                        debit=Decimal("0"),
                        credit=Decimal("0"),
                        balance=final_segment.ending_balance,
                        reference=None,
                        currency=final_segment.currency or "NGN",
                        raw_text="Closing Balance",
                        source_page=final_segment.end_page,
                        parser_name=self.bank_name,
                    )
                )

        return transactions

    def _extract_rows(
        self,
        pdf: pdfplumber.PDF,
        segments: list[SegmentInfo],
    ) -> list[ParsedRow]:
        page_to_segment = {}
        for segment in segments:
            for page_number in range(segment.start_page, segment.end_page + 1):
                page_to_segment[page_number] = segment.index

        rows: list[ParsedRow] = []
        for page_number, page in enumerate(pdf.pages, start=1):
            segment_index = page_to_segment.get(page_number)
            if segment_index is None:
                continue
            rows.extend(self._extract_page_rows(page, page_number, segment_index))
        return rows

    def _extract_page_rows(
        self,
        page: pdfplumber.page.Page,
        page_number: int,
        segment_index: int,
    ) -> list[ParsedRow]:
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
            row = ParsedRow(
                segment_index=segment_index,
                page_number=page_number,
                top=min(float(word["top"]) for word in group),
            )
            columns = {
                "book_date": [],
                "reference": [],
                "description": [],
                "value_date": [],
                "debit": [],
                "credit": [],
                "balance": [],
            }

            for word in sorted(group, key=lambda item: item["x0"]):
                x0 = float(word["x0"])
                text = word["text"]

                if x0 < 98:
                    columns["book_date"].append(text)
                elif x0 < 186:
                    columns["reference"].append(text)
                elif x0 < 263:
                    columns["description"].append(text)
                elif x0 < 334:
                    columns["value_date"].append(text)
                elif x0 < 413:
                    columns["debit"].append(text)
                elif x0 < 492:
                    columns["credit"].append(text)
                else:
                    columns["balance"].append(text)

            row.book_date = " ".join(columns["book_date"]).strip()
            row.reference = " ".join(columns["reference"]).strip()
            row.description = " ".join(columns["description"]).strip()
            row.value_date = " ".join(columns["value_date"]).strip()
            row.debit = " ".join(columns["debit"]).strip()
            row.credit = " ".join(columns["credit"]).strip()
            row.balance = " ".join(columns["balance"]).strip()

            text_blob = " ".join(
                part
                for part in (
                    row.book_date,
                    row.reference,
                    row.description,
                    row.value_date,
                    row.debit,
                    row.credit,
                    row.balance,
                )
                if part
            ).upper()
            if not text_blob:
                continue
            if any(
                term in text_blob
                for term in (
                    "ACCOUNT STATEMENT FOR THE PERIOD",
                    "ACCOUNT NUMBER :",
                    "ACCOUNT NAME :",
                    "CURRENCY :",
                    "ACCOUNT TYPE:",
                    "SHORT NAME:",
                    "PAGE ",
                    "SEPTEMBER 2023",
                    "16:57:12",
                    "17:3:24",
                    "17:7:45",
                    "17:9:34",
                    "17:10:47",
                    "*** NO ENTRIES FOR PERIOD ***",
                )
            ):
                continue
            rows.append(row)

        return rows

    def _build_attachments(self, rows: list[ParsedRow]) -> dict[int, list[ParsedRow]]:
        attachments: dict[int, list[ParsedRow]] = {}
        anchor_indices = [index for index, row in enumerate(rows) if row.has_date or row.is_balance_marker()]

        for index, row in enumerate(rows):
            if row.is_header() or row.has_date or row.is_balance_marker() or not row.is_attachment_candidate():
                continue

            prev_same_segment = next(
                (
                    candidate
                    for candidate in reversed(anchor_indices)
                    if candidate < index
                    and rows[candidate].segment_index == row.segment_index
                    and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            next_same_segment = next(
                (
                    candidate
                    for candidate in anchor_indices
                    if candidate > index
                    and rows[candidate].segment_index == row.segment_index
                    and rows[candidate].page_number == row.page_number
                ),
                None,
            )
            prev_any_page = next(
                (
                    candidate
                    for candidate in reversed(anchor_indices)
                    if candidate < index and rows[candidate].segment_index == row.segment_index
                ),
                None,
            )

            if prev_same_segment is None and prev_any_page is not None and row.top <= 240:
                attachments.setdefault(prev_any_page, []).append(row)
                continue

            choices: list[tuple[float, int]] = []
            if prev_same_segment is not None:
                choices.append((abs(row.top - rows[prev_same_segment].top), prev_same_segment))
            if next_same_segment is not None:
                choices.append((abs(rows[next_same_segment].top - row.top), next_same_segment))
            if not choices:
                continue

            distance, target = min(choices, key=lambda item: item[0])
            if distance > 60:
                continue
            attachments.setdefault(target, []).append(row)

        return attachments


def build_segments(pdf: pdfplumber.PDF) -> list[SegmentInfo]:
    segments: list[SegmentInfo] = []
    current_segment: dict | None = None

    for page_number, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        period_match = re.search(
            r"Account Statement for the period\s+(\d{8})\s+To\s+(\d{8})",
            text,
            flags=re.IGNORECASE,
        )
        footer_match = re.search(r"Page\s+1\s+of\s+(\d+)", text, flags=re.IGNORECASE)

        if period_match and footer_match:
            if current_segment is not None:
                segments.append(finalize_segment(current_segment, page_number - 1, len(segments)))

            current_segment = {
                "start_page": page_number,
                "period_start": parse_compact_date(period_match.group(1)),
                "period_end": parse_compact_date(period_match.group(2)),
                "opening_balance": parse_decimal(extract_regex(text, r"Opening Balance:\s*([0-9,]+\.\d{2})")),
                "closing_balance": parse_decimal(extract_regex(text, r"Closing Balance:\s*([0-9,]+\.\d{2})")),
                "ending_balance": extract_ending_balance(text),
                "account_name": extract_regex(text, r"Account Name\s*:\s*(.+?)\s+Phone"),
                "account_number": extract_regex(text, r"Account Number\s*:\s*(\d+)"),
                "currency": extract_regex(text, r"Currency\s*:\s*([A-Z]{3})"),
            }
            continue

        if current_segment is not None:
            ending_balance = extract_ending_balance(text)
            if ending_balance is not None:
                current_segment["ending_balance"] = ending_balance

    if current_segment is not None:
        segments.append(finalize_segment(current_segment, len(pdf.pages), len(segments)))

    return segments


def finalize_segment(segment_data: dict, end_page: int, index: int) -> SegmentInfo:
    return SegmentInfo(
        index=index,
        start_page=segment_data["start_page"],
        end_page=end_page,
        period_start=segment_data["period_start"],
        period_end=segment_data["period_end"],
        opening_balance=segment_data["opening_balance"],
        closing_balance=segment_data["closing_balance"],
        ending_balance=segment_data["ending_balance"],
        account_name=segment_data["account_name"],
        account_number=segment_data["account_number"],
        currency=segment_data["currency"],
    )


def extract_ending_balance(text: str) -> Decimal | None:
    return parse_decimal(
        extract_regex(
            text,
            r"Balance at Period E\s*([0-9,]+\.\d{2})\s*nd",
        )
    ) or parse_decimal(
        extract_regex(
            text,
            r"Balance at Period S\s*([0-9,]+\.\d{2})\s*tart",
        )
    )


def build_overall_metadata(segments: list[SegmentInfo]) -> StatementMetadata | None:
    if not segments:
        return None

    first = segments[0]
    last = segments[-1]
    return StatementMetadata(
        account_name=first.account_name,
        account_number=first.account_number,
        currency=first.currency,
        opening_balance=first.opening_balance,
        closing_balance=last.ending_balance or last.closing_balance,
        period_start=first.period_start,
        period_end=last.period_end,
    )


def extract_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else None


def parse_compact_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%Y%m%d").date()


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%d %b %y")
        return True
    except ValueError:
        return False


def parse_date(value: str):
    return datetime.strptime(value.strip(), "%d %b %y").date()


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    if not cleaned or not any(character.isdigit() for character in cleaned):
        return None
    if cleaned.startswith("."):
        cleaned = f"0{cleaned}"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").split())
