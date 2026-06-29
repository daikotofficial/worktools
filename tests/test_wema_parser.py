from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from statement_analyzer.parsers.wema import WemaStatementParser
from statement_analyzer.service import StatementAnalysisService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WemaParserTests(unittest.TestCase):
    def test_wema_sample_parses_all_transactions_and_totals(self) -> None:
        parser = WemaStatementParser()
        pdf_path = PROJECT_ROOT / "_SOL-TAYLOR WEMA.pdf"

        self.assertTrue(parser.can_parse(pdf_path))

        transactions = parser.parse(pdf_path)
        metadata = parser.last_metadata

        self.assertIsNotNone(metadata)
        self.assertEqual(len(transactions), 1743)
        self.assertEqual(sum(1 for item in transactions if item.credit > 0), 28)
        self.assertEqual(sum(1 for item in transactions if item.debit > 0), 1715)
        self.assertEqual(
            sum((item.credit for item in transactions), Decimal("0")),
            metadata.total_credit,
        )
        self.assertEqual(
            sum((item.debit for item in transactions), Decimal("0")),
            metadata.total_debit,
        )
        self.assertEqual(transactions[0].balance, metadata.opening_balance)
        self.assertEqual(transactions[-1].balance, metadata.closing_balance)

        overprinted_row = next(
            item
            for item in transactions
            if item.reference == "S16261379" and item.debit == Decimal("170200.00")
        )
        self.assertIn("RABADE PETROLEUM", overprinted_row.description)

    def test_wema_sample_reconciles_in_service_summary(self) -> None:
        pdf_path = PROJECT_ROOT / "_SOL-TAYLOR WEMA.pdf"
        service = StatementAnalysisService()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "SOL_TAYLOR_WEMA_ANALYZED.xlsx"
            result = service.analyze(pdf_path, output_path)
            self.assertTrue(output_path.exists())

        self.assertEqual(result.summary.parser_name, "wema")
        self.assertEqual(result.summary.available_check_count, 4)
        self.assertEqual(result.summary.matched_check_count, 4)
        self.assertEqual(result.summary.total_credit, 248085026.01)
        self.assertEqual(result.summary.total_debit, 247560631.57)


if __name__ == "__main__":
    unittest.main()
