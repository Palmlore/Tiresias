"""Entity directory for a UI: ``GET /entities`` (pick a company, with type-ahead) and
``GET /entities/{cik}/suggestions`` (questions derived from that company's own filed data).

Two tiers:
- **ready**: registered already, so a run starts at once (live: the entity's Zetaris facts table,
  filings view and at least one supported metric view; sample: the ``data/sample/`` slice).
- **searchable** (live only): any SEC registrant in the Zetaris ``tiresias.sec_tickers`` view
  matching ``?q=`` (ticker, CIK or name), and a partly registered entity matching it. A run
  registers it first. Sample mode lists only the slice and says why (it cannot search SEC).

Each entity carries the expected wall time of its first full investigation, from the measured
constants in ``config.FIRST_RUN_SECONDS`` (docs/measurements/live-any-company.md). Every call
emits ``api`` trace lines. Nothing here names an entity; everything comes from the sources.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .adapters.catalog import CatalogError, Discovery, TickerRow
from .adapters.zetaris import (
    Entity,
    EvidenceSource,
    SourceError,
    concept_aliases,
    concept_slug,
    object_ticker,
)
from .agents import suggester
from .agents.knowledge import SUGGESTED_METRICS
from .config import FIRST_RUN_SECONDS
from .models import EntityInfo, EntityList, ExpectedWait, SuggestionList

AGENT = "api"
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MIN_QUERY = 2
NAME_CHUNK = 10  # CIKs per sec_tickers lookup when naming registered entities
_TICKERISH = re.compile(r"^[A-Za-z][A-Za-z0-9.-]{0,9}$")
LIVE_HINT = (
    "run live (SAMPLE_MODE=false, or ?sample_mode=false) with Zetaris configured to search "
    "every SEC registrant through tiresias.sec_tickers"
)


class DirectoryError(Exception):
    """A request the directory refuses, with the HTTP status and the contract's error type."""

    def __init__(self, status: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


def expected_wait(tier: str, sample: bool) -> ExpectedWait:
    lo, hi, basis = FIRST_RUN_SECONDS["sample" if sample else tier]
    return ExpectedWait(min_seconds=lo, max_seconds=hi, basis=basis)


def _views(source: EvidenceSource) -> set[str] | None:
    from .orchestrator import _find  # late import: orchestrator imports the agents

    fn = _find(source, "registered_views")
    if not callable(fn):
        return None
    try:
        return fn()
    except SourceError:
        return None


def _metrics(entity: Entity, source: EvidenceSource, views: set[str] | None) -> list[str]:
    out = []
    for metric in SUGGESTED_METRICS:
        if views is not None:
            if any(f"{entity.ticker}_{concept_slug(c)}" in views for c in concept_aliases(metric)):
                out.append(metric)
            continue
        try:
            if len(source.concept_series(entity.ticker, metric)):
                out.append(metric)
        except (SourceError, ValueError):
            continue
    return out


def _ready_ok(entity: Entity, metrics: list[str], views: set[str] | None) -> bool:
    if not metrics:
        return False
    return views is None or f"{entity.ticker}_filings" in views


def _names(discovery: Discovery | None, entities: Sequence[Entity]) -> dict[str, str]:
    """Names for registered entities that have none (live tables carry only ticker and CIK)."""
    missing = [e.cik for e in entities if not e.name]
    if not missing or discovery is None:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(missing), NAME_CHUNK):
        try:
            rows = discovery.lookup(ciks=missing[i : i + NAME_CHUNK])
        except (SourceError, CatalogError, ValueError):
            return out
        for r in rows:
            out.setdefault(r.cik10, r.title)
    return out


@dataclass
class _Ready:
    entity: Entity
    metrics: list[str]
    ready: bool


def _registered(
    source: EvidenceSource, discovery: Callable[[], Discovery] | None, sample: bool
) -> tuple[list[_Ready], bool]:
    listed = source.entities()
    degraded = bool(getattr(listed, "degraded", False))
    views = None if sample else _views(source)
    names = {} if sample else _names(discovery() if discovery else None, listed)
    out = []
    for e in listed:
        e = Entity(e.ticker, e.cik, e.name or names.get(e.cik, ""))
        metrics = _metrics(e, source, views)
        out.append(_Ready(e, metrics, _ready_ok(e, metrics, views)))
    return out, degraded


