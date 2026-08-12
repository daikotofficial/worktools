from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from statement_analyzer.classifiers import rules as rules_module
from statement_analyzer.models import TransactionDirection
from statement_analyzer import service as service_module
from statement_analyzer.service import AnalysisSummary, ReconciliationCheck, ReviewRow
from statement_analyzer import webapp
from statement_analyzer.webapp import friendly_job_error, refresh_summary_review_options


class WebErrorTests(unittest.TestCase):
    def test_assertion_error_gets_friendly_message(self) -> None:
        message = friendly_job_error(AssertionError())
        self.assertIn("unexpected structure", message)

    def test_existing_error_message_is_preserved(self) -> None:
        message = friendly_job_error(ValueError("Unsupported bank statement format for now."))
        self.assertEqual(message, "Unsupported bank statement format for now.")

    def test_password_error_gets_password_message(self) -> None:
        PDFPasswordIncorrect = type("PDFPasswordIncorrect", (Exception,), {})
        message = friendly_job_error(PDFPasswordIncorrect())
        self.assertIn("password-protected", message)

    def test_password_error_with_library_message_still_gives_retry_instruction(self) -> None:
        PDFPasswordIncorrect = type("PDFPasswordIncorrect", (Exception,), {})
        message = friendly_job_error(PDFPasswordIncorrect("Incorrect password for encrypted PDF"))
        self.assertEqual(
            message,
            "This PDF is password-protected. Enter the PDF password and upload it again.",
        )

    def test_empty_unknown_exception_gets_fallback_message(self) -> None:
        class EmptyError(Exception):
            pass

        message = friendly_job_error(EmptyError())
        self.assertIn("EmptyError", message)
        self.assertIn("could not be analyzed automatically", message)

    def test_resource_limit_rejects_large_pdf_before_page_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "large.pdf"
            pdf_path.write_bytes(b"0" * 2048)

            with patch.dict("os.environ", {"STATEMENT_ANALYZER_MAX_UPLOAD_MB": "1"}, clear=False):
                with patch.object(service_module, "get_pdf_page_count", return_value=1) as mocked_page_count:
                    service_module.validate_pdf_resource_limits(pdf_path)
                    mocked_page_count.assert_called_once_with(pdf_path)

            with patch.dict("os.environ", {"STATEMENT_ANALYZER_MAX_UPLOAD_MB": "1"}, clear=False):
                pdf_path.write_bytes(b"0" * (1024 * 1024 + 1))
                with patch.object(service_module, "get_pdf_page_count") as mocked_page_count:
                    with self.assertRaisesRegex(ValueError, "too large"):
                        service_module.validate_pdf_resource_limits(pdf_path)
                    mocked_page_count.assert_not_called()

    def test_resource_limit_rejects_too_many_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "many-pages.pdf"
            pdf_path.write_bytes(b"%PDF")

            with patch.dict("os.environ", {"STATEMENT_ANALYZER_MAX_PAGES": "2"}, clear=False):
                with patch.object(service_module, "get_pdf_page_count", return_value=3):
                    with self.assertRaisesRegex(ValueError, "too many pages"):
                        service_module.validate_pdf_resource_limits(pdf_path)

    def test_analysis_output_path_is_unique_but_download_name_is_clean(self) -> None:
        output_path = webapp.analysis_output_path("abc123", "statement.pdf")

        self.assertEqual(output_path.name, "statement_abc123_ANALYZED.xlsx")
        self.assertEqual(webapp.download_filename("statement.pdf"), "statement_ANALYZED.xlsx")

    def test_find_analysis_output_by_token_recovers_generated_workbook(self) -> None:
        original_output_dir = webapp.OUTPUT_DIR

        with tempfile.TemporaryDirectory() as temp_dir:
            webapp.OUTPUT_DIR = Path(temp_dir)
            output_path = Path(temp_dir) / "bank_statement_abc123_ANALYZED.xlsx"
            output_path.write_bytes(b"excel")

            try:
                self.assertEqual(webapp.find_analysis_output_by_token("abc123"), output_path)
                self.assertEqual(webapp.recovered_download_filename(output_path), "bank_statement_ANALYZED.xlsx")
                self.assertIsNone(webapp.find_analysis_output_by_token("../abc123"))
            finally:
                webapp.OUTPUT_DIR = original_output_dir

    def test_refresh_summary_review_options_includes_new_custom_category(self) -> None:
        original_rules_file = rules_module.RULES_FILE

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_rules_file = Path(temp_dir) / "business_rules.json"
            temp_rules_file.write_text(json.dumps({"inflow_rules": [], "outflow_rules": []}), encoding="utf-8")
            rules_module.RULES_FILE = temp_rules_file

            try:
                summary = AnalysisSummary(
                    parser_name="test",
                    page_count=1,
                    account_name=None,
                    account_number=None,
                    currency="NGN",
                    period_label=None,
                    opening_balance=0.0,
                    closing_balance=100.0,
                    net_movement=100.0,
                    total_transactions=1,
                    inflow_count=1,
                    outflow_count=0,
                    total_credit=100.0,
                    total_debit=0.0,
                    classified_inflow_total=0.0,
                    classified_outflow_total=0.0,
                    unclassified_inflow_total=100.0,
                    unclassified_outflow_total=0.0,
                    inflow_breakdown=[],
                    outflow_breakdown=[],
                    reconciliation_checks=[
                        ReconciliationCheck(
                            label="Opening Balance",
                            expected=None,
                            actual=None,
                            matched=False,
                            difference=None,
                            available=False,
                        )
                    ],
                    available_check_count=0,
                    matched_check_count=0,
                    review_rows=[
                        ReviewRow(
                            transaction_index=0,
                            transaction_date=None,
                            description="Customer payment",
                            amount=100.0,
                            direction=TransactionDirection.INFLOW.value,
                            suggested_category=None,
                            selected_category=None,
                            confidence=0.35,
                            rule_name=None,
                            category_options=["Sales"],
                        )
                    ],
                    review_total_amount=100.0,
                )

                rules_module.add_custom_category("inflow", "Capital Injection")
                refresh_summary_review_options(summary)

                self.assertIn("Capital Injection", summary.review_rows[0].category_options)
            finally:
                rules_module.RULES_FILE = original_rules_file

    def test_analysis_jobs_run_one_at_a_time(self) -> None:
        original_executor = webapp._analysis_executor
        original_runner = webapp.run_analysis_job
        test_executor = ThreadPoolExecutor(max_workers=1)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        started_jobs: list[str] = []

        def fake_runner(job_id: str, **kwargs) -> None:
            started_jobs.append(job_id)
            if job_id == "first":
                first_started.set()
                release_first.wait(timeout=2)
                return
            second_started.set()

        webapp._analysis_executor = test_executor
        webapp.run_analysis_job = fake_runner
        try:
            webapp.start_analysis_job("first")
            self.assertTrue(first_started.wait(timeout=1))

            webapp.start_analysis_job("second")
            self.assertFalse(second_started.wait(timeout=0.2))

            release_first.set()
            self.assertTrue(second_started.wait(timeout=1))
            self.assertEqual(["first", "second"], started_jobs)
        finally:
            release_first.set()
            test_executor.shutdown(wait=True, cancel_futures=True)
            webapp._analysis_executor = original_executor
            webapp.run_analysis_job = original_runner


if __name__ == "__main__":
    unittest.main()
