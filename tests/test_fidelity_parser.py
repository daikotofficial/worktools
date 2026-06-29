from __future__ import annotations

import unittest
from pathlib import Path

from statement_analyzer.parsers.fidelity import FidelityStatementParser


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FidelityParserTests(unittest.TestCase):
    def test_fidelity_sample_extracts_expected_transactions(self) -> None:
        parser = FidelityStatementParser()
        pdf_path = PROJECT_ROOT / "Fidelity S&P sub.pdf"

        self.assertTrue(parser.can_parse(pdf_path))

        transactions = parser.parse(pdf_path)
        self.assertEqual(len(transactions), 682)
        self.assertEqual(sum(1 for item in transactions if item.credit > 0), 66)
        self.assertEqual(sum(1 for item in transactions if item.debit > 0), 615)

        opening = transactions[0]
        self.assertEqual(opening.description, "Opening Balance")
        self.assertEqual(float(opening.balance), 0.0)

        first_transaction = transactions[1]
        self.assertEqual(first_transaction.transaction_date.isoformat(), "2025-08-18")
        self.assertIn("ANTALLAGI SOLUT/To FIDELITY BANK", first_transaction.description)
        self.assertEqual(float(first_transaction.credit), 5400000.0)
        self.assertEqual(float(first_transaction.balance), 5400000.0)

        payaza_transfer = transactions[14]
        self.assertEqual(payaza_transfer.transaction_date.isoformat(), "2025-08-20")
        self.assertIn("COB TRF TO PAYAZA", payaza_transfer.description)
        self.assertEqual(float(payaza_transfer.debit), 20000000.0)
        self.assertEqual(float(payaza_transfer.credit), 0.0)
        self.assertEqual(float(payaza_transfer.balance), 1458976.75)

        last_transaction = transactions[-1]
        self.assertEqual(last_transaction.transaction_date.isoformat(), "2026-03-30")
        self.assertIn("SMS ALERT CHARGES 28MAR 26", last_transaction.description)
        self.assertEqual(float(last_transaction.debit), 48.0)
        self.assertEqual(float(last_transaction.balance), 1428923.28)


if __name__ == "__main__":
    unittest.main()