def _info(e: Entity, tier: str, sample: bool, metrics: Sequence[str] = ()) -> EntityInfo:
    return EntityInfo(
        cik=e.cik,
        ticker=e.ticker.upper(),
        name=e.name,
        tier=tier,  # type: ignore[arg-type]
        metrics=list(metrics),
        expected_first_run=expected_wait(tier, sample),
    )


def _matches(q: str, e: Entity) -> bool:
    q = q.strip().lower()
    if q.isdigit():
        return int(q) == int(e.cik)
    return q in e.ticker.lower() or q in (e.name or "").lower()


def _search(discovery: Discovery, q: str) -> tuple[list[TickerRow], list[str], bool]:
    rows: list[TickerRow] = []
    notes: list[str] = []
    degraded = False
    text = q.strip()
    calls: list[tuple[str, Callable[[], Any]]] = []
    if text.isdigit():
        calls.append(("cik", lambda: discovery.lookup(ciks=[text])))
    elif _TICKERISH.match(text):
        calls.append(("ticker", lambda: discovery.lookup(tickers=[text])))
    if not text.isdigit():
        calls.append(("name", lambda: discovery.search(text)))
    for how, call in calls:
        try:
            found = call()
        except ValueError as err:
            notes.append(f"{how} search skipped: {err}")
            continue
        except (SourceError, CatalogError) as err:
            notes.append(f"{how} search failed: {err}")
            continue
        degraded = degraded or bool(getattr(found, "degraded", False))
        rows.extend(found)
    return rows, notes, degraded


