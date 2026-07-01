from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from threading import RLock

from statement_analyzer.models import ClassifiedTransaction, StatementMetadata, Transaction, TransactionDirection

INFLOW_CATEGORIES = [
    "Own Account",
    "Reversals",
    "Sales",
    "Individual Transfer",
    "Business Income",
    "Cash Deposit",
    "Loan Inflow",
    "Interest",
    "Other Inflow",
]

OUTFLOW_CATEGORIES = [
    "Charges",
    "Tax & Levy",
    "Commission",
    "Own Account",
    "Savings",
    "Relatives",
    "Individual Transfer",
    "Business Transfer",
    "Transport",
    "Drinks",
    "Construction",
    "Airtime",
    "Goods",
    "Rent",
    "Travel & Accomodation",
    "Repair & Maintenance",
    "Salary",
    "Food",
    "Church Support",
    "Loan",
    "Gas",
    "POS / Merchant",
    "Cash Withdrawal",
    "Reversals",
    "Other Outflow",
]

BUSINESS_KEYWORDS = (
    " LIMITED ",
    " LTD ",
    " VENTURES ",
    " SERVICES ",
    " ENTERPRISE ",
    " ENTERPRISES ",
    " HUB ",
    " COMPANY ",
    " GLOBAL ",
    " HOSPITAL ",
    " RESTAURANT ",
    " BAR ",
    " HOTEL ",
    " PLC ",
    " PROPERTIES ",
    " PROPERTY ",
    " CONSTRUCTION ",
    " CONC ",
    " ENERGY ",
    " OIL ",
    " PETROLEUM ",
    " MEDICAL ",
    " REAL ESTATE ",
    " INTEGRATED ",
)

TRANSFER_MARKERS = (
    " NIP ",
    " NIP/",
    " TRF ",
    " TRANSFER ",
    " MBANKING TRF ",
    " MOBILE TRF ",
    " OUTWARD TRANSFER ",
    " INWARD TRANSFER ",
    " TNF-",
    " TRFFRM ",
    " TRF FROM ",
    " TRF TO ",
    " DDS ",
    " BY DD ",
    " CIP/CR/",
    " CIP/DR/",
)

ENTITY_NOISE_TOKENS = {
    "ACCOUNT",
    "CURRENT",
    "SAVINGS",
    "CORPORATE",
    "LIMITED",
    "LTD",
    "PLC",
    "BANK",
    "ENTERPRISE",
    "ENTERPRISES",
    "SERVICES",
    "SERVICE",
    "VENTURES",
    "COMPANY",
    "GLOBAL",
    "PROPERTIES",
    "PROPERTY",
    "HUB",
    "NIGERIA",
    "ACCOUNTS",
    "MOB",
    "CR",
    "DR",
    "CIP",
    "NIP",
}

CHARGE_MARKERS = (
    " ACCOUNT MAINTENANCE ",
    " MAINTENANCE FEE ",
    " SMS ALERT ",
    " SMS CHARGE ",
    " ALERT FEE ",
    " ETOKEN ",
    " CARD MAINTENANCE ",
    " DEBIT INTEREST CAPITALIZATION ",
    " PENAL CHARGE ",
    " CHARGES ",
)

SLASH_TRANSFER_PREFIXES = {"CIP", "NIP"}
SLASH_TRANSFER_SKIP_TOKENS = {
    "CIP",
    "NIP",
    "CR",
    "DR",
    "MOB",
    "MOBILE",
    "TRF",
    "TRANSFER",
}
PROVIDER_TOKENS = {
    "ACCESS",
    "ACCESS BANK",
    "ECOBANK",
    "FCMB",
    "FIDELITY",
    "FIRSTBANK",
    "FIRST BANK",
    "GLOBUS",
    "GTBANK",
    "GTB",
    "JAIZ",
    "KEYSTONE",
    "KUDA",
    "LOTUS",
    "MONIEPOINT",
    "OPAY",
    "PALMPAY",
    "PROVIDUS",
    "STERLING",
    "STANBIC",
    "TAJ",
    "UBA",
    "UNION",
    "WEMA",
    "ZENITH",
}

