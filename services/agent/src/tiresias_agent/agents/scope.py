"""Scope check: runs after entity resolution and before Loop A. Tiresias answers one kind of
question, "why did this filed number of an SEC registrant change?"; anything else ends the run
at once with plain reasons and 2-3 runnable alternatives instead of a misleading answer.

Reasons (``ScopeReason.type``):
- ``forecast``: the question asks about the future or a hypothetical. A documented rule over the
  question text (``forward_cue``), plus a data rule: a period that ends after today cannot be in
  any filing yet. Low false positives by design: strong cues ("what if", "will", "going to",
  "suppose", "what/how would" without "have", "would ... if") always count; weak cues
  ("forecast", "predict", "projection", "outlook", "expect ... to", "next year/quarter") count
  only when the question is not framed as explaining the past ("Why did ...", "What caused ...").
  So "Why did revenue fall even if orders rose?", "... less than expected?", "Why did the company miss its
  forecast?" and "How would revenue have changed ..." stay in scope.
- ``not-sec-registrant`` (live): the asked entity is not in the SEC ticker list
  (``tiresias.sec_tickers`` through Zetaris); not claimed when the lookup was degraded.
- ``not-in-sample`` (sample mode): the asked entity is not in the offline slice; sample mode
  cannot tell whether it is an SEC registrant, so it says so.

Alternatives (at most ``MAX_ALTERNATIVES``), each a complete ``RunRequest`` validated against
data before it is offered, in this order:
1. **named in the question**: SEC registrants the question names (capitalised phrases looked up
   in ``sec_tickers``; exactly one registrant must match), e.g. the driver of a what-if.
2. **same entity, past period**: for an SEC registrant, its most recent filed change of the
   metric instead of a forecast.
3. **same SIC peer**: registered entities whose submissions carry the asked entity's SIC code.
A registered entity's alternative comes from ``suggester.latest_change`` (the framer measured a
non-trivial change); an unregistered registrant's comes from its own submissions (the latest
fiscal year between two 10-K report dates), and the run registers it first.

A company that is not an SEC registrant has no SIC code in our data, so without a model the
alternatives can only come from SEC registrants named in the question. A Nightshift model could
propose the industry (and so peers) later: the model proposes, the agent validates (BACKLOG).
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

from ..adapters.catalog import CatalogError, Discovery, Profile, TickerRow
from ..adapters.zetaris import Entity, EvidenceSource, SourceError, object_ticker
from ..config import FIRST_RUN_SECONDS
from ..models import Alternative, RunOptions, RunRequest, ScopeCheck, ScopeReason
from ..models import Entity as EntityRef
from .knowledge import GENERIC_NAME_WORDS, SUGGESTED_METRICS, family
from .suggester import latest_change

AGENT = "scope"
MAX_ALTERNATIVES = 3
MAX_NAMED = 3  # capitalised phrases looked up per question
MAX_PEER_PROBES = 6  # registered entities whose SIC is read per check
_SIC_CACHE_SIZE = 256
_SIC_CACHE: OrderedDict[str, str] = OrderedDict()  # cik10 -> sic (SIC codes rarely change)

# --- the forecast / what-if rule -----------------------------------------------------------------

# "would" followed, within three words, by "have" is a past counterfactual ("How would revenue
# have changed without the disposal?"): that is what the intervention test measures, in scope.
_NO_HAVE = r"(?!(\s+\w+){0,3}?\s+have\b)"
_STRONG = (
    re.compile(r"\bwhat\s+if\b", re.IGNORECASE),
    re.compile(r"\bwill\b", re.IGNORECASE),
    re.compile(r"\bgoing\s+to\b", re.IGNORECASE),
    re.compile(r"\b(suppose|supposing|hypothetical(ly)?|imagine)\b", re.IGNORECASE),
    re.compile(r"\b(what|how\s+much|how\s+many|how)\s+would\b" + _NO_HAVE, re.IGNORECASE),
    re.compile(r"\bwould\b" + _NO_HAVE + r".*\bif\b|\bif\b.*\bwould\b" + _NO_HAVE, re.IGNORECASE),
)
_WEAK = (
    re.compile(r"\b(forecast\w*|predict\w*|projection\w*|outlook)\b", re.IGNORECASE),
    re.compile(r"\bexpect(s|ed)?\b(\s+\w+){0,3}\s+to\b", re.IGNORECASE),
    re.compile(r"\bnext\s+(year|quarter|fiscal\s+year|fy)\b", re.IGNORECASE),
)
_PAST_FRAME = re.compile(
    r"^\s*(why|what\s+(caused|drove|explains|explained|led\s+to|happened)|how\s+(did|come)|"
    r"explain)\b",
    re.IGNORECASE,
)


def forward_cue(question: str) -> str | None:
    """The words that make the question forward-looking or hypothetical, else None."""
    for rx in _STRONG:
        m = rx.search(question)
        if m:
            return " ".join(m.group(0).split())[:60]
    if _PAST_FRAME.search(question):
        return None
    for rx in _WEAK:
        m = rx.search(question)
        if m:
            return " ".join(m.group(0).split())[:60]
    return None


# --- registrants named in the question -------------------------------------------------------------

_PHRASE = re.compile(r"[A-Z][A-Za-z0-9&.-]*(?:['’]s)?(?:\s+[A-Z][A-Za-z0-9&.-]*(?:['’]s)?)*")
_PERIOD_WORD = re.compile(r"^(FY|Q[1-4]|H[12]|CY)?\d", re.IGNORECASE)  # FY2024, Q3, 2025, ...
_NOT_A_NAME = frozenset(
    {
        # question and sentence words
        "why", "what", "how", "when", "where", "which", "who", "whose", "if", "did", "does",
        "do", "was", "were", "is", "are", "will", "would", "could", "should", "can", "the",
        "a", "an", "in", "and", "or", "between", "explain",
        # periods and generic finance/tech acronyms that are not companies
        "fy", "q1", "q2", "q3", "q4", "h1", "h2", "ytd", "january", "february", "march",
        "april", "may", "june", "july", "august", "september", "october", "november",
        "december", "us", "usa", "uk", "eu", "sec", "gaap", "ifrs", "eps", "ebitda", "ceo",
        "cfo", "ipo", "ai", "gpu", "gpus", "cpu", "cpus", "esg", "yoy", "r&d", "m&a",
        "10-k", "10-q", "8-k", "u.s.",
    }
)  # fmt: skip


def name_phrases(question: str, asked: Sequence[str] = ()) -> list[str]:
    """Capitalised phrases that may name a company, without question words, generic acronyms
    or the asked entity's own words. Capped at MAX_NAMED."""
    skip = {w.lower() for a in asked if a for w in re.split(r"[\s,.]+", a) if w}
    out: list[str] = []
    for m in _PHRASE.finditer(question):
        words = [re.sub(r"['’]s$", "", w) for w in m.group(0).split()]
        while words and words[0].lower() in _NOT_A_NAME:
            words.pop(0)
        words = [w for w in words if w.lower() not in _NOT_A_NAME and not _PERIOD_WORD.match(w)]
        if not words or all(w.lower() in skip for w in words):
            continue
        if len(words) == 1 and words[0].lower() in GENERIC_NAME_WORDS:
            continue
        phrase = " ".join(words)
        if phrase.lower() not in {p.lower() for p in out}:
            out.append(phrase)
    return out[:MAX_NAMED]


