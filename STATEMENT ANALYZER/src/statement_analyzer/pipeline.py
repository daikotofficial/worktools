from __future__ import annotations

from pathlib import Path

from statement_analyzer.classifiers.rules import RuleBasedClassifier
from statement_analyzer.models import StatementAnalysis, TransactionDirection
from statement_analyzer.parsers.registry import ParserRegistry


class StatementPipeline:
    def __init__(self, parser_registry: ParserRegistry, classifier: RuleBasedClassifier | None = None) -> None:
        self.parser_registry = parser_registry
        self.classifier = classifier or RuleBasedClassifier()
        self.last_parser = None

    def run(
        self,
        pdf_path: Path,
        *,
        allow_low_confidence_adaptive: bool = False,
        adaptive_column_overrides: dict[int, str] | None = None,
        adaptive_save_template: bool = True,
        adaptive_template_name: str | None = None,
        adaptive_rename_existing_template: bool = False,
    ) -> StatementAnalysis:
        parser = self.parser_registry.detect(pdf_path)
        self.last_parser = parser
        parse_with_options = getattr(parser, "parse_with_options", None)
        if callable(parse_with_options):
            transactions = parse_with_options(
                pdf_path,
                allow_low_confidence=allow_low_confidence_adaptive,
                column_overrides=adaptive_column_overrides,
                save_template=adaptive_save_template,
                preferred_template_name=adaptive_template_name,
                rename_existing_template=adaptive_rename_existing_template,
            )
        else:
            transactions = parser.parse(pdf_path)
        metadata = getattr(parser, 'last_metadata', None)

        classified_transactions = []
        inflows = []
        outflows = []

        for transaction in transactions:
            classified = self.classifier.classify(transaction, metadata=metadata)
            classified_transactions.append(classified)
            if transaction.direction == TransactionDirection.INFLOW:
                inflows.append(classified)
            elif transaction.direction == TransactionDirection.OUTFLOW:
                outflows.append(classified)

        return StatementAnalysis(
            all_transactions=transactions,
            classified_transactions=classified_transactions,
            inflows=inflows,
            outflows=outflows,
            parser_name=parser.bank_name,
            metadata=metadata,
        )
