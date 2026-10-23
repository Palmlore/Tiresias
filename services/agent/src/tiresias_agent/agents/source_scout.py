"""Source scout (Loop B): resolves the gap analyst's entity hint to one concrete registrant and
picks the catalog template that holds the missing variable (docs/architecture.md, Loop B).

Only from the catalog: every read goes through a catalog template (``sec_tickers``,
``sec_submissions``, ``sec_fulltext``), served in live mode by Zetaris
(``adapters.catalog.ZetarisDiscovery``), and the chosen template must be a catalog entry the
onboarder can register. Nothing here names a company, a filing or an amount; the rules are about
how SEC filers behave:

1. The hint must point at a disposal filing (8-K item 2.01) of the source entity, with its date.
2. Candidates come from filing evidence first, then from the name:
   a. **named**: filers whose Form 10 registration (``knowledge.REGISTRATION_FORMS``) in the
      REGISTRATION_LOOKBACK_DAYS before the disposal contains the source's name, as it was at the
      disposal (``search_phrase``: the former name in force then, legal-form words dropped),
      found by full-text search. A business that leaves as a new public registrant files a
      Form 10 that names its parent, whatever its own name. Kept: at most MAX_NAMED_CANDIDATES
      filers with at least NAMED_SHARE of the top filer's matching documents.
   b. **brand**: registrants in the ticker map whose name carries the source's brand (first word
      of the title equals the source's ticker or the first word of its title, unless that word
      is generic, ``knowledge.GENERIC_NAME_WORDS``), newest CIK first. A ranking signal and the
      only source of candidates where full-text search is unavailable (sample mode).
   At most MAX_CANDIDATES in all, named first.
3. For each candidate, its own filing history (sec_submissions) must show that it became a
   separate registrant at that disposal: it existed by the disposal date (earliest former name or
   filing), had filed no annual report before it (or at most MAX_ANNUAL_AFTER_FORM10, all after
   its own Form 10 filed within REGISTRATION_LOOKBACK_DAYS of the disposal: a new registrant
   whose fiscal year ended between registration and separation), and filed its own current
   report within SEPARATION_WINDOW_DAYS of it.
4. If no candidate qualifies, the scout looks for a buyer: filers whose own 8-K within
   SEPARATION_WINDOW_DAYS of the disposal names the source. An existing registrant (annual
   reports before the disposal) whose 8-K there reports item 2.01 (completion of an acquisition)
   is a sale to an existing company: its figures mix its own business with the acquired one, so
   it cannot measure the disposed business, and the scout refuses with that reason.
5. Ranking of qualifying candidates: named by a Form 10, then a change-in-control item (5.01) in
   the near report, then the brand, then the distance in days. Exactly one best candidate
   resolves the hint; none, or a tie on every signal, leaves the need unresolved and the run at
   the evidence frontier with the reason.
6. Template: among catalog templates that carry XBRL facts and that the onboarder can register,
   the one that covers the variable and all its aliases with the fewest registrations.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..adapters.catalog import (
    Catalog,
    CatalogEntry,
    CatalogError,
    Discovery,
    NamingRow,
    Profile,
    TickerRow,
)
from ..adapters.zetaris import CATALOG_TABLES, Entity, SourceError, object_ticker
from ..models import EvidenceItem, InformationNeed
from .common import ROWS_SHOWN
from .knowledge import (
    ANNUAL_FORMS,
    CURRENT_REPORT_FORMS,
    DISPOSAL,
    GENERIC_NAME_WORDS,
    LEGAL_NAME_WORDS,
    REGISTRATION_FORMS,
    SEPARATION_ITEMS,
)

AGENT = "source_scout"
MAX_CANDIDATES = 10
SEPARATION_WINDOW_DAYS = 7
# A new registrant's Form 10 is filed months before the separation (often a confidential draft
# first, then the public filing 2-6 months ahead); 18 months covers the slow cases.
REGISTRATION_LOOKBACK_DAYS = 540
MAX_NAMED_CANDIDATES = 3
NAMED_SHARE = 0.25  # of the top filer's matching documents; drops passing mentions
MAX_ANNUAL_AFTER_FORM10 = 1
FACTS_PATH = "/api/xbrl/"
_READ_ERRORS = (SourceError, CatalogError, ValueError)
_FORM10 = {g for f in REGISTRATION_FORMS for g in (f, f"{f}/A")}


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
    named: int = 0  # documents of its Form 10 filings that contain the source's name
    named_in: str = ""  # which search found it: "registration" or "current report"
    brand: bool = False
    acquirer: bool = False
    qualifies: bool = False
    reason: str = ""

    def row(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "cik": self.cik10,
            "title": self.title,
            "found_by": self.named_in or ("brand" if self.brand else ""),
            "named_documents": self.named,
            "brand": self.brand,
            "registered_since": self.registered_since,
            "annual_reports_before": self.annual_before,
            "current_reports_near": self.near[:3],
            "change_in_control": self.control,
            "acquirer": self.acquirer,
            "qualifies": self.qualifies,
            "reason": self.reason,
        }

    def rank(self) -> tuple[bool, bool, bool, int]:
        """Sort key, best first (rule 5)."""
        return (
            not (self.named and self.named_in == "registration"),
            not self.control,
            not self.brand,
            self.distance if self.distance is not None else 10**6,
        )


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
    annual = [
        d
        for d in (_day(f.filing_date) for f in profile.filings if f.form in ANNUAL_FORMS)
        if d and d < disposal
    ]
    a.annual_before = len(annual)
    form10 = [
        d
        for d in (_day(f.filing_date) for f in profile.filings if f.form in _FORM10)
        if d and d <= disposal
    ]
    # A new registrant whose fiscal year ends between its own Form 10 and the separation files
    # one annual report in between; an existing company has annual reports before any Form 10,
    # and an earlier separation's registrant has a Form 10 long before this disposal.
    tolerated = (
        bool(form10)
        and (disposal - min(form10)).days <= REGISTRATION_LOOKBACK_DAYS
        and len(annual) <= MAX_ANNUAL_AFTER_FORM10
        and all(d > min(form10) for d in annual)
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
    a.acquirer = bool(a.annual_before) and any(
        i in (n["items"] or "").split(",") for n in a.near for i in DISPOSAL.items
    )
    if since is None or since > disposal:
        a.reason = f"not a registrant by the disposal ({a.registered_since or 'no dates'})"
    elif a.acquirer:
        a.reason = (
            f"an existing registrant ({a.annual_before} annual report(s) before the disposal) "
            f"that reported completing an acquisition (8-K item {'/'.join(DISPOSAL.items)}) "
            f"{a.distance} day(s) from it: a sale to an existing company, not a separation"
        )
    elif a.annual_before and not tolerated:
        a.reason = f"already filed {a.annual_before} annual report(s) before the disposal"
    elif not a.near:
        a.reason = f"no current report within {SEPARATION_WINDOW_DAYS} days of the disposal"
    else:
        a.qualifies = True
        annual_text = (
            f"{a.annual_before} annual report(s) before the disposal, all after its own Form 10 "
            f"filed {min(form10)}"
            if a.annual_before
            else "no annual report before the disposal"
        )
        a.reason = (
            f"registrant since {a.registered_since}, {annual_text}, "
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


def search_phrase(title: str, profile: Profile | None, disposal: date) -> str:
    """The source's name as filings around the disposal would write it: the former name in
    force at that date if there is one, else the current name; legal-form words, state tags
    (``/DE/``) and punctuation other than ``& . ' -`` dropped. Pure."""
    name = (profile.name if profile and profile.name else "") or title
    for f in profile.former_names if profile else ():
        lo, hi = _day(f.get("from")), _day(f.get("to"))
        if f.get("name") and lo and hi and lo <= disposal <= hi:
            name = f["name"]
            break
    words = []
    for w in re.split(r"[\s,]+", name.lower()):
        if not w or w[0] in "/\\":
            continue
        w = re.sub(r"[^a-z0-9&.'-]", "", w).strip(".'-")
        if w and w not in LEGAL_NAME_WORDS:
            words.append(w)
    return " ".join(words)


