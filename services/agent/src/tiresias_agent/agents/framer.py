"""Framer: turns the question into one measured effect (docs/architecture.md, Agents).

Two kinds of effect, both like-for-like:
- restatement: the same period as reported by two filings (``compare.filing_a`` / ``filing_b``,
  e.g. "10-K filed YYYY-MM-DD"); delta = value in filing B - value in filing A.
- change: the period against the prior period of the same length, both as reported by the
  first annual filing that reports the target period (its comparative column).
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

from ..adapters.zetaris import EvidenceSource, Fact, SourceError, concept_aliases, ticker_of
from ..models import RunRequest
from .common import (
    Frame,
    LoopError,
    evidence_item,
    fact_row,
    latest_for_period,
    observation,
    same_period,
    shift_year,
    value_in,
)
from .knowledge import money, pct

AGENT = "framer"
_FILING_REF = re.compile(
    r"(10-K/A|10-K|10-Q/A|10-Q|8-K/A|8-K)\s+filed\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)
_ANNUAL = ("10-K", "10-K/A")


class FrameFailure(LoopError):
    """The question could not be measured from registered sources (the metric or period is not
    there). The orchestrator turns this into an evidence-frontier for the metric itself."""


def parse_filing_ref(text: Any) -> tuple[str, str] | None:
    """'10-K filed 2020-01-31' -> ('10-K', '2020-01-31'); anything else -> None."""
    if not isinstance(text, str):
        return None
    m = _FILING_REF.search(text)
    return (m.group(1).upper(), m.group(2)) if m else None


def _match_filing(facts: list[Fact], form: str, filed: str) -> Fact | None:
    """The fact filed on that date with that form; else the first filed within 31 days after."""
    exact = [f for f in facts if f.filed == filed and (f.form or "").upper() == form]
    if exact:
        return exact[0]
    later = sorted(
        (f for f in facts if (f.form or "").upper() == form and (f.filed or "") >= filed),
        key=lambda f: f.filed or "",
    )
    if later and _days_between(filed, later[0].filed or filed) <= 31:
        return later[0]
    return None


def _days_between(a: str, b: str) -> int:
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)


def frame(request: RunRequest, source: EvidenceSource, tracer: Any) -> Frame:
    start_t = time.perf_counter()
    try:
        ticker = ticker_of(request.entity.ticker or "")
        aliases = concept_aliases(request.metric)
    except ValueError as err:
        raise LoopError("InvalidInput", str(err)) from err
    start, end = request.period.start.isoformat(), request.period.end.isoformat()
    try:
        series = source.concept_series(ticker, request.metric)
    except SourceError as err:
        raise LoopError("SourceError", str(err), recoverable=err.recoverable) from err
    facts = [f for f in series if same_period(f, start, end)]
    if not facts:
        raise FrameFailure(
            "MetricNotFound",
            f"no {request.metric} value for {ticker} {start}..{end} in the registered sources",
        )

    compare = request.compare or {}
    ref_a, ref_b = (
        parse_filing_ref(compare.get("filing_a")),
        parse_filing_ref(compare.get("filing_b")),
    )
    kind = "change"
    baseline_fact = target_fact = None
    note = None
    if ref_a and ref_b:
        baseline_fact, target_fact = _match_filing(facts, *ref_a), _match_filing(facts, *ref_b)
        if baseline_fact and target_fact and baseline_fact.accn != target_fact.accn:
            kind = "restatement"
        else:
            note = "compare filings not both found for the period; framed as a change instead"
            baseline_fact = target_fact = None
    elif compare.get("filing_a") or compare.get("filing_b"):
        note = "compare filings not recognised (expected '<form> filed YYYY-MM-DD'); framed as a change"
    if kind == "change":
        annual = [f for f in facts if (f.form or "") in _ANNUAL] or facts
        target_fact = min(annual, key=lambda f: f.filed or "")
        prior_start, prior_end = shift_year(start, -1), shift_year(end, -1) or end
        prior = [f for f in series if same_period(f, prior_start, prior_end)]
        target_obs = observation(target_fact)
        baseline_fact = value_in(
            prior, target_obs.model_copy(update={"start": prior_start, "end": prior_end})
        )
        if baseline_fact is None:
            baseline_fact = latest_for_period(prior, prior_start, prior_end)
            note = "; ".join(
                n
                for n in (
                    note,
                    "no comparative in the same filing; baseline is the latest prior value",
                )
                if n
            )
        if baseline_fact is None:
            raise FrameFailure(
                "BaselineNotFound",
                f"no {request.metric} value for {ticker} {prior_start}..{prior_end} to compare with",
            )
    if baseline_fact is None or target_fact is None:  # unreachable; keeps the types honest
        raise FrameFailure("BaselineNotFound", "no baseline or target value")
    if float(target_fact.val) == float(baseline_fact.val):
        raise LoopError(
            "NoEffect",
            f"{request.metric} for {start}..{end} equals its baseline "
            f"({baseline_fact.form} filed {baseline_fact.filed}): nothing to explain",
        )
    rows = [fact_row(f) for f in sorted(facts, key=lambda f: f.filed or "")]
    if kind == "change":
        rows.insert(0, fact_row(baseline_fact))
    ev = evidence_item(series, rows, filter=f"period_start = '{start}' AND period_end = '{end}'")
    if note:
        ev.note = "; ".join(n for n in (ev.note, note) if n)
    result = Frame(
        question=request.question,
        ticker=ticker,
        cik=request.entity.cik,
        metric=request.metric,
        aliases=aliases,
        period_start=start,
        period_end=end,
        kind=kind,
        baseline=observation(baseline_fact),
        target=observation(target_fact),
        asker=request.asker,
        evidence=ev,
    )
    tracer.emit(
        agent=AGENT,
        action="frame",
        node_id="n0",
        target_agent="hypothesiser",
        input_summary=request.question[:200],
        output_summary=summary(result),
        sql=series.sql,
        rows=len(series),
        duration_ms=round((time.perf_counter() - start_t) * 1000),
    )
    return result


def summary(f: Frame) -> str:
    if f.kind == "restatement":
        return (
            f"{f.metric} for {f.period_label} was restated from {money(f.baseline.value)} "
            f"({f.baseline.form} filed {f.baseline.filed}) to {money(f.target.value)} "
            f"({f.target.form} filed {f.target.filed}): {money(f.delta)} "
            f"({pct(f.relative_change)})"
        )
    return (
        f"{f.metric} for {f.period_label} was {money(f.target.value)} against "
        f"{money(f.baseline.value)} for the prior period ({f.target.form} filed "
        f"{f.target.filed}): {money(f.delta)} ({pct(f.relative_change)})"
    )
