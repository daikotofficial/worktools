from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from statement_analyzer.layouts import detect_layout
from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import open_pdf


@dataclass(slots=True)
class Row:
    page: int
    top: float
    date: str = ""
    value_date: str = ""
    reference: str = ""
    description: str = ""
    withdrawal: str = ""
    deposit: str = ""
    balance: str = ""


class OPTransactionHistoryParser(StatementParser):
    bank_name = "opay-transaction-history"

    def can_parse(self, pdf_path: Path) -> bool:
        profile = detect_layout(pdf_path)
        return profile is not None and profile.key == "op_transaction_history"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._metadata(pdf_path)
        rows = self._rows(pdf_path)
        transactions: list[Transaction] = []
        for row in rows:
            date = parse_date(row.date)
            if date is None or not row.balance:
                continue
            withdrawal = parse_decimal(row.withdrawal) or Decimal("0")
            deposit = parse_decimal(row.deposit) or Decimal("0")
            balance = parse_decimal(row.balance)
            if balance is None or (withdrawal == 0 and deposit == 0):
                continue
            transactions.append(
                Transaction(
                    transaction_date=date,
                    description=row.description or "Unlabeled Transaction",
                    debit=withdrawal,
                    credit=deposit,
                    balance=balance,
                    reference=row.reference or None,
                    currency="NGN",
                    raw_text=" ".join(part for part in (row.date, row.value_date, row.reference, row.description, row.withdrawal, row.deposit, row.balance) if part),
                    source_page=row.page,
                    parser_name=self.bank_name,
                )
            )
        dated = [item for item in transactions if item.transaction_date is not None]
        if len(dated) >= 2 and dated[0].transaction_date > dated[-1].transaction_date:
            transactions.reverse()
        return transactions

    def _metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages[:2])
        compact = " ".join(text.split())
        period = re.search(r"Statement Period\s+(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})", compact, re.I)
        return StatementMetadata(
            account_name=match_text(compact, r"Branch\s+\S+\s+([A-Z][A-Z .&]+?)\s+Your Transactions"),
            account_number=match_text(compact, r"Account Number\s+(\d+)"),
            currency="NGN",
            opening_balance=parse_decimal(match_text(compact, r"Opening Balance \([^)]*\):\s*([0-9,]+\.\d{2})")),
            total_debit=parse_decimal(match_text(compact, r"Withdrawal\s+([0-9,]+\.\d{2})")),
            total_credit=parse_decimal(match_text(compact, r"Deposit\s+([0-9,]+\.\d{2})")),
            closing_balance=parse_decimal(match_text(compact, r"Closing Balance\s+([0-9,]+\.\d{2})")),
            period_start=parse_date(period.group(1)) if period else None,
            period_end=parse_date(period.group(2)) if period else None,
        )

    def _rows(self, pdf_path: Path) -> list[Row]:
        rows: list[Row] = []
        with open_pdf(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                groups = group_words(page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False))
                anchors = []
                for group in groups:
                    columns = {key: [] for key in ("date", "value_date", "reference", "description", "withdrawal", "deposit", "balance")}
                    for word in group:
                        x = float(word["x0"])
                        key = "date" if x < 96 else "value_date" if x < 145 else "reference" if x < 240 else "description" if x < 446 else "withdrawal" if x < 566 else "deposit" if x < 686 else "balance"
                        columns[key].append(word["text"])
                    row = Row(page_number, min(float(word["top"]) for word in group), **{key: clean(" ".join(value)) for key, value in columns.items()})
                    if parse_date(row.date):
                        anchors.append(row)
                        rows.append(row)
                    elif anchors and row.description and not row.balance and not row.withdrawal and not row.deposit:
                        anchors[-1].description = clean(f"{anchors[-1].description} {row.description}")
        return rows


def group_words(words: list[dict]) -> list[list[dict]]:
    result: list[list[dict]] = []
    current: list[dict] = []
    top: float | None = None
    for word in sorted(words or [], key=lambda item: (item["top"], item["x0"])):
        value = float(word["top"])
        if top is None or abs(value - top) <= 2.8:
            current.append(word)
            top = value if top is None else (top + value) / 2
        else:
            result.append(current)
            current = [word]
            top = value
    if current:
        result.append(current)
    return result


def clean(value: str) -> str:
    return " ".join(value.split())


def match_text(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.I)
    return match.group(1).strip() if match else None


def parse_date(value: str | None):
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_decimal(value: str | None) -> Decimal | None:
    if not value or value.strip() in {"-", "--"}:
        return None
    try:
        return Decimal(re.sub(r"[^0-9.-]", "", value))
    except InvalidOperation:
        return None