def list_entities(
    q: str | None,
    *,
    sample: bool,
    source: EvidenceSource,
    discovery: Callable[[], Discovery],
    tracer: Any,
    limit: int = DEFAULT_LIMIT,
) -> EntityList:
    start = time.perf_counter()
    limit = max(1, min(int(limit), MAX_LIMIT))
    query = (q or "").strip() or None
    ready, degraded = _registered(source, discovery, sample)
    # Partly registered entities (e.g. a disposed business Loop B registered with its facts
    # only) are not ready: a run registers the rest first, so they count as searchable and are
    # listed only when the query matches them.
    entities = [
        _info(r.entity, "ready", sample, r.metrics)
        if r.ready
        else _info(r.entity, "searchable", sample)
        for r in ready
        if (r.ready and query is None) or (query is not None and _matches(query, r.entity))
    ]
    notes: list[str] = []
    searched = False
    if sample:
        covered = ", ".join(x.ticker.upper() for x in (r.entity for r in ready)) or "nothing"
        notes.append(
            f"sample mode lists only the offline slice ({covered}, data/sample/); {LIVE_HINT}"
        )
    elif query is None or len(query) < MIN_QUERY:
        notes.append(
            f"pass ?q= ({MIN_QUERY}+ characters: ticker, CIK or name) to search every SEC "
            "registrant (tiresias.sec_tickers)"
        )
    else:
        searched = True
        rows, search_notes, search_degraded = _search(discovery(), query)
        notes += search_notes
        degraded = degraded or search_degraded
        known = {e.cik for e in entities} | {r.entity.cik for r in ready}
        for row in rows:
            if row.cik10 in known:
                continue
            try:
                ticker = object_ticker(row.ticker)
            except ValueError:
                continue
            known.add(row.cik10)
            entities.append(_info(Entity(ticker, row.cik10, row.title), "searchable", sample))
        if search_degraded:
            notes.append("Zetaris unreachable: searched the offline sample only")
    entities = entities[:limit]
    result = EntityList(
        mode="sample" if sample else "live",
        query=query,
        entities=entities,
        searchable=searched,
        note="; ".join(notes) or None,
        degraded=degraded,
    )
    tracer.emit(
        agent=AGENT,
        action="entities",
        status="success",
        input_summary=f"q={query!r}",
        output_summary=f"{sum(e.tier == 'ready' for e in entities)} ready, "
        f"{sum(e.tier == 'searchable' for e in entities)} searchable"
        + (f"; {result.note}" if result.note else ""),
        rows=len(entities),
        searchable=searched,
        degraded=degraded,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return result


def _cik10(cik: str) -> str:
    text = (cik or "").strip()
    if not text.isdigit() or len(text) > 10:
        raise DirectoryError(422, "ValidationError", f"cik must be 1-10 digits, got {cik!r}")
    return f"{int(text):010d}"


def suggestions(
    cik: str,
    *,
    sample: bool,
    source: EvidenceSource,
    discovery: Callable[[], Discovery],
    tracer: Any,
    role: str = "analyst",
    limit: int = suggester.DEFAULT_LIMIT,
) -> SuggestionList:
    """Validated questions about one registered entity; DirectoryError otherwise."""
    start = time.perf_counter()
    cik10 = _cik10(cik)
    ready, _ = _registered(source, discovery, sample)
    hit = next((r for r in ready if r.entity.cik == cik10), None)
    if hit is None or not hit.ready:
        _refuse(cik10, hit, sample=sample, discovery=discovery, tracer=tracer, start=start)
    assert hit is not None
    found = suggester.suggest(
        hit.entity,
        source,
        tracer=tracer,
        asker=suggester.default_asker(role),
        limit=limit,
        sample_mode=sample,
    )
    result = SuggestionList(
        mode="sample" if sample else "live",
        entity=_info(hit.entity, "ready", sample, hit.metrics),
        suggestions=found.suggestions,
        considered=found.considered,
        rejected=found.rejected,
        note=found.note,
    )
    tracer.emit(
        agent=AGENT,
        action="suggestions",
        status="success" if found.suggestions else "rejected",
        input_summary=f"cik={cik10} role={role}",
        output_summary=f"{len(found.suggestions)} suggestions for {hit.entity.ticker.upper()}",
        entity={"ticker": hit.entity.ticker, "cik": cik10},
        rows=len(found.suggestions),
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return result


def _refuse(
    cik10: str,
    hit: _Ready | None,
    *,
    sample: bool,
    discovery: Callable[[], Discovery],
    tracer: Any,
    start: float,
) -> None:
    if sample:
        err = DirectoryError(
            404,
            "EntityNotInSample",
            f"CIK {cik10} is not in the offline sample (data/sample/); {LIVE_HINT}",
        )
    else:
        try:
            rows = list(discovery().lookup(ciks=[cik10]))
        except (SourceError, CatalogError, ValueError) as e:
            rows, why = [], str(e)
        else:
            why = ""
        if hit is not None:
            name = hit.entity.name or hit.entity.ticker.upper()
            err = DirectoryError(
                409,
                "EntityNotRegistered",
                f"{name} (CIK {cik10}) is only partly registered in Zetaris (no filings view or "
                "no supported metric view); ask any question about it (POST /run) to finish the "
                "registration, then call this again",
            )
        elif rows:
            r = rows[0]
            lo, hi, _ = FIRST_RUN_SECONDS["searchable"]
            err = DirectoryError(
                409,
                "EntityNotRegistered",
                f"{r.title} (CIK {cik10}, ticker {r.ticker}) is an SEC registrant but not "
                "registered in Zetaris yet; suggestions come from its own filed data, so ask any "
                f"question about it first (POST /run registers it; expected first run "
                f"{lo:.0f}-{hi:.0f} s), then call this again",
            )
        else:
            err = DirectoryError(
                404,
                "EntityNotFound",
                f"CIK {cik10} is not an SEC registrant in tiresias.sec_tickers"
                + (f" ({why})" if why else ""),
            )
    tracer.emit(
        agent=AGENT,
        action="suggestions",
        status="rejected",
        input_summary=f"cik={cik10}",
        output_summary=str(err),
        error_type=err.error_type,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    raise err
