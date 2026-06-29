from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

from statement_analyzer.exporters.excel import ExcelExporter
from statement_analyzer.models import ClassifiedTransaction, StatementAnalysis, StatementMetadata, Transaction


NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class ExcelExportTests(unittest.TestCase):
    def test_export_uses_clean_final_workbook_layout(self) -> None:
        inflow = Transaction(
            transaction_date=date(2025, 1, 2),
            description="Invoice payment from OMEGA LOGISTICS LTD",
            credit=Decimal("250000.00"),
            debit=Decimal("0"),
            balance=Decimal("500000.00"),
            reference="REF-001",
        )
        outflow = Transaction(
            transaction_date=date(2025, 1, 3),
            description="CIP/CR/MOB/RASHIDAT OJONUGWA YAKUBU/OPAY /000015251023115919653147211996/Wedding/ZMO5644679640",
            credit=Decimal("0"),
            debit=Decimal("200000.00"),
            balance=Decimal("300000.00"),
            reference="REF-002",
        )
        analysis = StatementAnalysis(
            all_transactions=[inflow, outflow],
            classified_transactions=[
                ClassifiedTransaction(transaction=inflow, classification="Sales", confidence=0.9, rule_name="sales"),
                ClassifiedTransaction(
                    transaction=outflow,
                    classification="Individual Transfer",
                    confidence=0.55,
                    rule_name="transfer",
                ),
            ],
            inflows=[ClassifiedTransaction(transaction=inflow, classification="Sales", confidence=0.9, rule_name="sales")],
            outflows=[
                ClassifiedTransaction(
                    transaction=outflow,
                    classification="Individual Transfer",
                    confidence=0.55,
                    rule_name="transfer",
                )
            ],
            parser_name="test-parser",
            metadata=StatementMetadata(
                account_name="ACTION ENERGY LTD",
                account_number="0000000000",
                currency="NGN",
                opening_balance=Decimal("250000.00"),
                total_credit=Decimal("250000.00"),
                total_debit=Decimal("200000.00"),
                closing_balance=Decimal("300000.00"),
                period_start=date(2025, 1, 1),
                period_end=date(2025, 1, 31),
            ),
        )

        exporter = ExcelExporter()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "analysis.xlsx"
            exporter.export(analysis, output_path)

            with ZipFile(output_path) as archive:
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                sheets = workbook.find("main:sheets", NS)
                self.assertIsNotNone(sheets)
                sheet_info = [sheet.attrib["name"] for sheet in sheets]
                self.assertEqual(
                    sheet_info,
                    ["Main", "Inflows", "Outflows", "Analysis"],
                )

                shared_strings = load_shared_strings(archive)
                inflow_headers = first_row_texts(archive, "xl/worksheets/sheet2.xml", shared_strings)
                outflow_headers = first_row_texts(archive, "xl/worksheets/sheet3.xml", shared_strings)

        self.assertEqual(
            inflow_headers[:5],
            ["DATE", "DESCRIPTION", "DEBIT", "CREDIT", "CLASSIFICATION"],
        )
        self.assertEqual(
            outflow_headers[:6],
            ["DATE", "DESCRIPTION", "DEBIT", "CONFIRM", "DIFF", "CLASSIFICATION"],
        )


def load_shared_strings(archive: ZipFile) -> list[str]:
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("main:si", NS):
        text = "".join(node.text or "" for node in item.findall(".//main:t", NS))
        values.append(text)
    return values


def first_row_texts(archive: ZipFile, worksheet_path: str, shared_strings: list[str]) -> list[str]:
    root = ET.fromstring(archive.read(worksheet_path))
    row = root.find("main:sheetData/main:row", NS)
    if row is None:
        return []

    values: list[str] = []
    for cell in row.findall("main:c", NS):
        value = cell.find("main:v", NS)
        if value is None:
            continue
        if cell.attrib.get("t") == "s":
            values.append(shared_strings[int(value.text)])
        else:
            values.append(value.text or "")
    return values


if __name__ == "__main__":
    unittest.main()
