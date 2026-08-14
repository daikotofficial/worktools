from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class TransactionDirection(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class Transaction:
    transaction_date: date | None
    description: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    balance: Decimal | None = None
    reference: str | None = None
    currency: str = "NGN"
    transaction_fee: Decimal = Decimal("0")
    raw_text: str | None = None
    source_page: int | None = None
    parser_name: str | None = None

    @property
    def direction(self) -> TransactionDirection:
        if self.credit > 0 and self.debit == 0:
            return TransactionDirection.INFLOW
        if self.debit > 0 and self.credit == 0:
            return TransactionDirection.OUTFLOW
        return TransactionDirection.UNKNOWN

    @property
    def amount(self) -> Decimal:
        if self.direction == TransactionDirection.INFLOW:
            return self.credit
        if self.direction == TransactionDirection.OUTFLOW:
            return self.debit
        return Decimal("0")


@dataclass(slots=True)
class ClassifiedTransaction:
    transaction: Transaction
    classification: str
    confidence: float = 1.0
    rule_name: str | None = None
    category_amounts: dict[str, Decimal] = field(default_factory=dict)


@dataclass(slots=True)
class StatementMetadata:
    account_name: str | None = None
    account_number: str | None = None
    currency: str | None = None
    opening_balance: Decimal | None = None
    total_debit: Decimal | None = None
    total_credit: Decimal | None = None
    closing_balance: Decimal | None = None
    blocked_amount: Decimal | None = None
    period_start: date | None = None
    period_end: date | None = None


@dataclass(slots=True)
class StatementAnalysis:
    all_transactions: list[Transaction]
    classified_transactions: list[ClassifiedTransaction]
    inflows: list[ClassifiedTransaction]
    outflows: list[ClassifiedTransaction]
    parser_name: str | None = None
    metadata: StatementMetadata | None = None
