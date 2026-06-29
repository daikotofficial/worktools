from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from statement_analyzer.models import StatementMetadata, Transaction


class StatementParser(ABC):
    bank_name: str = "unknown"
    last_metadata: StatementMetadata | None = None

    @abstractmethod
    def can_parse(self, pdf_path: Path) -> bool:
        """Return True if this parser can handle the provided statement."""

    @abstractmethod
    def parse(self, pdf_path: Path) -> list[Transaction]:
        """Parse the PDF and return normalized transactions."""
