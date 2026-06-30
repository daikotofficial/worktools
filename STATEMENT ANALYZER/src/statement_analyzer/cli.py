from __future__ import annotations

import argparse
from pathlib import Path

from statement_analyzer.exporters.excel import ExcelExporter
from statement_analyzer.parsers.clear_junction import ClearJunctionStatementParser
from statement_analyzer.parsers.customer_account_statement import CustomerAccountStatementParser
from statement_analyzer.parsers.fcmb import FCMBStatementParser
from statement_analyzer.parsers.fidelity import FidelityStatementParser
from statement_analyzer.parsers.firstbank import FirstBankStatementParser
from statement_analyzer.parsers.generic import GenericStatementParser
from statement_analyzer.parsers.globus import GlobusStatementParser
from statement_analyzer.parsers.gtbank import GTBankStatementParser
from statement_analyzer.parsers.jaiz import JaizStatementParser
from statement_analyzer.parsers.lotus import LotusStatementParser
from statement_analyzer.parsers.moniepoint import MoniepointStatementParser
from statement_analyzer.parsers.posting_value_ledger import PostingValueLedgerStatementParser
from statement_analyzer.parsers.providus import ProvidusStatementParser
from statement_analyzer.parsers.registry import ParserRegistry
from statement_analyzer.parsers.standard_chartered import StandardCharteredStatementParser
from statement_analyzer.parsers.summary_details import SummaryDetailsStatementParser
from statement_analyzer.parsers.taj import TajStatementParser
from statement_analyzer.parsers.uba import UBAStatementParser
from statement_analyzer.parsers.pdf_utils import clear_pdf_password, set_pdf_password
from statement_analyzer.parsers.wema_treasure import WemaTreasureStatementParser
from statement_analyzer.parsers.zenith import ZenithStyleParser
from statement_analyzer.pipeline import StatementPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a bank statement PDF.")
    parser.add_argument("pdf_path", type=Path, help="Path to the statement PDF")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Path to the output Excel workbook",
    )
    parser.add_argument(
        "--password",
        type=str,
        help="Password for a protected PDF statement",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_path = args.output or args.pdf_path.with_name(f"{args.pdf_path.stem}_ANALYZED.xlsx")

    registry = ParserRegistry(
        [
            ZenithStyleParser(),
            UBAStatementParser(),
            FCMBStatementParser(),
            ProvidusStatementParser(),
            FirstBankStatementParser(),
            FidelityStatementParser(),
            GTBankStatementParser(),
            GlobusStatementParser(),
            LotusStatementParser(),
            StandardCharteredStatementParser(),
            TajStatementParser(),
            JaizStatementParser(),
            CustomerAccountStatementParser(),
            SummaryDetailsStatementParser(),
            PostingValueLedgerStatementParser(),
            ClearJunctionStatementParser(),
            WemaTreasureStatementParser(),
            MoniepointStatementParser(),
            GenericStatementParser(),
        ]
    )
    pipeline = StatementPipeline(parser_registry=registry)
    set_pdf_password(args.pdf_path, args.password)
    try:
        analysis = pipeline.run(args.pdf_path)
    finally:
        clear_pdf_password(args.pdf_path)
    ExcelExporter().export(analysis, output_path)
    print(f"Analysis complete: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