def _named(
    discovery: Discovery,
    phrase: str,
    forms: tuple[str, ...],
    start: date,
    end: date,
    exclude: set[str],
    *,
    keep_items: tuple[str, ...] = (),
) -> tuple[list[NamingRow], EvidenceItem | None, str]:
    """Filers whose filings of ``forms`` in [start, end] contain ``phrase``: at most
    MAX_NAMED_CANDIDATES with at least NAMED_SHARE of the top filer's documents (or, with
    ``keep_items``, any filer whose matching filings carry one of those 8-K items)."""
    try:
        result = discovery.filers_naming(phrase, forms, start.isoformat(), end.isoformat())
    except _READ_ERRORS as err:
        return [], None, f"full-text search of {'/'.join(forms)} failed: {err}"
    rows = [r for r in result if r.cik10 not in exclude]
    top = max((r.hits for r in rows), default=0)
    kept = [
        r for r in rows if r.hits >= max(1, NAMED_SHARE * top) or set(r.items) & set(keep_items)
    ][:MAX_NAMED_CANDIDATES]
    flt = (
        f'documents containing "{phrase}" in {"/".join(forms)} filed {start}..{end}; '
        f"cik not the source; hits >= {NAMED_SHARE:g} x the top filer's"
        + (f" or items include {'/'.join(keep_items)}" if keep_items else "")
        + f"; first {MAX_NAMED_CANDIDATES}"
    )
    shown = [
        {
            "cik": r.cik10,
            "name": r.name,
            "documents": r.hits,
            "forms": ",".join(r.forms),
            "items": ",".join(r.items),
            "first_date": r.first_date,
        }
        for r in kept
    ]
    return kept, _item(result, shown, flt), ""


