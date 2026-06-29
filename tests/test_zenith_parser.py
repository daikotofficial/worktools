from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
import tempfile

from statement_analyzer.parsers.zenith import ZenithStyleParser
from statement_analyzer.service import StatementAnalysisService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ZenithParserTests(unittest.TestCase):
    def test_zenith_sample_with_shifted_balance_column_parses_successfully(self) -> None:
        parser = ZenithStyleParser()
        pdf_path = PROJECT_ROOT / "LE & EL REAL ESTATE LIMITED (1).pdf"

        self.assertTrue(parser.can_parse(pdf_path))

        transactions = parser.parse(pdf_path)
        self.assertEqual(len(transactions), 541)
        self.assertEqual(sum(1 for item in transactions if item.credit > 0), 50)
        self.assertEqual(sum(1 for item in transactions if item.debit > 0), 490)
        metadata = parser._extract_metadata(pdf_path)
        self.assertEqual(
            sum((item.credit for item in transactions), Decimal("0")),
            metadata.total_credit,
        )
        self.assertEqual(
            sum((item.debit for item in transactions), Decimal("0")),
            metadata.total_debit,
        )

        kyami_transfer = next(
            item
            for item in transactions
            if item.transaction_date
            and item.transaction_date.isoformat() == "2025-03-16"
            and item.credit == Decimal("25000000.00")
            and item.balance == Decimal("110156588.00")
        )
        self.assertIn("Kyami/16/3/25", kyami_transfer.description)

        etz_inflow = next(
            item
            for item in transactions
            if item.transaction_date
            and item.transaction_date.isoformat() == "2025-03-18"
            and item.credit == Decimal("18000000.00")
            and item.balance == Decimal("128156588.00")
        )
        self.assertIn(":ETZ INFLOW", etz_inflow.description)

    def test_zenith_sample_reconciles_in_service_summary(self) -> None:
        pdf_path = PROJECT_ROOT / "LE & EL REAL ESTATE LIMITED (1).pdf"
        service = StatementAnalysisService()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "LE_EL_REAL_ESTATE_LIMITED_ANALYZED.xlsx"
            result = service.analyze(pdf_path, output_path)
            self.assertTrue(output_path.exists())

        self.assertEqual(result.summary.parser_name, "zenith-style")
        self.assertEqual(result.summary.available_check_count, 4)
        self.assertEqual(result.summary.matched_check_count, 4)
        self.assertEqual(result.summary.total_credit, 3165811961.63)
        self.assertEqual(result.summary.total_debit, 3165771369.15)


if __name__ == "__main__":
    unittest.main()
