from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from statement_analyzer.models import StatementAnalysis, StatementMetadata, Transaction
from statement_analyzer.parsers.moniepoint import (
    DEFAULT_PAGE_WIDTH,
    MoniepointStatementParser,
    PendingRow,
    TableLayout,
    WordLine,
    build_transaction,
    infer_table_layout_from_header,
    word_amount_semantic,
)
from statement_analyzer.service import guard_against_silent_zero_totals, guard_against_total_mismatch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def word(text: str, x0: float, x1: float, top: float = 100.0) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top}


def scaled_word(text: str, x0: float, x1: float, scale: float, top: float = 100.0) -> dict:
    return word(text, x0 * scale, x1 * scale, top)


class MoniepointParserTests(unittest.TestCase):
    def test_scaled_moniepoint_layout_extracts_amount_columns(self) -> None:
        scale = 595.0 / DEFAULT_PAGE_WIDTH
        header = WordLine(
            page_number=1,
            top=100,
            words=[
                scaled_word("Date", 46.5, 68.7, scale),
                scaled_word("Narration", 114.0, 157.2, scale),
                scaled_word("Reference", 334.9, 383.3, scale),
                scaled_word("Debit", 659.7, 684.2, scale),
                scaled_word("Credit", 705.5, 733.5, scale),
                scaled_word("Balance", 756.9, 794.9, scale),
            ],
        )

        layout = infer_table_layout_from_header(header, page_width=595.0)

        self.assertIsNotNone(layout)
        assert layout is not None
        self.assertEqual(
            word_amount_semantic(scaled_word("23,000.00", 705.5, 735.6, scale), layout),
            "credit",
        )
        self.assertEqual(
            word_amount_semantic(scaled_word("23,000.00", 756.9, 787.0, scale), layout),
            "balance",
        )

        row = PendingRow(page_number=1, layout=layout)
        row.add_line(
            WordLine(
                page_number=1,
                top=120,
                words=[
                    scaled_word("2023-05-30T17:", 46.5, 94.9, scale, top=120),
                    scaled_word("Transfer", 114.0, 139.1, scale, top=120),
                    scaled_word("from", 141.0, 154.5, scale, top=120),
                    scaled_word("REF_CREDIT_0", 334.9, 420.0, scale, top=120),
                    scaled_word("0.00", 659.7, 672.9, scale, top=120),
                    scaled_word("23,000.00", 705.5, 735.6, scale, top=120),
                    scaled_word("23,000.00", 756.9, 787.0, scale, top=120),
                ],
            )
        )
        row.add_line(
            WordLine(
                page_number=1,
                top=128,
                words=[scaled_word("23:19", 46.5, 63.8, scale, top=128)],
            )
        )

        transaction = build_transaction(row, StatementMetadata(currency="NGN"))

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.transaction_date.isoformat(), "2023-05-30")
        self.assertEqual(transaction.description, "Transfer from")
        self.assertEqual(transaction.reference, "REF_CREDIT_0")
        self.assertEqual(transaction.debit, Decimal("0.00"))
        self.assertEqual(transaction.credit, Decimal("23000.00"))
        self.assertEqual(transaction.balance, Decimal("23000.00"))

    def test_page_width_fallback_handles_scaled_amounts_without_header(self) -> None:
        scale = 595.0 / DEFAULT_PAGE_WIDTH
        layout = TableLayout.from_page_width(595.0)

        self.assertEqual(
            word_amount_semantic(scaled_word("1,500.00", 659.7, 686.0, scale), layout),
            "debit",
        )
        self.assertEqual(
            word_amount_semantic(scaled_word("99.50", 705.5, 722.4, scale), layout),
            "credit",
        )
        self.assertEqual(
            word_amount_semantic(scaled_word("1,599.50", 756.9, 783.2, scale), layout),
            "balance",
        )

    def test_uploaded_moniepoint_statements_reconcile_to_pdf_totals(self) -> None:
        samples = (
            (
                "Moniepoint-Document-2026-05-12T10-42_260512_221856.pdf",
                Decimal("187651046.80"),
                Decimal("187655638.00"),
                Decimal("0.00"),
                Decimal("4591.20"),
                1970,
            ),
            (
                "Moniepoint-Document-2026-05-12T10-38_260512_222147.pdf",
                Decimal("945368368.32"),
                Decimal("946008484.60"),
                Decimal("4591.20"),
                Decimal("644707.48"),
                6844,
            ),
            (
                "Moniepoint-Document-2026-05-12T10-54_260512_222407.pdf",
                Decimal("1942874975.90"),
                Decimal("1944568094.02"),
                Decimal("644707.48"),
                Decimal("2337825.60"),
                14364,
            ),
        )

        for filename, expected_debit, expected_credit, expected_opening, expected_closing, expected_rows in samples:
            with self.subTest(filename=filename):
                parser = MoniepointStatementParser()
                transactions = parser.parse(PROJECT_ROOT / filename)
                metadata = parser.last_metadata

                self.assertEqual(len(transactions), expected_rows)
                self.assertEqual(metadata.total_debit, expected_debit)
                self.assertEqual(metadata.total_credit, expected_credit)
                self.assertEqual(metadata.opening_balance, expected_opening)
                self.assertEqual(metadata.closing_balance, expected_closing)
                self.assertEqual(sum(item.debit for item in transactions), expected_debit)
                self.assertEqual(sum(item.credit for item in transactions), expected_credit)


class AnalysisSafetyTests(unittest.TestCase):
    def test_positive_statement_totals_cannot_complete_with_zero_parsed_totals(self) -> None:
        analysis = StatementAnalysis(
            all_transactions=[
                Transaction(
                    transaction_date=None,
                    description="Opening Balance",
                    balance=Decimal("100.00"),
                )
            ],
            classified_transactions=[],
            inflows=[],
            outflows=[],
            parser_name="moniepoint",
            metadata=StatementMetadata(
                total_credit=Decimal("500.00"),
                total_debit=Decimal("100.00"),
            ),
        )

        with self.assertRaises(ValueError):
            guard_against_silent_zero_totals(analysis)

    def test_positive_statement_totals_cannot_complete_with_mismatched_parsed_totals(self) -> None:
        analysis = StatementAnalysis(
            all_transactions=[
                Transaction(
                    transaction_date=None,
                    description="Partial inflow",
                    credit=Decimal("400.00"),
                ),
                Transaction(
                    transaction_date=None,
                    description="Partial outflow",
                    debit=Decimal("25.00"),
                ),
            ],
            classified_transactions=[],
            inflows=[],
            outflows=[],
            parser_name="moniepoint",
            metadata=StatementMetadata(
                total_credit=Decimal("500.00"),
                total_debit=Decimal("100.00"),
            ),
        )

        with self.assertRaises(ValueError):
            guard_against_total_mismatch(analysis)


if __name__ == "__main__":
    unittest.main()
