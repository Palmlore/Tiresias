"""Request and result models for the agent API (contracts/openapi/agent.yaml)."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["analyst", "auditor", "board"]
HitlMode = Literal["auto", "interactive", "scripted"]
MemoryMode = Literal["warm", "cold"]
ModelLanesMode = Literal["auto", "on", "off"]
Depth = Literal["quick", "thorough", "deep"]
ModelLane = Literal["hypothesiser", "critic", "reporter"]
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
    name: str | None = Field(
        default=None,
        description="Registrant name, used only when neither ticker nor CIK is given (live mode "
        "resolves it through the Zetaris sec_tickers view)",
    )


class Period(BaseModel):
    start: date
    end: date


class RunOptions(BaseModel):
    hitl_mode: HitlMode | None = None
    memory: MemoryMode | None = Field(
        default=None,
        description="warm: recall lessons, judge them, store a new one (H-MEM, ADR-0005); cold: "
        "no memory call. Default MEMORY_MODE (warm)",
    )
    sample_mode: bool | None = None
    decisions: dict[str, Any] | None = Field(default=None, description="Scripted gate decisions")
    depth: Depth | None = Field(
        default=None,
        description="Investigation depth (ADR-0011): quick (default) = rules only; thorough = "
        "rules + the critic's deep-lane second opinion; deep = every model lane (hypothesiser, "
        "critic second opinion, reporter wording). Sample mode makes no model call at any depth; "
        "MODEL_LANES=off forces quick",
    )
    model_lanes: ModelLanesMode | None = Field(
        default=None,
        description="Deprecated (ADR-0011): use depth. on = deep, off = quick, auto = as if "
        "absent; ignored when depth is set",
        json_schema_extra={"deprecated": True},
    )


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


class ModelOpinion(BaseModel):
    """The deep lane's second opinion on a link (ADR-0010). Advisory: the rule critic's verdict
    stays the decision; a disagreement goes to the critic_escalation gate."""

    lane: str
    requested_model: str
    routed_model: str | None = Field(
        default=None, description="Model that answered, from the Nightshift receipt"
    )
    provider: str | None = None
    receipt_id: str | None = None
    decision: Literal["accept", "prune"]
    label: Literal["breakdown", "causal", "background"]
    rationale: str | None = Field(
        default=None,
        description="One paragraph from the model; dropped (None) when it quotes a number that is "
        "not in the measurement it was shown",
    )
    grounded: bool = Field(description="Every number in the rationale is in the measurement")
    agrees: bool = Field(description="Same decision (and label, for an accepted link) as the rules")
    rule_decision: str = Field(description="The rule critic's decision when the model was asked")
    gate_choice: str | None = Field(
        default=None, description="critic_escalation gate choice when the two disagreed"
    )


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
    proposed_by: Literal["rules", "model"] = Field(
        default="rules", description="Who proposed the hypothesis (the model lane, ADR-0010)"
    )
    model_opinion: ModelOpinion | None = Field(
        default=None, description="The model's second opinion on the critic's verdict (live)"
    )


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


ScopeReasonType = Literal["forecast", "not-sec-registrant", "not-in-sample"]
EntityTier = Literal["ready", "searchable"]


class ScopeReason(BaseModel):
    """Why a question is outside what Tiresias answers (a why-question about a filed change of
    an SEC registrant), in plain words."""

    type: ScopeReasonType
    message: str
    cue: str | None = Field(default=None, description="The words that triggered a forecast rule")


class Suggestion(BaseModel):
    """A question derived from the entity's own filed data, validated by framing it (the
    framer measured a non-trivial effect). ``request`` can be posted to /run as-is."""

    kind: EffectKind
    basis: str = Field(
        description="restatement | largest change | lowest value | highest value | latest change"
    )
    question: str
    metric: str
    period: Period
    baseline: Observation
    target: Observation
    delta: float
    relative_change: float | None = None
    checks: list[str] = Field(default_factory=list, description="What was validated, in words")
    request: RunRequest


class Alternative(BaseModel):
    """A runnable question offered instead of an out-of-scope one."""

    basis: str = Field(
        description="named in the question | same entity, past period | same SIC peer (<sic>)"
    )
    entity: Entity
    registered: bool
    checks: list[str] = Field(default_factory=list)
    request: RunRequest


class ScopeCheck(BaseModel):
    """Set on a RunResult when the scope check stopped the run before Loop A."""

    in_scope: bool
    reasons: list[ScopeReason] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    note: str | None = None


class ExpectedWait(BaseModel):
    """Expected wall time of a first full investigation (measured, see ``basis``)."""

    min_seconds: float
    max_seconds: float
    basis: str


class DepthWaits(BaseModel):
    """Expected wall time of a first full investigation at each depth (ADR-0011)."""

    quick: ExpectedWait
    thorough: ExpectedWait
    deep: ExpectedWait


class RunDepth(BaseModel):
    """The investigation depth of a run (ADR-0011): what was asked, what runs, how long to
    expect. Set on the POST /runs 202 body (before the run) and on RunResult.depth."""

    requested: Depth = Field(description="The depth asked for (quick when nothing asked)")
    requested_by: Literal["options.depth", "options.model_lanes", "MODEL_LANES", "default"] = Field(
        description="Where the requested depth came from (see precedence in ADR-0011)"
    )
    effective: Depth = Field(
        description="The depth that runs: quick in sample mode, with MODEL_LANES=off, with "
        "MAX_MODEL_CALLS_PER_RUN=0 or without a configured Nightshift Runtime"
    )
    lanes: list[ModelLane] = Field(
        default_factory=list, description="Model lanes on for the run (empty at quick)"
    )
    degraded: bool = Field(
        default=False,
        description="Model lanes were asked for but could not run (Nightshift not configured, "
        "the call cap is 0, or Nightshift became unavailable during the run)",
    )
    note: str = Field(description="Why the effective depth is what it is, in plain words")
    expected_wait: ExpectedWait


class EntityInfo(BaseModel):
    cik: str
    ticker: str
    name: str = ""
    tier: EntityTier
    metrics: list[str] = Field(
        default_factory=list, description="Supported metrics with registered data (ready only)"
    )
    expected_first_run: ExpectedWait = Field(description="At the default depth (quick, rules only)")
    expected_first_run_by_depth: DepthWaits = Field(
        description="At each depth, so a UI can warn before the run starts (ADR-0011)"
    )


class EntityList(BaseModel):
    mode: Literal["sample", "live"]
    query: str | None = None
    entities: list[EntityInfo] = Field(default_factory=list)
    searchable: bool = Field(description="Whether the searchable tier (sec_tickers) was searched")
    note: str | None = None
    degraded: bool = False


class SuggestionList(BaseModel):
    mode: Literal["sample", "live"]
    entity: EntityInfo
    suggestions: list[Suggestion] = Field(default_factory=list)
    considered: int = Field(default=0, description="Candidate questions derived from the data")
    rejected: int = Field(default=0, description="Candidates that failed validation")
    note: str | None = None


class RecalledLesson(BaseModel):
    """A lesson H-MEM returned for this run, and what the critic made of it."""

    memory_id: str
    content: str
    relevance: float | None = Field(default=None, description="H-MEM recall score")
    confidence: float
    status: Literal["supported", "rejected", "not-tested", "ignored"] = Field(
        description="supported: its step held when tested; rejected: the critic pruned its step "
        "(negative feedback sent); not-tested: none of its steps came up; ignored: no path tags"
    )
    node_ids: list[str] = Field(default_factory=list, description="Nodes that tested its steps")
    reason: str | None = None


class SkippedCandidate(BaseModel):
    """A candidate not tested because a recalled lesson's step held first (warm runs only)."""

    node_id: str
    rule: str
    variable: str
    claim: str
    memory_id: str | None = None


