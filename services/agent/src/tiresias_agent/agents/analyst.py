"""Analyst: tests one candidate against the evidence source (Zetaris, or its sample twin).

It never reads files: every number comes from ``EvidenceSource`` (adapters/zetaris.py), and
each query's SQL is kept with the rows it returned. It measures the candidate's contribution to
the root effect and, where the data allows, re-queries counterfactually (causal-criteria.md §1):
"reset the candidate to its baseline value: what is left of the effect?"

Scopes (set by the critic on a retry):
- broad: every matching filing event in the entity's history;
- window: only events inside the window in which the effect arose (Frame.window);
- relaxed: a component missing from the target filing is read from the latest filing for
  that period (flagged as not like-for-like).
"""

from __future__ import annotations

import time
from typing import Any

from ..adapters.zetaris import EvidenceSource, SourceError
from .common import (
    Candidate,
    Frame,
    Measurement,
    Scope,
    evidence_item,
    fact_row,
    filing_row,
    latest_for_period,
    value_in,
)
from .knowledge import DISCONTINUED_OPS, DISPOSAL_MAGNITUDE, FILING_EVENTS, family, money

AGENT = "analyst"


def first_scope(candidate: Candidate) -> Scope:
    return "broad" if candidate.rule == "filing_event" else "window"


