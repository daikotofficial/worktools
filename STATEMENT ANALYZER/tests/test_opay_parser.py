from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from statement_analyzer.parsers.opay import OPayStatementParser
from statement_analyzer.service import StatementAnalysisService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OPayParserTests(unittest.TestCase):
    def test_opay_sample_parses_wallet_and_savings_sections(self) -> None:
        parser = OPayStatementParser()
        pdf_path = PROJECT_ROOT / "IFEANYI DOUGLAS AGORUA_8138758064_20260505032135 (1).pdf"

        self.assertTrue(parser.can_parse(pdf_path))

        transactions = parser.parse(pdf_path)
        metadata = parser.last_metadata

        self.assertIsNotNone(metadata)
        self.assertEqual(len(transactions), 1656)
        self.assertEqual(sum(1 for item in transactions if item.debit > 0), 881)
        self.assertEqual(sum(1 for item in transactions if item.credit > 0), 773)
        self.assertEqual(
            sum((item.debit for item in transactions), Decimal("0")),
            Decimal("10944508.16"),
        )
        self.assertEqual(
            sum((item.credit for item in transactions), Decimal("0")),
            Decimal("10909849.53"),
        )
        self.assertEqual(transactions[0].description, "Wallet Account Opening Balance")
        self.assertEqual(transactions[0].balance, Decimal("50.20"))
        self.assertEqual(transactions[-1].description, "OWealth Interest Earned")
        self.assertEqual(transactions[-1].balance, Decimal("291.57"))

    def test_opay_sample_reconciles_in_service_summary(self) -> None:
        pdf_path = PROJECT_ROOT / "IFEANYI DOUGLAS AGORUA_8138758064_20260505032135 (1).pdf"
        service = StatementAnalysisService()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "IFEANYI_DOUGLAS_AGORUA_OPAY_ANALYZED.xlsx"
            result = service.analyze(pdf_path, output_path)
            self.assertTrue(output_path.exists())

        self.assertEqual(result.summary.parser_name, "opay")
        self.assertEqual(result.summary.available_check_count, 4)
        self.assertEqual(result.summary.matched_check_count, 4)
        self.assertEqual(result.summary.total_transactions, 1656)
        self.assertEqual(result.summary.total_credit, 10909849.53)
        self.assertEqual(result.summary.total_debit, 10944508.16)


if __name__ == "__main__":
    unittest.main()