def _tickers_for(
    discovery: Discovery, rows: list[NamingRow]
) -> tuple[dict[str, TickerRow], EvidenceItem | None, str]:
    if not rows:
        return {}, None, ""
    try:
        result = discovery.lookup(ciks=[r.cik10 for r in rows])
    except _READ_ERRORS as err:
        return {}, None, f"catalog read failed (sec_tickers by CIK): {err}"
    out: dict[str, TickerRow] = {}
    for t in sorted(result, key=lambda t: t.ticker):
        out.setdefault(t.cik10, t)
    shown = [{"ticker": t.ticker, "cik": t.cik10, "title": t.title} for t in out.values()]
    return out, _item(result, shown, "the named filers' tickers"), ""


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
    except _READ_ERRORS as err:
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
    evidence: list[EvidenceItem] = []
    problems: list[str] = []

    # Rule 2b: the brand (a ranking signal; the only candidates without full-text search).
    brand = {source.ticker.lower(), _first_word(source.title)} - {""} - GENERIC_NAME_WORDS
    seen: set[str] = {source.cik10}
    matches: list[TickerRow] = []
    try:
        tickers = discovery.brand(sorted(brand))
    except _READ_ERRORS as err:
        problems.append(f"catalog read failed (sec_tickers by brand): {err}")
    else:
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
        shown = [{"ticker": r.ticker, "cik": r.cik10, "title": r.title} for r in matches]
        evidence.append(_item(tickers, shown, flt))

    # Rule 2a: filers whose Form 10 names the source, from the source's name at the disposal.
    try:
        own = discovery.submissions(source.cik10, source.ticker)
        profile = own[0] if own else None
    except _READ_ERRORS:
        profile = None
    phrase = search_phrase(source.title, profile, disposal)
    assessments: list[Assessment] = []
    assessed: set[str] = {source.cik10}

    def check(named: list[NamingRow], kind: str, extra: list[TickerRow], budget: int) -> None:
        """Assess named filers (with their tickers) and then ``extra`` rows, within budget."""
        rows_by_cik, item, problem = _tickers_for(
            discovery, [r for r in named if r.cik10 not in assessed]
        )
        if item:
            evidence.append(item)
        if problem:
            problems.append(problem)
        queue: list[tuple[TickerRow | None, NamingRow | None]] = [
            (rows_by_cik.get(n.cik10), n) for n in named if n.cik10 not in assessed
        ]
        queue += [(t, None) for t in extra if t.cik10 not in assessed]
        for row, hit in queue:
            if len(assessments) >= budget:
                break
            cik = row.cik10 if row else hit.cik10 if hit else ""
            if cik in assessed:
                continue
            assessed.add(cik)
            if row is None:
                a = Assessment(ticker="", cik10=cik, title=hit.name if hit else "")
                a.named, a.named_in = (hit.hits if hit else 0), kind
                a.reason = "named in the filings but not in sec_tickers (no current listing)"
                assessments.append(a)
                continue
            try:
                object_ticker(row.ticker)
                subs = discovery.submissions(row.cik10, row.ticker)
            except _READ_ERRORS as err:
                a = Assessment(ticker=row.ticker, cik10=row.cik10, title=row.title)
                a.reason = f"catalog read failed (sec_submissions): {err}"
            else:
                a = assess(row, subs[0] if subs else None, disposal)
                if a.qualifies or a.acquirer:
                    evidence.append(
                        _item(
                            subs,
                            [{"registered_since": a.registered_since}, *a.near],
                            f"form IN ({', '.join(CURRENT_REPORT_FORMS)}) AND abs(filing_date - "
                            f"'{hint.filing_date}') <= {SEPARATION_WINDOW_DAYS} days; no "
                            f"{'/'.join(ANNUAL_FORMS)} before it",
                        )
                    )
            if hit:
                a.named, a.named_in = hit.hits, kind
            a.brand = _first_word(row.title) in brand
            assessments.append(a)

    registered: list[NamingRow] = []
    if phrase:
        registered, item, problem = _named(
            discovery,
            phrase,
            REGISTRATION_FORMS,
            disposal - timedelta(days=REGISTRATION_LOOKBACK_DAYS),
            disposal,
            {source.cik10},
        )
        if item:
            evidence.append(item)
        if problem:
            problems.append(problem)
    else:
        problems.append(f"no searchable name for {source.title!r}")
    check(registered, "registration", matches, MAX_CANDIDATES)

    ok = [a for a in assessments if a.qualifies]
    if not ok and phrase and len(assessments) < MAX_CANDIDATES:
        # Rule 4: no separation found; look for a buyer whose own 8-K names the source.
        window = timedelta(days=SEPARATION_WINDOW_DAYS)
        buyers, item, problem = _named(
            discovery,
            phrase,
            CURRENT_REPORT_FORMS,
            disposal - window,
            disposal + window,
            assessed,
            keep_items=DISPOSAL.items,
        )
        if item:
            evidence.append(item)
        if problem:
            problems.append(problem)
        check(buyers, "current report", [], MAX_CANDIDATES)
        ok = [a for a in assessments if a.qualifies]

    searched = f'name searched: "{phrase}"' if phrase else "no name searched"
    tail = f" ({len(assessments)} checked; {searched}" + (
        f"; {'; '.join(problems)})" if problems else ")"
    )
    if not ok:
        buyer = next((a for a in assessments if a.acquirer), None)
        if buyer:
            return ScoutResult(
                None,
                None,
                f"sale to an existing company: {buyer.title} (CIK {buyer.cik10}) is "
                f"{buyer.reason.split(': a sale', 1)[0]}; its reported figures combine its own "
                "business with the acquired one, so they cannot measure the disposed business"
                + tail,
                evidence,
                assessments,
            )
        return ScoutResult(
            None,
            None,
            f"no candidate became a separate registrant at the disposal filed {hint.filing_date}"
            + tail,
            evidence,
            assessments,
        )
    ok.sort(key=Assessment.rank)
    if len(ok) > 1 and ok[0].rank() == ok[1].rank():
        return ScoutResult(
            None,
            None,
            f"ambiguous: {ok[0].ticker} and {ok[1].ticker} match the disposal equally" + tail,
            evidence,
            assessments,
        )
    best = ok[0]
    signals = []
    if best.named:
        signals.append(f"named in {best.named} document(s) of its own {best.named_in} filings")
    if best.brand:
        signals.append("carries the source's brand")
    others = [a.ticker for a in ok[1:]]
    entity = Entity(object_ticker(best.ticker), best.cik10, best.title)
    return ScoutResult(
        entity,
        template,
        f"{best.title} (CIK {best.cik10}): {best.reason}"
        + (f"; {', '.join(signals)}" if signals else "")
        + (f"; also qualified, ranked lower: {', '.join(others)}" if others else "")
        + f"; template {why_template}",
        evidence,
        assessments,
    )
