from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from statement_analyzer.parsers.pdf_utils import open_pdf


@dataclass(slots=True)
class LayoutProfile:
    key: str
    label: str
    bank_name: str
    sample_files: tuple[str, ...]
    required_terms: tuple[str, ...]
    optional_terms: tuple[str, ...] = ()
    notes: str = ""

    def score(self, text: str) -> int:
        upper = text.upper()
        if not all(term in upper for term in self.required_terms):
            return -1
        return len(self.required_terms) * 10 + sum(1 for term in self.optional_terms if term in upper)


LAYOUT_PROFILES: tuple[LayoutProfile, ...] = (
    LayoutProfile(
        key='zenith_style',
        label='Zenith-style current statement',
        bank_name='Zenith-style',
        sample_files=('OKOTIE ENOCK CONSTRUCTION COMP. LTD.pdf',),
        required_terms=('DATE POSTED', 'VALUE DATE', 'DESCRIPTION', 'DEBIT', 'CREDIT', 'BALANCE'),
        optional_terms=('ACCOUNT NUMBER:', 'TOTAL DEBIT:', 'TOTAL CREDIT:', 'CLOSING BALANCE:'),
        notes='Already supported by the first working parser.',
    ),
    LayoutProfile(
        key='stanbic_ibtc_statement',
        label='Stanbic IBTC account statement layout',
        bank_name='Stanbic IBTC',
        sample_files=('STANBIC IBTC Account Statement (2).pdf',),
        required_terms=('POSTED', 'CREATE', 'NARRATION', 'DEBIT', 'CREDIT', 'BALANCE'),
        optional_terms=('INFLOW VS OUTFLOW', 'CURRENT BALANCE', 'ACCOUNT NUMBER:', 'DATE GENERATED:'),
        notes='Stanbic IBTC export with Posted Date / Create Date / Narration / Debit / Credit / Balance columns.',
    ),
    LayoutProfile(
        key='keystone_statement',
        label='Keystone Bank account statement layout',
        bank_name='Keystone Bank',
        sample_files=('WISDOM KWATI- ACCOUNT STATEMENT LOCATION.pdf',),
        required_terms=('KEYSTONE BANK', 'ACCOUNT STATEMENT SUMMARY DETAILS', 'DATE', 'NARRATION', 'DEBIT', 'CREDIT', 'BALANCE'),
        optional_terms=('TOTAL DEBITS', 'TOTAL CREDITS', 'CURRENCY NAIRA', 'PRIVATE & CONFIDENTIAL'),
        notes='Keystone corporate statement with Date / V. Date / Narration / Ref / Debit / Credit / Balance and wrapped rows.',
    ),
    LayoutProfile(
        key='uba_statement',
        label='UBA statement layout',
        bank_name='UBA',
        sample_files=('UBA_2.pdf',),
        required_terms=('BANK STATEMENT', 'ACCOUNT NUMBER:', 'TOTAL DEBIT:', 'TOTAL CREDIT:', 'TRANS DATE', 'NARRATION'),
        optional_terms=('HELLO', 'OPENING BALANCE:', 'CLOSING BALANCE:'),
        notes='Uses TRANS DATE / VALUE DATE / NARRATION and includes debit-credit totals in the header.',
    ),
    LayoutProfile(
        key='fcmb_statement',
        label='FCMB summary/details layout',
        bank_name='FCMB',
        sample_files=('1799745027-27150814102 (1).pdf',),
        required_terms=('ACCOUNT STATEMENT SUMMARY DETAILS', 'ACCOUNT NO:', 'TOTAL CREDIT:', 'TOTAL DEBIT:', 'DATE', 'REFERENCE', 'VALUEDATE'),
        optional_terms=('FCMB BUSINESS ACCOUNT', 'DEPOSIT', 'WITHDRAWAL', 'OPENING BALANCE:'),
        notes='Header shows summary details before a Date/Reference/Description table.',
    ),
    LayoutProfile(
        key='firstbank_statement',
        label='FirstBank personal statement layout',
        bank_name='FirstBank',
        sample_files=('3025345387 (2).pdf',),
        required_terms=('ACCOUNT NO:', 'ACCOUNT NAME:', 'TOTAL CREDIT:', 'TOTAL DEBIT:', 'TRANS DATE', 'REF. NUMBER', 'TRANSACTION DETAILS'),
        optional_terms=('WITHDRAWAL(DR)', 'DEPOSIT(CR)', 'OPENING BALANCE', 'FBNMOBILE'),
        notes='Likely FirstBank; includes Ref. Number and Withdrawal(DR)/Deposit(CR).',
    ),
    LayoutProfile(
        key='fidelity_statement',
        label='Fidelity business statement layout',
        bank_name='Fidelity Bank',
        sample_files=('Fidelity S&P sub.pdf',),
        required_terms=(
            'WWW.FIDELITYBANK.NG',
            'FIDELITY PREMIUM BUSINESS ACCOUNT',
            'TRANSACTION VALUE DATE REFERENCE CHANNEL DESCRIPTION',
            'PAY OUT',
            'ENDING BALANCE',
        ),
        optional_terms=('CUSTOMER SERVICE INFORMATION', 'PAY IN', 'TRANSACTIONS', 'BEGINNING BALANCE'),
        notes='Business-account statement with Transaction Date / Value Date / Reference / Channel / Description / Pay In / Pay Out / Balance and wrapped Online Banking continuations.',
    ),
    LayoutProfile(
        key='fidelity_account_statement_variant',
        label='Fidelity account statement pay-in/pay-out layout',
        bank_name='Fidelity Bank',
        sample_files=('Account_Statement_3935 (1).pdf',),
        required_terms=(
            'FIDELITYBANK.NG',
            'TRANSACTIONS',
            'TRANSACTION VALUE DATE',
            'CHANNEL DETAILS',
            'PAY IN',
            'PAY OUT',
            'BALANCE',
            'OPENING BALANCE',
        ),
        optional_terms=('CURRENCY: NGN', 'CLOSING BALANCE'),
        notes='Fidelity export with Transaction Date / Value Date / Channel / Details / Pay In / Pay Out / Balance and wrapped details.',
    ),
    LayoutProfile(
        key='taj_statement',
        label='TAJ bank corporate statement layout',
        bank_name='TAJ Bank',
        sample_files=('ACTION ENERGY LTD Account Number 0000071915 - Copy.pdf',),
        required_terms=('TAJ CORP CURRENT', 'TOTAL CREDIT', 'TOTAL DEBIT', 'STATEMENT OF ACCOUNT', 'TRANS DATE', 'TRANSACTION DETAILS'),
        optional_terms=('REFERENCE', 'DEPOSIT', 'WITHDRAWAL', 'BALANCE BROUGHT FORWARD'),
        notes='Corporate current account layout with branch column and long transaction-details field.',
    ),
    LayoutProfile(
        key='jaiz_statement',
        label='Jaiz bank corporate statement layout',
        bank_name='Jaiz Bank',
        sample_files=('ACTION_Jaiz 1.pdf',),
        required_terms=('CUSTOMER NAME:', 'ACCOUNT NO:', 'PERIOD:', 'TRANSACTI', 'NARRATION', 'DEBIT', 'BALANCE'),
        optional_terms=('JAIZ', 'CURRENT ACCOUNT CORPORATE', 'CLOSING', 'CREDIYT'),
        notes='Has OCR noise in the header and a split TRANSACTION DATE / VALUE DATE / NARRATION layout.',
    ),
    LayoutProfile(
        key='globus_statement',
        label='Globus single-page statement layout',
        bank_name='Globus Bank',
        sample_files=('Action 2022 statement_180923_013428_Globus Bank - Copy.pdf',),
        required_terms=('GENERATED ON', 'GLOBUS', 'SUMMARY STATEMENT FOR', 'TOTAL WITHDRAWALS', 'TRANSACTIONS'),
        optional_terms=('TOTAL LODGEMENT', 'TOTAL LODGEMENTS', 'S/N', 'POST DATE', 'VALUE DATE', 'DESCRIPTION', 'DEBIT', 'CREDIT'),
        notes='Compact single-page statement with summary and transaction table on one page.',
    ),
    LayoutProfile(
        key='lotus_statement',
        label='Lotus bank statement layout',
        bank_name='Lotus Bank',
        sample_files=('Action Energy Statement Lotus Bank - Copy.pdf',),
        required_terms=('ACCOUNT STATEMENT FOR THE PERIOD', 'ACCOUNT NUMBER :', 'ACCOUNT NAME :', 'BOOK DATE', 'REFERENCE', 'DESCRIPTION', 'CLOSING BALANCE'),
        optional_terms=('LOTUS', 'OPENING BALANCE:', 'VALUE DATE', 'DEBIT', 'CREDIT'),
        notes='Long multiline descriptions, with Book Date and Closing Balance columns.',
    ),
    LayoutProfile(
        key='standard_chartered_statement',
        label='Standard Chartered savings statement layout',
        bank_name='Standard Chartered',
        sample_files=('Bank 2 JOSHUA IDA SAMSON 2023.pdf',),
        required_terms=(
            'ACCOUNT STATEMENT',
            'STANDARD CHARTERED BANK',
            'ACCOUNT NUMBER',
            'NOMINEE REGISTERED',
            'DEPOSIT',
            'WITHDRAWAL',
            'BALANCE BROUGHT FORWARD',
        ),
        optional_terms=('VALUE DATE', 'CHEQUE', 'STATEMENT DATE', 'NIGERIAN NAIRA'),
        notes='Savings statement with Date / Value Date / Description / Cheque / Deposit / Withdrawal / Balance and repeated page headers.',
    ),
    LayoutProfile(
        key='gtbank_statement',
        label='GTBank customer statement layout',
        bank_name='GTBank',
        sample_files=('GTB STATEMENT_058713784 - Copy.pdf',),
        required_terms=('STATEMENT PERIOD', 'ACCOUNT NO', 'TOTAL DEBIT', 'TOTAL CREDIT', 'CUSTOMER STATEMENT', 'TRANS. DATE', 'REMARKS'),
        optional_terms=('DEBITS', 'CREDITS', 'BALANCE', 'USABLE BALANCE'),
        notes='Shows customer statement metadata before a Debits/Credits/Balance/Remarks table.',
    ),
    LayoutProfile(
        key='wema_statement',
        label='Wema bank corporate statement layout',
        bank_name='Wema Bank',
        sample_files=('_SOL-TAYLOR WEMA.pdf',),
        required_terms=(
            'ACCOUNT STATEMENT',
            'CURRENT BALANCE EFFECTIVE AVAILABLE BALANCE',
            'CREDIT COUNT DEBIT COUNT',
            'VALUE TRANSACTION REFERENCE',
            'TRANSACTION DETAILS',
            'CREDIT',
            'DEBIT',
            'BALANCE',
        ),
        optional_terms=('CURRENT ACCOUNT - CORPORATE', 'DATE PRINTED', 'START DATE', 'END DATE'),
        notes='Corporate Wema statement with Value Date / Transaction Date / Reference / Details / Credit / Debit / Balance columns.',
    ),
    LayoutProfile(
        key='wema_account_statement_variant',
        label='Wema account statement credit/debit layout',
        bank_name='Wema Bank',
        sample_files=('04032026030223_Statement_For_SOL-TAYLOR INVESTMENTS LTD.pdf',),
        required_terms=(
            'ACCOUNT STATEMENT',
            'CURRENT BALANCE EFFECTIVE AVAILABLE BALANCE',
            'CREDIT COUNT DEBIT COUNT',
            'TRANSACTION DETAILS',
            'CREDIT(₦)',
            'DEBIT(₦)',
            'BALANCE(₦)',
        ),
        optional_terms=('ACCOUNT TYPE', 'DATE PRINTED', 'START DATE', 'END DATE'),
        notes='Wema corporate export with a split Date / Reference / Transaction Details table and explicitly labeled Credit/Debit amount columns.',
    ),
    LayoutProfile(
        key='wema_treasure_statement',
        label='Wema Treasure individual statement layout',
        bank_name='Wema Bank',
        sample_files=('LIVINUS 004.pdf',),
        required_terms=(
            'STATEMENT PERIOD:',
            'ACCT NAME:',
            'ACCT NO:',
            'WEMA TREASURE ACCOUNT - INDIVIDUAL',
            'TRAN DATE',
            'VALUE DATE',
            'WITHDRAWALS',
            'DEPOSITS',
            'BALANCE',
        ),
        optional_terms=('DATE PRINTED:', 'DEBIT COUNT:', 'CREDIT COUNT:', 'TOTAL DEBIT:', 'TOTAL CREDIT:'),
        notes='Individual Wema Treasure export with Tran Date / Value Date / Narration / Tran ID / Withdrawals / Deposits / Balance.',
    ),
    LayoutProfile(
        key='moniepoint_statement',
        label='Moniepoint business statement layout',
        bank_name='Moniepoint',
        sample_files=('Moniepoint-Document-2026-05-12T10-42_260512_221856.pdf',),
        required_terms=(
            'ACCOUNT STATEMENT',
            'ACCOUNT SUMMARY',
            'BUSINESS NAME',
            'ACCOUNT NUMBER',
            'TOTAL DEBITS',
            'TOTAL CREDITS',
            'DATE NARRATION REFERENCE DEBIT CREDIT BALANCE',
        ),
        optional_terms=('OPENING BALANCE', 'CLOSING BALANCE', 'CURRENCY NGN'),
        notes='Moniepoint export with timestamped Date, Narration, Reference, Debit, Credit, and Balance columns.',
    ),
    LayoutProfile(
        key='opay_statement',
        label='OPay wallet and savings statement layout',
        bank_name='OPay',
        sample_files=('IFEANYI DOUGLAS AGORUA_8138758064_20260505032135 (1).pdf',),
        required_terms=(
            'ACCOUNT STATEMENT',
            'WALLET ACCOUNT PERIOD',
            'TRANS. TIME',
            'VALUE DATE',
            'BALANCE AFTER',
            'CHANNEL',
            'TRANSACTION REFERENCE',
        ),
        optional_terms=('SAVINGS ACCOUNT PERIOD', 'OWEALTH', 'OPAY', 'DEBIT COUNT', 'CREDIT COUNT'),
        notes='OPay export with a Wallet Account section followed by a Savings Account / OWealth section in the same PDF.',
    ),
    LayoutProfile(
        key='op_transaction_history',
        label='OPay transaction history layout',
        bank_name='OPay',
        sample_files=('OpTransactionHistoryUX504-03-2026.pdf',),
        required_terms=(
            'ACCOUNT STATEMENT',
            'ACCOUNT SUMMARY STATEMENT PERIOD',
            'ACCOUNT NUMBER',
            'WITHDRAWAL',
            'DEPOSIT',
            'CLOSING BALANCE',
            'YOUR TRANSACTIONS',
            'TRANSACTION VALUE DATE',
            'TRANSACTION REMARKS',
            'BALANCE',
        ),
        optional_terms=('CHEQUE NUMBER', 'ACCOUNT CURRENCY NGN'),
        notes='OPay account-summary export with Transaction Date / Value Date / Cheque Number / Transaction Remarks / Withdrawal / Deposit / Balance.',
    ),
    LayoutProfile(
        key='customer_account_statement_layout',
        label='Customer account statement layout',
        bank_name='Customer Account Statement',
        sample_files=('LOFTYINC ALLIED PARTNERS LIMITED-1775829475539.pdf',),
        required_terms=('CUSTOMER ACCOUNT STATEMENT', 'TOTAL DEBIT COUNT', 'TOTAL CREDIT COUNT', 'DATE', 'REFERENCE', 'NARRATION', 'BALANCE'),
        optional_terms=('PRINT DATE', 'ACCOUNT NUMBER', 'THIS IS A COMPUTER GENERATED STATEMENT', 'NIP COMMISSION'),
        notes='Pdfmake-generated statement with DATE / REFERENCE / NARRATION / DEBIT / CREDIT / BALANCE and multiline date/reference continuations.',
    ),
    LayoutProfile(
        key='summary_details_unknown',
        label='Summary-details corporate layout',
        bank_name='Unknown bank',
        sample_files=('0806772213_2023-01-01_2024-01-01_transaction.pdf',),
        required_terms=('ACCOUNT STATEMENT SUMMARY DETAILS', 'TOTAL WITHDRAWALS', 'TOTAL LODGEMENTS', 'DATE', 'REFERENCE', 'VALUE DATE'),
        optional_terms=('PRIVATE & CONFIDENTIAL', 'CURRENT ACC. - CORPORATE', 'CLEARED BALANCE', 'UNCLEARED BALANCE'),
        notes='Text-based corporate statement with withdrawals/lodgements columns; bank brand not obvious yet.',
    ),
    LayoutProfile(
        key='txn_date_remarks_layout',
        label='Providus TXN DATE / REMARKS layout',
        bank_name='Providus',
        sample_files=('Statement  2025.pdf',),
        required_terms=('STATEMENT OF ACCOUNT', 'TXN DATE', 'VAL DATE', 'REMARKS', 'DEBIT', 'CREDIT', 'BALANCE'),
        optional_terms=('OUTWARD TRANSFER', 'POINT OF SALE PURCHASE TRANSACTION', 'COMMISSION', 'VAT'),
        notes='Providus-style retail statement with multiline remarks and strong debit/credit normalization.',
    ),
)


def extract_signature_text(pdf_path: Path, max_pages: int = 2) -> str:
    with open_pdf(pdf_path) as pdf:
        text_parts = []
        for page in pdf.pages[:max_pages]:
            text_parts.append(page.extract_text() or '')
    return "\n".join(text_parts).upper()


def detect_layout(pdf_path: Path) -> LayoutProfile | None:
    text = extract_signature_text(pdf_path)
    ranked = sorted(
        ((profile.score(text), profile) for profile in LAYOUT_PROFILES),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_profile = ranked[0]
    return best_profile if best_score >= 0 else None
