"""Source scout (Loop B): resolves the gap analyst's entity hint to one concrete registrant and
picks the catalog template that holds the missing variable (docs/architecture.md, Loop B).

Only from the catalog: every read goes through a catalog template (``sec_tickers``,
``sec_submissions``), served in live mode by Zetaris (``adapters.catalog.ZetarisDiscovery``),
and the chosen template must be a catalog entry the onboarder can register.
Nothing here names a company, a filing or an amount; the rules are about how SEC filers behave:

1. The hint must point at a disposal filing (8-K item 2.01) of the source entity, with its date.
2. Candidates: registrants in the ticker map whose name carries the source's brand (first word
   of the title equals the source's ticker or the first word of its title), newest CIK first,
   at most MAX_CANDIDATES. A business that leaves as its own registrant usually keeps the brand.
3. For each candidate, its own filing history (sec_submissions) must show that it became a
   separate registrant at that disposal: it existed by the disposal date (earliest former name or
   filing), had filed no annual report before it, and filed its own current report within
   SEPARATION_WINDOW_DAYS of it. A change-in-control item (5.01) in that report ranks higher.
4. Exactly one best candidate resolves the hint; none, or a tie, leaves the need unresolved and
   the run at the evidence frontier.
5. Template: among catalog templates that carry XBRL facts and that the onboarder can register,
   the one that covers the variable and all its aliases with the fewest registrations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..adapters.catalog import Catalog, CatalogEntry, CatalogError, Discovery, Profile, TickerRow
from ..adapters.zetaris import CATALOG_TABLES, Entity, SourceError, object_ticker
from ..models import EvidenceItem, InformationNeed
from .common import ROWS_SHOWN
from .knowledge import ANNUAL_FORMS, CURRENT_REPORT_FORMS, DISPOSAL, SEPARATION_ITEMS

AGENT = "source_scout"
MAX_CANDIDATES = 10
SEPARATION_WINDOW_DAYS = 7
FACTS_PATH = "/api/xbrl/"


@dataclass
class Assessment:
    """Why one candidate does or does not match the hint (kept in the evidence)."""

    ticker: str
    cik10: str
    title: str
    registered_since: str | None = None
    annual_before: int = 0
    near: list[dict[str, Any]] = field(default_factory=list)
    control: bool = False
    distance: int | None = None
    qualifies: bool = False
    reason: str = ""

    def row(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "cik": self.cik10,
            "title": self.title,
            "registered_since": self.registered_since,
            "annual_reports_before": self.annual_before,
            "current_reports_near": self.near[:3],
            "change_in_control": self.control,
            "qualifies": self.qualifies,
            "reason": self.reason,
        }


@dataclass
class ScoutResult:
    entity: Entity | None
    template: CatalogEntry | None
    reason: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    candidates: list[Assessment] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.entity is not None and self.template is not None


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def _first_word(text: str) -> str:
    parts = text.strip().split()
    return parts[0].lower() if parts else ""


def _item(result: Any, rows: list[dict[str, Any]], flt: str) -> EvidenceItem:
    return EvidenceItem(
        sql=result.sql,
        filter=flt,
        source=result.source,
        row_count=len(result),
        matched=len(rows),
        rows=rows[:ROWS_SHOWN],
        degraded=bool(getattr(result, "degraded", False)),
        note=getattr(result, "note", None),
    )


def assess(row: TickerRow, profile: Profile | None, disposal: date) -> Assessment:
    """Rule 3 for one candidate. Pure, so it is unit-testable."""
    a = Assessment(ticker=row.ticker, cik10=row.cik10, title=row.title)
    if profile is None:
        a.reason = "no sec_submissions profile for this CIK"
        return a
    dates = [d for d in (_day(f.get("from")) for f in profile.former_names) if d]
    dates += [d for d in (_day(f.filing_date) for f in profile.filings) if d]
    since = min(dates) if dates else None
    a.registered_since = since.isoformat() if since else None
    a.annual_before = sum(
        1
        for f in profile.filings
        if f.form in ANNUAL_FORMS and (_day(f.filing_date) or disposal) < disposal
    )
    for f in profile.filings:
        d = _day(f.filing_date)
        if (
            f.form in CURRENT_REPORT_FORMS
            and d
            and abs((d - disposal).days) <= (SEPARATION_WINDOW_DAYS)
        ):
            a.near.append({"form": f.form, "filing_date": f.filing_date, "items": f.items})
    a.control = any(i in (n["items"] or "").split(",") for n in a.near for i in SEPARATION_ITEMS)
    if a.near:
        a.distance = min(
            abs(((_day(n["filing_date"]) or disposal) - disposal).days) for n in a.near
        )
    if since is None or since > disposal:
        a.reason = f"not a registrant by the disposal ({a.registered_since or 'no dates'})"
    elif a.annual_before:
        a.reason = f"already filed {a.annual_before} annual report(s) before the disposal"
    elif not a.near:
        a.reason = f"no current report within {SEPARATION_WINDOW_DAYS} days of the disposal"
    else:
        a.qualifies = True
        a.reason = (
            f"registrant since {a.registered_since}, no annual report before the disposal, "
            f"current report {a.distance} day(s) from it"
            + (f" with item {'/'.join(SEPARATION_ITEMS)}" if a.control else "")
        )
    return a


def pick_template(catalog: Catalog, need: InformationNeed) -> tuple[CatalogEntry | None, str]:
    """Rule 5: the registrable XBRL template with the fewest registrations for all aliases."""
    aliases = [need.variable, *need.variable_aliases]
    scored: list[tuple[int, CatalogEntry]] = []
    for e in catalog.entries:
        if FACTS_PATH not in e.endpoint or e.id not in CATALOG_TABLES:
            continue
        if not set(e.joins_on) & set(need.join_key):
            continue
        if set(e.params) - {"cik10", "concept"}:
            continue
        scored.append((len(aliases) if "concept" in e.params else 1, e))
    if not scored:
        return None, "no registrable catalog template carries XBRL facts for the join key"
    cost, best = min(scored, key=lambda x: (x[0], x[1].id))
    return best, f"{best.id}: {cost} registration(s) cover {', '.join(aliases)}"


def resolve(need: InformationNeed, *, discovery: Discovery, tracer: Any) -> ScoutResult:
    start = time.perf_counter()
    result = _resolve(need, discovery)
    tracer.emit(
        agent=AGENT,
        action="resolve",
        status="success" if result.resolved else "rejected",
        node_id=need.node_id,
        target_agent="onboarder" if result.resolved else "reporter",
        input_summary=f"{need.entity_hint.relation}: {need.entity_hint.description}"[:300],
        output_summary=result.reason,
        sql=result.evidence[0].sql if result.evidence else None,
        rows=result.evidence[0].matched if result.evidence else 0,
        entity=(
            {"ticker": result.entity.ticker, "cik": result.entity.cik, "name": result.entity.name}
            if result.entity
            else None
        ),
        template=result.template.id if result.template else None,
        candidates=[c.row() for c in result.candidates][:MAX_CANDIDATES],
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return result


def _resolve(need: InformationNeed, discovery: Discovery) -> ScoutResult:
    hint = need.entity_hint
    disposal = _day(hint.filing_date)
    if disposal is None or not set(hint.filing_items) & set(DISPOSAL.items):
        return ScoutResult(
            None,
            None,
            "the hint names no disposal filing (8-K item "
            f"{'/'.join(DISPOSAL.items)}) to trace a separate registrant from",
        )
    template, why_template = pick_template(discovery.catalog, need)
    if template is None:
        return ScoutResult(None, None, why_template)
    try:
        lookup = discovery.lookup(
            tickers=[hint.source_ticker] if hint.source_ticker else [],
            ciks=[hint.source_cik] if hint.source_cik else [],
        )
    except (SourceError, CatalogError, ValueError) as err:
        return ScoutResult(None, None, f"catalog read failed (sec_tickers): {err}")
    rows = list(lookup)
    source = next(
        (r for r in rows if hint.source_cik and r.cik10 == hint.source_cik),
        None,
    ) or next(
        (r for r in rows if hint.source_ticker and r.ticker.upper() == hint.source_ticker.upper()),
        None,
    )
    if source is None:
        return ScoutResult(
            None,
            None,
            f"the source entity ({hint.source_ticker or hint.source_cik}) is not in sec_tickers",
            [_item(lookup, [], "source entity lookup")],
        )
    brand = {source.ticker.lower(), _first_word(source.title)} - {""}
    try:
        tickers = discovery.brand(sorted(brand))
    except (SourceError, CatalogError, ValueError) as err:
        return ScoutResult(None, None, f"catalog read failed (sec_tickers by brand): {err}")
    seen: set[str] = {source.cik10}
    matches: list[TickerRow] = []
    for r in sorted(tickers, key=lambda r: r.cik10, reverse=True):  # newest registrant first
        if r.cik10 in seen or _first_word(r.title) not in brand:
            continue
        try:
            object_ticker(r.ticker)
        except ValueError:
            continue
        seen.add(r.cik10)
        matches.append(r)
    flt = f"cik != '{source.cik10}'; one row per CIK; first {MAX_CANDIDATES}"
    evidence = [
        _item(
            tickers,
            [{"ticker": r.ticker, "cik": r.cik10, "title": r.title} for r in matches],
            flt,
        )
    ]
    if not matches:
        return ScoutResult(
            None, None, "no other registrant in sec_tickers carries the source's name", evidence
        )
    assessments: list[Assessment] = []
    for r in matches[:MAX_CANDIDATES]:
        try:
            subs = discovery.submissions(r.cik10, r.ticker)
        except (SourceError, CatalogError, ValueError) as err:
            a = Assessment(ticker=r.ticker, cik10=r.cik10, title=r.title)
            a.reason = f"catalog read failed (sec_submissions): {err}"
            assessments.append(a)
            continue
        a = assess(r, subs[0] if subs else None, disposal)
        assessments.append(a)
        if a.qualifies:
            evidence.append(
                _item(
                    subs,
                    [{"registered_since": a.registered_since}, *a.near],
                    f"form IN ({', '.join(CURRENT_REPORT_FORMS)}) AND abs(filing_date - "
                    f"'{hint.filing_date}') <= {SEPARATION_WINDOW_DAYS} days; no "
                    f"{'/'.join(ANNUAL_FORMS)} before it",
                )
            )
    ok = sorted(
        (a for a in assessments if a.qualifies),
        key=lambda a: (not a.control, a.distance if a.distance is not None else 10**6),
    )
    if not ok:
        return ScoutResult(
            None,
            None,
            f"no candidate became a separate registrant at the disposal filed {hint.filing_date} "
            f"({len(assessments)} checked)",
            evidence,
            assessments,
        )
    if len(ok) > 1 and (ok[0].control, ok[0].distance) == (ok[1].control, ok[1].distance):
        return ScoutResult(
            None,
            None,
            f"ambiguous: {ok[0].ticker} and {ok[1].ticker} match the disposal equally",
            evidence,
            assessments,
        )
    best = ok[0]
    entity = Entity(object_ticker(best.ticker), best.cik10, best.title)
    return ScoutResult(
        entity,
        template,
        f"{best.title} (CIK {best.cik10}): {best.reason}; template {why_template}",
        evidence,
        assessments,
    )
