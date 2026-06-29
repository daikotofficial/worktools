from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from statement_analyzer.service import StatementAnalysisService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_SAMPLES = (
    ("OKOTIE ENOCK CONSTRUCTION COMP. LTD.pdf", "zenith-style", 4),
    ("UBA_2.pdf", "uba", 4),
    ("1799745027-27150814102 (1).pdf", "fcmb", 4),
    ("Statement  2025.pdf", "providus", 2),
    ("3025345387 (2).pdf", "firstbank", 4),
    ("Fidelity S&P sub.pdf", "fidelity", 4),
    ("GTB STATEMENT_058713784 - Copy.pdf", "gtbank", 4),
    ("_SOL-TAYLOR WEMA.pdf", "wema", 4),
    ("LIVINUS 004.pdf", "wema-treasure", 4),
    ("Moniepoint-Document-2026-05-12T10-42_260512_221856.pdf", "moniepoint", 4),
    ("Action 2022 statement_180923_013428_Globus Bank - Copy.pdf", "globus", 4),
    ("Action Energy Statement Lotus Bank - Copy.pdf", "lotus", 2),
    ("Bank 2 JOSHUA IDA SAMSON 2023.pdf", "standard-chartered", 2),
    ("ACTION ENERGY LTD Account Number 0000071915 - Copy.pdf", "taj", 4),
    ("ACTION_Jaiz 1.pdf", "jaiz", 4),
    ("LOFTYINC ALLIED PARTNERS LIMITED-1775829475539.pdf", "customer-account-statement", 4),
    ("0806772213_2023-01-01_2024-01-01_transaction.pdf", "summary-details", 4),
)


class SupportedSampleTests(unittest.TestCase):
    def test_supported_statement_samples_parse_and_reconcile(self) -> None:
        service = StatementAnalysisService()

        for filename, expected_parser, min_available_checks in SUPPORTED_SAMPLES:
            with self.subTest(filename=filename):
                pdf_path = PROJECT_ROOT / filename
                with tempfile.TemporaryDirectory() as temp_dir:
                    output_path = Path(temp_dir) / f"{pdf_path.stem}_ANALYZED.xlsx"
                    result = service.analyze(pdf_path, output_path)
                    self.assertTrue(output_path.exists())

                self.assertEqual(result.summary.parser_name, expected_parser)
                self.assertGreater(result.summary.total_transactions, 0)
                self.assertGreaterEqual(result.summary.available_check_count, min_available_checks)
                self.assertEqual(
                    result.summary.matched_check_count,
                    result.summary.available_check_count,
                )


if __name__ == "__main__":
    unittest.main()