class MemoryFeedback(BaseModel):
    memory_id: str
    kind: Literal["helpful", "not_helpful", "wrong"]
    applied: bool = Field(description="Cognition confirmed the confidence update")
    reason: str


class StoredLesson(BaseModel):
    """The write-back: the lesson this run distilled and what the memory gate decided."""

    stored: bool
    memory_id: str | None = Field(
        default=None, description="The new lesson, or the existing one with the same path"
    )
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    gate_choice: Literal["store", "discard"] | None = None
    decided_by: str | None = None
    reason: str


class RunMemory(BaseModel):
    """H-MEM use of one run (ADR-0005): lessons recalled, judged, fed back and stored."""

    mode: MemoryMode
    available: bool | None = Field(
        default=None, description="Cognition answered the recall (None: cold, or not reached)"
    )
    namespace: str | None = None
    query: str | None = Field(default=None, description="The recall query (generic, no entity)")
    recalled: list[RecalledLesson] = Field(default_factory=list)
    rejected: list[str] = Field(
        default_factory=list, description="Memory ids the critic rejected for this run"
    )
    skipped: list[SkippedCandidate] = Field(default_factory=list)
    fallback: bool = Field(
        default=False,
        description="A lesson's shortcut did not reach a cause, so Loop A ran again in full",
    )
    feedback: list[MemoryFeedback] = Field(default_factory=list)
    stored: StoredLesson | None = None
    note: str | None = None


class RunResult(BaseModel):
    """Leads with the decision (ADR-0009): end state and recommended action, then the evidence."""

    end_state: EndState | None = None
    action: str | None = Field(default=None, description="Recommended action for the asker")
    action_source: Literal["template", "model"] = Field(
        default="template",
        description="template: the rule template; model: drafted by the fast lane from the "
        "template and validated (no number or name absent from it)",
    )
    action_template: str | None = Field(
        default=None, description="The template action, set when the model's wording replaced it"
    )
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
    depth: RunDepth | None = Field(
        default=None,
        description="Investigation depth asked and run, the model lanes on and the expected wait "
        "(ADR-0011); stats.model_calls counts the calls made",
    )
    trace_file: str | None = None
    sample_mode: bool | None = None
    error: ErrorDetail | None = None
    scope: ScopeCheck | None = Field(
        default=None,
        description="Set when the scope check stopped the run before Loop A (end state "
        "evidence-frontier): the reasons and runnable alternatives",
    )
    memory: RunMemory | None = Field(
        default=None,
        description="H-MEM use (ADR-0005): mode, lessons recalled and the critic's call on each, "
        "candidates skipped, feedback sent and the lesson stored (None for Loop A only runs)",
    )


class ErrorResponse(BaseModel):
    run_id: str | None = None
    status: Literal["error"] = "error"
    error: ErrorDetail


class PurgeRequest(BaseModel):
    namespace: str | None = Field(default=None, description="Omit to purge every namespace")
    requested_by: str | None = Field(default=None, max_length=100)


class PurgeResult(BaseModel):
    status: Literal["purged"] = "purged"
    purged: int = Field(description="Lessons the memory service confirmed deleted")
    namespace: str | None = Field(default=None, description="None: every namespace")
    requested_by: str
