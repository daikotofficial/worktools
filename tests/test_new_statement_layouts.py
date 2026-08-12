from decimal import Decimal
from pathlib import Path
import unittest

from statement_analyzer.parsers.fidelity import FidelityStatementParser
from statement_analyzer.parsers.op_transaction_history import OPTransactionHistoryParser


ROOT = Path(__file__).resolve().parents[1]


class NewStatementLayoutTests(unittest.TestCase):
    def test_opay_transaction_history_uses_withdrawal_and_deposit_columns(self) -> None:
        parser = OPTransactionHistoryParser()
        transactions = parser.parse(ROOT / "OpTransactionHistoryUX504-03-2026.pdf")
        metadata = parser.last_metadata
        self.assertEqual(len(transactions), 62)
        self.assertEqual(sum((item.credit for item in transactions), Decimal("0")), Decimal("0"))
        self.assertEqual(sum((item.debit for item in transactions), Decimal("0")), metadata.total_debit)
        self.assertEqual(transactions[-1].balance, metadata.closing_balance)

    def test_fidelity_account_statement_variant_uses_pay_in_and_pay_out(self) -> None:
        parser = FidelityStatementParser()
        transactions = parser.parse(ROOT / "Account_Statement_3935 (1).pdf")
        metadata = parser.last_metadata
        self.assertEqual(len(transactions), 76)
        self.assertEqual(sum((item.credit for item in transactions), Decimal("0")), Decimal("1390700.00"))
        self.assertEqual(sum((item.debit for item in transactions), Decimal("0")), Decimal("4675323.90"))
        self.assertEqual(transactions[-1].balance, metadata.closing_balance)


if __name__ == "__main__":
    unittest.main()
