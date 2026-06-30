from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from statement_analyzer.parsers.generic import (
    AdaptiveReviewRequired,
    apply_column_overrides,
    CONFIDENCE_THRESHOLD,
    GenericStatementParser,
    HeaderColumn,
    HeaderPlan,
    assess_adaptive_parse,
    build_adaptive_template,
    extract_generic_metadata,
    extract_transactions_from_pages,
    infer_header_plan,
    load_adaptive_templates,
    match_adaptive_template,
    parse_decimal_from_cell,
    save_adaptive_template,
)


class FakePage:
    def __init__(self, words: list[dict], text: str = "", width: float = 700) -> None:
        self._words = words
        self._text = text
        self.width = width

    def extract_words(self, **kwargs):
        return self._words

    def extract_text(self):
        return self._text


class FakePdf:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def make_word(text: str, x0: float, top: float) -> dict:
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + max(len(text) * 5, 20),
        "top": top,
    }


class AdaptiveParserTests(unittest.TestCase):
    def test_infers_unknown_layout_and_extracts_transactions(self) -> None:
        words = [
            make_word("Date", 20, 10),
            make_word("Description", 130, 10),
            make_word("Debit", 430, 10),
            make_word("Credit", 520, 10),
            make_word("Balance", 620, 10),
            make_word("Opening", 130, 28),
            make_word("Balance", 190, 28),
            make_word("1,000.00", 620, 28),
            make_word("02/01/2024", 20, 46),
            make_word("Sale", 130, 46),
            make_word("of", 165, 46),
            make_word("Tyres", 185, 46),
            make_word("500.00", 520, 46),
            make_word("1,500.00", 620, 46),
            make_word("03/01/2024", 20, 64),
            make_word("Transfer", 130, 64),
            make_word("to", 190, 64),
            make_word("Vendor", 210, 64),
            make_word("200.00", 430, 64),
            make_word("1,300.00", 620, 64),
            make_word("04/01/2024", 20, 82),
            make_word("Bank", 130, 82),
            make_word("Charge", 165, 82),
            make_word("25.00", 430, 82),
            make_word("1,275.00", 620, 82),
        ]
        page = FakePage(
            words,
            text=(
                "Account Name: Demo Ventures Ltd Account Number: 1234567890 "
                "Opening Balance: 1,000.00 Total Credit: 500.00 Total Debit: 225.00 Closing Balance: 1,275.00"
            ),
        )

        plan = infer_header_plan([page])

        self.assertIsNotNone(plan)
        assert isinstance(plan, HeaderPlan)
        self.assertTrue({"date", "description", "debit", "credit", "balance"}.issubset(plan.semantics))

        transactions = extract_transactions_from_pages(
            [page],
            plan,
            parser_name="adaptive-unknown",
            initial_balance=Decimal("1000.00"),
        )

        self.assertEqual(len(transactions), 4)
        self.assertEqual(transactions[0].description, "Opening Balance")
        self.assertEqual(transactions[1].credit, Decimal("500.00"))
        self.assertEqual(transactions[2].debit, Decimal("200.00"))

        metadata = extract_generic_metadata([page])
        assessment = assess_adaptive_parse(transactions, metadata, plan)

        self.assertGreaterEqual(assessment.score, CONFIDENCE_THRESHOLD)

    def test_adaptive_training_saves_and_reuses_named_layout(self) -> None:
        words = [
            make_word("Date", 20, 10),
            make_word("Description", 130, 10),
            make_word("Debit", 430, 10),
            make_word("Credit", 520, 10),
            make_word("Balance", 620, 10),
            make_word("Opening", 130, 28),
            make_word("Balance", 190, 28),
            make_word("1,000.00", 620, 28),
            make_word("02/01/2024", 20, 46),
            make_word("Sale", 130, 46),
            make_word("of", 165, 46),
            make_word("Tyres", 185, 46),
            make_word("500.00", 520, 46),
            make_word("1,500.00", 620, 46),
            make_word("03/01/2024", 20, 64),
            make_word("Vendor", 130, 64),
            make_word("Payment", 185, 64),
            make_word("200.00", 430, 64),
            make_word("1,300.00", 620, 64),
            make_word("04/01/2024", 20, 82),
            make_word("Bank", 130, 82),
            make_word("Charge", 165, 82),
            make_word("25.00", 430, 82),
            make_word("1,275.00", 620, 82),
        ]
        page = FakePage(
            words,
            text=(
                "Date Description Debit Credit Balance "
                "Account Name: Demo Ventures Ltd Account Number: 1234567890 "
                "Opening Balance: 1,000.00 Total Credit: 500.00 Total Debit: 225.00 Closing Balance: 1,275.00"
            ),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            templates_path = Path(tmp_dir) / "adaptive_layouts.json"
            fake_pdf = FakePdf([page])
            with patch("statement_analyzer.parsers.generic.open_pdf", return_value=fake_pdf), patch(
                "statement_analyzer.parsers.generic.detect_layout",
                return_value=None,
            ):
                trainer = GenericStatementParser(templates_file=templates_path)
                trained_transactions = trainer.parse_with_options(
                    Path("unknown.pdf"),
                    preferred_template_name="Demo Statement",
                    save_template=True,
                )

            self.assertEqual(len(trained_transactions), 4)
            templates = load_adaptive_templates(templates_path)
            self.assertEqual(len(templates), 1)
            self.assertEqual(templates[0].name, "Demo Statement")

            with patch("statement_analyzer.parsers.generic.open_pdf", return_value=FakePdf([page])), patch(
                "statement_analyzer.parsers.generic.detect_layout",
                return_value=None,
            ):
                parser = GenericStatementParser(templates_file=templates_path)
                reused_transactions = parser.parse_with_options(Path("same-layout.pdf"), save_template=False)

            self.assertEqual(len(reused_transactions), 4)
            self.assertEqual(parser.bank_name, "Demo Statement")
            self.assertIsNotNone(parser.last_matched_template)

    def test_infers_money_in_money_out_header_layout(self) -> None:
        words = [
            make_word("Trans", 20, 10),
            make_word("Date", 55, 10),
            make_word("Narration", 130, 10),
            make_word("Value", 350, 10),
            make_word("Date", 385, 10),
            make_word("Money", 500, 10),
            make_word("In", 540, 10),
            make_word("Money", 590, 10),
            make_word("Out", 630, 10),
            make_word("Balance", 680, 10),
            make_word("12-Aug-2025", 20, 30),
            make_word("Customer", 130, 30),
            make_word("Payment", 185, 30),
            make_word("12-Aug-2025", 350, 30),
            make_word("20,000.00", 500, 30),
            make_word("20,000.00", 680, 30),
            make_word("13-Aug-2025", 20, 48),
            make_word("Legal", 130, 48),
            make_word("Search", 165, 48),
            make_word("13-Aug-2025", 350, 48),
            make_word("10,000.00", 590, 48),
            make_word("10,000.00", 680, 48),
        ]
        page = FakePage(words, text="Trans Date Narration Value Date Money In Money Out Balance")

        plan = infer_header_plan([page])

        self.assertIsNotNone(plan)
        assert isinstance(plan, HeaderPlan)
        self.assertEqual(
            [(column.label, column.semantic) for column in sorted(plan.columns, key=lambda item: item.center)],
            [
                ("Trans Date", "date"),
                ("Narration", "description"),
                ("Value Date", "date"),
                ("Money In", "credit"),
                ("Money Out", "debit"),
                ("Balance", "balance"),
            ],
        )

        transactions = extract_transactions_from_pages([page], plan, parser_name="adaptive-unknown")

        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0].credit, Decimal("20000.00"))
        self.assertEqual(transactions[0].debit, Decimal("0"))
        self.assertEqual(transactions[1].debit, Decimal("10000.00"))
        self.assertEqual(transactions[1].credit, Decimal("0"))

    def test_metadata_totals_allow_counts_after_labels(self) -> None:
        text = (
            "ASCEND SYSTEM LIMITED\n"
            "0126032915 - (NGN)\n"
            "Opening balance: 0.00 NGN Total Credit (10): 20,111,700.00 NGN\n"
            "Closing balance: 55,370.85 NGN Total Debit (28): 20,056,329.15 NGN\n"
        )
        metadata = extract_generic_metadata([FakePage([], text=text)])

        self.assertEqual(metadata.account_name, "ASCEND SYSTEM LIMITED")
        self.assertEqual(metadata.account_number, "0126032915")
        self.assertEqual(metadata.currency, "NGN")
        self.assertEqual(metadata.total_credit, Decimal("20111700.00"))
        self.assertEqual(metadata.total_debit, Decimal("20056329.15"))

    def test_rejects_low_confidence_sparse_layout(self) -> None:
        words = [
            make_word("Date", 20, 10),
            make_word("Description", 150, 10),
            make_word("Debit", 500, 10),
            make_word("02/01/2024", 20, 28),
            make_word("Only", 150, 28),
            make_word("one", 180, 28),
            make_word("row", 210, 28),
            make_word("100.00", 500, 28),
            make_word("03/01/2024", 20, 46),
            make_word("Ambiguous", 150, 46),
        ]
        page = FakePage(words, text="Account Name: Sparse Layout Account Number: 123")

        plan = infer_header_plan([page])

        self.assertIsNone(plan)

    def test_successful_adaptive_parse_can_be_saved_as_template(self) -> None:
        words = [
            make_word("Trans", 20, 10),
            make_word("Date", 55, 10),
            make_word("Narration", 170, 10),
            make_word("Debit", 460, 10),
            make_word("Credit", 550, 10),
            make_word("Balance", 640, 10),
            make_word("05/01/2024", 20, 30),
            make_word("Customer", 170, 30),
            make_word("Payment", 220, 30),
            make_word("450.00", 550, 30),
            make_word("1,450.00", 640, 30),
            make_word("06/01/2024", 20, 48),
            make_word("Fuel", 170, 48),
            make_word("Purchase", 205, 48),
            make_word("200.00", 460, 48),
            make_word("1,250.00", 640, 48),
        ]
        page = FakePage(
            words,
            text=(
                "Trans Date Narration Debit Credit Balance "
                "Statement of Account Account Number: 88776655 Currency: NGN "
                "Opening Balance: 1,000.00 Total Credit: 450.00 Total Debit: 200.00 Closing Balance: 1,250.00"
            ),
        )
        plan = infer_header_plan([page])
        self.assertIsNotNone(plan)
        assert isinstance(plan, HeaderPlan)

        with tempfile.TemporaryDirectory() as tmp_dir:
            templates_path = Path(tmp_dir) / "adaptive_layouts.json"
            learned = save_adaptive_template(page.extract_text(), plan, templates_path)
            self.assertIsNotNone(learned)
            assert learned is not None
            self.assertTrue(templates_path.exists())

            templates = load_adaptive_templates(templates_path)
            self.assertEqual(len(templates), 1)

            matched = match_adaptive_template(page.extract_text(), templates)
            self.assertIsNotNone(matched)
            assert matched is not None
            self.assertEqual(matched.key, learned.key)

    def test_build_template_uses_header_and_metadata_terms(self) -> None:
        words = [
            make_word("Date", 20, 10),
            make_word("Description", 160, 10),
            make_word("Debit", 470, 10),
            make_word("Credit", 560, 10),
            make_word("Balance", 650, 10),
        ]
        page = FakePage(words, text="Account Name: Demo Ltd Account Number: 123 Opening Balance: 100.00")
        plan = infer_header_plan([page])
        self.assertIsNotNone(plan)
        assert isinstance(plan, HeaderPlan)

        template = build_adaptive_template(page.extract_text(), plan)

        self.assertIsNotNone(template)
        assert template is not None
        self.assertIn("DATE", template.required_terms)
        self.assertIn("DESCRIPTION", template.required_terms)
        self.assertIn("ACCOUNT NAME", [*template.required_terms, *template.optional_terms])

    def test_column_overrides_can_correct_detected_roles(self) -> None:
        words = [
            make_word("Date", 20, 10),
            make_word("Description", 150, 10),
            make_word("Credit", 470, 10),
            make_word("Debit", 560, 10),
            make_word("Balance", 650, 10),
            make_word("02/01/2024", 20, 28),
            make_word("Customer", 150, 28),
            make_word("Payment", 205, 28),
            make_word("100.00", 470, 28),
            make_word("0.00", 560, 28),
            make_word("1,100.00", 650, 28),
        ]
        page = FakePage(words, text="Date Description Credit Debit Balance")
        base_columns = [
            HeaderColumn("date", 20, 60, "Date"),
            HeaderColumn("description", 150, 250, "Description"),
            HeaderColumn("debit", 470, 520, "Credit"),
            HeaderColumn("credit", 560, 610, "Debit"),
            HeaderColumn("balance", 650, 710, "Balance"),
        ]
        corrected_columns = apply_column_overrides(base_columns, {2: "credit", 3: "debit"})
        plan = HeaderPlan(page_number=1, top=10, page_width=700, columns=corrected_columns, score=9.0)

        transactions = extract_transactions_from_pages(
            [page],
            plan,
            parser_name="adaptive-unknown",
            initial_balance=Decimal("1000.00"),
        )

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].credit, Decimal("100.00"))
        self.assertEqual(transactions[0].debit, Decimal("0"))

    def test_low_confidence_unknown_layout_pauses_for_review_then_can_continue(self) -> None:
        words = [
            make_word("Date", 20, 10),
            make_word("Description", 150, 10),
            make_word("Debit", 470, 10),
            make_word("Credit", 560, 10),
            make_word("Balance", 650, 10),
            make_word("02/01/2024", 20, 28),
            make_word("Mixed", 150, 28),
            make_word("Row", 190, 28),
            make_word("100.00", 470, 28),
            make_word("40.00", 560, 28),
            make_word("940.00", 650, 28),
            make_word("03/01/2024", 20, 46),
            make_word("Another", 150, 46),
            make_word("Mixed", 205, 46),
            make_word("90.00", 470, 46),
            make_word("20.00", 560, 46),
            make_word("870.00", 650, 46),
            make_word("04/01/2024", 20, 64),
            make_word("Third", 150, 64),
            make_word("Mixed", 185, 64),
            make_word("80.00", 470, 64),
            make_word("10.00", 560, 64),
            make_word("800.00", 650, 64),
        ]
        page = FakePage(
            words,
            text="Date Description Debit Credit Balance Account Name: Demo Ltd Account Number: 123",
        )
        fake_pdf = FakePdf([page])

        with tempfile.TemporaryDirectory() as tmp_dir:
            parser = GenericStatementParser(templates_file=Path(tmp_dir) / "adaptive_layouts.json")
            with patch("statement_analyzer.parsers.generic.pdfplumber.open", return_value=fake_pdf), patch(
                "statement_analyzer.parsers.generic.detect_layout",
                return_value=None,
            ):
                with self.assertRaises(AdaptiveReviewRequired) as context:
                    parser.parse_with_options(Path("unknown.pdf"))

                self.assertEqual(len(context.exception.preview_rows), 3)
                self.assertGreater(len(context.exception.reasons), 0)

                transactions = parser.parse_with_options(Path("unknown.pdf"), allow_low_confidence=True)

        self.assertEqual(len(transactions), 3)

    def test_row_pattern_fallback_can_infer_unknown_layout_without_header_row(self) -> None:
        words = [
            make_word("Opening", 120, 10),
            make_word("Balance", 180, 10),
            make_word("1,000.00", 620, 10),
            make_word("02/01/2024", 20, 30),
            make_word("Customer", 150, 30),
            make_word("Payment", 210, 30),
            make_word("500.00", 520, 30),
            make_word("1,500.00", 620, 30),
            make_word("03/01/2024", 20, 48),
            make_word("Fuel", 150, 48),
            make_word("Purchase", 190, 48),
            make_word("200.00", 520, 48),
            make_word("1,300.00", 620, 48),
            make_word("04/01/2024", 20, 66),
            make_word("Bank", 150, 66),
            make_word("Charge", 185, 66),
            make_word("25.00", 520, 66),
            make_word("1,275.00", 620, 66),
        ]
        page = FakePage(
            words,
            text=(
                "Statement of Account Account Name: Demo Ventures Ltd "
                "Opening Balance: 1,000.00 Total Credit: 500.00 Total Debit: 225.00 Closing Balance: 1,275.00"
            ),
        )

        plan = infer_header_plan([page])

        self.assertIsNotNone(plan)
        assert isinstance(plan, HeaderPlan)
        self.assertIn("amount", plan.semantics)

        transactions = extract_transactions_from_pages(
            [page],
            plan,
            parser_name="adaptive-unknown",
            initial_balance=Decimal("1000.00"),
        )

        self.assertEqual(len(transactions), 3)
        self.assertEqual(transactions[0].credit, Decimal("500.00"))
        self.assertEqual(transactions[1].debit, Decimal("200.00"))

    def test_row_pattern_fallback_separates_double_date_columns_from_description(self) -> None:
        words = [
            make_word("02", 20, 30),
            make_word("Mar", 38, 30),
            make_word("2026", 60, 30),
            make_word("02", 115, 30),
            make_word("Mar", 133, 30),
            make_word("2026", 155, 30),
            make_word("000012260302082642558544467310", 230, 30),
            make_word("Trf", 410, 30),
            make_word("to", 430, 30),
            make_word("AYM", 445, 30),
            make_word("SHAFA", 470, 30),
            make_word("25.00", 745, 30),
            make_word("-", 815, 30),
            make_word("40,836,603.59", 870, 30),
            make_word("MAIZUBE", 235, 48),
            make_word("02", 20, 78),
            make_word("Mar", 38, 78),
            make_word("2026", 60, 78),
            make_word("02", 115, 78),
            make_word("Mar", 133, 78),
            make_word("2026", 155, 78),
            make_word("Org.", 230, 78),
            make_word("Amt:", 255, 78),
            make_word("50000", 285, 78),
            make_word("CBN", 470, 78),
            make_word("STAMPDUTY", 500, 78),
            make_word("50.00", 745, 78),
            make_word("-", 815, 78),
            make_word("40,836,553.59", 870, 78),
            make_word("02", 20, 108),
            make_word("Mar", 38, 108),
            make_word("2026", 60, 108),
            make_word("02", 115, 108),
            make_word("Mar", 133, 108),
            make_word("2026", 155, 108),
            make_word("MOMOHNG", 230, 108),
            make_word("REHOBOTH", 300, 108),
            make_word("ENTERPRISES", 375, 108),
            make_word("-", 745, 108),
            make_word("900,000.00", 800, 108),
            make_word("41,736,553.59", 870, 108),
        ]
        page = FakePage(
            words,
            text="Statement of Account Opening Balance: 40,836,628.59 Closing Balance: 40,836,603.59",
            width=980,
        )

        plan = infer_header_plan([page])

        self.assertIsNotNone(plan)
        assert isinstance(plan, HeaderPlan)
        self.assertGreaterEqual(sum(1 for column in plan.columns if column.semantic == "date"), 2)

        transactions = extract_transactions_from_pages([page], plan, parser_name="adaptive-unknown")

        self.assertEqual(len(transactions), 3)
        self.assertTrue(transactions[0].description.startswith("000012260302082642558544467310 Trf to AYM SHAFA"))
        self.assertIn("MAIZUBE", transactions[0].description)
        self.assertNotIn("02 Mar 2026", transactions[0].description)

    def test_amount_cell_prefers_terminal_money_value_over_reference_digits(self) -> None:
        self.assertEqual(
            parse_decimal_from_cell("5213727602/090405/ 3.75", semantic="debit"),
            Decimal("3.75"),
        )
        self.assertEqual(
            parse_decimal_from_cell(
                "Org. Amt: 5000000 20260302_ 0193461_ 1_SB12693502 ... 50.00",
                semantic="debit",
            ),
            Decimal("50.00"),
        )

    def test_line_fallback_can_parse_text_only_unknown_statement(self) -> None:
        text = "\n".join(
            [
                "Statement of Account",
                "Account Name: Demo Ventures Ltd",
                "Account Number: 1234567890",
                "Opening Balance: 1,000.00",
                "Total Credit: 500.00",
                "Total Debit: 225.00",
                "Closing Balance: 1,275.00",
                "02/01/2024 Customer Payment 500.00 1,500.00",
                "03/01/2024 Fuel Purchase 200.00 1,300.00",
                "04/01/2024 Bank Charge 25.00 1,275.00",
            ]
        )
        page = FakePage([], text=text)
        fake_pdf = FakePdf([page])

        with tempfile.TemporaryDirectory() as tmp_dir:
            parser = GenericStatementParser(templates_file=Path(tmp_dir) / "adaptive_layouts.json")
            with patch("statement_analyzer.parsers.generic.pdfplumber.open", return_value=fake_pdf), patch(
                "statement_analyzer.parsers.generic.detect_layout",
                return_value=None,
            ):
                transactions = parser.parse_with_options(Path("text_only_unknown.pdf"))

        self.assertEqual(len(transactions), 3)
        self.assertEqual(transactions[0].credit, Decimal("500.00"))
        self.assertEqual(transactions[1].debit, Decimal("200.00"))
        self.assertEqual(transactions[2].debit, Decimal("25.00"))


if __name__ == "__main__":
    unittest.main()
