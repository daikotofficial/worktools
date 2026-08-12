from decimal import Decimal
from pathlib import Path
import unittest

from statement_analyzer.parsers.wema import WemaStatementParser


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WemaAccountStatementVariantTests(unittest.TestCase):
    def test_split_date_credit_debit_layout_reconciles(self) -> None:
        pdf_path = PROJECT_ROOT / "04032026030223_Statement_For_SOL-TAYLOR INVESTMENTS LTD.pdf"
        parser = WemaStatementParser()

        self.assertTrue(parser.can_parse(pdf_path))
        transactions = parser.parse(pdf_path)
        metadata = parser.last_metadata

        self.assertEqual(len(transactions), 1202)
        self.assertEqual(sum(1 for item in transactions if item.credit > 0), 28)
        self.assertEqual(sum(1 for item in transactions if item.debit > 0), 1174)
        self.assertEqual(sum((item.credit for item in transactions), Decimal("0")), metadata.total_credit)
        self.assertEqual(sum((item.debit for item in transactions), Decimal("0")), metadata.total_debit)
        self.assertEqual(transactions[-1].balance, metadata.closing_balance)


if __name__ == "__main__":
    unittest.main()
