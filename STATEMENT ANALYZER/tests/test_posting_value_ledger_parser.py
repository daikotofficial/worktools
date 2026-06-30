from __future__ import annotations

import unittest
from decimal import Decimal

from statement_analyzer.parsers.posting_value_ledger import (
    extract_dashboard_metadata,
    extract_ledger_rows_from_pages,
    infer_owner_name,
    split_reference_and_description,
)


class FakePage:
    def __init__(self, words: list[dict], text: str = "", width: float = 595) -> None:
        self._words = words
        self._text = text
        self.width = width

    def extract_words(self, **kwargs):
        return self._words

    def extract_text(self):
        return self._text


def make_word(text: str, x0: float, top: float) -> dict:
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + max(len(text) * 5, 20),
        "top": top,
    }


class PostingValueLedgerParserTests(unittest.TestCase):
    def test_extracts_dashboard_balances_and_period(self) -> None:
        metadata = extract_dashboard_metadata(
            "INFLOW VS OUTFLOW Current Balance "
            "FROM TO 01 Mar 2026 31 Mar 2026 "
            "48,322,293.71 12,155,098.17 Opening Balance Closing Balance"
        )

        self.assertEqual(metadata.currency, "NGN")
        self.assertEqual(metadata.opening_balance, Decimal("48322293.71"))
        self.assertEqual(metadata.closing_balance, Decimal("12155098.17"))
        self.assertEqual(str(metadata.period_start), "2026-03-01")
        self.assertEqual(str(metadata.period_end), "2026-03-31")

    def test_extracts_rows_and_continuations_for_ledger_layout(self) -> None:
        words = [
            make_word("POSTING", 37, 10),
            make_word("DATE", 61, 10),
            make_word("VALUE", 90, 10),
            make_word("DATE", 107, 10),
            make_word("DESCRIPTION", 143, 10),
            make_word("OUTFLOW", 448, 10),
            make_word("INFLOW", 488, 10),
            make_word("BALANCE", 524, 10),
            make_word("01", 37, 30),
            make_word("Mar", 44, 30),
            make_word("2026", 55, 30),
            make_word("01", 90, 30),
            make_word("Mar", 97, 30),
            make_word("2026", 108, 30),
            make_word("000012260301210719738161560917", 143, 30),
            make_word("SOL-TAYLOR", 229, 30),
            make_word("INVESTMENT", 276, 30),
            make_word("LTD", 310, 30),
            make_word("SABON", 321, 30),
            make_word("TASHA", 341, 30),
            make_word("SELF", 360, 30),
            make_word("5,000,000.00", 451, 30),
            make_word("-", 491, 30),
            make_word("40,886,684.22", 524, 30),
            make_word("MAIN", 143, 46),
            make_word("ACCOUNT", 171, 46),
            make_word("MOVE", 208, 46),
            make_word("02", 37, 64),
            make_word("Mar", 44, 64),
            make_word("2026", 55, 64),
            make_word("02", 90, 64),
            make_word("Mar", 97, 64),
            make_word("2026", 108, 64),
            make_word("Org.", 143, 64),
            make_word("Amt:", 156, 64),
            make_word("50000", 170, 64),
            make_word("STAMPDUTY02MAR2026/01252692", 240, 64),
            make_word("50.00", 451, 64),
            make_word("-", 491, 64),
            make_word("40,886,553.59", 524, 64),
        ]
        page = FakePage(words, text="POSTING DATE VALUE DATE DESCRIPTION OUTFLOW INFLOW BALANCE")

        rows = extract_ledger_rows_from_pages([page])

        self.assertEqual(len(rows), 2)
        self.assertIn("MAIN ACCOUNT MOVE", rows[0].description)
        self.assertEqual(rows[0].outflow, "5,000,000.00")
        self.assertEqual(rows[1].description, "Org. Amt: 50000 STAMPDUTY02MAR2026/01252692")
        self.assertEqual(infer_owner_name(rows), "SOL-TAYLOR INVESTMENT LTD SABON TASHA")

    def test_splits_reference_without_removing_org_amount_prefix(self) -> None:
        description, reference = split_reference_and_description(
            "000012260302082642558544467310 AYM SHAFA MAIZUBE SABON TASHA DIESEL PURCHASE"
        )
        self.assertEqual(reference, "000012260302082642558544467310")
        self.assertEqual(description, "AYM SHAFA MAIZUBE SABON TASHA DIESEL PURCHASE")

        description, reference = split_reference_and_description(
            "Org. Amt: 50000 20260302_01252692 STAMPDUTY02MAR2026/01252692"
        )
        self.assertEqual(reference, "")
        self.assertEqual(description, "Org. Amt: 50000 20260302_01252692 STAMPDUTY02MAR2026/01252692")


if __name__ == "__main__":
    unittest.main()
