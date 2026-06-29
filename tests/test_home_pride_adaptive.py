from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from statement_analyzer.service import StatementAnalysisService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HomePrideAdaptiveParserTests(unittest.TestCase):
    def test_home_pride_wrapped_withdrawal_reconciles(self) -> None:
        pdf_path = PROJECT_ROOT / "HOME PRIDE 0001.pdf"
        service = StatementAnalysisService()

        analysis = service.pipeline.run(pdf_path)

        self.assertEqual(analysis.metadata.account_name, "HOME PRIDE GLOBAL RESOURCES LTD")
        self.assertEqual(analysis.metadata.account_number, "0125291662")
        self.assertEqual(analysis.metadata.total_debit, Decimal("207758251.56"))
        self.assertEqual(analysis.metadata.total_credit, Decimal("207760000.00"))
        self.assertEqual(sum(item.debit for item in analysis.all_transactions), analysis.metadata.total_debit)
        self.assertEqual(sum(item.credit for item in analysis.all_transactions), analysis.metadata.total_credit)

        wrapped_transfer = next(
            item
            for item in analysis.all_transactions
            if item.transaction_date.isoformat() == "2023-11-10" and "M44828" in item.description
        )
        self.assertEqual(wrapped_transfer.debit, Decimal("100000000.00"))
        self.assertEqual(wrapped_transfer.balance, Decimal("95166384.00"))

    def test_home_pride_service_summary_reconciles(self) -> None:
        pdf_path = PROJECT_ROOT / "HOME PRIDE 0001.pdf"
        service = StatementAnalysisService()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "HOME_PRIDE_ANALYZED.xlsx"
            result = service.analyze(pdf_path, output_path)
            self.assertTrue(output_path.exists())

        self.assertEqual(result.summary.total_debit, 207758251.56)
        self.assertEqual(result.summary.total_credit, 207760000.00)
        self.assertEqual(result.summary.available_check_count, 4)
        self.assertEqual(result.summary.matched_check_count, 4)


if __name__ == "__main__":
    unittest.main()
