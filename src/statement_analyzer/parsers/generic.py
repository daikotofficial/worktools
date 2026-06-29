from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_analyzer.layouts import detect_layout
from statement_analyzer.models import StatementMetadata, Transaction
from statement_analyzer.parsers.base import StatementParser
from statement_analyzer.parsers.pdf_utils import find_regex_in_pages, open_pdf

HEADER_TERMS: dict[str, tuple[str, ...]] = {
    "date": ("DATE", "TRANS", "TXN", "POST", "BOOK", "VALUE"),
    "description": ("DESCRIPTION", "NARRATION", "REMARKS", "DETAILS", "PARTICULARS", "MEMO"),
    "debit": ("DEBIT", "DEBITS", "WITHDRAWAL", "WITHDRAWALS", "DR"),
    "credit": ("CREDIT", "CREDITS", "DEPOSIT", "DEPOSITS", "LODGEMENT", "LODGEMENTS", "CR"),
    "balance": ("BALANCE", "BAL"),
    "reference": ("REFERENCE", "REF", "REF.", "ID"),
}
HEADER_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("TRANS", "DATE"), "date"),
    (("TRANSACTION", "DATE"), "date"),
    (("VALUE", "DATE"), "date"),
    (("MONEY", "IN"), "credit"),
    (("MONEY", "OUT"), "debit"),
    (("PAID", "IN"), "credit"),
    (("PAID", "OUT"), "debit"),
)
ADAPTIVE_COLUMN_ROLES = ("date", "description", "debit", "credit", "amount", "balance", "reference", "ignore")

DATE_PATTERNS = (
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%d %b %Y",
    "%d %b %y",
    "%Y-%m-%d",
)

CONFIDENCE_THRESHOLD = 0.62
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ADAPTIVE_TEMPLATES_FILE = PROJECT_ROOT / "config" / "adaptive_layouts.json"
METADATA_MARKERS = (
    "ACCOUNT NAME",
    "CUSTOMER NAME",
    "ACCOUNT NUMBER",
    "ACCOUNT NO",
    "ACC. NO.",
    "CURRENCY",
    "OPENING BALANCE",
    "TOTAL DEBIT",
    "TOTAL CREDIT",
    "TOTAL WITHDRAWALS",
    "TOTAL DEPOSITS",
    "TOTAL LODGEMENTS",
    "CLOSING BALANCE",
    "AVAILABLE BALANCE",
    "USABLE BALANCE",
    "STATEMENT OF ACCOUNT",
    "ACCOUNT STATEMENT",
    "VALUE DATE",
)


@dataclass(slots=True)
class AdaptiveWordRow:
    top: float
    words: list[dict]

    @property
    def text(self) -> str:
        return " ".join(word["text"] for word in sorted(self.words, key=lambda item: item["x0"]))


@dataclass(slots=True)
class HeaderColumn:
    semantic: str
    x0: float
    x1: float
    label: str

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(slots=True)
class HeaderPlan:
    page_number: int
    top: float
    page_width: float
    columns: list[HeaderColumn]
    score: float

    @property
    def semantics(self) -> set[str]:
        return {column.semantic for column in self.columns}


@dataclass(slots=True)
class ParseAssessment:
    score: float
    reasons: list[str]


@dataclass(slots=True)
class AdaptivePreviewRow:
    transaction_date: str | None
    description: str
    debit: str | None
    credit: str | None
    balance: str | None


@dataclass(slots=True)
class AdaptiveDetectedColumn:
    index: int
    label: str
    semantic: str


@dataclass(slots=True)
class InferredRowPattern:
    top: float
    date_spans: tuple[tuple[float, float], ...]
    amount_spans: tuple[tuple[float, float], ...]
    balance_span: tuple[float, float]


@dataclass(slots=True)
class DetachedAmount:
    page_number: int
    top: float
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    amount: Decimal | None = None


@dataclass(slots=True)
class AdaptiveTemplate:
    key: str
    name: str
    required_terms: tuple[str, ...]
    optional_terms: tuple[str, ...] = ()
    column_order: tuple[str, ...] = ()
    header_labels: tuple[str, ...] = ()
    column_ratios: tuple[float, ...] = ()

    def score(self, signature_text: str) -> int:
        upper = normalized_text(signature_text)
        if not all(term in upper for term in self.required_terms):
            return -1
        return len(self.required_terms) * 10 + sum(1 for term in self.optional_terms if term in upper)


@dataclass(slots=True)
class AdaptiveReviewRequired(Exception):
    confidence: float
    reasons: list[str]
    detected_columns: tuple[AdaptiveDetectedColumn, ...]
    preview_rows: list[AdaptivePreviewRow]
    template_name: str

    def __str__(self) -> str:
        detail = "; ".join(self.reasons) if self.reasons else "confidence checks were too weak"
        return (
            "The adaptive parser found a likely transaction table, but confidence was too low to continue automatically "
            f"({self.confidence:.2f}). {detail}."
        )