def measure(
    candidate: Candidate,
    frame: Frame,
    source: EvidenceSource,
    *,
    attempt: int,
    scope: Scope,
    tracer: Any,
) -> Measurement:
    start = time.perf_counter()
    m = Measurement(candidate_id=candidate.node_id, attempt=attempt, scope=scope)
    try:
        if candidate.rule in {"component", "event_concept"}:
            _measure_concept(candidate, frame, source, m)
        elif candidate.rule == "filing_event":
            _measure_filing_event(candidate, frame, source, m)
        elif candidate.rule == "concept_switch":
            _measure_concept_switch(frame, m)
        elif candidate.rule == "comparative_restated":
            _measure_comparative(frame, source, m)
        else:
            m.error = f"no test for rule {candidate.rule!r}"
    except SourceError as err:
        m.error = f"source error: {err}"
    except ValueError as err:  # invalid concept or ticker for a template
        m.error = f"query not possible: {err}"
    tracer.emit(
        agent=AGENT,
        action="measure",
        status="error" if m.error else "success",
        node_id=candidate.node_id,
        target_agent="critic",
        input_summary=f"{candidate.rule}: {candidate.variable} (scope {scope})",
        output_summary=_summary(m),
        sql=m.primary_sql,
        filter=m.primary.filter if m.primary else None,
        rows=m.rows,
        queries=len(m.evidence),
        retry_count=attempt,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return m


def _summary(m: Measurement) -> str:
    if m.error:
        return m.error
    parts = []
    if m.effect is not None:
        parts.append(f"effect {money(m.effect)}")
    if m.counterfactual is not None:
        parts.append(f"counterfactual {money(m.counterfactual)}")
    if m.events or m.events_outside_window:
        parts.append(f"{len(m.events)} event(s) in scope, {m.events_outside_window} outside window")
    parts.extend(m.mechanism)
    if m.missing:
        parts.append(f"missing: {m.missing}")
    return "; ".join(parts) or "no evidence"


def _measure_concept(c: Candidate, frame: Frame, source: EvidenceSource, m: Measurement) -> None:
    concept = c.params["concept"]
    sign = int(c.params.get("sign", 1))
    series = source.concept_series(frame.ticker, concept)
    facts = list(series)
    tgt = value_in(facts, frame.target)
    base = value_in(facts, frame.baseline)
    flt = f"accn IN ('{frame.baseline.accn}', '{frame.target.accn}')"
    if m.scope == "relaxed":
        tgt = tgt or latest_for_period(facts, frame.target.start, frame.target.end)
        base = base or latest_for_period(facts, frame.baseline.start, frame.baseline.end)
        flt = "latest filing per period (not like-for-like)"
    rows = [fact_row(f) for f in (base, tgt) if f is not None]
    m.evidence.append(evidence_item(series, rows, filter=flt))
    if tgt is None or base is None:
        m.missing = f"{concept} for {'target' if tgt is None else 'baseline'} period/filing"
        m.testable = bool(facts) and m.scope != "relaxed"
        return
    m.variable_baseline, m.variable_target = float(base.val), float(tgt.val)
    m.effect = sign * (m.variable_target - m.variable_baseline)
    # Intervention re-query on the returned rows: reset the candidate to its baseline value and
    # let the metric follow (nothing downstream of it is frozen).
    m.counterfactual = frame.target.value - m.effect


def _measure_filing_event(
    c: Candidate, frame: Frame, source: EvidenceSource, m: Measurement
) -> None:
    event = next(fe for fe in FILING_EVENTS if fe.key == c.params["event"])
    filings = source.filings(frame.ticker, event.forms)
    after, until = frame.window()
    matching = [
        f
        for f in filings
        if (f.form in {"10-K/A"} and "10-K/A" in event.forms)
        or any(item in (f.items or "").split(",") for item in event.items)
    ]
    in_window = [f for f in matching if after < f.filing_date <= until]
    items_sql = " OR ".join(f"items LIKE '%{i}%'" for i in event.items)
    if m.scope == "broad" and len(in_window) < len(matching):
        flt = f"({items_sql})"
        m.events = matching
        m.events_outside_window = len(matching) - len(in_window)
        m.evidence.append(evidence_item(filings, [filing_row(f) for f in matching], filter=flt))
        return
    m.scope = "window"  # nothing outside the window: the broad query is already the narrow one
    flt = f"({items_sql}) AND filing_date > '{after}' AND filing_date <= '{until}'"
    m.events = in_window
    m.evidence.append(evidence_item(filings, [filing_row(f) for f in in_window], filter=flt))
    if not in_window or event.key != "disposal":
        if in_window:
            m.missing = "magnitude of the corrected amount"
            m.testable = False
        return
    # Mechanism: a disposal is presented as discontinued operations, restating the period.
    disc = source.concept_series(frame.ticker, DISCONTINUED_OPS)
    d_base, d_tgt = value_in(list(disc), frame.baseline), value_in(list(disc), frame.target)
    m.evidence.append(
        evidence_item(
            disc,
            [fact_row(f) for f in (d_base, d_tgt) if f is not None],
            filter=f"accn IN ('{frame.baseline.accn}', '{frame.target.accn}')",
        )
    )
    if d_base is not None and d_tgt is not None and d_base.val != d_tgt.val:
        m.mechanism.append(
            f"discontinued operations for the period re-presented: {money(d_base.val)} -> "
            f"{money(d_tgt.val)}"
        )
    # Magnitude: what the filer itself reports for the disposed business in the target filing.
    concept = DISPOSAL_MAGNITUDE.get(family(frame.metric))
    if concept is None or concept == frame.metric:
        m.missing = f"{frame.metric} of the disposed business"
        m.testable = False
        return
    mag = source.concept_series(frame.ticker, concept)
    mag_tgt = value_in(list(mag), frame.target) or latest_for_period(
        list(mag), frame.target.start, frame.target.end
    )
    m.evidence.append(
        evidence_item(
            mag,
            [fact_row(mag_tgt)] if mag_tgt else [],
            filter=f"period_start = '{frame.period_start}' AND period_end = '{frame.period_end}'",
        )
    )
    if mag_tgt is None or family(frame.metric) != "revenue":
        m.missing = f"{frame.metric} of the disposed business for {frame.period_label}"
        m.testable = False
        return
    m.variable_target = float(mag_tgt.val)
    m.effect = -float(mag_tgt.val) if frame.kind == "restatement" else None
    if m.effect is not None:
        m.counterfactual = frame.target.value - m.effect


def _measure_concept_switch(frame: Frame, m: Measurement) -> None:
    rows = [
        r for r in frame.evidence.rows if r.get("accn") in {frame.baseline.accn, frame.target.accn}
    ]
    m.evidence.append(
        frame.evidence.model_copy(
            update={
                "filter": f"accn IN ('{frame.baseline.accn}', '{frame.target.accn}')",
                "rows": rows,
                "matched": len(rows),
            }
        )
    )
    if frame.baseline.concept != frame.target.concept:
        m.mechanism.append(f"concept changed: {frame.baseline.concept} -> {frame.target.concept}")
        m.effect = frame.delta
        m.counterfactual = frame.baseline.value


def _measure_comparative(frame: Frame, source: EvidenceSource, m: Measurement) -> None:
    series = source.concept_series(frame.ticker, frame.metric)
    b = frame.baseline
    reports = sorted(
        (f for f in series if f.end == b.end and f.start == b.start),
        key=lambda f: f.filed or "",
    )
    m.evidence.append(
        evidence_item(
            series,
            [fact_row(f) for f in reports],
            filter=f"period_start = '{b.start}' AND period_end = '{b.end}'",
        )
    )
    first = reports[0] if reports else None
    if first is None or first.accn == b.accn or float(first.val) == b.value:
        return
    m.variable_baseline, m.variable_target = float(first.val), b.value
    m.mechanism.append(
        f"prior-period value first reported {money(first.val)} ({first.form} filed "
        f"{first.filed}), {money(b.value)} as the comparative in the target filing"
    )
    # Using the original instead of the comparative would have added this much to the change.
    m.effect = first.val - b.value
