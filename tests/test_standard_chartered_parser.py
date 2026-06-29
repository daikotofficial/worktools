from __future__ import annotations

import unittest
from pathlib import Path

from statement_analyzer.parsers.standard_chartered import StandardCharteredStatementParser


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StandardCharteredParserTests(unittest.TestCase):
    def test_standard_chartered_sample_extracts_expected_transactions(self) -> None:
        parser = StandardCharteredStatementParser()
        pdf_path = PROJECT_ROOT / "Bank 2 JOSHUA IDA SAMSON 2023.pdf"

        self.assertTrue(parser.can_parse(pdf_path))

        transactions = parser.parse(pdf_path)
        self.assertGreater(len(transactions), 100)

        opening = transactions[0]
        self.assertEqual(opening.description, "Opening Balance")
        self.assertEqual(float(opening.balance), 2691766.27)

        first_transaction = transactions[1]
        self.assertEqual(first_transaction.transaction_date.isoformat(), "2023-03-02")
        self.assertIn("TRANSFER TO PERSONAL ACCOUNT", first_transaction.description)
        self.assertEqual(float(first_transaction.debit), 500000.00)
        self.assertEqual(float(first_transaction.balance), 2191766.27)

        last_transaction = transactions[-1]
        self.assertEqual(last_transaction.transaction_date.isoformat(), "2023-12-28")
        self.assertIn("CHIBEST AND SONS BLOCK INDUSTRY", last_transaction.description)
        self.assertEqual(float(last_transaction.debit), 1000000.00)
        self.assertEqual(float(last_transaction.balance), -1396929.44)


if __name__ == "__main__":
    unittest.main()
