# Architecture

## Product direction

Bank statements do not share one universal structure. The safest architecture is:

- bank-specific parsers
- a shared normalized transaction schema
- a shared classification engine
- a shared Excel export layer

This allows us to support one bank at a time without breaking the entire system.

## Processing flow

```text
PDF Statement
  -> Bank detection / parser selection
  -> Raw text and table extraction
  -> Normalized transaction rows
  -> Inflow/outflow split
  -> Classification rules
  -> Workbook generation
  -> Review and correction layer
```

## System modules

### 1. Parser layer

Responsibility:

- detect or receive the bank type
- extract statement rows from the PDF
- handle layout quirks such as multiline descriptions and page breaks
- return normalized transactions

Design:

- one parser base interface
- one parser implementation per supported bank
- optional auto-detection by keywords or PDF header patterns

### 2. Normalization layer

Responsibility:

- convert parser output into one common model
- standardize dates
- clean amount fields
- map debit and credit into numeric columns
- preserve source metadata

Recommended normalized transaction fields:

- `transaction_date`
- `description`
- `reference`
- `debit`
- `credit`
- `balance`
- `currency`
- `direction`
- `raw_text`
- `source_page`
- `parser_name`

### 3. Classification layer

Responsibility:

- classify inflows and outflows into business categories
- support both default rules and client-specific rules
- expose confidence or match reason where possible

Recommended approach:

- use rule-based classification first
- add AI only for uncertain rows later

Rule sources:

- keyword matching
- counterparty names
- prefixes such as `NIP`, `POS`, `ATM`, `REV`, `CHARGE`, `VAT`
- amount patterns
- transaction direction

Example inflow categories:

- Own Account
- Reversals
- Sales
- Individual Transfer
- Loan Received
- Refund

Example outflow categories:

- Charges
- Salary
- Transport
- Construction
- Rent
- Food
- Loan
- Gas
- Goods
- Airtime
- Repairs and Maintenance

### 4. Export layer

Responsibility:

- generate Excel output that matches the business reporting format
- preserve totals
- create dedicated sheets for raw and classified data

Workbook sheets for MVP:

- `Main`
- `Inflows`
- `Outflows`
- `Analysis`

Recommended later additions:

- `Exceptions`
- `Audit Trail`
- `Rules Applied`

### 5. Review layer

Responsibility:

- flag ambiguous classifications
- allow manual correction
- store reusable correction rules for future runs

This is important because transaction descriptions are often inconsistent across banks and businesses.

## Sample Inventory

We now have real sample PDFs spanning multiple bank layouts. See `docs/bank_support_matrix.md` for the current sample-bank inventory, signature headers, and recommended parser order.

## Multi-bank strategy

The system should never assume all banks use the same columns or debit/credit naming style.

Instead:

- treat each parser as an adapter
- keep the normalized schema stable
- keep classification and export logic bank-agnostic

This makes the system scalable.

## Suggested MVP boundaries

Phase 1:

- support one bank
- parse text-based PDFs only
- export workbook matching the sample structure
- implement rule-based classification

Phase 2:

- support more banks
- add review UI
- add client-specific category rules

Phase 3:

- scanned PDF OCR
- AI-assisted classification
- dashboard and job history

## Key technical risks

- scanned or image-only PDFs
- multiline descriptions merged across rows
- statements with missing debit or credit columns
- banks that use running balance in unusual formats
- business-specific classification rules that differ by client

## Recommended initial implementation

Build the first version around:

- one target bank
- one known sample workbook format
- one deterministic parser
- one configurable rule engine

That gives us an accurate MVP faster than attempting a universal parser too early.
