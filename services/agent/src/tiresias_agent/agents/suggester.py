"""Suggester: questions derived from an entity's own filed data (``GET /entities/{cik}/
suggestions``, and the scope check's alternatives).

Candidates, per supported metric (``knowledge.SUGGESTED_METRICS``), from the entity's annual
(10-K / 10-K/A, 350-380 day) facts only:
- **restatement**: two annual filings that disagree on the same fiscal year; for each year the
  first time the reported value changed (consecutive filings). Most recent restating filing
  first, then the largest difference.
- **largest change**: the year-on-year changes with the largest absolute delta, as the framer
  measures a change (the first annual filing for the year against its own prior-year column).
- **lowest value** / **highest value**: the fiscal year whose value is the lowest / highest of
  every annual value the entity's filings report for the metric (restated values included),
  framed as a change against the prior year; omitted when that year cannot be framed.

Every candidate is a complete ``RunRequest`` and is **validated by the framer itself** on the
same data before it is returned: the entity is registered (the caller passes a registered one),
the metric has values, the framer frames the intended kind of effect, and the relative change is
at least ``MIN_RELATIVE_CHANGE``. A candidate that fails is counted in ``rejected``, never shown.
Wording is neutral ("change", "differ", "rose", "fell"). Nothing here names an entity, a filing
or an amount; every value comes from the evidence source.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..adapters.zetaris import (
    DEFAULT_FORMS,
    Entity,
    Evidence,
    EvidenceSource,
    Fact,
    Filing,
    SourceError,
)
from ..models import Asker, RunOptions, RunRequest, Suggestion
from .common import Frame, LoopError
from .framer import frame as frame_question
from .framer import summary as frame_summary
from .knowledge import DEFAULT_LEVERS, SUGGESTED_METRICS, metric_label, money, pct

AGENT = "suggester"
MIN_RELATIVE_CHANGE = 0.01  # below 1% of the baseline, there is little to explain
ANNUAL_DAYS = (350, 380)
ANNUAL_FORMS = ("10-K", "10-K/A")
MAX_RESTATEMENTS = 4
CHANGES_PER_METRIC = 3
DEFAULT_LIMIT = 12
MAX_LIMIT = 20
CHANGE_COMPARE = {"baseline": "prior fiscal year, as reported in the same annual filing"}


class _Silent:
    """The framer validates many candidates; one summary line per call is traced instead."""

    def emit(self, **_: Any) -> None:
        return None


class CachingSource:
    """Memoises one entity's reads, so validating many candidates queries each series once."""

    def __init__(self, inner: EvidenceSource) -> None:
        self.inner = inner
        self.name = inner.name
        self._series: dict[tuple[str, str], Evidence[Fact]] = {}
        self.queries = 0

    def entities(self) -> Evidence[Entity]:
        return self.inner.entities()

    def concept_series(self, entity: str, concept: str) -> Evidence[Fact]:
        key = (entity.lower(), concept)
        if key not in self._series:
            self.queries += 1
            self._series[key] = self.inner.concept_series(entity, concept)
        return self._series[key]

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS) -> Evidence[Filing]:
        return self.inner.filings(entity, forms)


@dataclass
class SuggestResult:
    suggestions: list[Suggestion] = field(default_factory=list)
    considered: int = 0
    rejected: int = 0
    note: str | None = None


def default_asker(role: str = "analyst") -> Asker:
    return Asker(role=role, levers=list(DEFAULT_LEVERS.get(role, ())))  # type: ignore[arg-type]


def _annual(f: Fact) -> bool:
    if not f.start or (f.form or "") not in ANNUAL_FORMS:
        return False
    days = (date.fromisoformat(f.end) - date.fromisoformat(f.start)).days
    return ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]


