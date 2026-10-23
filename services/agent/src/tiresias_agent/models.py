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


class RunStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    hops: int = 0
    queries: int = 0
    sources_onboarded: int = 0
    model_calls: int = 0
    tokens: int = 0
    seconds: float = 0.0


class RunResult(BaseModel):
    run_id: str
    parent_run_id: str | None = None
    parent_node_id: str | None = None
    status: RunStatus
    end_state: EndState | None = None
    root_cause: str | None = None
    action: str | None = Field(default=None, description="Recommended action for the asker")
    residual: float | None = Field(default=None, description="Share of the effect left unexplained")
    why_tree: list[Link] = Field(default_factory=list)
    stats: RunStats = Field(default_factory=RunStats)
    trace_file: str | None = None
    sample_mode: bool | None = None


class ErrorDetail(BaseModel):
    type: str
    message: str
    recoverable: bool = False


class ErrorResponse(BaseModel):
    run_id: str | None = None
    status: Literal["error"] = "error"
    error: ErrorDetail
