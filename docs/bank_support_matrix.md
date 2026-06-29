# Bank Support Matrix

This inventory is based on the sample PDFs currently in the project folder. The goal is one upload experience for users, with layout-specific parsing handled internally.

## Current sample coverage

| Status | Bank / Layout | Sample file | Key header signatures | Notes |
|---|---|---|---|---|
| Supported | Zenith-style | `OKOTIE ENOCK CONSTRUCTION COMP. LTD.pdf` | `DATE POSTED`, `VALUE DATE`, `DESCRIPTION`, `DEBIT`, `CREDIT`, `BALANCE` | Working parser exists and reconciliation already matches exactly. |
| Supported | UBA | `UBA_2.pdf` | `Bank Statement`, `TRANS DATE`, `VALUE DATE`, `NARRATION`, `DEBIT`, `CREDIT`, `BALANCE` | Parser now extracts rows and reconciles header totals and balances exactly. |
| Supported | FCMB | `1799745027-27150814102 (1).pdf` | `ACCOUNT STATEMENT SUMMARY DETAILS`, `Date`, `Reference`, `Description`, `ValueDate`, `Deposit`, `Withdrawal`, `Balance` | Parser now extracts rows and reconciles header totals and balances exactly. |
| Supported | FirstBank | `3025345387 (2).pdf` | `Trans Date`, `Ref. Number`, `Transaction Details`, `Withdrawal(DR)`, `Deposit(CR)`, `Balance` | Parser now extracts multiline rows across the full statement and reconciles header totals and balances exactly. |
| Supported | Fidelity Bank | `Fidelity S&P sub.pdf` | `Fidelity Premium Business Account`, `Transaction`, `Value Date`, `Reference`, `Channel`, `Description`, `Pay In`, `Pay Out`, `Balance` | Parser now handles wrapped Online Banking continuations, cross-page carry-over rows, and reconciles opening and closing balances exactly. |
| Supported | GTBank | `GTB STATEMENT_058713784 - Copy.pdf` | `STATEMENT PERIOD`, `CUSTOMER STATEMENT`, `Trans. Date`, `Value. Date`, `Reference`, `Debits`, `Credits`, `Balance`, `Remarks` | Parser now extracts rows, carries multiline remarks across page boundaries, and reconciles totals and balances exactly. |
| Supported | Wema Treasure individual | `LIVINUS 004.pdf` | `Statement Period`, `Acct Name`, `WEMA TREASURE ACCOUNT - INDIVIDUAL`, `Tran Date`, `Value Date`, `Narration`, `Withdrawals`, `Deposits`, `Balance` | Parser extracts wrapped narration, account metadata, totals, and reconciles all summary checks exactly. |
| Supported | Moniepoint business statement | `Moniepoint-Document-2026-05-12T10-42_260512_221856.pdf` | `Account Statement`, `Account Summary`, `Business Name`, `Date`, `Narration`, `Reference`, `Debit`, `Credit`, `Balance` | Parser handles long timestamped multi-page exports, cross-page row continuations, and reconciles all summary checks exactly. |
| Supported | TAJ Bank | `ACTION ENERGY LTD Account Number 0000071915 - Copy.pdf` | `TAJ CORP CURRENT`, `Trans Date`, `Value date`, `Transaction Details`, `Reference`, `Deposit`, `Withdrawal`, `Balance` | Parser now extracts fixed-width corporate rows, resolves spilled amount tokens against the running balance, and reconciles totals and balances exactly. |
| Supported | Jaiz Bank | `ACTION_Jaiz 1.pdf` | `CUSTOMER NAME`, `ACCOUNT NO`, `PERIOD`, `NARRATION`, `VALUE DATE`, `DEBIT`, `CREDIYT`, `BALANCE` | Parser now repairs OCR-noisy date fragments, stitches wrapped narration and split balance cents, and reconciles header totals and balances exactly. |
| Supported | Globus Bank | `Action 2022 statement_180923_013428_Globus Bank - Copy.pdf` | `Summary Statement`, `Total Withdrawals`, `Total Lodgement`, `S/n`, `Post Date`, `Value Date`, `Description`, `Debit`, `Credit`, `Balance` | Parser now extracts rows and reconciles summary totals and balances exactly. |
| Supported | Lotus Bank | `Action Energy Statement Lotus Bank - Copy.pdf` | `Book Date`, `Reference`, `Description`, `Value Date`, `Debit`, `Credit`, `Closing Balance` | Parser now handles bundled multi-period exports, multiline descriptions, wrapped balances, and opening/closing balance carry-forward logic. |
| Supported | Unknown corporate withdrawals/lodgements layout | `0806772213_2023-01-01_2024-01-01_transaction.pdf` | `Account Statement Summary Details`, `TOTAL WITHDRAWALS`, `TOTAL LODGEMENTS`, `Withdrawals`, `Lodgements`, `Balance` | Parser now extracts summary metadata, attaches multiline transaction details across page boundaries, and reconciles totals and balances exactly. |
| Supported | Providus TXN DATE / REMARKS layout | `Statement  2025.pdf` | `TXN DATE`, `VAL DATE`, `REMARKS`, `DEBIT`, `CREDIT`, `BALANCE` | Parser now extracts multiline rows and reconciles opening/closing balances exactly. |
| Supported | Clear Junction signed-amount layout | `newstatementfile.pdf` | `Clear Junction Limited`, `Oper. date`, `Order number`, `Amount`, `Transaction Fee`, `Cross Scheme` | Parser splits positive/negative Amount values into inflow/outflow columns and keeps transaction fees separate in a simplified workbook. |

## What this means technically

The user-facing system can still be one smart uploader for all banks.

Under the hood, we should use:

1. Layout detection
2. Bank/layout-specific extraction
3. One normalized transaction schema
4. One shared classification engine
5. One shared reconciliation and Excel output layer

## Recommended implementation order

1. Add more real-world samples for each supported Nigerian layout family.
2. Persist user-approved corrections as reusable business rules.
3. Add OCR support for image-based statements that are not text PDFs.

## Why this works

Most of the supplied statements are text-based PDFs, not scanned images. That is the best-case scenario for building a reliable multi-bank analyzer with strong reconciliation checks.
