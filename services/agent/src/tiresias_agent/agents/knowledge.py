"""Generic domain knowledge for the rule-based agents (ADR-0007).

Everything here is about SEC reporting in general: US-GAAP concept relations, 8-K item codes,
and how an asker's levers map to actions. Nothing names a company, a filing or an amount from
the examples; entity knowledge comes only from the evidence source (data/sample or Zetaris).
"""

from __future__ import annotations

from dataclasses import dataclass

# Concept families. A metric's family decides which rules apply.
REVENUE_CONCEPTS = frozenset(
    {"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"}
)
INCOME_CONCEPTS = frozenset(
    {
        "NetIncomeLoss",
        "ProfitLoss",
        "IncomeLossFromContinuingOperations",
        "IncomeLossFromDiscontinuedOperationsNetOfTax",
    }
)

CONTINUING_OPS = "IncomeLossFromContinuingOperations"
DISCONTINUED_OPS = "IncomeLossFromDiscontinuedOperationsNetOfTax"
DISPOSAL_GROUP_REVENUE = "DisposalGroupIncludingDiscontinuedOperationRevenue"

# Accounting identities: the metric is the sum of these components (a breakdown, never a cause;
# causal-criteria.md §2.1). Non-controlling interests are left in the residual.
COMPONENTS: dict[str, tuple[str, ...]] = {
    "NetIncomeLoss": (CONTINUING_OPS, DISCONTINUED_OPS),
    "ProfitLoss": (CONTINUING_OPS, DISCONTINUED_OPS),
}


@dataclass(frozen=True)
class EventConcept:
    """A concept that records an economic event (a charge, a write-down) rather than a slice
    of the total. Its abnormal change against the baseline is a causal candidate."""

    concept: str
    description: str
    category: str
    sign: int  # effect on the parent metric per unit of the concept (-1 for an expense)
    rolls_into: frozenset[str]


EVENT_CONCEPTS: tuple[EventConcept, ...] = (
    EventConcept(
        concept="GoodwillImpairmentLoss",
        description="goodwill impairment charge (write-down of the value paid for acquisitions)",
        category="impairment",
        sign=-1,
        rolls_into=frozenset({CONTINUING_OPS, "OperatingIncomeLoss"}),
    ),
)


@dataclass(frozen=True)
class FilingEvent:
    """An 8-K item code (or a form) that reports an event able to move reported numbers."""

    key: str
    items: tuple[str, ...]
    forms: tuple[str, ...]
    description: str
    category: str
    modes: frozenset[str]  # "restatement" and/or "change"
    families: frozenset[str]  # metric families it can explain


DISPOSAL = FilingEvent(
    key="disposal",
    items=("2.01",),
    forms=("8-K", "8-K/A"),
    description="completion of an acquisition or disposition of assets (8-K item 2.01)",
    category="portfolio_change",
    modes=frozenset({"restatement", "change"}),
    families=frozenset({"revenue", "income"}),
)
ERROR_CORRECTION = FilingEvent(
    key="error_correction",
    items=("4.02",),
    forms=("8-K", "8-K/A", "10-K/A"),
    description="non-reliance on previously issued financial statements (8-K item 4.02) "
    "or an amended annual report (10-K/A)",
    category="accounting",
    modes=frozenset({"restatement"}),
    families=frozenset({"revenue", "income"}),
)
FILING_EVENTS = (DISPOSAL, ERROR_CORRECTION)

# Loop B (source scout): how a disposed business shows up as its own registrant. When a
# business leaves its parent as a separate registrant (a spin-off or split-off), the new
# registrant files its own 8-K around the completion date, typically with item 5.01 (changes in
# control of registrant), and it has filed no annual report before the separation.
SEPARATION_ITEMS: tuple[str, ...] = ("5.01",)
ANNUAL_FORMS: tuple[str, ...] = ("10-K", "10-K/A")
CURRENT_REPORT_FORMS: tuple[str, ...] = ("8-K", "8-K/A")

# How the disposed business's share of a metric is reported by the filer itself.
DISPOSAL_MAGNITUDE: dict[str, str] = {
    "revenue": DISPOSAL_GROUP_REVENUE,
    "income": DISCONTINUED_OPS,
}


def family(concept: str) -> str:
    if concept in REVENUE_CONCEPTS:
        return "revenue"
    if concept in INCOME_CONCEPTS:
        return "income"
    return "other"


# --- Asker levers (causal-criteria.md §5) -------------------------------------------------------

LEVER_FAMILIES: dict[str, tuple[str, ...]] = {
    "model": ("model", "estimate", "forecast", "valuation", "rating", "price target"),
    "audit": ("testing", "scope", "follow-up", "sign-off", "audit", "request", "control"),
    "strategy": ("portfolio", "capital", "strategy", "allocation", "divest", "acquisition"),
}
ROLE_DEFAULT_FAMILY = {"analyst": "model", "auditor": "audit", "board": "strategy"}

