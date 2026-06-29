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

MONEY_PATTERN = r"(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}"
ANCHOR_PATTERN = re.compile(
    rf"^(?P<trans>\d{{2}} [A-Za-z]{{3}} \d{{4}} \d{{2}}:\d{{2}}:\d{{2}})\s+"
    rf"(?P<value>\d{{2}} [A-Za-z]{{3}} \d{{4}})\s+(?P<tail>.+)$"
)
TAIL_PATTERN = re.compile(
    rf"^(?:(?P<body>.*?)\s+)?"
    rf"(?P<debit>--|{MONEY_PATTERN})\s+"
    rf"(?P<credit>--|{MONEY_PATTERN})\s+"
    rf"(?P<balance>{MONEY_PATTERN})\s+"
    rf"(?P<channel>[A-Za-z]+)"
    rf"(?:\s+(?P<reference>.+))?$",
    flags=re.IGNORECASE,
)
SECTION_SUMMARY_PATTERN = re.compile(
    rf"(?P<label>Wallet|Savings)\s+Account\s+Period:\s+"
    rf"(?P<start>\d{{2}}\s+[A-Za-z]{{3}}\s+\d{{4}})\s+-\s+"
    rf"(?P<end>\d{{2}}\s+[A-Za-z]{{3}}\s+\d{{4}})\s+"
    rf"Opening Balance\s+Total Debit\s+Debit Count\s+"
    rf"\D*(?P<opening>{MONEY_PATTERN})\s+\D*(?P<debit>{MONEY_PATTERN})\s+(?P<debit_count>\d+)\s+"
    rf"Closing Balance\s+Total Credit\s+Credit Count\s+"
    rf"\D*(?P<closing>{MONEY_PATTERN})\s+\D*(?P<credit>{MONEY_PATTERN})\s+(?P<credit_count>\d+)",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class SectionSummary:
    label: str
    opening_balance: Decimal
    closing_balance: Decimal
    total_debit: Decimal
    total_credit: Decimal
    debit_count: int
    credit_count: int
    period_start: datetime
    period_end: datetime


@dataclass(slots=True)
class ParsedLine:
    page_number: int
    top: float
    raw_text: str
    description_fragment: str = ""
    reference_fragment: str = ""
    section_label: str = "Wallet Account"
    anchor: dict | None = None


class OPayStatementParser(StatementParser):
    bank_name = "opay"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "opay_statement"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        summaries = self._extract_section_summaries(pdf_path)
        self.last_metadata = self._extract_metadata(pdf_path, summaries)
        lines = self._extract_lines(pdf_path)
        attachments = self._build_attachments(lines)
        transactions: list[Transaction] = []
        opened_sections: set[str] = set()

        for index, line in enumerate(lines):
            if line.anchor is None:
                continue

            if line.section_label not in opened_sections:
                summary = summaries.get(line.section_label)
                if summary is not None:
                    transactions.append(
                        Transaction(
                            transaction_date=None,
                            description=f"{line.section_label} Opening Balance",
                            debit=Decimal("0"),
                            credit=Decimal("0"),
                            balance=summary.opening_balance,
                            currency="NGN",
                            raw_text=f"{line.section_label} Opening Balance",
                            source_page=line.page_number,
                            parser_name=self.bank_name,
                        )
                    )
                opened_sections.add(line.section_label)

            anchor = line.anchor
            attached_lines = sorted(attachments.get(index, []), key=lambda item: (item.page_number, item.top))
            description_parts: list[str] = []
            reference_parts: list[str] = []

            for item in attached_lines:
                if precedes(item, line):
                    append_if_present(description_parts, item.description_fragment)
                    append_if_present(reference_parts, item.reference_fragment)

            append_if_present(description_parts, anchor["body"])
            append_if_present(reference_parts, anchor["reference"])

            for item in attached_lines:
                if not precedes(item, line):
                    append_if_present(description_parts, item.description_fragment)
                    append_if_present(reference_parts, item.reference_fragment)

            description = clean_text(" ".join(description_parts))
            reference = clean_text(" ".join(reference_parts)) or None

            transactions.append(
                Transaction(
                    transaction_date=parse_datetime(anchor["transaction_time"]).date(),
                    description=description,
                    debit=anchor["debit"],
                    credit=anchor["credit"],
                    balance=anchor["balance"],
                    reference=reference,
                    currency="NGN",
                    raw_text=clean_text(
                        " ".join(
                            part
                            for part in (
                                line.section_label,
                                anchor["transaction_time"],
                                anchor["value_date"],
                                description,
                                format_decimal(anchor["debit"]),
                                format_decimal(anchor["credit"]),
                                format_decimal(anchor["balance"]),
                                anchor["channel"],
                                reference or "",
                            )
                            if part
                        )
                    ),
                    source_page=line.page_number,
                    parser_name=self.bank_name,
                )
            )

        if self.last_metadata is not None:
            actual_transactions = [item for item in transactions if item.debit > 0 or item.credit > 0]
            self.last_metadata.total_debit = sum((item.debit for item in actual_transactions), Decimal("0"))
            self.last_metadata.total_credit = sum((item.credit for item in actual_transactions), Decimal("0"))

        return transactions

    def _extract_metadata(
        self,
        pdf_path: Path,
        summaries: dict[str, SectionSummary],
    ) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""

        account_match = re.search(
            r"Account Name\s+Account Number\s+(.+?)\s+(\d{10})\s+Wallet Account Period:",
            clean_text(first_page_text),
            flags=re.IGNORECASE,
        )
        first_summary = summaries.get("Wallet Account")
        last_summary = summaries.get("Savings Account") or first_summary

        return StatementMetadata(
            account_name=account_match.group(1).strip() if account_match else None,
            account_number=account_match.group(2).strip() if account_match else None,
            currency="NGN",
            opening_balance=first_summary.opening_balance if first_summary else None,
            total_debit=None,
            total_credit=None,
            closing_balance=last_summary.closing_balance if last_summary else None,
            period_start=first_summary.period_start.date() if first_summary else None,
            period_end=first_summary.period_end.date() if first_summary else None,
        )

    def _extract_section_summaries(self, pdf_path: Path) -> dict[str, SectionSummary]:
        with open_pdf(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)

        summaries: dict[str, SectionSummary] = {}
        for match in SECTION_SUMMARY_PATTERN.finditer(clean_text(text)):
            label = f"{match.group('label').title()} Account"
            summaries[label] = SectionSummary(
                label=label,
                opening_balance=parse_decimal(match.group("opening")) or Decimal("0"),
                closing_balance=parse_decimal(match.group("closing")) or Decimal("0"),
                total_debit=parse_decimal(match.group("debit")) or Decimal("0"),
                total_credit=parse_decimal(match.group("credit")) or Decimal("0"),
                debit_count=int(match.group("debit_count")),
                credit_count=int(match.group("credit_count")),
                period_start=parse_period_datetime(match.group("start")),
                period_end=parse_period_datetime(match.group("end")),
            )
        return summaries

    def _extract_lines(self, pdf_path: Path) -> list[ParsedLine]:
        lines: list[ParsedLine] = []
        current_section = "Wallet Account"

        with open_pdf(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for group in group_words_into_lines(
                    page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
                ):
                    raw_text = clean_text(" ".join(word["text"] for word in sorted(group, key=lambda item: item["x0"])))
                    if not raw_text:
                        continue
                    if "Savings Account Period:" in raw_text:
                        current_section = "Savings Account"

                    line = ParsedLine(
                        page_number=page_number,
                        top=min(float(word["top"]) for word in group),
                        raw_text=raw_text,
                        description_fragment=extract_description_fragment(group),
                        reference_fragment=extract_reference_fragment(group),
                        section_label=current_section,
                        anchor=parse_anchor_line(raw_text),
                    )
                    lines.append(line)

        return lines

    def _build_attachments(self, lines: list[ParsedLine]) -> dict[int, list[ParsedLine]]:
        anchor_indices = [index for index, line in enumerate(lines) if line.anchor is not None]
        attachments: dict[int, list[ParsedLine]] = {}

        for index, line in enumerate(lines):
            if line.anchor is not None or is_non_transaction_text(line.raw_text):
                continue
            if not (line.description_fragment or line.reference_fragment):
                continue

            prev_anchor = next((candidate for candidate in reversed(anchor_indices) if candidate < index), None)
            next_anchor = next((candidate for candidate in anchor_indices if candidate > index), None)
            target: int | None = None
            upper_text = line.raw_text.upper()

            if next_anchor is not None and (
                next_anchor == index + 1 or upper_text.startswith(TRANSACTION_STARTERS)
            ):
                target = next_anchor
            elif prev_anchor is not None:
                target = prev_anchor

            if target is not None:
                attachments.setdefault(target, []).append(line)

        return attachments


TRANSACTION_STARTERS = (
    "TRANSFER ",
    "AIRTIME",
    "MOBILE DATA",
    "TV ",
    "ELECTRONIC ",
    "AUTO-SAVE",
    "OWEALTH ",
)


def group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    grouped: list[list[dict]] = []
    current_group: list[dict] = []
    current_top: float | None = None

    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        top = float(word["top"])
        if current_top is None or abs(top - current_top) <= 2.4:
            current_group.append(word)
            current_top = top if current_top is None else (current_top + top) / 2
        else:
            grouped.append(current_group)
            current_group = [word]
            current_top = top

    if current_group:
        grouped.append(current_group)
    return grouped


def parse_anchor_line(text: str) -> dict | None:
    anchor_match = ANCHOR_PATTERN.match(text)
    if not anchor_match:
        return None

    tail_match = TAIL_PATTERN.match(anchor_match.group("tail"))
    if not tail_match:
        return None

    return {
        "transaction_time": anchor_match.group("trans"),
        "value_date": anchor_match.group("value"),
        "body": clean_text(tail_match.group("body") or ""),
        "debit": parse_decimal(tail_match.group("debit")) or Decimal("0"),
        "credit": parse_decimal(tail_match.group("credit")) or Decimal("0"),
        "balance": parse_decimal(tail_match.group("balance")) or Decimal("0"),
        "channel": clean_text(tail_match.group("channel")),
        "reference": clean_text(tail_match.group("reference") or ""),
    }


def extract_description_fragment(words: list[dict]) -> str:
    return clean_text(
        " ".join(
            word["text"]
            for word in sorted(words, key=lambda item: item["x0"])
            if 170 <= float(word["x0"]) < 300
        )
    )


def extract_reference_fragment(words: list[dict]) -> str:
    return clean_text(
        " ".join(
            word["text"]
            for word in sorted(words, key=lambda item: item["x0"])
            if float(word["x0"]) >= 450
        )
    )


def is_non_transaction_text(text: str) -> bool:
    if re.fullmatch(r"\d{2}\s+[A-Za-z]{3}\s+\d{4}", text.strip()):
        return True

    upper_text = text.upper()
    return any(
        term in upper_text
        for term in (
            "ACCOUNT STATEMENT",
            "GENERATED ON",
            "ACCOUNT NAME",
            "WALLET ACCOUNT PERIOD",
            "SAVINGS ACCOUNT PERIOD",
            "OPENING BALANCE",
            "CLOSING BALANCE",
            "TRANS. TIME",
            "BALANCE AFTER",
            "DEBIT COUNT",
            "CREDIT COUNT",
        )
    )


def precedes(candidate: ParsedLine, anchor: ParsedLine) -> bool:
    return (candidate.page_number, candidate.top) < (anchor.page_number, anchor.top)


def append_if_present(parts: list[str], value: str | None) -> None:
    cleaned = clean_text(value or "")
    if cleaned:
        parts.append(cleaned)


def parse_datetime(value: str):
    return datetime.strptime(value, "%d %b %Y %H:%M:%S")


def parse_period_datetime(value: str):
    return datetime.strptime(value, "%d %b %Y")


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = clean_text(value)
    if not cleaned or cleaned == "--":
        return Decimal("0")
    return Decimal(cleaned.replace(",", ""))


def format_decimal(value: Decimal) -> str:
    return f"{value:.2f}"


def clean_text(value: str) -> str:
    return " ".join(value.replace("\x00", " ").replace("\n", " ").split())