def _match(phrase: str, rows: Sequence[TickerRow]) -> list[TickerRow]:
    p = phrase.lower()
    return [
        r
        for r in rows
        if r.ticker.upper() == phrase.upper()
        or r.title.lower() == p
        or r.title.lower().startswith(p + " ")
        or r.title.lower().startswith(p + ",")
    ]


def named_registrants(
    question: str, asked: Sequence[str], discovery: Discovery, exclude_cik: str | None
) -> tuple[list[TickerRow], list[str]]:
    """SEC registrants the question names, each matched to exactly one CIK; plus notes."""
    found: list[TickerRow] = []
    notes: list[str] = []
    for phrase in name_phrases(question, asked):
        try:
            if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,5}", phrase):
                rows = list(discovery.lookup(tickers=[phrase]))
            else:
                rows = list(discovery.search(phrase))
        except (SourceError, CatalogError, ValueError) as err:
            notes.append(f"{phrase!r}: lookup failed ({err})")
            continue
        hits = _match(phrase, rows)
        ciks = {r.cik10 for r in hits} - {exclude_cik}
        if len(ciks) == 1:
            cik = next(iter(ciks))
            found.append(next(r for r in hits if r.cik10 == cik))
        elif len(ciks) > 1:
            notes.append(f"{phrase!r} matches {len(ciks)} registrants; not offered")
        elif not hits:
            notes.append(f"{phrase!r} is not an SEC registrant name or ticker")
    return found, notes


# --- alternatives ------------------------------------------------------------------------------------


