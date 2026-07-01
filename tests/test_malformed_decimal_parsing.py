from __future__ import annotations

import unittest
from decimal import Decimal

from statement_analyzer.parsers import (
    fcmb,
    firstbank,
    globus,
    gtbank,
    jaiz,
    lotus,
    opay,
    providus,
    summary_details,
    taj,
    uba,
    zenith,
)


class MalformedDecimalParsingTests(unittest.TestCase):
    def test_bank_parsers_ignore_malformed_amounts_without_crashing(self) -> None:
        parsers = (
            fcmb.parse_decimal,
            firstbank.parse_decimal,
            globus.parse_decimal,
            gtbank.parse_decimal,
            jaiz.parse_decimal,
            lotus.parse_decimal,
            opay.parse_decimal,
            providus.parse_decimal,
            summary_details.parse_decimal,
            taj.parse_decimal,
            uba.parse_decimal,
            zenith.parse_decimal,
        )

        for parse_decimal in parsers:
            with self.subTest(parser=parse_decimal.__module__):
                parsed = parse_decimal("12-34.56")
                self.assertTrue(parsed is None or isinstance(parsed, Decimal))


if __name__ == "__main__":
    unittest.main()
