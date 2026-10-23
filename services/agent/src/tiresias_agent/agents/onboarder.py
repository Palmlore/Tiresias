"""Onboarder (Loop B): registers the source the scout picked, behind the source gate (ADR-0006).

Order: source cap (MAX_NEW_SOURCES_PER_RUN) -> dry run (render every statement from the
zetaris/sql templates, nothing executed) -> source gate -> registration, one statement at a time.

- Live: ``adapters.zetaris.bootstrap`` for the one entity and only the template's raw table (and
  the concept views for the variable and its aliases), then the evidence source forgets its cached
  view list. An outage (recoverable SourceError) degrades the registration to the sample slice,
  traced ``degraded``; a non-recoverable failure (bad SQL, auth) fails the registration and is
  never served from the sample.
- Sample: no Zetaris call. "Registration" makes the entity's slice in data/sample/ visible to the
  Loop B analyst, and the trace says so (``registration: sample``) with the SQL that live mode
  would have run.

The ``Registry`` is what makes registration matter: the Loop B analyst queries through it, and it
refuses any entity other than the asked one until that entity is registered.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..adapters.zetaris import (
    CATALOG_TABLES,
    DEFAULT_FORMS,
    Entity,
    EvidenceSource,
    SourceError,
    ZetarisClient,
    bootstrap,
    bootstrap_plan,
    sample_dir,
    ticker_of,
)
from ..gates import Gate, GateRequest
from ..models import EvidenceItem, InformationNeed
from .source_scout import ScoutResult

AGENT = "onboarder"
GATE = "source"
OPTIONS = ("register", "deny")
SAMPLE_USER_AGENT = "sample-mode dry-run"
USER_AGENT_ENV = "SEC_USER_AGENT"
_DONE = {"created", "exists", "cached", "replaced"}

Status = Literal["registered", "degraded", "denied", "capped", "failed"]


class Registry:
    """The entities this run may query: the asked one, plus each one the onboarder registered."""

    def __init__(self, source: EvidenceSource, asked: str) -> None:
        self.source = source
        self.name = source.name
        self.allowed = {ticker_of(asked)}
        self.onboarded: list[Entity] = []

    def add(self, entity: Entity) -> None:
        self.allowed.add(ticker_of(entity.ticker))
        self.onboarded.append(entity)

    def _check(self, entity: str) -> str:
        ticker = ticker_of(entity)
        if ticker not in self.allowed:
            raise SourceError(f"{ticker} is not a registered source in this run", recoverable=False)
        return ticker

    def entities(self):
        return self.source.entities()

    def concept_series(self, entity: str, concept: str):
        return self.source.concept_series(self._check(entity), concept)

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS):
        return self.source.filings(self._check(entity), forms)


@dataclass
class Registration:
    status: Status
    reason: str
    entity: Entity | None = None
    template: str | None = None
    mode: str | None = None  # "zetaris" | "sample" | None (not attempted)
    evidence: list[EvidenceItem] = field(default_factory=list)
    gate_decided: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"registered", "degraded"}


def _mask(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret and secret != SAMPLE_USER_AGENT else text


def sample_slice(entity: Entity, data_dir: Path | None = None) -> Path | None:
    """The sample file that holds the entity's companyfacts, if the slice has it for that CIK."""
    path = Path(data_dir or sample_dir()) / f"{ticker_of(entity.ticker)}_companyfacts.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return path if f"{int(data.get('cik', 0)):010d}" == entity.cik else None
    except (TypeError, ValueError):
        return None