def _metric(request: RunRequest) -> str:
    return request.metric if family(request.metric) != "other" else SUGGESTED_METRICS[0]


def _wait(tier: str) -> str:
    lo, hi, _ = FIRST_RUN_SECONDS[tier]
    return f"{lo:.0f}-{hi:.0f} s"


def _registered(source: EvidenceSource) -> list[Entity]:
    try:
        return list(source.entities())
    except SourceError:
        return []


def _profile(discovery: Discovery, cik10: str, ticker: str) -> Profile | None:
    try:
        found = discovery.registrant(cik10, ticker)
    except (SourceError, CatalogError, ValueError):
        return None
    return found[0] if found else None


def _sic(discovery: Discovery, entity: Entity) -> str:
    if entity.cik in _SIC_CACHE:
        _SIC_CACHE.move_to_end(entity.cik)
        return _SIC_CACHE[entity.cik]
    profile = _profile(discovery, entity.cik, entity.ticker)
    sic = profile.sic if profile else ""
    if profile is not None:
        _SIC_CACHE[entity.cik] = sic
        while len(_SIC_CACHE) > _SIC_CACHE_SIZE:
            _SIC_CACHE.popitem(last=False)
    return sic


def _fiscal_year(profile: Profile) -> tuple[str, str, str] | None:
    """(start, end, filed) of the latest fiscal year between two 10-K report dates."""
    reports = sorted(
        {
            (f.report_date, f.filing_date)
            for f in profile.filings
            if f.form == "10-K" and f.report_date
        },
        reverse=True,
    )
    ends = sorted({r for r, _ in reports}, reverse=True)
    if len(ends) < 2:
        return None
    end, prev = date.fromisoformat(ends[0]), date.fromisoformat(ends[1])
    start = prev + timedelta(days=1)
    if not 350 <= (end - start).days <= 380:
        return None
    filed = next(f for r, f in reports if r == ends[0])
    return start.isoformat(), end.isoformat(), filed


def _alternative(
    basis: str,
    entity: Entity,
    registered: bool,
    request: RunRequest,
    source: EvidenceSource,
    discovery: Discovery,
    sample: bool,
) -> Alternative | None:
    metric = _metric(request)
    options = RunOptions(hitl_mode="auto", sample_mode=request.options.sample_mode)
    if registered:
        s = latest_change(entity, source, metric, asker=request.asker, sample_mode=None)
        if s is None:
            return None
        req = s.request.model_copy(update={"options": options})
        return Alternative(
            basis=basis,
            entity=EntityRef(
                ticker=entity.ticker.upper(), cik=entity.cik, name=entity.name or None
            ),
            registered=True,
            checks=s.checks,
            request=req,
        )
    if sample:
        return None  # the offline slice cannot register anything
    profile = _profile(discovery, entity.cik, entity.ticker)
    fy = _fiscal_year(profile) if profile else None
    if fy is None:
        return None
    start, end, filed = fy
    name = entity.name or (profile.name if profile else "")
    who = entity.ticker.upper()
    label = {"revenue": "revenue", "income": "net income"}.get(family(metric), metric)
    question = f"Why did {who}'s {label} change in FY{end[:4]}?"
    req = RunRequest(
        question=question,
        entity={"ticker": who, "cik": entity.cik, "name": name or None},
        metric=metric,
        period={"start": start, "end": end},
        compare={"baseline": "prior fiscal year, as reported in the same annual filing"},
        asker=request.asker,
        options=options,
    )
    return Alternative(
        basis=basis,
        entity=EntityRef(ticker=who, cik=entity.cik, name=name or None),
        registered=False,
        checks=[
            f"SEC registrant: {name} (CIK {entity.cik}) in sec_tickers",
            f"fiscal year {start}..{end} from its 10-K filed {filed} (sec_submissions)",
            f"not registered yet: the run registers it first (expected first run {_wait('searchable')})",
            f"{metric} is checked when the run frames the question",
        ],
        request=req,
    )


def _entity_of(row: TickerRow) -> Entity | None:
    try:
        return Entity(object_ticker(row.ticker), row.cik10, row.title)
    except ValueError:
        return None


