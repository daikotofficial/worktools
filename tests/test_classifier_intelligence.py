from __future__ import annotations

import unittest
from decimal import Decimal

from statement_analyzer.classifiers.rules import (
    RuleBasedClassifier,
    extract_counterparty,
    extract_purpose,
    outflow_categories,
)
from statement_analyzer.models import StatementMetadata, Transaction


class ClassifierIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = RuleBasedClassifier()

    def test_default_outflow_categories_do_not_include_sample_names(self) -> None:
        categories = outflow_categories()

        self.assertNotIn("Kess", categories)
        self.assertNotIn("Kesiena", categories)

    def test_inflow_from_same_owner_is_own_account(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="NIBSS Trf Credit ACTION ENERGY LTD To JAIZ BANK | ACTION ENERGY LTD Ref: 340294534",
            credit=Decimal("81000000.00"),
            debit=Decimal("0"),
        )
        metadata = StatementMetadata(account_name="ACTION ENERGY LTD")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Own Account")

    def test_outflow_beneficiary_person_is_not_confused_with_business_owner(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="Mob Trf IFO ACTION ENERGY LTD BO NASIRU BALA Nb Ref: 340286267",
            credit=Decimal("0"),
            debit=Decimal("25000000.00"),
        )
        metadata = StatementMetadata(account_name="ACTION ENERGY LTD")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Individual Transfer")

    def test_outflow_to_same_owner_is_own_account(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="Mob Trf IFO ACTION ENERGY LTD BO ACTION ENERGY LTD Nb Ref: 340286267",
            credit=Decimal("0"),
            debit=Decimal("25000000.00"),
        )
        metadata = StatementMetadata(account_name="ACTION ENERGY LTD")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Own Account")

    def test_sms_alert_fee_with_vat_stays_charge_not_tax(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="SMS Alert Fee-24/11-23/12/2023 + VAT 534122280",
            credit=Decimal("0"),
            debit=Decimal("68.80"),
        )

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Charges")

    def test_sales_markers_drive_sales_classification(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="PP_ENAROW/3 pieces of TYRES FOR TRUCKS PP_BEN_103334 /OMEGA LOGISTICS LTD",
            credit=Decimal("304000.00"),
            debit=Decimal("0"),
        )
        metadata = StatementMetadata(account_name="EMPIRE ENERGY LTD")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Sales")

    def test_transfer_to_business_beneficiary_stays_business_transfer(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="Mob Trf IFO ACTION ENERGY LTD BO OMEGA LOGISTICS LTD Nb Ref: 340286267",
            credit=Decimal("0"),
            debit=Decimal("500000.00"),
        )
        metadata = StatementMetadata(account_name="ACTION ENERGY LTD")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Business Transfer")

    def test_business_account_customer_purchase_inflow_is_sales(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="Ridwan Soliu SABON TASHA Purchase",
            credit=Decimal("1000000.00"),
            debit=Decimal("0"),
        )
        metadata = StatementMetadata(account_name="SOL-TAYLOR INVESTMENT LTD SABON TASHA")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Sales")

    def test_whole_cow_outflow_is_goods(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="MOHAMMED ABUBAKAR INUWA SABON TASHA WHOLE COW",
            credit=Decimal("0"),
            debit=Decimal("2542600.00"),
        )

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Goods")

    def test_business_owner_fipmb_inflow_is_sales(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="MATTHEW FRIDAY EDACHE SABON TASHA FIPMBMATTHEW FRIDAY EDASOL TAYLOR INVESTMENTS LIMI",
            credit=Decimal("1500000.00"),
            debit=Decimal("0"),
        )
        metadata = StatementMetadata(account_name="SOL-TAYLOR INVESTMENT LTD SABON TASHA")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Sales")

    def test_business_owner_payment_inflow_is_sales(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="BENJAMIN SABON TASHA Payment",
            credit=Decimal("32000.00"),
            debit=Decimal("0"),
        )
        metadata = StatementMetadata(account_name="SOL-TAYLOR INVESTMENT LTD SABON TASHA")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Sales")

    def test_business_owner_pack_receipt_is_sales(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="EMMANUEL BISILATEEF SABON TASHA 50cl 8packs",
            credit=Decimal("14400.00"),
            debit=Decimal("0"),
        )
        metadata = StatementMetadata(account_name="SOL-TAYLOR INVESTMENT LTD SABON TASHA")

        result = self.classifier.classify(transaction, metadata=metadata)

        self.assertEqual(result.classification, "Sales")

    def test_towing_outflow_is_transport(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="IDOWU LEKAN SAMSON SABON TASHA TOWING OF TRUCK",
            credit=Decimal("0"),
            debit=Decimal("100000.00"),
        )

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Transport")

    def test_driver_allowance_is_transport(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="SUNDAY FUNSO ABIODUN SABON TASHA DRIVERS ALLOWANCE",
            credit=Decimal("0"),
            debit=Decimal("45000.00"),
        )

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Transport")

    def test_break_drum_for_supply_van_is_repair(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="JAMES NWOYE SABON TASHA BREAK DRUM FOR SUPPLY VAN",
            credit=Decimal("0"),
            debit=Decimal("58000.00"),
        )

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Repair & Maintenance")

    def test_slash_mobile_transfer_extracts_person_counterparty_and_classifies(self) -> None:
        description = "CIP/CR/MOB/RASHIDAT OJONUGWA YAKUBU/OPAY /000015251023115919653147211996/Wedding/ZMO5644679640"
        transaction = Transaction(
            transaction_date=None,
            description=description,
            credit=Decimal("0"),
            debit=Decimal("200000.00"),
        )

        self.assertEqual(extract_counterparty(description), "RASHIDAT OJONUGWA YAKUBU")
        self.assertEqual(extract_purpose(description), "WEDDING")

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Individual Transfer")

    def test_charge_reversal_inflow_is_classified_as_reversal(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="RVSL SMS CHARGE JUN 2025",
            credit=Decimal("24.00"),
            debit=Decimal("0"),
        )

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Reversals")

    def test_rev_vat_inflow_is_classified_as_reversal(self) -> None:
        transaction = Transaction(
            transaction_date=None,
            description="03032026090308 SABON TASHA REV-VAT",
            credit=Decimal("3774.62"),
            debit=Decimal("0"),
        )

        result = self.classifier.classify(transaction)

        self.assertEqual(result.classification, "Reversals")


if __name__ == "__main__":
    unittest.main()
