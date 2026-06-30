from __future__ import annotations

from pathlib import Path

from statement_analyzer.parsers.base import StatementParser


class ParserRegistry:
    def __init__(self, parsers: list[StatementParser] | None = None) -> None:
        self.parsers = parsers or []

    def register(self, parser: StatementParser) -> None:
        self.parsers.append(parser)

    def detect(self, pdf_path: Path) -> StatementParser:
        for parser in self.parsers:
            if parser.can_parse(pdf_path):
                return parser
        raise ValueError(f"No parser matched statement: {pdf_path}")