def alternatives(
    request: RunRequest,
    asked: Entity | None,
    *,
    source: EvidenceSource,
    discovery: Discovery,
    sample: bool,
    tracer: Any,
) -> tuple[list[Alternative], list[str]]:
    registered = {e.cik: e for e in _registered(source)}
    out: list[Alternative] = []
    notes: list[str] = []
    seen: set[str] = set()

    def add(basis: str, entity: Entity) -> None:
        if len(out) >= MAX_ALTERNATIVES or entity.cik in seen:
            return
        seen.add(entity.cik)
        known = registered.get(entity.cik)
        alt = _alternative(
            basis,
            known or entity,
            known is not None,
            request,
            source,
            discovery,
            sample,
        )
        tracer.emit(
            agent=AGENT,
            action="alternative",
            status="success" if alt else "rejected",
            basis=basis,
            entity={"ticker": entity.ticker, "cik": entity.cik},
            registered=known is not None,
            output_summary=alt.request.question if alt else "no validated question for it",
        )
        if alt is not None:
            out.append(alt)

    e = request.entity
    words = [e.ticker or "", e.name or "", asked.name if asked else ""]
    named, notes = named_registrants(
        request.question, words, discovery, asked.cik if asked else None
    )
    for row in named:
        ent = _entity_of(row)
        if ent is not None:
            add("named in the question", ent)
    if asked is not None:
        add("same entity, past period", asked)
        sic = _sic(discovery, asked)
        if sic:
            probes = [x for x in registered.values() if x.cik != asked.cik][:MAX_PEER_PROBES]
            for peer in probes:
                if len(out) >= MAX_ALTERNATIVES:
                    break
                if _sic(discovery, peer) == sic:
                    add(f"same SIC peer ({sic})", peer)
        else:
            notes.append(f"no SIC code for {asked.name or asked.ticker.upper()}: no peers")
    else:
        notes.append(
            "no SEC data for the asked company, so no industry (SIC) code to find peers: "
            "alternatives come only from SEC registrants named in the question (a model could "
            "propose the industry later; the agent would still validate each alternative)"
        )
    return out, notes


# --- the check --------------------------------------------------------------------------------------


def check(
    request: RunRequest,
    resolution: Any,
    *,
    sample: bool,
    source: EvidenceSource,
    discovery: Callable[[], Discovery],
    tracer: Any,
    today: date | None = None,
) -> ScopeCheck:
    """In scope, or the reasons and runnable alternatives. Never raises."""
    start = time.perf_counter()
    today = today or date.today()
    reasons: list[ScopeReason] = []
    cue = forward_cue(request.question)
    if cue is None and request.period.end > today:
        cue = f"period ends {request.period.end.isoformat()}, after today"
    if cue is not None:
        reasons.append(
            ScopeReason(
                type="forecast",
                cue=cue,
                message=f"This asks about the future or a hypothetical ({cue!r}). Tiresias "
                "explains changes already reported in SEC filings; it does not forecast or "
                "answer what-ifs.",
            )
        )
    evidence = getattr(resolution, "evidence", None) or []
    last = evidence[-1] if evidence else None
    if (
        not sample
        and getattr(resolution, "error_type", None) == "EntityNotFound"
        and last is not None
        and last.matched == 0
        and not last.degraded
    ):
        e = request.entity
        asked = e.name or e.ticker or e.cik
        reasons.append(
            ScopeReason(
                type="not-sec-registrant",
                message=f"{asked} is not an SEC registrant ({resolution.reason}, read through "
                "Zetaris); Tiresias reads SEC filings only.",
            )
        )
    elif sample and getattr(resolution, "error_type", None) == "EntityNotInSample":
        reasons.append(ScopeReason(type="not-in-sample", message=resolution.reason))
    if not reasons:
        tracer.emit(
            agent=AGENT,
            action="check",
            status="success",
            input_summary=request.question[:200],
            output_summary="in scope: a why-question about a filed change",
            in_scope=True,
            duration_ms=round((time.perf_counter() - start) * 1000),
        )
        return ScopeCheck(in_scope=True)
    asked = resolution.entity if getattr(resolution, "ok", False) else None
    try:
        alts, notes = alternatives(
            request, asked, source=source, discovery=discovery(), sample=sample, tracer=tracer
        )
    except (SourceError, CatalogError, ValueError) as err:
        alts, notes = [], [f"alternatives unavailable: {err}"]
    result = ScopeCheck(
        in_scope=False, reasons=reasons, alternatives=alts, note="; ".join(notes) or None
    )
    tracer.emit(
        agent=AGENT,
        action="check",
        status="rejected",
        input_summary=request.question[:200],
        output_summary="; ".join(r.message for r in reasons)[:400],
        in_scope=False,
        reasons=[r.type for r in reasons],
        cue=cue,
        alternatives=[a.request.question for a in alts],
        note=result.note,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return result