class GenericStatementParser(StatementParser):
    bank_name = "adaptive-unknown"

    def __init__(self, templates_file: Path | None = None) -> None:
        self.templates_file = templates_file or ADAPTIVE_TEMPLATES_FILE
        self.last_template_key: str | None = None
        self.last_signature_text: str | None = None
        self.last_plan: HeaderPlan | None = None
        self.last_assessment: ParseAssessment | None = None
        self.last_matched_template: AdaptiveTemplate | None = None

    def can_parse(self, pdf_path: Path) -> bool:
        return pdf_path.suffix.lower() == ".pdf"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        return self.parse_with_options(pdf_path)

    def parse_with_options(
        self,
        pdf_path: Path,
        *,
        allow_low_confidence: bool = False,
        column_overrides: dict[int, str] | None = None,
        save_template: bool = True,
        preferred_template_name: str | None = None,
        rename_existing_template: bool = False,
    ) -> list[Transaction]:
        self.bank_name = "adaptive-unknown"
        self.last_signature_text = None
        self.last_plan = None
        self.last_assessment = None
        self.last_matched_template = None
        self.last_template_key = None

        profile = detect_layout(pdf_path)
        with open_pdf(pdf_path) as pdf:
            sample_words = sum(len(page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)) for page in pdf.pages[:2])
            sample_text_lines = extract_text_lines(pdf.pages[:2])
            if sample_words == 0 and not sample_text_lines:
                raise NotImplementedError(
                    "This PDF appears to be scanned or image-only. The adaptive parser currently needs text-based statements; OCR support is still pending."
                )

            signature_text = extract_signature_text(pdf.pages)
            templates = load_adaptive_templates(self.templates_file)
            matched_template = match_adaptive_template(signature_text, templates)
            self.last_signature_text = signature_text
            self.last_metadata = extract_generic_metadata(pdf.pages)
            plan = infer_header_plan(
                pdf.pages[: min(3, len(pdf.pages))],
                template=matched_template,
                column_overrides=column_overrides,
            )
            if plan is None and len(pdf.pages) > 3:
                plan = infer_header_plan(
                    pdf.pages[: min(8, len(pdf.pages))],
                    template=matched_template,
                    column_overrides=column_overrides,
                )
            if plan is None and matched_template is not None:
                plan = build_template_ratio_plan(
                    pdf.pages[: min(3, len(pdf.pages))],
                    matched_template,
                    column_overrides=column_overrides,
                )

            transactions: list[Transaction] = []
            used_line_fallback = False
            if plan is not None:
                active_template = matched_template if template_matches_plan(matched_template, plan) else None
                template_name = active_template.name if active_template else self.bank_name
                self.bank_name = template_name
                self.last_matched_template = active_template
                self.last_template_key = active_template.key if active_template else None
                transactions = extract_transactions_from_pages(
                    pdf.pages,
                    plan,
                    parser_name=self.bank_name,
                    initial_balance=self.last_metadata.opening_balance if self.last_metadata else None,
                )

            if (plan is None or len(transactions) < 3) and sample_text_lines:
                line_plan = build_line_fallback_plan(float(pdf.pages[0].width if pdf.pages else 700))
                line_transactions = extract_transactions_from_text_lines(
                    pdf.pages,
                    parser_name=self.bank_name,
                    initial_balance=self.last_metadata.opening_balance if self.last_metadata else None,
                )
                if len(line_transactions) >= len(transactions):
                    transactions = line_transactions
                    plan = line_plan
                    used_line_fallback = True

            if plan is None or not transactions:
                if profile is not None:
                    raise NotImplementedError(
                        f"A likely layout was detected ({profile.label}, {profile.bank_name}), but the adaptive parser could not safely infer the transaction table yet."
                    )
                raise NotImplementedError(
                    "No reliable transaction table was found in this PDF. This statement likely needs a dedicated parser or guided review."
                )

            active_template = matched_template if template_matches_plan(matched_template, plan) else None
            template_name = active_template.name if active_template else ("adaptive-line-fallback" if used_line_fallback else self.bank_name)
            self.bank_name = template_name
            self.last_matched_template = active_template
            self.last_template_key = active_template.key if active_template else None
            assessment = assess_adaptive_parse(
                transactions,
                self.last_metadata,
                plan,
                template=active_template,
            )
            self.last_plan = plan
            self.last_assessment = assessment
            if assessment.score < CONFIDENCE_THRESHOLD and not allow_low_confidence:
                raise AdaptiveReviewRequired(
                    confidence=assessment.score,
                    reasons=assessment.reasons,
                    detected_columns=tuple(
                        AdaptiveDetectedColumn(index=index, label=column.label, semantic=column.semantic)
                        for index, column in enumerate(sorted(plan.columns, key=lambda item: item.center))
                    ),
                    preview_rows=build_preview_rows(transactions),
                    template_name=template_name,
                )

            if save_template:
                learned_template = self.save_last_template(
                    preferred_name=preferred_template_name,
                    existing_templates=templates,
                    rename_existing=rename_existing_template,
                )
                if learned_template is not None:
                    self.bank_name = learned_template.name
                    self.last_template_key = learned_template.key

        return transactions

    def save_last_template(
        self,
        *,
        preferred_name: str | None = None,
        existing_templates: list[AdaptiveTemplate] | None = None,
        rename_existing: bool = False,
    ) -> AdaptiveTemplate | None:
        if self.last_signature_text is None or self.last_plan is None:
            return None

        template_name = preferred_name or (self.last_matched_template.name if self.last_matched_template else None)
        learned_template = save_adaptive_template(
            self.last_signature_text,
            self.last_plan,
            self.templates_file,
            existing_templates=existing_templates,
            preferred_name=template_name,
            rename_existing=rename_existing,
        )
        if learned_template is not None:
            self.bank_name = learned_template.name
            self.last_template_key = learned_template.key
        return learned_template


def infer_header_plan(
    pages: list[pdfplumber.page.Page],
    *,
    template: AdaptiveTemplate | None = None,
    column_overrides: dict[int, str] | None = None,
) -> HeaderPlan | None:
    best_plan: HeaderPlan | None = None
    for page_number, page in enumerate(pages, start=1):
        rows = group_words_into_rows(page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False))
        for row in rows:
            columns = infer_columns_from_row(row.words)
            if not columns:
                continue
            columns = apply_column_overrides(columns, column_overrides)
            if not columns:
                continue
            score = score_header_columns(columns, template=template)
            if best_plan is None or score > best_plan.score:
                best_plan = HeaderPlan(
                    page_number=page_number,
                    top=row.top,
                    page_width=float(page.width),
                    columns=columns,
                    score=score,
                )
    if not best_plan:
        return infer_row_pattern_plan(pages, template=template, column_overrides=column_overrides)
    if not plan_has_required_semantics(best_plan):
        fallback_plan = infer_row_pattern_plan(pages, template=template, column_overrides=column_overrides)
        if fallback_plan is not None and fallback_plan.score >= best_plan.score:
            return fallback_plan
        return None
    return best_plan


def build_template_ratio_plan(
    pages: list[pdfplumber.page.Page],
    template: AdaptiveTemplate,
    *,
    column_overrides: dict[int, str] | None = None,
) -> HeaderPlan | None:
    if not pages or not template.column_ratios or not template.column_order:
        return None

    page_width = float(pages[0].width)
    centers = [ratio * page_width for ratio in template.column_ratios]
    labels = template.header_labels or tuple(role.title() for role in template.column_order)
    columns: list[HeaderColumn] = []

    for index, semantic in enumerate(template.column_order):
        center = centers[index]
        left_boundary = 0.0 if index == 0 else (centers[index - 1] + center) / 2
        right_boundary = page_width if index == len(centers) - 1 else (center + centers[index + 1]) / 2
        columns.append(
            HeaderColumn(
                semantic=semantic,
                x0=max(0.0, left_boundary),
                x1=min(page_width, right_boundary),
                label=labels[index] if index < len(labels) else semantic.title(),
            )
        )

    columns = apply_column_overrides(columns, column_overrides)
    candidate = HeaderPlan(
        page_number=1,
        top=0.0,
        page_width=page_width,
        columns=columns,
        score=score_header_columns(columns, template=template) + 0.6,
    )
    return candidate if plan_has_required_semantics(candidate) else None


