from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from statement_analyzer.classifiers import rules as rules_module
from statement_analyzer.classifiers.rules import (
    RuleBasedClassifier,
    add_custom_category,
    inflow_categories,
    learn_rules_from_approved_transactions,
    outflow_categories,
)
from statement_analyzer.models import StatementMetadata, Transaction


class RuleLearningTests(unittest.TestCase):
    def test_manual_approval_can_be_persisted_as_reusable_rule(self) -> None:
        original_rules_file = rules_module.RULES_FILE

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_rules_file = Path(temp_dir) / "business_rules.json"
            temp_rules_file.write_text(json.dumps({"inflow_rules": [], "outflow_rules": []}), encoding="utf-8")
            rules_module.RULES_FILE = temp_rules_file

            try:
                transaction = Transaction(
                    transaction_date=None,
                    description="NIP TRF TO OMEGA VILLA SUPPLIES // project support",
                    debit=Decimal("50000.00"),
                    credit=Decimal("0"),
                    balance=Decimal("1000.00"),
                )

                baseline = RuleBasedClassifier().classify(transaction)
                self.assertNotEqual(baseline.classification, "Goods")

                source_metadata = StatementMetadata(account_name="SOURCE BUSINESS LTD")
                added = learn_rules_from_approved_transactions(
                    [(transaction, "Goods")],
                    account_name=source_metadata.account_name,
                )
                self.assertEqual(added, 1)

                config = json.loads(temp_rules_file.read_text(encoding="utf-8"))
                self.assertEqual(len(config["outflow_rules"]), 1)
                self.assertEqual(config["outflow_rules"][0]["category"], "Goods")
                self.assertEqual(config["outflow_rules"][0]["account_name_any"], ["SOURCE BUSINESS LTD"])

                relearned = RuleBasedClassifier().classify(transaction, metadata=source_metadata)
                self.assertEqual(relearned.classification, "Goods")
                self.assertEqual(relearned.rule_name, "outflow-goods")
                self.assertGreaterEqual(relearned.confidence, 0.99)

                other_account = RuleBasedClassifier().classify(
                    transaction,
                    metadata=StatementMetadata(account_name="OTHER BUSINESS LTD"),
                )
                self.assertNotEqual(other_account.classification, "Goods")
            finally:
                rules_module.RULES_FILE = original_rules_file

    def test_custom_categories_are_persisted_and_returned_in_dropdown_lists(self) -> None:
        original_rules_file = rules_module.RULES_FILE

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_rules_file = Path(temp_dir) / "business_rules.json"
            temp_rules_file.write_text(json.dumps({"inflow_rules": [], "outflow_rules": []}), encoding="utf-8")
            rules_module.RULES_FILE = temp_rules_file

            try:
                self.assertEqual(add_custom_category("outflow", "Education"), "Education")
                self.assertEqual(add_custom_category("inflow", "Capital Injection"), "Capital Injection")

                config = json.loads(temp_rules_file.read_text(encoding="utf-8"))
                self.assertIn("Education", config["custom_categories"]["outflow"])
                self.assertIn("Capital Injection", config["custom_categories"]["inflow"])
                self.assertIn("Education", outflow_categories())
                self.assertIn("Capital Injection", inflow_categories())
            finally:
                rules_module.RULES_FILE = original_rules_file


if __name__ == "__main__":
    unittest.main()