# Which lever families can act on each cause category.
CATEGORY_FAMILIES: dict[str, frozenset[str]] = {
    "portfolio_change": frozenset({"model", "audit", "strategy"}),
    "impairment": frozenset({"model", "audit", "strategy"}),
    "accounting": frozenset({"model", "audit"}),
}

ACTIONS: dict[tuple[str, str], str] = {
    ("portfolio_change", "model"): (
        "Re-base the {period} {metric} in the model on continuing operations "
        "({target} as now reported, {delta} vs {baseline}); treat the change as a disposal "
        "({event}) and keep the residual open until the disposed business's {metric} is "
        "reconciled."
    ),
    ("portfolio_change", "audit"): (
        "Request the registrant's reconciliation of the restated {period} {metric} "
        "({baseline} to {target}, {delta}) to the business disposed of ({event}), and scope "
        "testing to the discontinued-operations reclassification; residual open until reconciled."
    ),
    ("portfolio_change", "strategy"): (
        "Assess the portfolio effect of the disposal ({event}): the {period} {metric} moved by "
        "{delta}; review what the remaining businesses earn without it."
    ),
    ("impairment", "model"): (
        "Normalise {period} earnings in the model: the {driver} is non-cash and explains "
        "{share} of the {delta} deviation; model the remaining businesses on the lower asset base."
    ),
    ("impairment", "audit"): (
        "Test the impairment: obtain the cash-flow forecasts and discount rates behind the "
        "{driver}, which explains {share} of the {delta} deviation."
    ),
    ("impairment", "strategy"): (
        "Review the acquired businesses behind the {driver}: it explains {share} of the "
        "{period} {metric} deviation ({delta}); decide which to fix, hold or divest and how "
        "future acquisitions are priced."
    ),
    ("accounting", "model"): (
        "Replace the {period} {metric} in the model with the corrected value ({target}); "
        "re-check earlier periods for the same error."
    ),
    ("accounting", "audit"): (
        "Extend testing to the corrected {period} {metric} ({baseline} to {target}) and to the "
        "controls that let the error through."
    ),
}
# Actions once Loop B has reconciled the effect against the disposed business's own filings.
RECONCILED_ACTIONS: dict[tuple[str, str], str] = {
    ("portfolio_change", "model"): (
        "Re-base the {period} {metric} in the model on continuing operations ({target} as now "
        "reported, {delta} vs {baseline}): the restatement removes {counterparty} ({event}), "
        "whose own filings report {counterparty_value} for the period; residual: {residual}."
    ),
    ("portfolio_change", "audit"): (
        "The restated {period} {metric} ({baseline} to {target}) reconciles to the removal of "
        "{counterparty} ({event}): its own filings report {counterparty_value} for the period "
        "(residual: {residual}). Scope follow-up requests to that residual and to the "
        "discontinued-operations reclassification before sign-off."
    ),
    ("portfolio_change", "strategy"): (
        "Assess the portfolio without {counterparty} ({event}): it accounted for "
        "{counterparty_value} of the {period} {metric}; residual: {residual}."
    ),
}
EXTERNAL_ACTION = (
    "Monitor or adapt: the cause found ({driver}) is outside the asker's levers ({levers}); "
    "track it rather than act on it."
)


def lever_families(levers: list[str], role: str | None) -> set[str]:
    """Lever families an asker holds, from the words of their levers (role as a fallback)."""
    found: set[str] = set()
    for lever in levers:
        text = lever.lower()
        for fam, words in LEVER_FAMILIES.items():
            if any(w in text for w in words):
                found.add(fam)
    if not found and not levers and role in ROLE_DEFAULT_FAMILY:
        found.add(ROLE_DEFAULT_FAMILY[role])
    return found


def actionable_family(category: str, levers: list[str], role: str | None) -> str | None:
    """The asker's lever family that can act on this category, preferring the role's own."""
    usable = lever_families(levers, role) & CATEGORY_FAMILIES.get(category, frozenset())
    if not usable:
        return None
    preferred = ROLE_DEFAULT_FAMILY.get(role or "")
    return preferred if preferred in usable else min(usable)


# --- Formatting ---------------------------------------------------------------------------------


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e9:
        return f"{sign}${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.1f}M"
    return f"{sign}${v:,.0f}"


def residual_text(amount: float | None, share: float | None) -> str:
    """A signed residual in words: what is left unexplained, or by how much it over-explains."""
    if amount is None or share is None:
        return "n/a"
    if share >= 0:
        return f"{money(abs(amount))} unexplained ({share * 100:.1f}% of the effect)"
    return f"over-explained by {money(abs(amount))} ({-share * 100:.1f}% of the effect)"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"