def group_words_into_rows(words: list[dict]) -> list[AdaptiveWordRow]:
    grouped: list[list[dict]] = []
    current_group: list[dict] = []
    current_top: float | None = None

    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        top = float(word["top"])
        if current_top is None or abs(top - current_top) <= 2.5:
            current_group.append(word)
            current_top = top if current_top is None else (current_top + top) / 2
        else:
            grouped.append(current_group)
            current_group = [word]
            current_top = top

    if current_group:
        grouped.append(current_group)

    return [
        AdaptiveWordRow(
            top=min(float(word["top"]) for word in row_words),
            words=sorted(row_words, key=lambda item: item["x0"]),
        )
        for row_words in grouped
    ]


def infer_columns_from_row(words: list[dict]) -> list[HeaderColumn]:
    candidates: list[HeaderColumn] = []
    ordered_words = sorted(words, key=lambda item: item["x0"])
    consumed_indexes: set[int] = set()

    for index, word in enumerate(ordered_words):
        if index in consumed_indexes:
            continue
        for phrase, semantic in HEADER_PHRASES:
            phrase_words = ordered_words[index : index + len(phrase)]
            if len(phrase_words) != len(phrase):
                continue
            normalized_words = tuple(normalize_header_word(item["text"]) for item in phrase_words)
            if normalized_words != phrase:
                continue
            candidates.append(
                HeaderColumn(
                    semantic=semantic,
                    x0=float(phrase_words[0]["x0"]),
                    x1=float(phrase_words[-1]["x1"]),
                    label=" ".join(item["text"] for item in phrase_words),
                )
            )
            consumed_indexes.update(range(index, index + len(phrase)))
            break

    for index, word in enumerate(ordered_words):
        if index in consumed_indexes:
            continue
        semantic = classify_header_token(word["text"])
        if not semantic:
            continue
        candidates.append(
            HeaderColumn(
                semantic=semantic,
                x0=float(word["x0"]),
                x1=float(word["x1"]),
                label=word["text"],
            )
        )
    if len(candidates) < 3:
        return []

    merged: list[HeaderColumn] = []
    for candidate in sorted(candidates, key=lambda item: item.x0):
        if merged and merged[-1].semantic == candidate.semantic and candidate.x0 - merged[-1].x1 <= 40:
            previous = merged[-1]
            merged[-1] = HeaderColumn(
                semantic=previous.semantic,
                x0=previous.x0,
                x1=max(previous.x1, candidate.x1),
                label=f"{previous.label} {candidate.label}",
            )
            continue
        merged.append(candidate)
    return merged


def infer_row_pattern_plan(
    pages: list[pdfplumber.page.Page],
    *,
    template: AdaptiveTemplate | None = None,
    column_overrides: dict[int, str] | None = None,
) -> HeaderPlan | None:
    best_plan: HeaderPlan | None = None
    for page_number, page in enumerate(pages, start=1):
        rows = group_words_into_rows(page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False))
        patterns = [pattern for row in rows if (pattern := infer_transaction_row_pattern(row.words))]
        if len(patterns) < 3:
            continue

        columns = build_columns_from_row_patterns(patterns, float(page.width))
        columns = apply_column_overrides(columns, column_overrides)
        if not plan_has_required_semantics(HeaderPlan(page_number, 0, float(page.width), columns, 0)):
            continue
        score = score_header_columns(columns, template=template) + min(len(patterns), 12) * 0.35
        candidate = HeaderPlan(
            page_number=page_number,
            top=min(pattern.top for pattern in patterns),
            page_width=float(page.width),
            columns=columns,
            score=score,
        )
        if best_plan is None or candidate.score > best_plan.score:
            best_plan = candidate
    return best_plan


def infer_transaction_row_pattern(words: list[dict]) -> InferredRowPattern | None:
    ordered = sorted(words, key=lambda item: item["x0"])
    date_spans = find_date_spans(ordered)
    if not date_spans:
        return None

    numeric_spans = [
        (float(word["x0"]), float(word["x1"]))
        for word in ordered
        if parse_decimal(word["text"]) is not None
    ]
    numeric_spans = [span for span in numeric_spans if span[0] > date_spans[-1][1]]
    if len(numeric_spans) < 2:
        return None

    balance_span = numeric_spans[-1]
    amount_spans = tuple(numeric_spans[:-1][-2:])
    if not amount_spans:
        return None

    return InferredRowPattern(
        top=min(float(word["top"]) for word in ordered),
        date_spans=date_spans,
        amount_spans=amount_spans,
        balance_span=balance_span,
    )


def find_date_span(words: list[dict]) -> tuple[float, float] | None:
    spans = find_date_spans(words)
    return spans[0] if spans else None


def find_date_spans(words: list[dict]) -> tuple[tuple[float, float], ...]:
    spans: list[tuple[float, float]] = []
    index = 0
    while index < len(words):
        matched = False
        for window in (3, 2, 1):
            if index + window > len(words):
                continue
            candidate = " ".join(words[index + offset]["text"] for offset in range(window))
            if parse_date(candidate):
                spans.append((float(words[index]["x0"]), float(words[index + window - 1]["x1"])))
                index += window
                matched = True
                break
        if not matched:
            index += 1
        if len(spans) >= 2:
            break
    return tuple(spans)


def build_columns_from_row_patterns(patterns: list[InferredRowPattern], page_width: float) -> list[HeaderColumn]:
    primary_date_spans = [pattern.date_spans[0] for pattern in patterns if pattern.date_spans]
    secondary_date_spans = [pattern.date_spans[1] for pattern in patterns if len(pattern.date_spans) > 1]
    date_x0 = min(span[0] for span in primary_date_spans)
    date_x1 = median_span_end(primary_date_spans, index=1)
    balance_x0 = median_span_start([pattern.balance_span for pattern in patterns], index=0)
    balance_x1 = max(pattern.balance_span[1] for pattern in patterns)

    amount_centers = [((span[0] + span[1]) / 2) for pattern in patterns for span in pattern.amount_spans]
    amount_clusters = cluster_numeric_centers(amount_centers)

    last_date_x1 = median_span_end(secondary_date_spans, index=1) if secondary_date_spans else date_x1
    description_x0 = min(last_date_x1 + 8, page_width * 0.45)
    first_numeric_start = min(cluster["x0"] for cluster in amount_clusters) if amount_clusters else balance_x0
    description_x1 = max(description_x0 + 80, first_numeric_start - 8)

    columns = [HeaderColumn("date", date_x0, date_x1, "Date")]
    if secondary_date_spans:
        columns.append(
            HeaderColumn(
                "date",
                median_span_start(secondary_date_spans, index=0),
                median_span_end(secondary_date_spans, index=1),
                "Value Date",
            )
        )
    columns.append(HeaderColumn("description", description_x0, description_x1, "Description"))

    if len(amount_clusters) >= 2:
        columns.append(HeaderColumn("debit", amount_clusters[0]["x0"], amount_clusters[0]["x1"], "Debit"))
        columns.append(HeaderColumn("credit", amount_clusters[1]["x0"], amount_clusters[1]["x1"], "Credit"))
    else:
        cluster = amount_clusters[0]
        columns.append(HeaderColumn("amount", cluster["x0"], cluster["x1"], "Amount"))

    columns.append(HeaderColumn("balance", balance_x0, balance_x1, "Balance"))
    return sorted(columns, key=lambda item: item.center)


