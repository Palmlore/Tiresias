"""Entity resolver: maps the request's entity (ticker, CIK or name) to one SEC registrant before
Loop A, so a question about a company outside the showcase set can be answered (live mode).

1. Registered already? The evidence source's entities (Zetaris: one ``<ticker>_facts_raw``
   table each; sample: the ``data/sample/`` slice), matched on CIK, then ticker, then name. In
   live mode the entity also needs its filings view and a view for the metric (or an alias);
   otherwise it is resolved again and the missing objects are registered.
2. Sample mode stops here: an entity outside the slice ends the run fast, with a reason that
   names what the slice covers and how to run live.
3. Live: discovery through Zetaris (``tiresias.sec_tickers``: by ticker/CIK, else by name).
   Exactly one CIK resolves it; none is ``EntityNotFound``, several is ``EntityAmbiguous`` (the
   reason lists them). Nothing is guessed.

The orchestrator then registers an unregistered entity behind the source gate
(``onboarder.onboard_entity``) and turns any failure into an evidence frontier.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..adapters.catalog import CatalogError, Discovery, TickerRow
from ..adapters.zetaris import (
    Entity,
    EvidenceSource,
    SourceError,
    concept_aliases,
    concept_slug,
    object_ticker,
)
from ..models import EvidenceItem, RunRequest
from .common import ROWS_SHOWN

AGENT = "entity_resolver"
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,11}$")
_CIK = re.compile(r"^\d{1,10}$")
MAX_LISTED = 5
LIVE_HINT = (
    "to ask about any SEC registrant, run live: options.sample_mode=false (CLI --live, or "
    "SAMPLE_MODE=false for the API) with Zetaris configured (ZETARIS_REST_URL, ZETARIS_API_KEY, "
    "SEC_USER_AGENT)"
)


@dataclass
class Resolution:
    entity: Entity | None
    registered: bool
    reason: str
    error_type: str | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)
    candidates: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.entity is not None


def _cik10(text: str | None) -> str | None:
    text = (text or "").strip()
    return f"{int(text):010d}" if text.isdigit() else None


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


def _asked(request: RunRequest) -> str:
    e = request.entity
    parts = [
        f"ticker {e.ticker}" if e.ticker else "",
        f"CIK {e.cik}" if e.cik else "",
        f"name {e.name!r}" if getattr(e, "name", None) else "",
    ]
    return ", ".join(p for p in parts if p) or "no entity"


def _known(request: RunRequest, entities: Sequence[Entity]) -> Entity | None:
    e = request.entity
    cik = _cik10(e.cik)
    if cik:
        hit = next((x for x in entities if x.cik == cik), None)
        if hit:
            return hit
    if e.ticker:
        try:
            t = object_ticker(e.ticker)
        except ValueError:
            t = ""
        hit = next((x for x in entities if x.ticker == t), None)
        if hit and (not cik or hit.cik == cik):
            return hit
    name = (getattr(e, "name", None) or "").strip().lower()
    if name and not e.ticker and not cik:
        hits = [x for x in entities if x.name and name in x.name.lower()]
        if len(hits) == 1:
            return hits[0]
    return None


def _ready(entity: Entity, metric: str, views: set[str] | None) -> bool:
    """Live: the filings view and a view for the metric (or an alias) exist."""
    if views is None:
        return True
    t = entity.ticker
    metric_views = {f"{t}_{concept_slug(c)}" for c in concept_aliases(metric)}
    return f"{t}_filings" in views and bool(metric_views & views)


def resolve(
    request: RunRequest,
    *,
    source: EvidenceSource,
    sample: bool,
    tracer: Any,
    discovery: Callable[[], Discovery] | None = None,
    views: Callable[[], set[str]] | None = None,
) -> Resolution:
    start = time.perf_counter()
    result = _resolve(request, source, sample, discovery, views)
    tracer.emit(
        agent=AGENT,
        action="resolve",
        status="success" if result.ok else "rejected",
        node_id="n0",
        target_agent=(
            "framer"
            if result.ok and result.registered
            else "onboarder"
            if result.ok
            else "reporter"
        ),
        input_summary=_asked(request),
        output_summary=result.reason[:400],
        entity=(
            {"ticker": result.entity.ticker, "cik": result.entity.cik, "name": result.entity.name}
            if result.entity
            else None
        ),
        registered=result.registered,
        error_type=result.error_type,
        candidates=result.candidates[:MAX_LISTED],
        sql=result.evidence[-1].sql if result.evidence else None,
        rows=result.evidence[-1].matched if result.evidence else 0,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return result


def _resolve(
    request: RunRequest,
    source: EvidenceSource,
    sample: bool,
    discovery: Callable[[], Discovery] | None,
    views: Callable[[], set[str]] | None,
) -> Resolution:
    asked = _asked(request)
    e = request.entity
    if not (e.ticker or e.cik or getattr(e, "name", None)):
        return Resolution(None, False, "the request names no entity", "InvalidInput")
    if e.ticker and not _SYMBOL.match(e.ticker.strip()):
        return Resolution(None, False, f"invalid entity ticker: {e.ticker!r}", "InvalidInput")
    if e.cik and not _CIK.match(e.cik.strip()):
        return Resolution(None, False, f"invalid entity CIK: {e.cik!r}", "InvalidInput")
    try:
        listed = source.entities()
    except SourceError as err:
        return Resolution(None, False, f"could not list registered entities: {err}", "SourceError")
    known = _known(request, list(listed))
    if known is not None:
        try:
            ready = _ready(known, request.metric, views() if views and not sample else None)
        except SourceError as err:
            if not err.recoverable:
                return Resolution(
                    None, False, f"could not list registered views: {err}", "SourceError"
                )
            ready = True  # an outage: the evidence source falls back to the sample per query
        except ValueError as err:
            return Resolution(None, False, str(err), "InvalidInput")
        if ready:
            return Resolution(
                known,
                True,
                f"{known.name or known.ticker.upper()} (CIK {known.cik}) is registered "
                f"({listed.source})",
                evidence=[_item(listed, [{"ticker": known.ticker, "cik": known.cik}], asked)],
            )
    if sample:
        covered = ", ".join(
            f"{x.ticker.upper()} ({x.name})" if x.name else x.ticker.upper() for x in listed
        )
        return Resolution(
            None,
            False,
            f"{asked} is not in the offline sample: sample mode only covers "
            f"{covered or 'no entity'} (data/sample/); {LIVE_HINT}",
            "EntityNotInSample",
            [_item(listed, [], f"entity matches {asked}")],
        )
    if discovery is None:
        return Resolution(None, False, f"{asked} is not registered and discovery is unavailable")
    return _discover(request, discovery, known)


def _discover(
    request: RunRequest, discovery: Callable[[], Discovery], known: Entity | None
) -> Resolution:
    e = request.entity
    asked = _asked(request)
    cik = _cik10(e.cik)
    name = (getattr(e, "name", None) or "").strip()
    try:
        disc = discovery()
        if e.ticker or cik:
            found = disc.lookup(tickers=[e.ticker] if e.ticker else [], ciks=[cik] if cik else [])
            how = "ticker/CIK"
        else:
            found = disc.search(name)
            how = "name"
    except (SourceError, CatalogError) as err:
        return Resolution(None, False, f"sec_tickers read failed for {asked}: {err}", "SourceError")
    except ValueError as err:
        return Resolution(None, False, f"invalid entity in the request: {err}", "InvalidInput")
    rows: list[TickerRow] = list(found)
    if e.ticker and cik:
        both = [r for r in rows if r.cik10 == cik and r.ticker.upper() == e.ticker.upper()]
        if not both and rows:
            listed = sorted({f"{r.ticker} (CIK {r.cik10}, {r.title})" for r in rows})
            return Resolution(
                None,
                False,
                f"{asked} disagree in sec_tickers: {'; '.join(listed[:MAX_LISTED])}",
                "EntityAmbiguous",
                [_item(found, _rows(rows), f"{how} = {asked}")],
                _rows(rows),
            )
        rows = both or rows
    by_cik: dict[str, list[TickerRow]] = {}
    for r in rows:
        by_cik.setdefault(r.cik10, []).append(r)
    if name and len(by_cik) > 1:
        exact = {c: rs for c, rs in by_cik.items() if rs[0].title.lower() == name.lower()}
        by_cik = exact or by_cik
    evidence = [_item(found, _rows(rows), f"{how} = {asked}")]
    if not by_cik:
        degraded = (
            " (Zetaris unreachable: searched the offline sample only)" if found.degraded else ""
        )
        return Resolution(
            None,
            False,
            f"no SEC registrant matches {asked} in sec_tickers{degraded}",
            "EntityNotFound",
            evidence,
        )
    if len(by_cik) > 1:
        listed = [f"{rs[0].ticker} (CIK {c}, {rs[0].title})" for c, rs in by_cik.items()]
        return Resolution(
            None,
            False,
            f"{asked} matches {len(by_cik)} registrants; name one by ticker or CIK: "
            + "; ".join(listed[:MAX_LISTED]),
            "EntityAmbiguous",
            evidence,
            _rows(rows),
        )
    cik10, choices = next(iter(by_cik.items()))
    wanted = (e.ticker or "").upper()
    row = next((r for r in choices if r.ticker.upper() == wanted), choices[0])
    try:
        ticker = object_ticker(row.ticker)
    except ValueError:
        return Resolution(
            None,
            False,
            f"{row.title} (CIK {cik10}) has no ticker usable as a Zetaris object name "
            f"({row.ticker!r})",
            "EntityNotFound",
            evidence,
        )
    if known is not None and known.cik != cik10:
        return Resolution(
            None,
            False,
            f"{ticker} is already registered for CIK {known.cik}, not {cik10}",
            "EntityAmbiguous",
            evidence,
        )
    entity = Entity(ticker, cik10, row.title)
    return Resolution(
        entity,
        False,
        f"{row.title} (CIK {cik10}, ticker {row.ticker}) resolved from sec_tickers through "
        f"{found.source}; not registered yet",
        evidence=evidence,
    )


def _rows(rows: Sequence[TickerRow]) -> list[dict[str, str]]:
    return [{"ticker": r.ticker, "cik": r.cik10, "title": r.title} for r in rows]
