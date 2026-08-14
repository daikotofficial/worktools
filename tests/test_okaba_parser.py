from pathlib import Path
import unittest

from statement_analyzer.parsers.okaba import OkabaStatementParser


ROOT = Path(__file__).resolve().parents[1]


class OkabaParserTests(unittest.TestCase):
    def test_all_okaba_years_parse_and_reconcile(self) -> None:
        expected = {
            "CUSTOMER_STATEMENT_OF_OKABA_2022.pdf": ("2022", 664804652.27, 734199942.50),
            "okaba 2023.pdf": ("2023", 1058751140.61, 1012221632.00),
            "okaba 2024.pdf": ("2024", 1644925685.49, 1613942726.71),
        }
        for filename, (year, debit_total, credit_total) in expected.items():
            with self.subTest(filename=filename):
                parser = OkabaStatementParser()
                transactions = parser.parse(ROOT / filename)
                self.assertTrue(parser.can_parse(ROOT / filename))
                self.assertGreater(len(transactions), 50)
                self.assertEqual(parser.last_metadata.period_start.year, int(year))
                self.assertAlmostEqual(float(sum(item.debit for item in transactions)), debit_total, places=2)
                self.assertAlmostEqual(float(sum(item.credit for item in transactions)), credit_total, places=2)
                self.assertTrue(all(item.balance is not None for item in transactions))


if __name__ == "__main__":
    unittest.main()