def cluster_numeric_centers(centers: list[float], tolerance: float = 48) -> list[dict[str, float]]:
    if not centers:
        return []

    clusters: list[list[float]] = []
    for center in sorted(centers):
        if not clusters or abs(center - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
            clusters.append([center])
        else:
            clusters[-1].append(center)

    results: list[dict[str, float]] = []
    for cluster in clusters[-2:]:
        midpoint = sum(cluster) / len(cluster)
        results.append({"x0": midpoint - 28, "x1": midpoint + 28})
    return results


def median_span_end(spans: list[tuple[float, float]], *, index: int) -> float:
    values = sorted(span[index] for span in spans)
    return values[len(values) // 2]


def median_span_start(spans: list[tuple[float, float]], *, index: int) -> float:
    values = sorted(span[index] for span in spans)
    return values[len(values) // 2]


def apply_column_overrides(
    columns: list[HeaderColumn],
    overrides: dict[int, str] | None,
) -> list[HeaderColumn]:
    if not overrides:
        return columns

    adjusted: list[HeaderColumn] = []
    for index, column in enumerate(sorted(columns, key=lambda item: item.center)):
        override = normalize_column_role(overrides.get(index))
        if override == "ignore":
            continue
        if override:
            adjusted.append(
                HeaderColumn(
                    semantic=override,
                    x0=column.x0,
                    x1=column.x1,
                    label=column.label,
                )
            )
        else:
            adjusted.append(column)
    return adjusted


def score_header_columns(columns: list[HeaderColumn], *, template: AdaptiveTemplate | None = None) -> float:
    semantics = {column.semantic for column in columns}
    score = float(len(semantics))
    if {"date", "description"}.issubset(semantics):
        score += 3
    if "balance" in semantics:
        score += 2
    if {"debit", "credit", "amount"} & semantics:
        score += 2
    if "amount" in semantics:
        score += 0.4
    if template:
        template_order = list(template.column_order)
        actual_order = [column.semantic for column in columns]
        if template_order and actual_order[: len(template_order)] == template_order[: len(actual_order)]:
            score += 1.2
        if template.header_labels:
            template_labels = {label.upper() for label in template.header_labels}
            column_labels = {column.label.upper() for column in columns}
            score += 0.2 * len(template_labels & column_labels)
    return score


def classify_header_token(value: str) -> str | None:
    cleaned = normalize_header_word(value)
    if not cleaned or re.search(r"\d", cleaned):
        return None
    for semantic, terms in HEADER_TERMS.items():
        if cleaned in terms:
            return semantic
    return None


def normalize_header_word(value: str) -> str:
    return re.sub(r"[^A-Z.]", "", value.upper())


def extract_transactions_from_pages(
    pages: list[pdfplumber.page.Page],
    plan: HeaderPlan,
    *,
    parser_name: str,
    initial_balance: Decimal | None = None,
) -> list[Transaction]:
    transactions: list[Transaction] = []
    opening_balance_added = False
    previous_balance: Decimal | None = initial_balance
    pending_detached_amount: DetachedAmount | None = None

    for page_number, page in enumerate(pages, start=1):
        rows = group_words_into_rows(page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False))
        for row in rows:
            if is_header_row(row, plan):
                pending_detached_amount = None
                continue

            cells = split_row_by_plan(row, plan)
            date_value = first_parseable_date(cells, plan.columns)
            description = extract_description(cells, plan.columns)
            debit = first_decimal(cells, plan.columns, "debit") or Decimal("0")
            credit = first_decimal(cells, plan.columns, "credit") or Decimal("0")
            amount = first_decimal(cells, plan.columns, "amount")
            balance = first_decimal(cells, plan.columns, "balance")

            if amount is not None and debit == 0 and credit == 0:
                debit, credit = infer_amount_direction(amount, balance, previous_balance, description)

            if not date_value and is_opening_balance_row(description, balance) and not opening_balance_added:
                pending_detached_amount = None
                transactions.append(
                    Transaction(
                        transaction_date=None,
                        description="Opening Balance",
                        debit=Decimal("0"),
                        credit=Decimal("0"),
                        balance=balance or Decimal("0"),
                        raw_text=description,
                        source_page=page_number,
                        parser_name=parser_name,
                    )
                )
                opening_balance_added = True
                previous_balance = balance if balance is not None else previous_balance
                continue

            if date_value is None:
                detached_amount = capture_detached_amount(
                    page_number=page_number,
                    row=row,
                    plan=plan,
                    description=description,
                    debit=debit,
                    credit=credit,
                    amount=amount,
                    balance=balance,
                )
                if detached_amount is not None:
                    pending_detached_amount = detached_amount
                    continue

                pending_detached_amount = None
                if description and not (debit or credit):
                    append_continuation(transactions, description, page_number)
                continue

            pending_detached_amount, debit, credit = apply_detached_amount(
                pending_detached_amount,
                page_number=page_number,
                row_top=row.top,
                debit=debit,
                credit=credit,
                balance=balance,
                previous_balance=previous_balance,
                description=description,
            )

            if not description and not (debit or credit or balance):
                continue

            transactions.append(
                Transaction(
                    transaction_date=date_value,
                    description=description or "Unlabeled Transaction",
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    raw_text=description,
                    source_page=page_number,
                    parser_name=parser_name,
                )
            )
            previous_balance = balance if balance is not None else previous_balance

    return transactions


def capture_detached_amount(
    *,
    page_number: int,
    row: AdaptiveWordRow,
    plan: HeaderPlan,
    description: str,
    debit: Decimal,
    credit: Decimal,
    amount: Decimal | None,
    balance: Decimal | None,
) -> DetachedAmount | None:
    if row.top <= plan.top or description or balance is not None:
        return None

    debit_value = debit if debit > 0 else Decimal("0")
    credit_value = credit if credit > 0 else Decimal("0")
    amount_value = amount if amount is not None and amount > 0 else None
    if not (debit_value or credit_value or amount_value is not None):
        return None

    return DetachedAmount(
        page_number=page_number,
        top=row.top,
        debit=debit_value,
        credit=credit_value,
        amount=amount_value if not (debit_value or credit_value) else None,
    )


def apply_detached_amount(
    detached: DetachedAmount | None,
    *,
    page_number: int,
    row_top: float,
    debit: Decimal,
    credit: Decimal,
    balance: Decimal | None,
    previous_balance: Decimal | None,
    description: str,
) -> tuple[DetachedAmount | None, Decimal, Decimal]:
    if detached is None:
        return None, debit, credit
    if detached.page_number != page_number or not 0 < row_top - detached.top <= 18:
        return None, debit, credit
    if debit > 0 or credit > 0:
        return None, debit, credit
    if detached.debit > 0:
        return None, detached.debit, credit
    if detached.credit > 0:
        return None, debit, detached.credit
    if detached.amount is not None:
        inferred_debit, inferred_credit = infer_amount_direction(detached.amount, balance, previous_balance, description)
        return None, inferred_debit, inferred_credit
    return None, debit, credit


def build_line_fallback_plan(page_width: float) -> HeaderPlan:
    columns = [
        HeaderColumn("date", 0.0, page_width * 0.18, "Date"),
        HeaderColumn("description", page_width * 0.18, page_width * 0.72, "Description"),
        HeaderColumn("amount", page_width * 0.72, page_width * 0.86, "Amount"),
        HeaderColumn("balance", page_width * 0.86, page_width, "Balance"),
    ]
    return HeaderPlan(
        page_number=1,
        top=0.0,
        page_width=page_width,
        columns=columns,
        score=5.2,
    )


def extract_transactions_from_text_lines(
    pages: list[pdfplumber.page.Page],
    *,
    parser_name: str,
    initial_balance: Decimal | None = None,
) -> list[Transaction]:
    transactions: list[Transaction] = []
    previous_balance = initial_balance
    pending_line: str | None = None
    pending_page: int | None = None

    def flush_pending() -> None:
        nonlocal pending_line, pending_page, previous_balance
        if not pending_line or pending_page is None:
            pending_line = None
            pending_page = None
            return
        transaction = parse_transaction_from_text_line(
            pending_line,
            page_number=pending_page,
            parser_name=parser_name,
            previous_balance=previous_balance,
        )
        pending_line = None
        pending_page = None
        if transaction is None:
            return
        transactions.append(transaction)
        previous_balance = transaction.balance if transaction.balance is not None else previous_balance

    for page_number, page in enumerate(pages, start=1):
        for line in extract_text_lines([page]):
            cleaned = clean_text(line)
            if not cleaned:
                continue
            if looks_like_transaction_start(cleaned):
                flush_pending()
                pending_line = cleaned
                pending_page = page_number
                continue
            if pending_line and not looks_like_non_transaction_line(cleaned):
                pending_line = clean_text(f"{pending_line} {cleaned}")
        flush_pending()

    return transactions


def extract_text_lines(pages: list[pdfplumber.page.Page]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        text = page.extract_text() or ""
        lines.extend(line for line in text.splitlines() if clean_text(line))
    return lines


def looks_like_transaction_start(line: str) -> bool:
    parts = clean_text(line).split()
    if not parts:
        return False
    for first_window in (3, 2, 1):
        if len(parts) < first_window:
            continue
        first_candidate = " ".join(parts[:first_window])
        if not parse_date(first_candidate):
            continue
        remainder = parts[first_window:]
        if not remainder:
            return False
        for second_window in (3, 2, 1):
            if len(remainder) < second_window:
                continue
            second_candidate = " ".join(remainder[:second_window])
            if parse_date(second_candidate):
                remainder = remainder[second_window:]
                break
        money_matches = re.findall(r"-?[0-9,]+\.\d{2}", " ".join(remainder))
        return len(money_matches) >= 2
    return False


def looks_like_non_transaction_line(line: str) -> bool:
    upper = normalized_text(line)
    if not upper:
        return True
    if upper.startswith("PAGE ") or "PAGE " in upper and " OF " in upper:
        return True
    return any(marker in upper for marker in METADATA_MARKERS)


def consume_leading_dates(text: str) -> tuple[date | None, str]:
    parts = clean_text(text).split()
    if not parts:
        return None, ""

    parsed_date = None
    consumed = 0
    while consumed < len(parts):
        matched = False
        for window in (3, 2, 1):
            if consumed + window > len(parts):
                continue
            candidate = " ".join(parts[consumed : consumed + window])
            date_value = parse_date(candidate)
            if date_value:
                if parsed_date is None:
                    parsed_date = date_value
                consumed += window
                matched = True
                break
        if not matched:
            break
    return parsed_date, " ".join(parts[consumed:])


def parse_transaction_from_text_line(
    line: str,
    *,
    page_number: int,
    parser_name: str,
    previous_balance: Decimal | None,
) -> Transaction | None:
    date_value, remainder = consume_leading_dates(line)
    if date_value is None:
        return None

    money_matches = list(re.finditer(r"-?[0-9,]+\.\d{2}", remainder))
    if len(money_matches) < 2:
        return None

    balance = parse_decimal(money_matches[-1].group(0))
    amount_candidates = [parse_decimal(match.group(0)) for match in money_matches[:-1][-2:]]
    amount_candidates = [amount for amount in amount_candidates if amount is not None]
    if balance is None or not amount_candidates:
        return None

    description = clean_text(remainder[: money_matches[0].start()])
    if not description:
        description = "Unlabeled Transaction"

    debit = Decimal("0")
    credit = Decimal("0")
    if len(amount_candidates) >= 2:
        left = amount_candidates[-2]
        right = amount_candidates[-1]
        if left == 0 and right > 0:
            credit = right
        elif right == 0 and left > 0:
            debit = left
        elif previous_balance is not None:
            expected = previous_balance - left + right
            if abs(expected - balance) <= Decimal("1.00"):
                debit = left
                credit = right
            else:
                expected = previous_balance - right + left
                if abs(expected - balance) <= Decimal("1.00"):
                    debit = right
                    credit = left
                else:
                    debit = left
                    credit = Decimal("0")
        else:
            debit = left
            credit = Decimal("0")
    else:
        amount = amount_candidates[0]
        debit, credit = infer_amount_direction(amount, balance, previous_balance, description)

    if debit > 0 and credit > 0:
        if debit >= credit:
            debit -= credit
            credit = Decimal("0")
        else:
            credit -= debit
            debit = Decimal("0")

    return Transaction(
        transaction_date=date_value,
        description=description,
        debit=debit,
        credit=credit,
        balance=balance,
        raw_text=line,
        source_page=page_number,
        parser_name=parser_name,
    )


def split_row_by_plan(row: AdaptiveWordRow, plan: HeaderPlan) -> list[str]:
    columns = sorted(plan.columns, key=lambda item: item.center)
    boundaries = [0.0]
    for left, right in zip(columns, columns[1:]):
        boundaries.append((left.x1 + right.x0) / 2)
    boundaries.append(plan.page_width + 1)

    cell_words: list[list[str]] = [[] for _ in columns]
    for word in row.words:
        center = (float(word["x0"]) + float(word["x1"])) / 2
        for index in range(len(columns)):
            if boundaries[index] <= center < boundaries[index + 1]:
                cell_words[index].append(word["text"])
                break

    return [" ".join(parts).strip() for parts in cell_words]


def plan_has_required_semantics(plan: HeaderPlan) -> bool:
    required = {"date", "description", "balance"}
    if not required.issubset(plan.semantics):
        return False
    return bool({"debit", "credit", "amount"} & plan.semantics)


def first_parseable_date(cells: list[str], columns: list[HeaderColumn]):
    for cell, column in zip(cells, columns):
        if column.semantic != "date":
            continue
        parsed = parse_date(cell)
        if parsed:
            return parsed
    return None


def extract_description(cells: list[str], columns: list[HeaderColumn]) -> str:
    parts: list[str] = []
    for cell, column in zip(cells, columns):
        if column.semantic in {"description", "reference"} and cell:
            parts.append(cell)
    return strip_leading_dates(clean_text(" ".join(parts)))


def first_decimal(cells: list[str], columns: list[HeaderColumn], semantic: str) -> Decimal | None:
    for cell, column in zip(cells, columns):
        if column.semantic != semantic:
            continue
        parsed = parse_decimal_from_cell(cell, semantic=semantic)
        if parsed is not None:
            return parsed
    return None


def infer_amount_direction(
    amount: Decimal,
    balance: Decimal | None,
    previous_balance: Decimal | None,
    description: str,
) -> tuple[Decimal, Decimal]:
    if balance is not None and previous_balance is not None:
        delta = balance - previous_balance
        if abs(delta - amount) <= Decimal("1.00"):
            return Decimal("0"), amount
        if abs(delta + amount) <= Decimal("1.00"):
            return amount, Decimal("0")

    description_upper = normalized_text(description)
    credit_markers = ("CREDIT", "CR", "DEPOSIT", "LODGEMENT", "INFLOW", "PAYMENT IN", "TRANSFER FROM")
    debit_markers = ("DEBIT", "DR", "WITHDRAW", "CHARGE", "TRANSFER TO", "PAYMENT", "PURCHASE", "BILL")
    if any(marker in description_upper for marker in credit_markers):
        return Decimal("0"), amount
    if any(marker in description_upper for marker in debit_markers):
        return amount, Decimal("0")
    return amount, Decimal("0")


def strip_leading_dates(description: str) -> str:
    cleaned = clean_text(description)
    if not cleaned:
        return ""

    parts = cleaned.split()
    for window in (3, 2, 1):
        while len(parts) >= window:
            candidate = " ".join(parts[:window])
            if not parse_date(candidate):
                break
            parts = parts[window:]
    return " ".join(parts)


def parse_decimal_from_cell(value: str | None, *, semantic: str) -> Decimal | None:
    if value is None:
        return None
    candidates = parse_decimal_candidates(value)
    if not candidates:
        return None

    if semantic == "balance":
        return candidates[-1]
    if semantic in {"debit", "credit", "amount"}:
        decimal_candidates = [candidate for candidate in candidates if candidate.as_tuple().exponent < 0]
        if decimal_candidates:
            return decimal_candidates[-1]
        return candidates[-1]
    return candidates[0]


def parse_decimal_candidates(value: str) -> list[Decimal]:
    cleaned = clean_text(value).upper()
    if not cleaned:
        return []

    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = re.sub(r"\b(?:CR|DR|NGN)\b", "", cleaned)
    matches = re.findall(r"-?[0-9,]+(?:\.\d{1,2})?", cleaned)
    results: list[Decimal] = []
    for match in matches:
        try:
            parsed = Decimal(match.replace(",", ""))
        except InvalidOperation:
            continue
        results.append(-parsed if negative else parsed)
    return results


def is_header_row(row: AdaptiveWordRow, plan: HeaderPlan) -> bool:
    row_columns = infer_columns_from_row(row.words)
    if not row_columns:
        return False
    row_semantics = {column.semantic for column in row_columns}
    return len(row_semantics & plan.semantics) >= min(3, len(plan.semantics))


def is_opening_balance_row(description: str, balance: Decimal | None) -> bool:
    return bool(balance is not None and "OPENING BALANCE" in normalized_text(description))


def append_continuation(transactions: list[Transaction], description: str, page_number: int) -> None:
    if not transactions:
        return
    last = transactions[-1]
    if last.source_page != page_number:
        return
    last.description = clean_text(f"{last.description} {description}")
    last.raw_text = last.description


def assess_adaptive_parse(
    transactions: list[Transaction],
    metadata: StatementMetadata | None,
    plan: HeaderPlan,
    *,
    template: AdaptiveTemplate | None = None,
) -> ParseAssessment:
    reasons: list[str] = []
    if len(transactions) < 3:
        return ParseAssessment(score=0.2, reasons=["fewer than three transaction rows were extracted"])

    score = 0.0
    if {"date", "description", "balance"} <= plan.semantics and {"debit", "credit", "amount"} & plan.semantics:
        score += 0.3
    else:
        reasons.append("the inferred header was missing core transaction columns")

    score += 0.2 if len(transactions) >= 10 else 0.12

    debit_credit_rows = [
        tx for tx in transactions
        if tx.transaction_date is not None and (tx.debit > 0 or tx.credit > 0)
    ]
    if not debit_credit_rows:
        reasons.append("no transaction rows had debit or credit values")
        return ParseAssessment(score=score, reasons=reasons)

    single_sided_ratio = sum(1 for tx in debit_credit_rows if (tx.debit > 0) ^ (tx.credit > 0)) / len(debit_credit_rows)
    if single_sided_ratio >= 0.9:
        score += 0.18
    elif single_sided_ratio >= 0.75:
        score += 0.1
    else:
        reasons.append("too many rows had ambiguous debit and credit amounts")

    balance_rows = [tx for tx in debit_credit_rows if tx.balance is not None]
    if len(balance_rows) / len(debit_credit_rows) >= 0.8:
        score += 0.12
    elif balance_rows:
        score += 0.05
    else:
        reasons.append("balance values were missing from most rows")

    balance_match_ratio = compute_balance_match_ratio(transactions)
    if balance_match_ratio >= 0.7:
        score += 0.15
    elif balance_match_ratio == 0:
        reasons.append("running balance checks could not be confirmed")

    total_checks = 0
    matched_checks = 0
    if metadata:
        actual_debit = sum(tx.debit for tx in debit_credit_rows)
        actual_credit = sum(tx.credit for tx in debit_credit_rows)
        if metadata.total_debit is not None:
            total_checks += 1
            if abs(actual_debit - metadata.total_debit) <= Decimal("1.00"):
                matched_checks += 1
        if metadata.total_credit is not None:
            total_checks += 1
            if abs(actual_credit - metadata.total_credit) <= Decimal("1.00"):
                matched_checks += 1
    if total_checks:
        score += 0.05 * (matched_checks / total_checks)
    elif metadata and not (metadata.total_debit or metadata.total_credit):
        reasons.append("statement totals were not available for reconciliation")

    if template:
        score += 0.03

    return ParseAssessment(score=min(score, 0.99), reasons=reasons)


def compute_balance_match_ratio(transactions: list[Transaction]) -> float:
    comparable = [
        tx for tx in transactions
        if tx.transaction_date is not None and tx.balance is not None and ((tx.debit > 0) ^ (tx.credit > 0))
    ]
    if len(comparable) < 2:
        return 0.0

    matches = 0
    checks = 0
    previous_balance = comparable[0].balance
    for transaction in comparable[1:]:
        if previous_balance is None or transaction.balance is None:
            previous_balance = transaction.balance
            continue
        expected = previous_balance - transaction.debit + transaction.credit
        checks += 1
        if abs(expected - transaction.balance) <= Decimal("1.00"):
            matches += 1
        previous_balance = transaction.balance

    return matches / checks if checks else 0.0


def extract_generic_metadata(pages: list[pdfplumber.page.Page]) -> StatementMetadata:
    first_page_text = pages[0].extract_text() or ""
    inline_identity = extract_inline_account_identity(first_page_text)
    total_debit = extract_amount_after_label(
        first_page_text,
        ("TOTAL DEBIT", "TOTAL WITHDRAWAL", "TOTAL WITHDRAWALS"),
    ) or extract_amount_before_label(first_page_text, ("TOTAL DEBIT", "TOTAL WITHDRAWAL", "TOTAL WITHDRAWALS"))
    total_credit = extract_amount_after_label(
        first_page_text,
        ("TOTAL CREDIT", "TOTAL LODGEMENT", "TOTAL LODGEMENTS", "TOTAL DEPOSIT", "TOTAL DEPOSITS"),
    )
    return StatementMetadata(
        account_name=extract_regex_any(
            first_page_text,
            (
                r"ACCT NAME[:\s]+(.+?)\s+\d{2}-\d{2}-\d{4}\s+TO\b",
                r"ACCOUNT NAME[:\s]+(.+?)\s+(?:ACCOUNT NUMBER|ACCOUNT NO|CURRENCY|OPENING BALANCE|START DATE)",
                r"CUSTOMER NAME[:\s]+(.+?)\s+(?:ACCOUNT NUMBER|ACCOUNT NO|PERIOD|CURRENCY)",
            ),
        ) or (inline_identity[0] if inline_identity else None),
        account_number=extract_regex_any(
            first_page_text,
            (
                r"ACCT NO[:.\s]+(\d+)",
                r"ACCOUNT NUMBER[:.\s]+(\d+)",
                r"ACCOUNT NO[:.\s]+(\d+)",
                r"ACC\. NO\.[:\s]+(\d+)",
            ),
        ) or (inline_identity[1] if inline_identity else None),
        currency=extract_regex_any(
            first_page_text,
            (
                r"CURRENCY[:\s]+([A-Z]{3})",
                r"CURRENCY\s+([A-Z]{3})",
            ),
        ) or (inline_identity[2] if inline_identity else None),
        opening_balance=parse_decimal(
            find_regex_in_pages(pages, r"OPENING BAL(?:ANCE)?[:\s]+(-?[0-9,]+\.\d{1,2})", flags=re.IGNORECASE)
        ),
        total_debit=total_debit,
        total_credit=total_credit,
        closing_balance=parse_decimal(
            find_regex_in_pages(
                pages,
                r"(?:CLOSING BAL(?:ANCE)?|AVAILABLE BALANCE|USABLE BALANCE)[:\s]+(-?[0-9,]+\.\d{1,2})",
                flags=re.IGNORECASE,
                reverse=True,
            )
        ),
    )


def extract_amount_after_label(text: str, labels: tuple[str, ...]) -> Decimal | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"(?:{label_pattern})(?:\s*\([^)]*\))?\s*:?\s*(?:[A-Z]{{3}}\s*)?(-?[0-9,]+\.\d{{1,2}})",
        flags=re.IGNORECASE,
    )
    for line in (clean_text(line) for line in text.splitlines()):
        match = pattern.search(line)
        if match:
            return parse_decimal(match.group(1))
    return None


def extract_amount_before_label(text: str, labels: tuple[str, ...]) -> Decimal | None:
    label_pattern = re.compile("|".join(re.escape(label) for label in labels), flags=re.IGNORECASE)
    lines = [clean_text(line) for line in text.splitlines()]
    for index, line in enumerate(lines):
        match = label_pattern.search(line)
        if not match:
            continue

        prefix_amounts = re.findall(r"-?[0-9,]+\.\d{1,2}", line[: match.start()])
        if prefix_amounts:
            return parse_decimal(prefix_amounts[-1])

        for previous in reversed(lines[max(0, index - 3) : index]):
            if re.fullmatch(r"-?[0-9,]+\.\d{1,2}", previous):
                return parse_decimal(previous)
    return None


def extract_inline_account_identity(text: str) -> tuple[str, str, str] | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    account_pattern = re.compile(r"^(\d{8,})\s*-\s*\(([A-Z]{3})\)$", flags=re.IGNORECASE)
    for index, line in enumerate(lines):
        match = account_pattern.match(line)
        if not match or index == 0:
            continue
        account_name = lines[index - 1]
        if not re.search(r"[A-Z]", account_name, flags=re.IGNORECASE):
            continue
        return account_name, match.group(1), match.group(2).upper()
    return None


def extract_signature_text(pages: list[pdfplumber.page.Page], max_pages: int = 2) -> str:
    return "\n".join((page.extract_text() or "") for page in pages[:max_pages])


def load_adaptive_templates(path: Path = ADAPTIVE_TEMPLATES_FILE) -> list[AdaptiveTemplate]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    templates = data.get("templates", data if isinstance(data, list) else [])
    return [template_from_dict(item) for item in templates]


def save_adaptive_templates(templates: list[AdaptiveTemplate], path: Path = ADAPTIVE_TEMPLATES_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "templates": [template_to_dict(template) for template in templates],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def match_adaptive_template(signature_text: str, templates: list[AdaptiveTemplate]) -> AdaptiveTemplate | None:
    ranked = sorted(
        ((template.score(signature_text), template) for template in templates),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked:
        return None
    score, template = ranked[0]
    return template if score >= 0 else None


def template_matches_plan(template: AdaptiveTemplate | None, plan: HeaderPlan | None) -> bool:
    if template is None or plan is None:
        return False

    actual_order = tuple(column.semantic for column in sorted(plan.columns, key=lambda item: item.center))
    if template.column_order and actual_order != template.column_order:
        return False

    if template.header_labels:
        actual_labels = {column.label.upper() for column in plan.columns}
        template_labels = {label.upper() for label in template.header_labels}
        if len(actual_labels & template_labels) < min(2, len(template_labels)):
            return False

    return True


def save_adaptive_template(
    signature_text: str,
    plan: HeaderPlan,
    path: Path = ADAPTIVE_TEMPLATES_FILE,
    *,
    existing_templates: list[AdaptiveTemplate] | None = None,
    preferred_name: str | None = None,
    rename_existing: bool = False,
) -> AdaptiveTemplate | None:
    templates = list(existing_templates) if existing_templates is not None else load_adaptive_templates(path)
    template = build_adaptive_template(signature_text, plan, preferred_name=preferred_name)
    if template is None:
        return None

    for index, existing in enumerate(templates):
        if existing.key == template.key:
            if rename_existing and preferred_name and existing.name != template.name:
                updated = AdaptiveTemplate(
                    key=existing.key,
                    name=template.name,
                    required_terms=existing.required_terms,
                    optional_terms=existing.optional_terms,
                    column_order=existing.column_order,
                    header_labels=existing.header_labels,
                    column_ratios=existing.column_ratios,
                )
                templates[index] = updated
                save_adaptive_templates(templates, path)
                return updated
            return existing

    templates.append(template)
    save_adaptive_templates(templates, path)
    return template


def build_adaptive_template(
    signature_text: str,
    plan: HeaderPlan,
    *,
    preferred_name: str | None = None,
) -> AdaptiveTemplate | None:
    header_labels = tuple(column.label.upper() for column in sorted(plan.columns, key=lambda item: item.center))
    if len(header_labels) < 3:
        return None

    metadata_terms = tuple(term for term in METADATA_MARKERS if term in normalized_text(signature_text))
    required_terms = tuple(dict.fromkeys([*header_labels[: min(4, len(header_labels))], *metadata_terms[:2]]))
    optional_terms = tuple(term for term in metadata_terms if term not in required_terms)
    if len(required_terms) < 3:
        return None

    column_order = tuple(column.semantic for column in sorted(plan.columns, key=lambda item: item.center))
    column_ratios = tuple(round(column.center / max(plan.page_width, 1.0), 3) for column in sorted(plan.columns, key=lambda item: item.center))
    key = make_template_key(required_terms, column_order)
    clean_name = clean_template_name(preferred_name) if preferred_name else None
    name = clean_name or f"adaptive-template:{key}"
    return AdaptiveTemplate(
        key=key,
        name=name,
        required_terms=required_terms,
        optional_terms=optional_terms,
        column_order=column_order,
        header_labels=header_labels,
        column_ratios=column_ratios,
    )


def template_from_dict(data: dict) -> AdaptiveTemplate:
    return AdaptiveTemplate(
        key=str(data["key"]),
        name=str(data.get("name", f"adaptive-template:{data['key']}")),
        required_terms=tuple(data.get("required_terms", [])),
        optional_terms=tuple(data.get("optional_terms", [])),
        column_order=tuple(data.get("column_order", [])),
        header_labels=tuple(data.get("header_labels", [])),
        column_ratios=tuple(float(value) for value in data.get("column_ratios", [])),
    )


def template_to_dict(template: AdaptiveTemplate) -> dict:
    return {
        "key": template.key,
        "name": template.name,
        "required_terms": list(template.required_terms),
        "optional_terms": list(template.optional_terms),
        "column_order": list(template.column_order),
        "header_labels": list(template.header_labels),
        "column_ratios": list(template.column_ratios),
    }


def make_template_key(required_terms: tuple[str, ...], column_order: tuple[str, ...]) -> str:
    tokens = [*required_terms[:3], *column_order[:3]]
    slug = "-".join(
        re.sub(r"[^a-z0-9]+", "-", token.lower()).strip("-")
        for token in tokens
        if token
    )
    return slug[:80] or "adaptive-template"


def clean_template_name(name: str | None) -> str | None:
    if name is None:
        return None
    cleaned = clean_text(name)
    return cleaned[:90] or None


def extract_regex_any(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))
    return None


def normalized_text(value: str) -> str:
    return " ".join((value or "").upper().replace("\n", " ").split())


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").split())


def parse_date(value: str):
    cleaned = clean_text(value)
    if not cleaned:
        return None
    match = re.search(r"\b\d{1,2}[/-][A-Za-z0-9]{1,3}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", cleaned)
    if match:
        cleaned = match.group(0)
    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = clean_text(value).upper()
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = re.sub(r"\b(?:CR|DR|NGN)\b", "", cleaned)
    match = re.search(r"-?[0-9,]+(?:\.\d{1,2})?", cleaned)
    if not match:
        return None
    try:
        parsed = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


def build_preview_rows(transactions: list[Transaction], *, limit: int = 12) -> list[AdaptivePreviewRow]:
    rows: list[AdaptivePreviewRow] = []
    for transaction in transactions[:limit]:
        rows.append(
            AdaptivePreviewRow(
                transaction_date=transaction.transaction_date.isoformat() if transaction.transaction_date else None,
                description=transaction.description,
                debit=format_decimal(transaction.debit),
                credit=format_decimal(transaction.credit),
                balance=format_decimal(transaction.balance),
            )
        )
    return rows


def format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:,.2f}"


def normalize_column_role(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    if cleaned in ADAPTIVE_COLUMN_ROLES:
        return cleaned
    return None