def _periods(series: Sequence[Fact]) -> dict[tuple[str, str], list[Fact]]:
    """Annual facts by (start, end); per filing (form, filed) the fact the framer would pick
    (the first in series order), so detection and validation agree."""
    out: dict[tuple[str, str], list[Fact]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for f in series:
        if not _annual(f):
            continue
        key = (f.start or "", f.end, f.form or "", f.filed or "")
        if key in seen:
            continue
        seen.add(key)
        out.setdefault((f.start or "", f.end), []).append(f)
    for facts in out.values():
        facts.sort(key=lambda f: (f.filed or "", f.accn or ""))
    return out


def _fy(iso: str) -> str:
    return f"FY{date.fromisoformat(iso).year}"


def _label(entity: Entity) -> str:
    return (entity.ticker or entity.name or "").upper()


def _request(
    entity: Entity,
    metric: str,
    period: tuple[str, str],
    compare: dict[str, Any],
    asker: Asker,
    sample_mode: bool | None,
) -> RunRequest:
    return RunRequest(
        question="(validated below)",
        entity={"ticker": _label(entity), "cik": entity.cik, "name": entity.name or None},
        metric=metric,
        period={"start": period[0], "end": period[1]},
        compare=compare,
        asker=asker,
        options=RunOptions(hitl_mode="auto", sample_mode=sample_mode),
    )


def _question(entity: Entity, fr: Frame, basis: str) -> str:
    who, label = _label(entity), metric_label(fr.metric)
    b, t = money(fr.baseline.value), money(fr.target.value)
    if fr.kind == "restatement":
        return (
            f"Why does {who}'s reported {label} for {_fy(fr.period_end)} differ between its "
            f"{fr.baseline.form} filed {fr.baseline.filed} ({b}) and its {fr.target.form} filed "
            f"{fr.target.filed} ({t})?"
        )
    prior = f"FY{date.fromisoformat(fr.period_end).year - 1}"
    if basis in {"lowest value", "highest value"}:
        which = basis.split()[0]
        return (
            f"Why was {who}'s {label} {t} in {_fy(fr.period_end)}, the {which} annual value in "
            f"its filings ({b} in {prior})?"
        )
    verb = "rise" if fr.delta > 0 else "fall"
    return f"Why did {who}'s {label} {verb} from {b} in {prior} to {t} in {_fy(fr.period_end)}?"


def _validate(req: RunRequest, kind: str, source: CachingSource) -> tuple[Frame | None, str | None]:
    """Frame the candidate with the framer itself; returns (frame, None) or (None, why)."""
    try:
        fr = frame_question(req, source, _Silent())
    except LoopError as err:
        return None, f"{err.error_type}: {err}"
    if fr.kind != kind:
        return None, f"framed as a {fr.kind}, not a {kind}"
    rel = fr.relative_change
    if (rel is not None and abs(rel) < MIN_RELATIVE_CHANGE) or fr.delta == 0:
        return None, f"change {pct(rel)} is below {MIN_RELATIVE_CHANGE:.0%}"
    return fr, None


def _suggestion(
    entity: Entity, req: RunRequest, fr: Frame, basis: str, source_name: str, n_values: int
) -> Suggestion:
    question = _question(entity, fr, basis)
    checks = [
        f"entity registered ({source_name})",
        f"{fr.metric} present: {n_values} annual values",
        f"framed as a {fr.kind}: {frame_summary(fr)}",
        f"non-trivial: {pct(fr.relative_change)} (threshold {MIN_RELATIVE_CHANGE:.0%})",
    ]
    return Suggestion(
        kind=fr.kind,
        basis=basis,
        question=question,
        metric=fr.metric,
        period={"start": fr.period_start, "end": fr.period_end},
        baseline=fr.baseline,
        target=fr.target,
        delta=fr.delta,
        relative_change=fr.relative_change,
        checks=checks,
        request=req.model_copy(update={"question": question}),
    )


def _restatements(
    entity: Entity,
    metric: str,
    periods: dict[tuple[str, str], list[Fact]],
    source: CachingSource,
    asker: Asker,
    sample_mode: bool | None,
    n_values: int,
    result: SuggestResult,
) -> list[Suggestion]:
    found = []
    for period, facts in periods.items():
        pair = next(
            (
                (a, b)
                for a, b in itertools.pairwise(facts)
                if a.accn != b.accn
                and a.val != 0
                and abs(float(b.val) - float(a.val)) / abs(float(a.val)) >= MIN_RELATIVE_CHANGE
            ),
            None,
        )
        if pair is None:
            continue
        a, b = pair
        compare = {"filing_a": f"{a.form} filed {a.filed}", "filing_b": f"{b.form} filed {b.filed}"}
        req = _request(entity, metric, period, compare, asker, sample_mode)
        result.considered += 1
        fr, _why = _validate(req, "restatement", source)
        if fr is None:
            result.rejected += 1
            continue
        found.append(_suggestion(entity, req, fr, "restatement", source.name, n_values))
    return found


def _changes(
    entity: Entity,
    metric: str,
    periods: dict[tuple[str, str], list[Fact]],
    source: CachingSource,
    asker: Asker,
    sample_mode: bool | None,
    n_values: int,
    result: SuggestResult,
) -> list[tuple[RunRequest, Frame]]:
    framed = []
    for period in sorted(periods, key=lambda p: p[1]):
        req = _request(entity, metric, period, dict(CHANGE_COMPARE), asker, sample_mode)
        result.considered += 1
        fr, _why = _validate(req, "change", source)
        if fr is None:
            result.rejected += 1
            continue
        framed.append((req, fr))
    return framed


def _series(source: CachingSource, entity: Entity, metric: str) -> Evidence[Fact] | None:
    try:
        return source.concept_series(entity.ticker, metric)
    except (SourceError, ValueError):
        return None


def suggest(
    entity: Entity,
    source: EvidenceSource,
    *,
    tracer: Any,
    asker: Asker | None = None,
    metrics: Sequence[str] = SUGGESTED_METRICS,
    limit: int = DEFAULT_LIMIT,
    sample_mode: bool | None = None,
) -> SuggestResult:
    """Validated questions about one **registered** entity, restatements first."""
    start = time.perf_counter()
    cached = source if isinstance(source, CachingSource) else CachingSource(source)
    asker = asker or default_asker()
    limit = max(1, min(int(limit), MAX_LIMIT))
    result = SuggestResult()
    restated: list[Suggestion] = []
    changes: list[Suggestion] = []
    missing = []
    for metric in metrics:
        series = _series(cached, entity, metric)
        periods = _periods(series or [])
        n_values = sum(len(v) for v in periods.values())
        if not periods:
            missing.append(metric)
            continue
        restated += _restatements(
            entity, metric, periods, cached, asker, sample_mode, n_values, result
        )
        framed = _changes(entity, metric, periods, cached, asker, sample_mode, n_values, result)
        picked: dict[str, tuple[str, RunRequest, Frame]] = {}
        for req, fr in sorted(framed, key=lambda x: abs(x[1].delta), reverse=True)[
            :CHANGES_PER_METRIC
        ]:
            picked[fr.period_end] = ("largest change", req, fr)
        # An extreme is claimed only when it is the extreme of every annual value the entity's
        # filings report for the metric (restated ones included), and the year can be framed.
        values = [float(f.val) for facts in periods.values() for f in facts]
        for basis, pick in (("lowest value", min), ("highest value", max)):
            extreme = pick(values)
            hit = next(((r, f) for r, f in framed if f.target.value == extreme), None)
            if hit is not None:
                picked.setdefault(hit[1].period_end, (basis, *hit))
        for basis, req, fr in picked.values():
            changes.append(_suggestion(entity, req, fr, basis, cached.name, n_values))
    restated.sort(key=lambda s: (s.target.filed or "", abs(s.delta)), reverse=True)
    out = [*restated[:MAX_RESTATEMENTS], *changes][:limit]
    result.suggestions = out
    if missing:
        result.note = f"no annual values for {', '.join(missing)} in the registered sources"
    tracer.emit(
        agent=AGENT,
        action="suggest",
        status="success" if out else "rejected",
        input_summary=f"{_label(entity)} (CIK {entity.cik}): {', '.join(metrics)}",
        output_summary=f"{len(out)} validated of {result.considered} candidates "
        f"({result.rejected} failed validation)" + (f"; {result.note}" if result.note else ""),
        entity={"ticker": entity.ticker, "cik": entity.cik},
        considered=result.considered,
        rejected=result.rejected,
        returned=len(out),
        queries=cached.queries,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return result


def latest_change(
    entity: Entity,
    source: EvidenceSource,
    metric: str,
    *,
    asker: Asker,
    sample_mode: bool | None = None,
) -> Suggestion | None:
    """The most recent validated year-on-year change of ``metric`` (the past-period
    replacement for a forward-looking question). Not traced here: the caller traces it."""
    cached = source if isinstance(source, CachingSource) else CachingSource(source)
    periods = _periods(_series(cached, entity, metric) or [])
    n_values = sum(len(v) for v in periods.values())
    for period in sorted(periods, key=lambda p: p[1], reverse=True):
        req = _request(entity, metric, period, dict(CHANGE_COMPARE), asker, sample_mode)
        fr, _why = _validate(req, "change", cached)
        if fr is not None:
            s = _suggestion(entity, req, fr, "latest change", cached.name, n_values)
            return s
    return None