SALE_MARKERS = (
    " SALE ",
    " SALES ",
    " PAYMENT FOR ",
    " PART PAYMENT ",
    " CUSTOMER ",
    " PURCHASE ",
    " PACK ",
    " PACKS ",
    " 50CL ",
    " TYRE ",
    " TYRES ",
    " FUEL ",
    " DIESEL ",
    " SWAN ",
    " SWAN WATER ",
    " GOODS ",
    " PRODUCT ",
    " SUPPLY ",
    " INVOICE ",
    " INV ",
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RULES_FILE = PROJECT_ROOT / "config" / "business_rules.json"
REVIEW_CONFIDENCE_THRESHOLD = 0.6
CUSTOM_CATEGORIES_KEY = "custom_categories"
_RULE_CONFIG_LOCK = RLock()


@dataclass(slots=True)
class MatchingRule:
    category: str
    match_any: tuple[str, ...] = ()
    counterparty_any: tuple[str, ...] = ()
    purpose_any: tuple[str, ...] = ()
    account_name_any: tuple[str, ...] = ()
    confidence: float = 0.9

    def matches(self, description: str, counterparty: str, purpose: str, owner_name: str | None = None) -> bool:
        if self.account_name_any and not any(owner_name_matches_rule(term, owner_name) for term in self.account_name_any):
            return False

        checks: list[bool] = []
        if self.match_any:
            checks.append(any(term in description for term in self.match_any))
        if self.counterparty_any:
            checks.append(any(term in counterparty for term in self.counterparty_any))
        if self.purpose_any:
            checks.append(any(term in purpose for term in self.purpose_any))
        return bool(checks) and all(checks)


@dataclass(slots=True)
class RuleBasedClassifier:
    fallback_category: str = "Unclassified"
    inflow_rules: list[MatchingRule] = field(init=False, default_factory=list)
    outflow_rules: list[MatchingRule] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        config = load_rule_config()
        self.inflow_rules = build_rules(config.get("inflow_rules", []))
        self.outflow_rules = build_rules(config.get("outflow_rules", []))

    def classify(
        self,
        transaction: Transaction,
        metadata: StatementMetadata | None = None,
    ) -> ClassifiedTransaction:
        raw_description = " ".join(part for part in (transaction.description, transaction.reference or "") if part)
        description = normalized(raw_description)
        counterparty = extract_counterparty(raw_description)
        purpose = extract_purpose(raw_description)
        owner_name = metadata.account_name if metadata else None

        if transaction.direction == TransactionDirection.INFLOW:
            category, rule_name, confidence = self._classify_inflow(description, counterparty, purpose, owner_name)
        elif transaction.direction == TransactionDirection.OUTFLOW:
            category, rule_name, confidence = self._classify_outflow(description, counterparty, purpose, owner_name)
        else:
            category, rule_name, confidence = self.fallback_category, None, 0.0

        amount = transaction.amount if transaction.amount > 0 else Decimal("0")
        category_amounts = {category: amount} if category != self.fallback_category else {}

        return ClassifiedTransaction(
            transaction=transaction,
            classification=category,
            confidence=confidence,
            rule_name=rule_name,
            category_amounts=category_amounts,
        )

    def _classify_inflow(
        self,
        description: str,
        counterparty: str,
        purpose: str,
        owner_name: str | None,
    ) -> tuple[str, str | None, float]:
        business_owner = bool(owner_name and looks_like_business_entity(owner_name))
        for rule in self.inflow_rules:
            if rule.matches(description, counterparty, purpose, owner_name):
                return rule.category, f"inflow-{slugify(rule.category)}", rule.confidence

        if contains_any(description, (" RSVL ", " RVSL ", " REVERSAL ", " REJECT FOR ", " REFUND ", " RETURNED ", " REV-VAT ", " REV VAT ")):
            return "Reversals", "inflow-reversal-heuristic", 0.98

        if looks_like_transfer(description) and owner_name_matches_text(owner_name, counterparty):
            return "Own Account", "inflow-owner-match", 0.99

        if contains_any(description, (" STLB ", " OWN ACCOUNT ", " SELF ")):
            return "Own Account", "inflow-own-account-heuristic", 0.92

        if contains_any(description, (" CASH DEPOSIT ", " CASH DEP ", " LODGEMENT ", " TELLER ", " CHEQUE DEPOSIT ")):
            return "Cash Deposit", "inflow-cash-deposit-heuristic", 0.9

        if contains_any(description, (" DIVIDEND ", " DIV ", " INTEREST ", " CREDIT INTEREST ")):
            return "Interest", "inflow-interest-heuristic", 0.92

        if contains_any(description, (" LOAN ", " FACILITY DISBURSEMENT ")):
            return "Loan Inflow", "inflow-loan-heuristic", 0.9

        if qualifies_sales_inflow(description, counterparty, purpose, owner_name):
            return "Sales", "inflow-sales-pattern", 0.9

        if business_owner and contains_any(description, (" FIPMB", " FIPM ")):
            return "Business Income", "inflow-business-channel-heuristic", 0.64

        if contains_any(description, (" SALE ", " PART PAYMENT ", " PROPERTY ", " RENT ", " LAND ", " HOUSE ", " PLOT ")):
            return "Sales", "inflow-sales-heuristic", 0.82

        if contains_any(description, (" JUDGMENT ", " COST AWARDED ", " AWARD ")):
            return "Business Income", "inflow-business-legal-heuristic", 0.8

        if looks_like_transfer(description):
            if looks_like_business_entity(counterparty) or looks_like_business_entity(description):
                return "Business Income", "inflow-business-transfer-heuristic", 0.75
            if looks_like_person_name(counterparty):
                return "Individual Transfer", "inflow-person-transfer-heuristic", 0.72
            return "Individual Transfer", "inflow-transfer-default", 0.72

        if looks_like_business_entity(description):
            return "Business Income", "inflow-business-heuristic", 0.62

        return "Other Inflow", "inflow-review-bucket", 0.35

    def _classify_outflow(
        self,
        description: str,
        counterparty: str,
        purpose: str,
        owner_name: str | None,
    ) -> tuple[str, str | None, float]:
        for rule in self.outflow_rules:
            if rule.matches(description, counterparty, purpose, owner_name):
                return rule.category, f"outflow-{slugify(rule.category)}", rule.confidence

        if contains_any(description, (" REJECT FOR ", " REVERSAL ", " RETURNED ", " REFUND ", " RVSL ")):
            return "Reversals", "outflow-reversal-heuristic", 0.88

        if looks_like_bank_charge(description, purpose):
            return "Charges", "outflow-charge-pattern", 0.98

        if contains_any(description, (" STAMP DUTY ", " STAMPDUTY ", " EMT LEVY ", " FGN ", " VAT ", " LEVY ")):
            return "Tax & Levy", "outflow-tax-heuristic", 0.97

        if contains_any(description, (" COMMISSION ", " COMMN ")):
            return "Commission", "outflow-commission-heuristic", 0.95

        if contains_any(description, (" INT.COLL ", " INTEREST RUN ", " LOAN ", " INTEREST COLLECTION ")):
            return "Loan", "outflow-loan-heuristic", 0.93

        if contains_any(description, (" POINT OF SALE ", " POS ", " WEB PURCHASE ", " CARD PAYMENT ", " MERCHANT ")):
            return "POS / Merchant", "outflow-pos-heuristic", 0.94

        if contains_any(description, (" CASH WITHDRAWAL ", " ATM ", " CASH WD ", " CHEQUE WITHDRAWAL ")):
            return "Cash Withdrawal", "outflow-cash-heuristic", 0.94

        if contains_any(purpose, (" SAVE ", " SAVES ", " SAVINGS ")):
            return "Savings", "outflow-savings-purpose", 0.92

        if contains_any(description, (" AIRTIME//", " AIRTIME ")):
            return "Airtime", "outflow-airtime-heuristic", 0.96

        if contains_any(purpose, (" FLIGHT ", " HOTEL ", " ACCOMODATION ", " ACCOMMODATION ", " TRAVEL ")):
            return "Travel & Accomodation", "outflow-travel-heuristic", 0.94

        if contains_any(purpose, (" SALARY ", " PAYROLL ")):
            return "Salary", "outflow-salary-heuristic", 0.95

        if contains_any(purpose, (" CHURCH ", " HEALING ", " STREAM ")):
            return "Church Support", "outflow-church-heuristic", 0.92

        if contains_any(purpose, (" RENT ",)):
            return "Rent", "outflow-rent-heuristic", 0.93

        if contains_any(purpose, (" BOLT ", " FUEL ", " TRANSPORT ", " BIKE ", " DRIVER ", " DRIVERS ", " DISPATCH ", " TICKET ")):
            return "Transport", "outflow-transport-heuristic", 0.92

        if contains_any(purpose, (" GAS ",)):
            return "Gas", "outflow-gas-heuristic", 0.92

        if contains_any(purpose, (" DRINK ", " DRINKS ", " WINE ")):
            return "Drinks", "outflow-drinks-heuristic", 0.92

        if contains_any(purpose, (" FOOD ",)):
            return "Food", "outflow-food-heuristic", 0.92

        if contains_any(
            purpose,
            (
                " GOODS ",
                " VEST ",
                " MATERIAL ",
                " MATERIALS ",
                " FURNITURE ",
                " EQUIPMENT ",
                " EQUIPMENTS ",
                " INTERIOR ",
                " INVERTER ",
                " DIESEL ",
                " WHOLE COW ",
                " WHOLE GOAT ",
                " COW LEG ",
                " SWAN WATER ",
                " WATER ",
                " VALVES ",
            ),
        ):
            return "Goods", "outflow-goods-heuristic", 0.86

        if contains_any(purpose, (" REPAIR ", " MAINTENANCE ", " SERVICE ", " COLDROOM ", " MECHANIC ", " BREAK DRUM ", " SUPPLY VAN ")):
            return "Repair & Maintenance", "outflow-repair-heuristic", 0.88

        if contains_any(purpose, (" TOWING ", " TRUCK ")):
            return "Transport", "outflow-transport-logistics", 0.9

        if contains_any(
            purpose,
            (
                " PLUMBING ",
                " CLEANING ",
                " POP ",
                " CEILING ",
                " CEMENT ",
                " WELDER ",
                " WELDERS ",
                " PAINT ",
                " TILES ",
                " BLOCK ",
                " PROJECT ",
                " BRICK ",
                " BRICKLAYING ",
                " PAINTER ",
                " WOOD ",
                " RUG ",
                " ELECTRICAL ",
                " BOREHOLE ",
                " WINDOW ",
                " RAIL ",
                " DEED ",
                " CCTV ",
                " FLOOR ",
                " ROOF ",
                " CURTAIN ",
                " GLASS ",
                " LIGHT ",
                " TILLING ",
            ),
        ):
            return "Construction", "outflow-construction-heuristic", 0.9

        if looks_like_transfer(description) and owner_name_matches_text(owner_name, counterparty):
            return "Own Account", "outflow-owner-match", 0.97

        if looks_like_transfer(description):
            if looks_like_person_name(counterparty):
                return "Individual Transfer", "outflow-person-transfer-heuristic", 0.72
            if looks_like_business_entity(counterparty):
                return "Business Transfer", "outflow-business-transfer-counterparty", 0.76
            if looks_like_business_entity(purpose):
                return "Business Transfer", "outflow-business-transfer-purpose", 0.73
            if looks_like_business_entity(description):
                return "Business Transfer", "outflow-business-transfer-description", 0.7
            return "Individual Transfer", "outflow-transfer-default", 0.7

        if looks_like_person_name(counterparty) or looks_like_person_name(description):
            return "Individual Transfer", "outflow-person-name-fallback", 0.62

        if looks_like_business_entity(description):
            return "Business Transfer", "outflow-business-vendor-fallback", 0.62

        return "Other Outflow", "outflow-review-bucket", 0.35


def build_rules(items: list[dict]) -> list[MatchingRule]:
    return [
        MatchingRule(
            category=item["category"],
            match_any=tuple(normalized_term(term) for term in item.get("match_any", [])),
            counterparty_any=tuple(normalized_term(term) for term in item.get("counterparty_any", [])),
            purpose_any=tuple(normalized_term(term) for term in item.get("purpose_any", [])),
            account_name_any=tuple(normalized_term(term) for term in item.get("account_name_any", [])),
            confidence=float(item.get("confidence", 0.9)),
        )
        for item in items
    ]


def load_rule_config() -> dict:
    with _RULE_CONFIG_LOCK:
        if RULES_FILE.exists():
            config = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        else:
            config = {"inflow_rules": [], "outflow_rules": []}

    config.setdefault(CUSTOM_CATEGORIES_KEY, {})
    config[CUSTOM_CATEGORIES_KEY].setdefault("inflow", [])
    config[CUSTOM_CATEGORIES_KEY].setdefault("outflow", [])
    return config


def save_rule_config(config: dict) -> None:
    with _RULE_CONFIG_LOCK:
        RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = RULES_FILE.with_suffix(f"{RULES_FILE.suffix}.tmp")
        temp_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        temp_path.replace(RULES_FILE)


def learn_rules_from_approved_transactions(
    approved_items: list[tuple[Transaction, str]],
    *,
    account_name: str | None = None,
) -> int:
    if not approved_items:
        return 0

    with _RULE_CONFIG_LOCK:
        config = load_rule_config()
        inflow_rules = list(config.get("inflow_rules", []))
        outflow_rules = list(config.get("outflow_rules", []))
        existing_keys = {
            canonical_rule_key(rule)
            for rule in [*inflow_rules, *outflow_rules]
        }

        added = 0
        for transaction, category in approved_items:
            rule = build_learned_rule(transaction, category, account_name=account_name)
            if not rule:
                continue
            key = canonical_rule_key(rule)
            if key in existing_keys:
                continue

            if transaction.direction == TransactionDirection.INFLOW:
                inflow_rules.append(rule)
            elif transaction.direction == TransactionDirection.OUTFLOW:
                outflow_rules.append(rule)
            else:
                continue

            existing_keys.add(key)
            added += 1

        if added:
            config["inflow_rules"] = inflow_rules
            config["outflow_rules"] = outflow_rules
            save_rule_config(config)

    return added


def normalized(text: str) -> str:
    collapsed = " ".join((text or "").upper().replace("\n", " ").split())
    return f" {collapsed} " if collapsed else " "


def normalized_term(text: str) -> str:
    return normalized(text).strip()


def contains_any(value: str, terms: tuple[str, ...]) -> bool:
    haystack = normalized(value)
    return any(normalized(term) in haystack or term.strip().upper() in haystack for term in terms)


def extract_counterparty(description: str) -> str:
    raw = " ".join((description or "").replace("\n", " ").split())
    slash_counterparty = extract_slash_transfer_counterparty(raw)
    if slash_counterparty:
        return slash_counterparty
    upper = raw.upper()

    patterns = (
        r"^Mob Trf IFO .+? BO (.+?)(?: Nb Ref:| Bbs Payment| Nura Ref:| Ref:|$)",
        r"^ACCT TRF TRF BO (.+?)(?: Ref:|$)",
        r"^Corporate Payment - BO (.+?)(?:/| Ref:|$)",
        r"^NIBSS Trf Credit (.+?) To [A-Z ]+\| .+?(?: Ref:|$)",
        r"^NEFT Inflow [A-Z ]*From (.+?) IFO .+?(?: Ref:|$)",
        r"^TNF-([^/]+)",
        r"^NIP IFO (.+?) Frm ",
        r"^NIP FRM (.+?)(?:-|$)",
        r"^NXG ?:TRFFRM (.+?) TO ",
        r"^TRF From App: To [A-Z]+ (.+)$",
        r"^TRF TO ([^/]+)",
        r"^NIP CR/MOB/([^/]+)",
        r"^OUTWARD TRANSFER[: ]+(.+?)(?:/|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return normalized(match.group(1)).strip()

    if raw.startswith("TRF TO ") and "//" in raw:
        return normalized(raw.split("TRF TO ", 1)[1].split("//", 1)[0]).strip()

    if upper.startswith("AIRTIME//"):
        return normalized("AIRTIME").strip()

    if upper.startswith("NIP/"):
        parts = [part.strip() for part in raw.split("/")]
        if len(parts) >= 3:
            return normalized(parts[2]).strip()

    return normalized(raw).strip()


def extract_purpose(description: str) -> str:
    raw = " ".join((description or "").replace("\n", " ").split())
    upper = raw.upper()

    if upper.startswith("AIRTIME//"):
        return normalized("AIRTIME").strip()

    slash_purpose = extract_slash_transfer_purpose(raw)
    if slash_purpose:
        return slash_purpose

    beneficiary_match = re.search(
        r"\bBO (.+?)(?: Nb Ref:| Bbs Payment| Nura Ref:| Ref:|$)",
        raw,
        flags=re.IGNORECASE,
    )
    if beneficiary_match:
        return normalized(beneficiary_match.group(1)).strip()

    if "//" in raw:
        return normalized(raw.rsplit("//", 1)[-1]).strip()

    if upper.startswith("NIP/"):
        parts = [part.strip() for part in raw.split("/")]
        if len(parts) >= 4:
            return normalized(" / ".join(part for part in parts[3:] if part)).strip()

    if " - " in raw:
        return normalized(raw.rsplit(" - ", 1)[-1]).strip()

    return normalized(raw).strip()


def looks_like_bank_charge(description: str, purpose: str) -> bool:
    return contains_any(description, CHARGE_MARKERS) or contains_any(purpose, CHARGE_MARKERS)


def qualifies_sales_inflow(
    description: str,
    counterparty: str,
    purpose: str,
    owner_name: str | None,
) -> bool:
    business_owner = bool(owner_name and looks_like_business_entity(owner_name))
    if contains_any(description, (" CASH DEP ", " CASH DEPOSIT ", " LODGEMENT ", " TELLER ")):
        return False
    if contains_any(description, SALE_MARKERS) or contains_any(purpose, SALE_MARKERS):
        return True
    if owner_name_matches_text(owner_name, counterparty):
        return False
    if business_owner and contains_any(description, (" SWAN ", " WATER ", " CUSTOMER ", " PURCHASE ", " INV ", " PAYMENT ")):
        return True
    if description.strip().startswith(" PP_") and looks_like_business_entity(description):
        return True
    return False


def looks_like_transfer(value: str) -> bool:
    return contains_any(value, TRANSFER_MARKERS)


def looks_like_business_entity(value: str) -> bool:
    if not value.strip():
        return False
    return contains_any(value, BUSINESS_KEYWORDS)


def looks_like_person_name(value: str) -> bool:
    cleaned = re.sub(r"[^A-Z ]", " ", normalized(value).strip())
    tokens = [token for token in cleaned.split() if len(token) > 1]
    if not 2 <= len(tokens) <= 6:
        return False
    if looks_like_business_entity(cleaned):
        return False
    banned = {"TRANSFER", "ACCOUNT", "BANK", "PAYMENT", "CHARGE", "ALERT", "LEVY", "FCMB", "UBA", "GTB", "NIP", "TRF"}
    return not any(token in banned for token in tokens)


def owner_name_matches_text(owner_name: str | None, candidate: str) -> bool:
    if not owner_name or not candidate:
        return False

    candidate_tokens = significant_entity_tokens(candidate)
    if len(candidate_tokens) < 2:
        return False

    for owner_variant in owner_name_variants(owner_name):
        overlap = owner_variant & candidate_tokens
        if len(overlap) < 2:
            continue
        if len(overlap) >= min(len(owner_variant), len(candidate_tokens)):
            return True
        if len(overlap) / min(len(owner_variant), len(candidate_tokens)) >= 0.67:
            return True

    return False


def owner_name_matches_rule(rule_owner_name: str, current_owner_name: str | None) -> bool:
    if not rule_owner_name or not current_owner_name:
        return False

    rule_clean = normalized(rule_owner_name).strip()
    current_clean = normalized(current_owner_name).strip()
    return rule_clean == current_clean or owner_name_matches_text(rule_owner_name, current_owner_name)


def owner_name_variants(owner_name: str) -> list[set[str]]:
    variants: list[set[str]] = []
    raw_segments = [owner_name, *re.split(r"[()/|,-]+", owner_name)]
    for segment in raw_segments:
        tokens = significant_entity_tokens(segment)
        if len(tokens) >= 2 and tokens not in variants:
            variants.append(tokens)
    return variants


def significant_entity_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"[^A-Z0-9 ]", " ", normalized(value).strip())
    tokens = {
        token
        for token in cleaned.split()
        if len(token) > 1 and token not in ENTITY_NOISE_TOKENS
    }
    return tokens


def slugify(value: str) -> str:
    return value.lower().replace(" & ", "-").replace(" ", "-").replace("/", "-")


def build_learned_rule(
    transaction: Transaction,
    category: str,
    *,
    account_name: str | None = None,
) -> dict | None:
    if category == "Unclassified" or transaction.direction == TransactionDirection.UNKNOWN:
        return None

    description = normalized(transaction.description).strip()
    counterparty = extract_counterparty(transaction.description)
    purpose = extract_purpose(transaction.description)
    anchor = extract_learning_anchor(transaction.description)

    rule: dict[str, object] = {
        "category": category,
        "confidence": 0.99,
        "source": "manual_review",
    }
    if account_name:
        rule["account_name_any"] = [account_name]

    if is_useful_learning_term(counterparty):
        rule["counterparty_any"] = [counterparty]
    if is_useful_learning_term(purpose):
        rule["purpose_any"] = [purpose]
    if is_useful_learning_term(anchor):
        rule["match_any"] = [anchor]

    if not any(key in rule for key in ("match_any", "counterparty_any", "purpose_any")):
        if is_useful_learning_term(description):
            rule["match_any"] = [description]
        else:
            return None

    return rule


def extract_learning_anchor(description: str) -> str:
    raw = " ".join((description or "").replace("\n", " ").split())
    stripped = re.sub(r"\bREF[: ]+[A-Z0-9/-]+\b", "", raw, flags=re.IGNORECASE)
    stripped = re.sub(r"\b\d{6,}\b", "", stripped)
    stripped = stripped.split("//", 1)[0]
    stripped = stripped.split("@", 1)[0]
    stripped = stripped.split(" - ", 1)[0]
    if " TO - " in stripped.upper():
        stripped = stripped.split(" TO - ", 1)[0]
    return normalized(stripped).strip()


def extract_slash_transfer_counterparty(description: str) -> str:
    parts = slash_transfer_parts(description)
    if not parts:
        return ""

    name_index = 3 if len(parts) > 3 and parts[2] in {"MOB", "MOBILE"} else 2
    if name_index >= len(parts):
        return ""

    counterparty_parts: list[str] = []
    for part in parts[name_index:]:
        if is_provider_token(part) or looks_like_reference_token(part):
            break
        if part in SLASH_TRANSFER_SKIP_TOKENS:
            continue
        counterparty_parts.append(part)

    return normalized(" ".join(counterparty_parts)).strip() if counterparty_parts else ""


def extract_slash_transfer_purpose(description: str) -> str:
    parts = slash_transfer_parts(description)
    if not parts:
        return ""

    for part in reversed(parts):
        if (
            part in SLASH_TRANSFER_SKIP_TOKENS
            or is_provider_token(part)
            or looks_like_reference_token(part)
        ):
            continue
        return normalized(part).strip()

    return ""


def slash_transfer_parts(description: str) -> list[str]:
    raw = " ".join((description or "").replace("\n", " ").split()).strip()
    if not raw:
        return []

    parts = [part.strip().upper() for part in raw.split("/") if part.strip()]
    if not parts or parts[0] not in SLASH_TRANSFER_PREFIXES:
        return []
    return parts


def is_provider_token(value: str) -> bool:
    candidate = normalized(value).strip()
    if not candidate:
        return False
    if candidate in PROVIDER_TOKENS:
        return True
    return looks_like_business_entity(candidate) and "BANK" in candidate


def looks_like_reference_token(value: str) -> bool:
    candidate = normalized(value).strip()
    if not candidate:
        return False
    compact = re.sub(r"[^A-Z0-9]", "", candidate)
    if re.fullmatch(r"\d{6,}", compact):
        return True
    if re.fullmatch(r"[A-Z]{2,}\d{5,}", compact):
        return True
    return False


def is_useful_learning_term(value: str) -> bool:
    cleaned = normalized(value).strip()
    if len(cleaned) < 6:
        return False
    if not re.search(r"[A-Z]", cleaned):
        return False
    if re.fullmatch(r"[A-Z0-9 /.-]+", cleaned) and not re.search(r"[A-Z]{3,}", cleaned):
        return False

    weak_terms = {
        "TRANSFER",
        "TRF",
        "NIP",
        "CREDIT",
        "DEBIT",
        "CHARGE",
        "BANK",
        "ACCOUNT",
        "CURRENT",
        "PAYMENT",
        "WITHDRAWAL",
        "LODGEMENT",
    }
    tokens = {token for token in re.sub(r"[^A-Z ]", " ", cleaned).split() if len(token) > 2}
    return bool(tokens - weak_terms)


def canonical_rule_key(rule: dict) -> tuple:
    return (
        rule.get("category"),
        tuple(sorted(rule.get("match_any", []))),
        tuple(sorted(rule.get("counterparty_any", []))),
        tuple(sorted(rule.get("purpose_any", []))),
    )


def inflow_categories() -> list[str]:
    config = load_rule_config()
    custom = config.get(CUSTOM_CATEGORIES_KEY, {}).get("inflow", [])
    return unique_preserving_order([*INFLOW_CATEGORIES, *custom])


def outflow_categories() -> list[str]:
    config = load_rule_config()
    custom = config.get(CUSTOM_CATEGORIES_KEY, {}).get("outflow", [])
    return unique_preserving_order([*OUTFLOW_CATEGORIES, *custom])


def add_custom_category(direction: str, category_name: str) -> str | None:
    normalized_name = normalize_category_name(category_name)
    if not normalized_name:
        return None

    direction_key = direction.strip().lower()
    if direction_key not in {"inflow", "outflow"}:
        return None

    with _RULE_CONFIG_LOCK:
        config = load_rule_config()
        category_bucket = config.setdefault(CUSTOM_CATEGORIES_KEY, {}).setdefault(direction_key, [])
        existing = {normalize_category_name(item).casefold() for item in category_bucket}
        default_names = {
            name.casefold()
            for name in (INFLOW_CATEGORIES if direction_key == "inflow" else OUTFLOW_CATEGORIES)
        }
        if normalized_name.casefold() not in existing and normalized_name.casefold() not in default_names:
            category_bucket.append(normalized_name)
            save_rule_config(config)
    return normalized_name


def normalize_category_name(value: str) -> str:
    cleaned = " ".join((value or "").replace("\n", " ").split())
    return cleaned[:80]


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = normalize_category_name(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result
