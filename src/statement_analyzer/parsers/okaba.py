from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import open_pdf


DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


class OkabaStatementParser(StatementParser):
    """Parser for Okaba Oil and Gas statements exported by the bank system.

    The 2022/2023 export and the 2024 export have different column positions,
    but share the same transaction semantics and statement headers.
    """

    bank_name = "okaba"

    def can_parse(self, pdf_path: Path) -> bool:
        try:
            with open_pdf(pdf_path) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages[:2]).upper()
        except Exception:
            return False
        return all(
            term in text
            for term in (
                "STATEMENT OF ACCOUNT",
                "ACCOUNT NO 5632025507",
                "OKABA OIL AND GAS NIG LTD",
                "STATEMENT PERIOD",
            )
        ) and ("WITHDRAWALS" in text or "WITHDRAWLS" in text)

    def parse(self, pdf_path: Path) -> list[Transaction]:
        self.last_metadata = self._extract_metadata(pdf_path)
        transactions: list[Transaction] = []

        with open_pdf(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                transactions.extend(self._parse_page(page, page_number))

        return transactions

    def _extract_metadata(self, pdf_path: Path) -> StatementMetadata:
        with open_pdf(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""

        period = re.search(
            r"Statement\s+Period\s+(\d{1,2}-\d{1,2}-\d{4})\s+To\s+(\d{1,2}-\d{1,2}-\d{4})",
            text,
            re.IGNORECASE,
        )
        return StatementMetadata(
            account_name="OKABA OIL AND GAS NIG LTD",
            account_number=self._capture(text, r"Account\s+No\s+(\d+)"),
            currency=self._capture(text, r"Currency\s+([A-Z]{3})"),
            opening_balance=self._decimal(self._capture(text, r"Opening\s+Balance\s+(-?[\d,.]+)")),
            blocked_amount=self._decimal(self._capture(text, r"Blocked\s+Amt\s+(-?[\d,.]+)")),
            total_debit=self._decimal(self._capture(text, r"Total\s+Withdrawals\s+([\d,.]+)")),
            total_credit=self._decimal(self._capture(text, r"Total\s+Deposits\s+([\d,.]+)")),
            closing_balance=self._decimal(self._capture(text, r"Closing\s+Balance\s+(-?[\d,.]+)")),
            period_start=self._period_date(period.group(1)) if period else None,
            period_end=self._period_date(period.group(2)) if period else None,
        )

    def _parse_page(self, page: pdfplumber.page.Page, page_number: int) -> list[Transaction]:
        words = page.extract_words(x_tolerance=1, y_tolerance=2, keep_blank_chars=False)
        if not words:
            return []

        lines: list[list[dict]] = []
        for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
            if not lines or abs(float(word["top"]) - float(lines[-1][0]["top"])) > 2.8:
                lines.append([word])
            else:
                lines[-1].append(word)

        is_2024 = any(word["text"].lower() == "withdrawls" for word in words)
        date_rows = []
        for index, line in enumerate(lines):
            dates = [word for word in line if float(word["x0"]) < 65 and DATE_RE.match(word["text"])]
            has_balance = any(float(word["x0"]) >= (514 if is_2024 else 540) for word in line)
            if dates and has_balance:
                date_rows.append((index, dates[0]))

        parsed: list[Transaction] = []
        date_tops = [float(date_word["top"]) for _, date_word in date_rows]
        for row_position, (line_index, date_word) in enumerate(date_rows):
            top = float(date_word["top"])
            assigned = [
                word
                for line in lines
                for word in line
                if abs(float(word["top"]) - top) <= 28
                and min(range(len(date_tops)), key=lambda index: abs(date_tops[index] - float(word["top"]))) == row_position
                and float(word["top"]) < float(page.height) - 30
                and not self._is_footer_word(word["text"])
            ]

            # Amounts and the running balance are printed on the transaction's
            # date line. Description text may wrap above or below it, so only
            # use the exact line for financial columns.
            debit, credit, balance = self._amounts(lines[line_index], is_2024)
            if balance is None or (debit is None and credit is None):
                debit, credit, balance = self._amounts(assigned, is_2024)
            if balance is None or (debit is None and credit is None):
                continue

            description_limit = 220 if is_2024 else 290
            description = " ".join(
                word["text"]
                for word in sorted(assigned, key=lambda item: (float(item["top"]), float(item["x0"])))
                if 60 <= float(word["x0"]) < description_limit
                and not DATE_RE.match(word["text"])
            )
            description = " ".join(description.split())
            parsed.append(
                Transaction(
                    transaction_date=datetime.strptime(date_word["text"], "%d/%m/%Y").date(),
                    description=description,
                    debit=debit or Decimal("0"),
                    credit=credit or Decimal("0"),
                    balance=balance,
                    reference=self._reference(description),
                    currency=self.last_metadata.currency if self.last_metadata else "NGN",
                    raw_text=description,
                    source_page=page_number,
                    parser_name=self.bank_name,
                )
            )
        return parsed

    @staticmethod
    def _amounts(words: list[dict], is_2024: bool) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        debit_start, credit_start, balance_start = ((330, 424, 514) if is_2024 else (390, 470, 540))
        values: dict[str, list[str]] = {"debit": [], "credit": [], "balance": []}
        for word in words:
            x0 = float(word["x0"])
            if not re.fullmatch(r"-?[\d,]+(?:\.\d+)?", word["text"]):
                continue
            if debit_start <= x0 < credit_start:
                values["debit"].append(word["text"])
            elif credit_start <= x0 < balance_start:
                values["credit"].append(word["text"])
            elif x0 >= balance_start:
                values["balance"].append(word["text"])
        return tuple(OkabaStatementParser._decimal(" ".join(values[key])) for key in ("debit", "credit", "balance"))

    @staticmethod
    def _is_footer_word(value: str) -> bool:
        return value in {"##", "daderupatan", "RIBITOYE"} or value == "Page"

    @staticmethod
    def _capture(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else None

    @staticmethod
    def _decimal(value: str | None) -> Decimal | None:
        if not value:
            return None
        try:
            return Decimal(value.replace(",", "").strip())
        except InvalidOperation:
            return None

    @staticmethod
    def _period_date(value: str) -> date:
        day, month, year = (int(part) for part in value.split("-"))
        return date(year, month, day)

    @staticmethod
    def _reference(description: str) -> str | None:
        match = re.search(r"\b(?:PC|TRF|REF)[:/\s-]*([A-Z0-9|_-]{8,})", description, re.IGNORECASE)
        return match.group(0) if match else None
