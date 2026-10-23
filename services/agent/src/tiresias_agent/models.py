"""Request and result models for the agent API (contracts/openapi/agent.yaml)."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["analyst", "auditor", "board"]
HitlMode = Literal["auto", "interactive", "scripted"]
MemoryMode = Literal["warm", "cold"]
LinkLabel = Literal["breakdown", "causal", "leverage", "background"]
EvidenceStatus = Literal["evidenced", "partially-evidenced", "hypothesis"]
RunStatus = Literal["success", "partial", "error"]
EndState = Literal[
    "actionable", "branched", "cyclic", "evidence-frontier", "external-driver", "budget"
]


class Asker(BaseModel):
    role: Role
    levers: list[str] = Field(default_factory=list)
    horizon: str | None = None


class Entity(BaseModel):
    ticker: str | None = None
    cik: str | None = None


class Period(BaseModel):
    start: date
    end: date


class RunOptions(BaseModel):
    hitl_mode: HitlMode | None = None
    memory: MemoryMode | None = None
    sample_mode: bool | None = None
    decisions: dict[str, Any] | None = Field(default=None, description="Scripted gate decisions")


class RunRequest(BaseModel):
    question: str = Field(min_length=1)
    entity: Entity
    metric: str = Field(min_length=1)
    period: Period
    compare: dict[str, Any] | None = None
    asker: Asker
    options: RunOptions = Field(default_factory=RunOptions)


class BranchRequest(BaseModel):
    node_id: str = Field(min_length=1)
    question: str | None = None
    asker: Asker | None = None


Verdict = Literal["root", "accepted", "pruned", "frontier"]
EffectKind = Literal["restatement", "change"]


class EvidenceItem(BaseModel):
    """One query behind a claim: the SQL that ran (or, in sample mode, would have run), the
    filter the analyst applied to its rows, and the relevant rows (capped)."""

    sql: str
    filter: str | None = Field(default=None, description="Predicate applied to the returned rows")
    source: str
    row_count: int = Field(description="Rows the query returned")
    matched: int = Field(default=0, description="Rows that passed the filter (before the cap)")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="Relevant rows, capped")
    degraded: bool = False
    note: str | None = None


class Link(BaseModel):
    """One node of the why-tree: a labelled link from its parent, with its evidence."""

    node_id: str
    parent_id: str | None = None
    claim: str
    label: LinkLabel
    evidence_status: EvidenceStatus | None = None
    share_explained: float | None = None
    residual: float | None = None
    sql: str | None = None
    rows_ref: str | None = None
    depth: int = 0
    verdict: Verdict | None = Field(default=None, description="What the critic decided")
    rule: str | None = Field(default=None, description="Hypothesiser rule that proposed it")
    reason: str | None = Field(default=None, description="The critic's reason")
    attempts: int = 1
    evidence: list[EvidenceItem] = Field(default_factory=list)


class Observation(BaseModel):
    """One reported value of a concept: which filing reported it, for which period."""

    value: float
    concept: str
    start: str | None = None
    end: str
    form: str | None = None
    filed: str | None = None
    accn: str | None = None


class Effect(BaseModel):
    """What the run explains: target minus baseline, like-for-like."""

    entity: str
    metric: str
    kind: EffectKind = Field(
        description="restatement: same period, two filings; change: two periods"
    )
    period: Period
    baseline: Observation
    target: Observation
    delta: float
    relative_change: float | None = None
    summary: str


class EntityHint(BaseModel):
    """Where the missing evidence probably lives, from the run's own evidence (never a guess
    by name): e.g. the counterparty of a disposal reported in an 8-K."""

    relation: str
    source_ticker: str | None = None
    source_cik: str | None = None
    filing_accession: str | None = None
    filing_date: str | None = None
    filing_items: list[str] = Field(default_factory=list)
    description: str


class InformationNeed(BaseModel):
    """Loop A -> Loop B hand-off: what no registered source can show, and how to test it once
    a source is onboarded (gap analyst -> source scout, docs/architecture.md)."""

    node_id: str
    variable: str
    variable_aliases: list[str] = Field(default_factory=list)
    entity_hint: EntityHint
    period: Period
    join_key: list[str]
    expected_magnitude: float | None = Field(
        default=None, description="The unexplained amount the new evidence should account for"
    )
    test: str = Field(description="The intervention test to run once the source is registered")
    reason: str


class RunStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    hops: int = 0
    queries: int = 0
    sources_onboarded: int = 0
    model_calls: int = 0
    tokens: int = 0
    seconds: float = 0.0


class ErrorDetail(BaseModel):
    type: str
    message: str
    recoverable: bool = False


class RunResult(BaseModel):
    """Leads with the decision (ADR-0009): end state and recommended action, then the evidence."""

    end_state: EndState | None = None
    action: str | None = Field(default=None, description="Recommended action for the asker")
    root_cause: str | None = None
    residual: float | None = Field(default=None, description="Share of the effect left unexplained")
    run_id: str
    parent_run_id: str | None = None
    parent_node_id: str | None = None
    status: RunStatus
    effect: Effect | None = None
    information_need: InformationNeed | None = Field(
        default=None, description="Set when end_state is evidence-frontier (input to Loop B)"
    )
    why_tree: list[Link] = Field(default_factory=list)
    stats: RunStats = Field(default_factory=RunStats)
    trace_file: str | None = None
    sample_mode: bool | None = None
    error: ErrorDetail | None = None


class ErrorResponse(BaseModel):
    run_id: str | None = None
    status: Literal["error"] = "error"
    error: ErrorDetail