def onboard(
    scout: ScoutResult,
    need: InformationNeed,
    *,
    gate: Gate,
    registry: Registry,
    cap: int,
    sample: bool,
    tracer: Any,
    client: ZetarisClient | None = None,
    data_dir: Path | None = None,
    sec_user_agent: str | None = None,
) -> Registration:
    entity, template = scout.entity, scout.template
    if entity is None or template is None:
        return Registration("failed", "nothing resolved to register")
    kind = CATALOG_TABLES.get(template.id)
    base = {"entity": entity, "template": template.id}
    if kind is None:
        return _done(tracer, need, Registration("failed", f"{template.id} not registrable", **base))
    used = len(registry.onboarded)
    if used >= cap:
        return _done(
            tracer,
            need,
            Registration(
                "capped",
                f"MAX_NEW_SOURCES_PER_RUN={cap} reached ({used} onboarded); not registering "
                f"{template.id} for {entity.name}",
                **base,
            ),
        )

    # Dry run: render every statement, execute none.
    ua = sec_user_agent if sec_user_agent is not None else os.getenv(USER_AGENT_ENV, "")
    if sample and not ua:
        ua = SAMPLE_USER_AGENT
    aliases = [need.variable, *need.variable_aliases]
    try:
        plan = bootstrap_plan(ua, [entity], aliases, tables=(kind,))
    except (ValueError, KeyError, OSError) as err:
        return _done(
            tracer, need, Registration("failed", f"dry run failed: {_mask(str(err), ua)}", **base)
        )
    table_sql = next(_mask(sql, ua) for step, _, sql, _ in plan if step.endswith("_table"))
    plan_item = EvidenceItem(
        sql=table_sql,
        filter=f"dry run: {len(plan)} statements from zetaris/sql for catalog {template.id}",
        source="zetaris" if not sample else "sample",
        row_count=len(plan),
        matched=len(plan),
        rows=[{"step": step, "object": obj} for step, obj, _, _ in plan][:10],
        note=None if not sample else "sample mode: statements rendered, not run",
    )
    tracer.emit(
        agent=AGENT,
        action="dry_run",
        node_id=need.node_id,
        target_agent="gate",
        input_summary=f"{template.id} for {entity.name} (CIK {entity.cik})",
        output_summary=f"{len(plan)} statements: "
        + ", ".join(f"{step} {obj}" for step, obj, _, _ in plan)[:400],
        sql=table_sql,
        rows=len(plan),
        template=template.id,
    )

    decision = gate.decide(
        GateRequest(
            gate=GATE,
            question=f"Register catalog template {template.id} for {entity.name} "
            f"(CIK {entity.cik}) as {len(plan)} statements? ({used}/{cap} sources used)",
            options=OPTIONS,
            proposal="register",
            node_id=need.node_id,
            requested_by=AGENT,
            context={"template": template.id, "cik": entity.cik, "used": used, "cap": cap},
        )
    )
    base["gate_decided"] = True
    if decision.choice != "register":
        return _done(
            tracer,
            need,
            Registration(
                "denied",
                f"source gate ({decision.mode}, {decision.decided_by}) denied registering "
                f"{template.id} for {entity.name}",
                evidence=[plan_item],
                **base,
            ),
        )

    if sample:
        return _done(tracer, need, _sample(entity, plan_item, base, data_dir, registry, False))
    if client is None:
        return _done(
            tracer,
            need,
            Registration(
                "failed", "live mode without a Zetaris client", evidence=[plan_item], **base
            ),
        )
    start = time.perf_counter()
    report = bootstrap(
        client, sec_user_agent=ua, entities=[entity], concepts=aliases, tables=(kind,)
    )
    table = next(e for e in report if e["step"] == f"{kind}_table")
    steps = [{k: e.get(k) for k in ("step", "object", "status")} for e in report]
    live_item = plan_item.model_copy(
        update={
            "filter": f"registered one statement at a time ({_ms(start)} ms)",
            "rows": steps[:10],
            "note": None,
        }
    )
    if table["status"] in _DONE:
        refresh = getattr(registry.source, "refresh", None)
        if callable(refresh):
            refresh()
        registry.add(entity)
        views = [e for e in report if e["step"] == "concept_view" and e["status"] in _DONE]
        return _done(
            tracer,
            need,
            Registration(
                "registered",
                f"registered {template.id} for {entity.name} in Zetaris "
                f"({table['status']}; {len(views)} concept view(s))",
                mode="zetaris",
                evidence=[live_item],
                **base,
            ),
        )
    if table.get("recoverable"):
        return _done(tracer, need, _sample(entity, live_item, base, data_dir, registry, True))
    return _done(
        tracer,
        need,
        Registration(
            "failed",
            f"Zetaris registration failed (not recoverable, not served from the sample): "
            f"{table.get('error', table['status'])}",
            mode="zetaris",
            evidence=[live_item],
            **base,
        ),
    )


def _sample(
    entity: Entity,
    item: EvidenceItem,
    base: dict[str, Any],
    data_dir: Path | None,
    registry: Registry,
    degraded: bool,
) -> Registration:
    path = sample_slice(entity, data_dir)
    if path is None:
        return Registration(
            "failed",
            f"the sample slice has no companyfacts for {entity.name} (CIK {entity.cik})",
            mode="sample",
            evidence=[item],
            **base,
        )
    registry.add(entity)
    why = (
        "Zetaris unreachable; registered from the sample slice"
        if degraded
        else "sample mode: Zetaris not called"
    )
    return Registration(
        "degraded" if degraded else "registered",
        f"{why}; data/sample/{path.name} made visible to the analyst for {entity.name}",
        mode="sample",
        evidence=[item.model_copy(update={"degraded": degraded, "source": "sample"})],
        **base,
    )


def _done(tracer: Any, need: InformationNeed, reg: Registration) -> Registration:
    status = {
        "registered": "success",
        "degraded": "degraded",
        "denied": "rejected",
        "capped": "rejected",
        "failed": "error",
    }[reg.status]
    tracer.emit(
        agent=AGENT,
        action="register",
        status=status,
        node_id=need.node_id,
        target_agent="analyst" if reg.ok else "reporter",
        output_summary=reg.reason,
        registration=reg.mode,
        outcome=reg.status,
        template=reg.template,
        entity=(
            {"ticker": reg.entity.ticker, "cik": reg.entity.cik, "name": reg.entity.name}
            if reg.entity
            else None
        ),
        sql=reg.evidence[0].sql if reg.evidence else None,
        rows=reg.evidence[0].row_count if reg.evidence else 0,
    )
    return reg


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
