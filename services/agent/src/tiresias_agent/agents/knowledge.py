"""Generic domain knowledge for the rule-based agents (ADR-0007).

Everything here is about SEC reporting in general: US-GAAP concept relations, 8-K item codes,
and how an asker's levers map to actions. Nothing names a company, a filing or an amount from
the examples; entity knowledge comes only from the evidence source (data/sample or Zetaris).
"""

from __future__ import annotations

from dataclasses import dataclass

# Concept families. A metric's family decides which rules apply.
REVENUE_CONCEPTS = frozenset(
    {
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    }
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
# Generic first words of registrant names. They say nothing about a brand ("International
# Business Machines" and "International Media Acquisition" share no business), so the scout
# never uses them as the brand word; the source's ticker and any other first word still count.
GENERIC_NAME_WORDS: frozenset[str] = frozenset(
    {
        "american",
        "first",
        "general",
        "global",
        "international",
        "national",
        "new",
        "the",
        "united",
        "universal",
    }
)

# Exchange Act registration statements (Form 10 under section 12(b) or 12(g)). A business that
# leaves its parent as a new public registrant files one before the separation, and its text
# names the parent (the separation and distribution agreements, the carve-out financials). The
# source scout searches these filings for the parent's name, so the new registrant is found from
# filing evidence whatever its own name. A sale to an existing company files no Form 10; the
# buyer's own 8-K (item 2.01, completion of an acquisition) names the seller instead.
REGISTRATION_FORMS: tuple[str, ...] = ("10-12B", "10-12G")
# Legal-form words dropped from a registrant's name before it is searched as a phrase in filing
# text ("... Corp" in the ticker map, "... Corporation" in the documents).
LEGAL_NAME_WORDS: frozenset[str] = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "llc",
        "lp",
        "ltd",
        "limited",
        "plc",
        "the",
    }
)

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


# --- Suggested questions, alternatives and the frontier wording -------------------------------

# Metrics the rules can investigate (one per concept family), and how a question names them.
SUGGESTED_METRICS: tuple[str, ...] = ("Revenues", "NetIncomeLoss")
METRIC_LABELS: dict[str, str] = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue",
    "SalesRevenueNet": "revenue",
    "NetIncomeLoss": "net income",
    "ProfitLoss": "profit",
}
# Levers a suggested question gives each role when the caller names none (the role's family).
DEFAULT_LEVERS: dict[str, tuple[str, ...]] = {
    "analyst": ("model adjustments", "estimates", "rating"),
    "auditor": ("testing scope", "follow-up requests", "sign-off"),
    "board": ("portfolio strategy", "capital allocation"),
}
# Hypothesiser rules that test an accounting or filing event (not a slice of the total). When
# every candidate tested was one of these and each was ruled out, the filed totals have nothing
# left to say: the next evidence is a breakdown of the metric, which SEC company facts lack.
EVENT_RULES: frozenset[str] = frozenset(
    {"filing_event", "event_concept", "concept_switch", "comparative_restated"}
)
RULED_OUT_TEXT: dict[str, str] = {
    "disposal": "acquisition or disposal (8-K item 2.01)",
    "error_correction": "error correction (8-K item 4.02 or a 10-K/A)",
    "concept_switch": "change of reported concept",
    "comparative_restated": "restated prior-year comparative",
}
BREAKDOWN_GAP = (
    "The filed totals don't break {label} down further (segment or product detail isn't in "
    "SEC company facts)."
)
BREAKDOWN_RELATION = "same entity, breakdown"


def metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


# --- Memory lessons (ADR-0005, causal-criteria.md §6) -----------------------------------------
# A lesson is a path of hypothesiser steps, each named by (rule, key): the key is the event key
# of a filing event, the concept of a component or event concept, "counterparty" for the Loop B
# reconciliation and the rule itself otherwise. Never an entity, a value or a date.

RECONCILE_KEY = "counterparty"


def step_key(rule: str | None, variable: str, params: dict | None = None) -> str:
    """The generic key of one hypothesis step (see above)."""
    if rule == "filing_event":
        if params and params.get("event"):
            return str(params["event"])
        for fe in FILING_EVENTS:
            if variable == f"8-K {'/'.join(fe.items)}":
                return fe.key
        return variable
    if rule in ("component", "event_concept"):
        return variable
    if rule == "reconcile":
        return RECONCILE_KEY
    return rule or variable


# Steps that test the same mechanism under another name, so a recalled lesson is tried (and
# judged by the critic) where its own step cannot be proposed: a disposal reaches an income
# total through discontinued operations (the hypothesiser tests it under that component).
STEP_EQUIVALENTS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("filing_event", DISPOSAL.key): (("component", DISCONTINUED_OPS),),
}

# How a lesson words each step (generic; {concept} is a US-GAAP concept name). Kept short: the
# model-free H-MEM embedder scores a short lesson closer to the recall query (memory.py).
LESSON_STEP_TEXT: dict[tuple[str, str], str] = {
    ("filing_event", "accepted"): "the {event} in the window carried most of the effect",
    ("filing_event", "frontier"): (
        "test the {event} in the window first (frontier: the amount moved to the separate "
        "registrant it created)"
    ),
    ("reconcile", "accepted"): "reconcile against that registrant",
    ("component", "accepted"): "the {concept} component carries most of the change",
    ("event_concept", "accepted"): "under it, test the {concept} event concept",
    ("concept_switch", "accepted"): "the two filings report the figure under different concepts",
    ("comparative_restated", "accepted"): "the prior-period comparative was itself restated",
}
LESSON_KIND_TEXT: dict[str, str] = {
    "restatement": "was restated between two annual reports",
    "change": "changed between two periods",
}


def lesson_event_text(rule: str, key: str) -> str:
    """Generic words for the event a lesson step tests."""
    if rule == "filing_event":
        for fe in FILING_EVENTS:
            if fe.key == key:
                return f"8-K item {'/'.join(fe.items)} {fe.key.replace('_', ' ')}"
    if rule == "event_concept":
        for ev in EVENT_CONCEPTS:
            if ev.concept == key:
                return "an abnormal " + ev.description.split(" (")[0]
    return key


def ruled_out_text(rule: str | None, variable: str) -> str:
    """What a pruned event candidate ruled out, in words (generic, from its rule)."""
    if rule == "filing_event":
        for fe in FILING_EVENTS:
            if variable == f"8-K {'/'.join(fe.items)}":
                return RULED_OUT_TEXT.get(fe.key, fe.description)
    if rule == "event_concept":
        for ev in EVENT_CONCEPTS:
            if ev.concept == variable:
                return ev.description.split(" (")[0]
    return RULED_OUT_TEXT.get(rule or "", variable)


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
