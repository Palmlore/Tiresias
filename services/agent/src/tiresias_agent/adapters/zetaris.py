"""Zetaris adapter: Lightning SQL over REST, a sample-mode twin, and a fallback (AGENTS.md rule 8).

Live mode runs SQL rendered from the templates in zetaris/sql/. Sample mode reads data/sample/
and reports the SQL it would have run, so traces show the query either way. In live mode a
recoverable SourceError (an outage, a dead engine, or any failure no rule identifies, see "Error
classification") falls back to the sample for that call, traced as "degraded" with its
``cause``; a known fatal error (bad SQL, auth) is raised.

CLI: python -m tiresias_agent.adapters.zetaris {bootstrap,smoke} [--sample]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

import httpx

from ..config import WARM_ENTITIES_DEFAULT, LivenessSettings, WarmSettings, max_cached_facts

AGENT = "zetaris_adapter"
REDACTED = "***"
_REPO_ROOT = Path(__file__).resolve().parents[5]

# Showcase concepts (the same fixed list as the sample trim) and their short view-name slugs.
CONCEPT_SLUGS: dict[str, str] = {
    "Revenues": "revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue_contract",
    "NetIncomeLoss": "net_income",
    "IncomeLossFromContinuingOperations": "income_cont_ops",
    "IncomeLossFromDiscontinuedOperationsNetOfTax": "income_disc_ops",
    "DisposalGroupIncludingDiscontinuedOperationRevenue": "disposal_revenue",
    "GoodwillImpairmentLoss": "goodwill_impairment",
}
# Concepts that filers use interchangeably over time. Queried together; the asked one first.
# IncludingAssessedTax (e.g. excise taxes kept in revenue, as energy filers report it) is not a
# showcase concept (no view for GE/GEHC/GEV); it is registered for an asked entity on demand.
CONCEPT_ALIASES: tuple[tuple[str, ...], ...] = (
    (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
)
DEFAULT_FORMS = ("10-K", "10-K/A", "8-K", "8-K/A")

_TICKER = re.compile(r"^[a-z][a-z0-9]{0,9}$")
_CIK10 = re.compile(r"^\d{10}$")
_CONCEPT = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,127}$")
_SLUG = re.compile(r"^[a-z0-9_]{1,40}$")
_FORM = re.compile(r"^[0-9A-Z][0-9A-Z/-]{0,15}$")
_USER_AGENT = re.compile(r"^[A-Za-z0-9 .@_+-]{3,200}$")
_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


# --- Domain types -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    ticker: str
    cik: str
    name: str = ""


SHOWCASE_ENTITIES: tuple[Entity, ...] = (
    Entity("ge", "0000040545", "GENERAL ELECTRIC CO"),
    Entity("gehc", "0001932393", "GE HealthCare Technologies Inc."),
    Entity("gev", "0001996810", "GE Vernova Inc."),
)


@dataclass(frozen=True)
class Fact:
    """One reported XBRL value. Restated values are separate facts with their own accn/filed."""

    concept: str
    unit: str
    val: int | float
    start: str | None
    end: str
    fy: int | None
    fp: str | None
    form: str | None
    filed: str | None
    accn: str | None
    frame: str | None = None


@dataclass(frozen=True)
class Filing:
    accession_number: str
    filing_date: str
    report_date: str | None
    form: str
    items: str
    primary_document: str | None


T = TypeVar("T")


class Evidence(list[T], Generic[T]):
    """A list of results that also carries the SQL behind it and where it came from."""

    def __init__(
        self,
        items: Iterable[T] = (),
        *,
        sql: str,
        source: str,
        duration_ms: int = 0,
        degraded: bool = False,
        error: str | None = None,
        note: str | None = None,
    ) -> None:
        super().__init__(items)
        self.sql = sql
        self.source = source
        self.duration_ms = duration_ms
        self.degraded = degraded
        self.error = error
        self.note = note


class EvidenceSource(Protocol):
    """What the agents use to read SEC evidence, live or from the sample."""

    name: str

    def entities(self) -> Evidence[Entity]: ...

    def concept_series(self, entity: str, concept: str) -> Evidence[Fact]: ...

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS) -> Evidence[Filing]: ...


class Tracer(Protocol):
    def emit(self, *, agent: str, action: str, status: str = "success", **fields: Any) -> None: ...


class _NullTracer:
    run_id: str | None = None

    def emit(self, *, agent: str, action: str, status: str = "success", **fields: Any) -> None:
        return None


# --- Error classification ---------------------------------------------------------------------
#
# The one place that decides what a failed Zetaris call means (zetaris/README.md, "Error
# handling"). The bundle we run is a single-machine hackathon build whose failure modes cannot be
# enumerated, so the default is the safe one: a failure we have not identified is an outage.
#
#   cause           recoverable  when
#   outage          yes          timeout, connection error, HTTP 5xx
#   dead_engine     yes          DEAD_ENGINE_SIGNATURES in the body, whatever the status or rows
#   auth            no           HTTP 401/403, or AUTH_SIGNATURES in a 4xx body
#   sql             no           SQL_SIGNATURES in a 4xx body (parse/analysis errors: our bug)
#   refused         no           REFUSED_SIGNATURES in a 4xx body ("already exists"; callers
#                                classify it as benign through ``run_sql(expected=)``), or an
#                                unclassified reply the caller's ``expected`` recognises
#   fatal           no           raised before sending (bad placeholder, missing SEC_USER_AGENT)
#                                or a configuration fault a caller found (a table bound to
#                                another CIK)
#   unclassified    yes          anything else: an unseen 4xx body, a non-JSON / empty / HTML /
#                                truncated reply, a 2xx of an unexpected shape, a row that does not
#                                parse, an unexpected exception inside a live read. Served from
#                                the sample like an outage, traced distinctly (``cause:
#                                "unclassified"``, ``http_status``, ``excerpt``), not retried.
#
# Recoverable causes are served from the sample by FallbackSource / FallbackDiscovery (traced
# ``degraded``); non-recoverable ones are raised and never served from the sample.
CAUSES = ("outage", "dead_engine", "auth", "sql", "refused", "fatal", "unclassified")
RECOVERABLE_CAUSES = frozenset({"outage", "dead_engine", "unclassified"})
AUTH_STATUSES = frozenset({401, 403})
# Lower-case fragments; any one in a 4xx reply body selects the cause (checked in this order:
# dead engine, auth, sql, refused).
AUTH_SIGNATURES: tuple[str, ...] = (
    "unauthorized",
    "unauthorised",
    "forbidden",
    "invalid token",
    "access denied",
    "permission denied",
)
SQL_SIGNATURES: tuple[str, ...] = (
    "parseexception",
    "analysisexception",
    "parse_syntax_error",
    "syntax error",
    "field_not_found",
    "table_or_view_not_found",
    "schema_not_found",
    "unresolved_column",
    "unresolved_routine",
    "invalid_extract_base_field_type",
    "datatype_mismatch",
)
REFUSED_SIGNATURES: tuple[str, ...] = ("already exist",)
EXCERPT_CHARS = 160
# Raised before any SQL is sent (an invalid identifier, a missing template parameter, a missing
# template file, raised as TemplateError, a ValueError): our input or configuration, fatal,
# re-raised as is by the fallbacks. Any other exception inside a live read is wrapped as
# ``unclassified`` (rule 8), an OSError from the transport included.
BEFORE_SENDING_ERRORS: tuple[type[Exception], ...] = (ValueError, KeyError)


class TemplateError(ValueError):
    """A SQL template file could not be read: configuration, raised before any SQL is sent."""


class SourceError(Exception):
    """A Zetaris call failed. ``cause`` is one of ``CAUSES`` (see the classification table
    above); ``recoverable`` is True for an outage, a dead engine or an unclassified failure, and
    then the fallbacks serve the sample. ``excerpt`` is a short scrubbed piece of the reply."""

    def __init__(
        self,
        message: str,
        *,
        recoverable: bool,
        status_code: int | None = None,
        retry_count: int = 0,
        detail: str = "",
        dead_engine: bool = False,
        cause: str | None = None,
        excerpt: str = "",
    ) -> None:
        super().__init__(message)
        self.recoverable = recoverable
        self.status_code = status_code
        self.retry_count = retry_count
        self.detail = detail
        self.dead_engine = dead_engine
        if cause is None:
            cause = "dead_engine" if dead_engine else ("outage" if recoverable else "fatal")
        if cause not in CAUSES:
            raise ValueError(f"unknown SourceError cause {cause!r}")
        self.cause = cause
        self.excerpt = (excerpt or detail)[:EXCERPT_CHARS]

    def trace_fields(self) -> dict[str, Any]:
        """The fields a trace line about this failure carries: ``cause``, plus ``http_status``
        and ``excerpt`` when there was a reply."""
        out: dict[str, Any] = {"cause": self.cause}
        if self.status_code is not None:
            out["http_status"] = self.status_code
        if self.excerpt:
            out["excerpt"] = self.excerpt
        return out


def unclassified(message: str, *, status_code: int | None = None, excerpt: str = "") -> SourceError:
    """A failure no rule identifies: recoverable (served from the sample), traced distinctly."""
    return SourceError(
        message,
        recoverable=True,
        status_code=status_code,
        detail=excerpt,
        cause="unclassified",
    )


# A Zetaris reply that shows its Spark engine is dead, whatever the HTTP status (BACKLOG B-24).
# Measured 2026-10-24: after an OutOfMemoryError in Spark's appStatus listener thread the driver
# stopped its SparkContext but the server kept running, and every statement then failed in about
# 0.1 s with HTTP 400 and one of these in the body, until the driver container was restarted.
# They mean an outage, so the call is recoverable and the fallbacks serve the sample, traced
# ``degraded``. Each signature is a tuple of lower-case fragments that must all appear in the
# reply body. This is the only list.
DEAD_ENGINE_SIGNATURES: tuple[tuple[str, ...], ...] = (
    ("cannot call methods on a stopped sparkcontext",),
    ("nullpointerexception", "fetchiterator.fetchnext"),
)
DEAD_ENGINE_HINT = (
    "Zetaris engine is down (SparkContext stopped); restart the driver "
    "(docker restart zetaris-driver-1)"
)


def dead_engine(text: str) -> bool:
    """True when a Zetaris reply body matches one of ``DEAD_ENGINE_SIGNATURES``."""
    body = (text or "").lower()
    return any(all(part in body for part in sig) for sig in DEAD_ENGINE_SIGNATURES)


def _dead_reply(response: httpx.Response) -> bool:
    """True when the first 20 kB of a reply body match a dead-engine signature, rows or not.

    The signatures are Spark driver errors, not text an SEC filing or fact carries, so a reply
    whose rows contain one is still treated as an outage."""
    return dead_engine(response.text[:20_000])


def classify_reply(status: int, body: str) -> str:
    """The cause (one of ``CAUSES``) of a failed reply (HTTP status >= 400) from its status and
    the first 20 kB of its body: a dead engine (any status), 5xx (outage), 401/403 or an auth
    body (auth), a parse/analysis body (sql), "already exists" (refused); anything else is
    ``unclassified``."""
    text = (body or "")[:20_000].lower()
    if dead_engine(text):
        return "dead_engine"
    if status >= 500:
        return "outage"
    if status in AUTH_STATUSES or any(sig in text for sig in AUTH_SIGNATURES):
        return "auth"
    if any(sig in text for sig in SQL_SIGNATURES):
        return "sql"
    if any(sig in text for sig in REFUSED_SIGNATURES):
        return "refused"
    return "unclassified"


# --- SQL templates ----------------------------------------------------------------------------


def sql_dir() -> Path:
    return Path(os.getenv("ZETARIS_SQL_DIR") or _REPO_ROOT / "zetaris" / "sql")


def sample_dir() -> Path:
    return Path(os.getenv("SAMPLE_DATA_DIR") or _REPO_ROOT / "data" / "sample")


def load_template(name: str, directory: Path | None = None) -> str:
    """Template text without comment lines or a trailing semicolon."""
    path = (directory or sql_dir()) / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"SQL template {path}: {exc}") from exc
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";").strip()


def render(template: str, **params: str) -> str:
    """Fill {{name}} placeholders; a missing parameter or a leftover brace is an error."""

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"template parameter {name!r} not given")
        return str(params[name])

    out = _PLACEHOLDER.sub(fill, template)
    if "{{" in out or "}}" in out:
        raise ValueError("unreplaced placeholder in rendered SQL")
    return out


def render_file(name: str, directory: Path | None = None, **params: str) -> str:
    return render(load_template(name, directory), **params)


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def ticker_of(entity: str) -> str:
    return _check(_TICKER, str(entity).strip().lower(), "entity ticker")


def object_ticker(symbol: str) -> str:
    """An SEC ticker as a Zetaris object-name prefix: lower case, letters and digits only
    ("BRK-B" -> "brkb"). Raises ValueError when nothing usable is left."""
    return ticker_of(re.sub(r"[^a-z0-9]", "", str(symbol).strip().lower()))


def concept_slug(concept: str) -> str:
    _check(_CONCEPT, concept, "concept")
    slug = CONCEPT_SLUGS.get(concept) or re.sub(r"(?<!^)(?=[A-Z])", "_", concept).lower()
    return _check(_SLUG, slug[:40], "concept slug")


def concept_aliases(concept: str) -> tuple[str, ...]:
    """The concept itself first, then any interchangeable names (an alias list, not per case)."""
    _check(_CONCEPT, concept, "concept")
    for group in CONCEPT_ALIASES:
        if concept in group:
            return (concept, *(c for c in group if c != concept))
    return (concept,)


def concept_series_sql(ticker: str, concepts: Sequence[str], directory: Path | None = None) -> str:
    select = load_template("30_concept_series.sql", directory)
    parts = [render(select, ticker=ticker, slug=concept_slug(c)) for c in concepts]
    return "\nUNION ALL\n".join(parts) + "\n" + load_template("39_order_facts.sql", directory)


def filings_sql(ticker: str, forms: Sequence[str], directory: Path | None = None) -> str:
    quoted = ", ".join(f"'{_check(_FORM, f, 'form')}'" for f in forms)
    if not quoted:
        raise ValueError("at least one form is required")
    return render_file("31_filings.sql", directory, ticker=ticker, forms=quoted)


def _fact_key(f: Fact) -> tuple:
    return (f.end or "", f.start or "", f.filed or "", f.concept)


def _dedupe(facts: Iterable[Fact], order: Sequence[str]) -> list[Fact]:
    """Drop a fact repeated under a later alias (same accn, period and value)."""
    rank = {c: i for i, c in enumerate(order)}
    kept: dict[tuple, Fact] = {}
    for f in sorted(facts, key=lambda f: rank.get(f.concept, len(rank))):
        kept.setdefault((f.accn, f.start, f.end, f.val), f)
    return sorted(kept.values(), key=_fact_key)


def _num(value: Any) -> int | float:
    if isinstance(value, int | float):
        return value
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        number = float(text)
        return int(number) if number.is_integer() else number


def _opt_int(value: Any) -> int | None:
    if value is None or str(value).strip() in {"", "null", "None"}:
        return None
    return int(_num(value))


def _opt_str(value: Any) -> str | None:
    if value is None or str(value) in {"", "null"}:
        return None
    return str(value)


# --- Live client ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ZetarisConfig:
    rest_url: str
    org_id: str = "1"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 20.0
    max_retries: int = 2

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ZetarisConfig:
        env = os.environ if env is None else env
        return cls(
            rest_url=(env.get("ZETARIS_REST_URL") or "http://localhost:8888/api/v1.0").rstrip("/"),
            org_id=env.get("ZETARIS_ORG_ID") or "1",
            api_key=env.get("ZETARIS_API_KEY") or "",
            timeout_seconds=float(env.get("SOURCE_TIMEOUT_SECONDS") or 20),
            max_retries=max(0, min(int(env.get("MAX_RETRIES") or 2), 5)),
        )


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    columns: list[str]
    sql: str
    duration_ms: int
    source: str = "zetaris"
    retry_count: int = 0


class ZetarisClient:
    """POST /sql-editor/sqls/run with a timeout and bounded retries. Never exposes the key."""

    BACKOFF_SECONDS = 0.5

    def __init__(
        self,
        config: ZetarisConfig,
        *,
        tracer: Tracer | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        row_limit: int = 10_000,
    ) -> None:
        self.config = config
        self.tracer = tracer or _NullTracer()
        self._sleep = sleep
        self._row_limit = row_limit
        self._http = httpx.Client(timeout=config.timeout_seconds, transport=transport)
        # X-Run-Id is always sent: without a caller or tracer run id, one is minted per client.
        self._default_run_id = getattr(self.tracer, "run_id", None) or f"run-{uuid.uuid4()}"
        self.down_reason: str | None = None  # set by mark_down (liveness probe found it down)
        self.down_dead_engine = False

    def __repr__(self) -> str:
        return f"ZetarisClient(rest_url={self.config.rest_url!r}, org_id={self.config.org_id!r})"

    def close(self) -> None:
        self._http.close()

    def _scrub(self, text: str, extra: Sequence[str] = ()) -> str:
        for secret in (self.config.api_key, *extra):
            if secret:
                text = text.replace(secret, REDACTED)
        return text

    def run_sql(
        self,
        sql: str,
        *,
        label: str,
        run_id: str | None = None,
        timeout: float | None = None,
        redact: Sequence[str] = (),
        expected: Callable[[SourceError], str | None] | None = None,
        max_retries: int | None = None,
        trace_fields: Mapping[str, Any] | None = None,
    ) -> QueryResult:
        """Run one statement. `redact` values are masked in the returned/traced SQL and errors.

        ``expected`` classifies a failure the caller treats as a benign outcome (e.g.
        "already exists" -> "exists"): its line is traced ``success`` with that ``outcome``
        instead of ``error``. It is consulted only for a non-recoverable failure, and for an
        unclassified 4xx (which it then turns into a non-recoverable ``refused``): a timeout,
        connection error, 5xx or dead engine always traces ``error``. The error is raised either way, so
        callers decide as before. ``max_retries`` overrides the configured retries for this
        call (``warm`` passes 0: a timed-out refetch is reported, not repeated).
        ``trace_fields`` are added to the statement's trace line (e.g. an UNCACHE's reason).

        Failures are classified by ``classify_reply`` and ``_parse`` (``cause``, see "Error
        classification"); only an outage is retried. A reply showing a dead engine
        (``dead_engine``) is recoverable whatever its status and is not retried (it does not
        heal in a second). The error line carries ``cause`` (and ``http_status``, ``excerpt``). Every line
        of a statement that was sent also carries the last attempt's ``exchange_fields`` (endpoint,
        method, service_host, http_status, request_bytes, response_bytes). After ``mark_down`` every statement
        fails at once as recoverable, without a request (the liveness probe found Zetaris down)."""
        retries = self.config.max_retries if max_retries is None else max(0, max_retries)
        url = f"{self.config.rest_url.rstrip('/')}/sql-editor/sqls/run"
        run_id = run_id or getattr(self.tracer, "run_id", None) or self._default_run_id
        shown_sql = self._scrub(sql, redact)
        extra = dict(trace_fields or {})
        wait = timeout or self.config.timeout_seconds
        start = time.perf_counter()
        if self.down_reason is not None:
            self.tracer.emit(
                agent=AGENT,
                action=label,
                status="error",
                outcome="engine_down",
                sql=shown_sql,
                rows=0,
                duration_ms=0,
                retry_count=0,
                source="zetaris",
                output_summary=f"not sent: {self.down_reason}",
                **extra,
            )
            raise SourceError(
                f"Zetaris {label}: not sent, {self.down_reason}",
                recoverable=True,
                dead_engine=self.down_dead_engine,
            )
        error: SourceError | None = None
        exchange: dict[str, Any] = {}
        for attempt in range(retries + 1):
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "X-Org-ID": str(self.config.org_id),
                "X-Request-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
                "X-Run-Id": run_id,
            }
            body = {
                "queryId": str(uuid.uuid4()),
                "sql": sql,
                "source": "SqlEditor",
                "limit": self._row_limit,
            }
            request = self._http.build_request(
                "POST", url, json=body, headers=headers, timeout=wait
            )
            exchange = exchange_fields(request, None)  # this attempt's; replaced on a reply
            try:
                response = self._http.send(request)
                exchange = exchange_fields(request, response)
            except httpx.TimeoutException:
                error = SourceError(
                    f"Zetaris {label}: timed out after {wait:g} s", recoverable=True
                )
            except httpx.TransportError as exc:
                error = SourceError(
                    f"Zetaris {label}: connection failed ({type(exc).__name__})", recoverable=True
                )
            except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError) as exc:
                # Any other httpx failure (e.g. a body that cannot be decoded): unclassified.
                error = unclassified(f"Zetaris {label}: request failed ({type(exc).__name__})")
            else:
                if response.status_code < 400 and _dead_reply(response):
                    # A 2xx can carry the dead-engine message too, with or without data rows;
                    # it is an outage, not a result.
                    detail = self._scrub(response.text[:500], redact)
                    error = SourceError(
                        f"Zetaris {label}: HTTP {response.status_code}: "
                        f"{DEAD_ENGINE_HINT}: {detail[:160]}",
                        recoverable=True,
                        status_code=response.status_code,
                        detail=detail,
                        dead_engine=True,
                    )
                elif response.status_code < 400:
                    result = self._parse(
                        response, label, shown_sql, start, attempt, extra, redact, exchange
                    )
                    if isinstance(result, QueryResult):
                        return result
                    error = result
                else:
                    detail = self._scrub(response.text[:500], redact)
                    # The whole body is checked: a stack trace can put the signature past 500.
                    cause = classify_reply(response.status_code, response.text)
                    if cause == "dead_engine":
                        shown = f"{DEAD_ENGINE_HINT}: {detail[:160]}"
                    elif cause == "unclassified":
                        shown = f"unclassified reply, served as an outage: {detail[:160]}"
                    else:
                        shown = detail[:200]
                    error = SourceError(
                        f"Zetaris {label}: HTTP {response.status_code}: {shown}",
                        recoverable=cause in RECOVERABLE_CAUSES,
                        status_code=response.status_code,
                        detail=detail,
                        dead_engine=cause == "dead_engine",
                        cause=cause,
                    )
                    if cause == "unclassified" and expected and expected(error):
                        # The caller recognises this reply as a benign outcome: not an outage.
                        error.recoverable = False
                        error.cause = "refused"
            error.retry_count = attempt
            # Only an outage is retried: not a fatal error, not a dead engine (it does not heal
            # in a second) and not an unclassified reply (a retry would only spend the budget).
            if error.cause != "outage" or attempt >= retries:
                break
            self._sleep(self.BACKOFF_SECONDS * 2**attempt)
        assert error is not None
        # A recoverable failure (outage, dead engine, unclassified) is never benign, whatever
        # its text says.
        outcome = expected(error) if expected and not error.recoverable else None
        if outcome:
            self.tracer.emit(
                agent=AGENT,
                action=label,
                status="success",
                outcome=outcome,
                sql=shown_sql,
                rows=0,
                duration_ms=_ms(start),
                retry_count=error.retry_count,
                source="zetaris",
                output_summary=f"{outcome}: {error}",
                **exchange,
                **extra,
            )
            raise error
        self.tracer.emit(
            agent=AGENT,
            action=label,
            status="error",
            sql=shown_sql,
            rows=0,
            duration_ms=_ms(start),
            retry_count=error.retry_count,
            source="zetaris",
            output_summary=str(error),
            **({"outcome": "engine_down"} if error.dead_engine else {}),
            **{**exchange, **error.trace_fields()},
            **extra,
        )
        raise error

    def mark_down(self, reason: str, *, dead: bool = False) -> None:
        """Send nothing more: every later ``run_sql`` fails at once as a recoverable outage with
        this reason, so the fallbacks serve the sample (set from the liveness probe, per run)."""
        self.down_reason = reason
        self.down_dead_engine = dead

    def _parse(
        self,
        response: httpx.Response,
        label: str,
        sql: str,
        start: float,
        attempt: int,
        extra: Mapping[str, Any] | None = None,
        redact: Sequence[str] = (),
        exchange: Mapping[str, Any] | None = None,
    ) -> QueryResult | SourceError:
        excerpt = self._scrub(response.text[:EXCERPT_CHARS], redact)
        status = response.status_code
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            return unclassified(
                f"Zetaris {label}: HTTP {status}: response is not JSON, served as an outage",
                status_code=status,
                excerpt=excerpt,
            )
        # An empty body (DDL) parses to {} and means no rows; anything that is not an
        # object with list-valued headers and data is malformed, not an empty result.
        headers = payload.get("headers") or [] if isinstance(payload, dict) else None
        data = payload.get("data") or [] if isinstance(payload, dict) else None
        if not isinstance(headers, list) or not isinstance(data, list):
            return unclassified(
                f"Zetaris {label}: HTTP {status}: response is not an object with headers and "
                "data lists, served as an outage",
                status_code=status,
                excerpt=excerpt,
            )
        columns: list[str] = [str(c) for c in headers]
        rows: list[dict[str, Any]] = []
        for row in data:
            # Every row must be keyed by exactly the response headers; a malformed
            # payload is an error, not a silently mis-keyed result.
            if isinstance(row, dict) and sorted(row) == sorted(columns):
                rows.append(dict(row))
            elif isinstance(row, list) and len(row) == len(columns):
                rows.append(dict(zip(columns, row)))
            else:
                return unclassified(
                    f"Zetaris {label}: HTTP {status}: row does not match response headers "
                    f"{columns}, served as an outage",
                    status_code=status,
                    excerpt=excerpt,
                )
        result = QueryResult(
            rows=rows,
            columns=columns,
            sql=sql,
            duration_ms=_ms(start),
            retry_count=attempt,
        )
        self.tracer.emit(
            agent=AGENT,
            action=label,
            status="success",
            sql=sql,
            rows=len(rows),
            duration_ms=result.duration_ms,
            retry_count=attempt,
            source="zetaris",
            **dict(exchange or {}),
            **dict(extra or {}),
        )
        return result


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def exchange_fields(request: httpx.Request, response: httpx.Response | None) -> dict[str, Any]:
    """What went over the wire on one attempt, for its trace line: the path only (no query
    string), the method, host:port (never userinfo), the HTTP status (None when no reply came
    back) and the bytes sent and received. Headers (the API key) are never included."""
    url = request.url
    return {
        "endpoint": url.path,
        "method": request.method,
        "service_host": f"{url.host}:{url.port}" if url.port else url.host,
        "http_status": response.status_code if response is not None else None,
        "request_bytes": len(request.content),
        "response_bytes": len(response.content) if response is not None else None,
    }


# --- Evidence sources -------------------------------------------------------------------------


def _fact_of(r: Mapping[str, Any]) -> Fact | None:
    if r.get("val") is None:
        return None
    return Fact(
        concept=str(r["concept"]),
        unit=str(r.get("unit") or "USD"),
        val=_num(r["val"]),
        start=_opt_str(r.get("period_start")),
        end=str(r["period_end"]),
        fy=_opt_int(r.get("fy")),
        fp=_opt_str(r.get("fp")),
        form=_opt_str(r.get("form")),
        filed=_opt_str(r.get("filed")),
        accn=_opt_str(r.get("accn")),
        frame=_opt_str(r.get("frame")),
    )


def _filing_of(r: Mapping[str, Any]) -> Filing:
    return Filing(
        accession_number=str(r["accession_number"]),
        filing_date=str(r["filing_date"]),
        report_date=_opt_str(r.get("report_date")),
        form=str(r["form"]),
        items=str(r.get("items") or ""),
        primary_document=_opt_str(r.get("primary_document")),
    )


def _typed_rows(
    label: str, result: QueryResult, make: Callable[[Mapping[str, Any]], T | None]
) -> list[T]:
    """Type a reply's rows; a row that does not parse (a missing column, a value that is not a
    number) is an unexpected reply shape: ``unclassified``, so the fallback serves the sample."""
    out: list[T] = []
    for row in result.rows:
        try:
            item = make(row)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise unclassified(
                f"Zetaris {label}: row does not parse ({type(exc).__name__}: {exc}), "
                "served as an outage",
                excerpt=str(row)[:EXCERPT_CHARS],
            ) from exc
        if item is not None:
            out.append(item)
    return out


class LiveZetarisSource:
    """Reads the tiresias.* views registered by bootstrap(). Only queries views that exist."""

    name = "zetaris"

    def __init__(self, client: ZetarisClient, *, directory: Path | None = None) -> None:
        self.client = client
        self.directory = directory
        self._views: set[str] | None = None

    def refresh(self) -> None:
        """Forget the cached view list (after a runtime registration, Loop B)."""
        self._views = None

    def registered_views(self) -> set[str]:
        if self._views is None:
            result = self.client.run_sql(
                load_template("33_views.sql", self.directory), label="list_views"
            )
            names = {str(r.get("table_identifier") or "").lower() for r in result.rows}
            self._views = {n for n in names if n}
        return self._views

    def entities(self) -> Evidence[Entity]:
        result = self.client.run_sql(
            load_template("32_entities.sql", self.directory), label="entities"
        )
        known = {e.ticker: e for e in SHOWCASE_ENTITIES}
        found: list[Entity] = []
        for row in result.rows:
            name = str(row.get("name") or "").lower()
            match = re.search(r"CIK(\d{10})", str(row.get("source_table") or ""))
            if name.endswith("_facts_raw") and match:
                ticker = name.removesuffix("_facts_raw")
                name = known[ticker].name if ticker in known else ""
                found.append(Entity(ticker, match.group(1), name))
        return Evidence(
            sorted(found, key=lambda e: e.ticker),
            sql=result.sql,
            source=self.name,
            duration_ms=result.duration_ms,
        )

    def concept_series(self, entity: str, concept: str) -> Evidence[Fact]:
        ticker = ticker_of(entity)
        aliases = concept_aliases(concept)
        views = self.registered_views()
        present = [c for c in aliases if f"{ticker}_{concept_slug(c)}" in views]
        if not present:
            return Evidence(
                sql=concept_series_sql(ticker, aliases, self.directory),
                source=self.name,
                note=f"no registered view for {ticker} {'/'.join(aliases)}",
            )
        sql = concept_series_sql(ticker, present, self.directory)
        FACTS_CACHE.touch(facts_table(ticker))  # most recently used (ZETARIS_MAX_CACHED_FACTS)
        result = self.client.run_sql(sql, label="concept_series")
        facts = _typed_rows("concept_series", result, _fact_of)
        return Evidence(
            _dedupe(facts, aliases), sql=sql, source=self.name, duration_ms=result.duration_ms
        )

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS) -> Evidence[Filing]:
        ticker = ticker_of(entity)
        sql = filings_sql(ticker, forms, self.directory)
        if f"{ticker}_filings" not in self.registered_views():
            return Evidence(sql=sql, source=self.name, note=f"no registered view {ticker}_filings")
        result = self.client.run_sql(sql, label="filings")
        rows = _typed_rows("filings", result, _filing_of)
        return Evidence(rows, sql=sql, source=self.name, duration_ms=result.duration_ms)


class SampleSource:
    """Same answers from data/sample/*.json, no network. Reports the SQL live mode would run."""

    name = "sample"

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        tracer: Tracer | None = None,
        directory: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir or sample_dir())
        self.tracer = tracer or _NullTracer()
        self.directory = directory
        self._cache: dict[str, Any] = {}

    def _load(self, name: str) -> Any:
        if name not in self._cache:
            path = self.data_dir / name
            self._cache[name] = (
                json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            )
        return self._cache[name]

    def _trace(self, action: str, result: Evidence[Any], start: float) -> Evidence[Any]:
        result.duration_ms = _ms(start)
        self.tracer.emit(
            agent=AGENT,
            action=action,
            status="success",
            sql=result.sql,
            rows=len(result),
            duration_ms=result.duration_ms,
            retry_count=0,
            source=self.name,
        )
        return result

    def entities(self) -> Evidence[Entity]:
        start = time.perf_counter()
        found = []
        for path in sorted(self.data_dir.glob("*_companyfacts.json")):
            data = self._load(path.name) or {}
            ticker = path.name.removesuffix("_companyfacts.json")
            found.append(
                Entity(ticker, f"{int(data.get('cik', 0)):010d}", data.get("entityName", ""))
            )
        sql = load_template("32_entities.sql", self.directory)
        return self._trace("entities", Evidence(found, sql=sql, source=self.name), start)

    def concept_series(self, entity: str, concept: str) -> Evidence[Fact]:
        start = time.perf_counter()
        ticker = ticker_of(entity)
        aliases = concept_aliases(concept)
        data = self._load(f"{ticker}_companyfacts.json")
        gaap = ((data or {}).get("facts") or {}).get("us-gaap") or {}
        present = [c for c in aliases if c in gaap]
        facts = [
            Fact(
                concept=c,
                unit="USD",
                val=_num(f["val"]),
                start=f.get("start"),
                end=f["end"],
                fy=_opt_int(f.get("fy")),
                fp=f.get("fp"),
                form=f.get("form"),
                filed=f.get("filed"),
                accn=f.get("accn"),
                frame=f.get("frame"),
            )
            for c in present
            for f in (gaap[c].get("units") or {}).get("USD") or []
        ]
        result = Evidence(
            _dedupe(facts, aliases),
            sql=concept_series_sql(ticker, present or aliases, self.directory),
            source=self.name,
            note=None if present else f"not in sample: {ticker} {'/'.join(aliases)}",
        )
        return self._trace("concept_series", result, start)

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS) -> Evidence[Filing]:
        start = time.perf_counter()
        ticker = ticker_of(entity)
        sql = filings_sql(ticker, forms, self.directory)
        data = self._load(f"{ticker}_submissions.json") or {}
        recent = (data.get("filings") or {}).get("recent") or {}
        wanted = set(forms)
        rows = [
            Filing(
                accession_number=accn,
                filing_date=recent["filingDate"][i],
                report_date=_opt_str(recent["reportDate"][i]),
                form=recent["form"][i],
                items=recent["items"][i] or "",
                primary_document=_opt_str(recent["primaryDocument"][i]),
            )
            for i, accn in enumerate(recent.get("accessionNumber") or [])
            if recent["form"][i] in wanted
        ]
        rows.sort(key=lambda f: (f.filing_date, f.accession_number), reverse=True)
        note = None if data else f"not in sample: {ticker}"
        return self._trace("filings", Evidence(rows, sql=sql, source=self.name, note=note), start)


class FallbackSource:
    """Live first; on a recoverable SourceError (outage, dead engine, unclassified; see "Error
    classification") serve the sample, traced degraded with the error's ``cause`` (and
    ``http_status``, ``excerpt``). Any other exception inside the live read is wrapped as
    ``unclassified`` first, except ``BEFORE_SENDING_ERRORS``. A non-recoverable error (bad SQL,
    auth) is a bug or misconfiguration, not an outage: it is raised, never papered over with
    sample rows."""

    def __init__(
        self, primary: EvidenceSource, fallback: EvidenceSource, tracer: Tracer | None = None
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.tracer = tracer or _NullTracer()
        self.name = primary.name

    def _call(self, method: str, *args: Any) -> Evidence[Any]:
        try:
            try:
                return getattr(self.primary, method)(*args)
            except (SourceError, *BEFORE_SENDING_ERRORS):
                raise  # classified, or raised before any SQL was sent
            except Exception as exc:  # rule 8: an unknown failure in a live read is an outage
                raise unclassified(
                    f"Zetaris {method}: unexpected {type(exc).__name__} in the live read"
                ) from exc
        except SourceError as err:
            if not err.recoverable:
                raise
            start = time.perf_counter()
            result = getattr(self.fallback, method)(*args)
            result.degraded = True
            result.error = str(err)
            self.tracer.emit(
                agent=AGENT,
                action=method,
                status="degraded",
                sql=result.sql,
                rows=len(result),
                duration_ms=_ms(start),
                retry_count=err.retry_count,
                source=result.source,
                output_summary=f"live query failed, served from sample: {err}",
                **err.trace_fields(),
            )
            return result

    def refresh(self) -> None:
        for inner in (self.primary, self.fallback):
            if hasattr(inner, "refresh"):
                inner.refresh()

    def entities(self) -> Evidence[Entity]:
        return self._call("entities")

    def concept_series(self, entity: str, concept: str) -> Evidence[Fact]:
        return self._call("concept_series", entity, concept)

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS) -> Evidence[Filing]:
        return self._call("filings", entity, tuple(forms))


def make_source(
    sample_mode: bool,
    tracer: Tracer | None = None,
    *,
    config: ZetarisConfig | None = None,
    client: ZetarisClient | None = None,
    data_dir: Path | None = None,
) -> EvidenceSource:
    """Sample mode: SampleSource. Live: Zetaris, falling back to the sample per call."""
    if sample_mode:
        return SampleSource(data_dir, tracer=tracer)
    client = client or ZetarisClient(config or ZetarisConfig.from_env(), tracer=tracer)
    return FallbackSource(LiveZetarisSource(client), SampleSource(data_dir), tracer)


# --- Registration -----------------------------------------------------------------------------


BOOTSTRAP_TIMEOUT_SECONDS = 180.0
MIN_STATEMENT_SECONDS = 1.0  # below this much run budget left, a statement is skipped
# Raw tables a registration can create, keyed by kind, with the catalog template behind each.
REGISTRABLE_TABLES = ("facts", "submissions")
CATALOG_TABLES = {"sec_companyfacts": "facts", "sec_submissions": "submissions"}
# Discovery objects (catalog id sec_tickers): registered once, never an evidence source.
TICKERS_TABLE = "sec_tickers_raw"
TICKERS_VIEW = "sec_tickers"
_DONE_STATUS = {
    "cache": "cached",
    "concept_view": "replaced",
    "filings_view": "replaced",
    "tickers_view": "replaced",
}


def already_exists(err: SourceError) -> str | None:
    """Return "exists" when Zetaris refused a CREATE because the object is already there, else None.

    Lightning SQL has no IF NOT EXISTS for databases, containers or REST tables."""
    return "exists" if "already exist" in err.detail.lower() else None


def bootstrap_outcome(step: str, err: SourceError) -> str | None:
    """The benign outcome of a failed bootstrap statement ("exists" / "absent"), else None."""
    if already_exists(err):
        return "exists"
    if step == "concept_view" and "FIELD_NOT_FOUND" in err.detail:
        return "absent"  # the entity never reported this concept
    return None


def bootstrap_plan(
    sec_user_agent: str,
    entities: Sequence[Entity] = SHOWCASE_ENTITIES,
    concepts: Sequence[str] = tuple(CONCEPT_SLUGS),
    directory: Path | None = None,
    tables: Sequence[str] = REGISTRABLE_TABLES,
    discovery: bool = False,
) -> list[tuple[str, str, str, str | None]]:
    """(step, object, sql, required raw table) in order. Only CREATE, CREATE OR REPLACE, CACHE.

    ``tables`` limits the raw tables (and the views over them) to register: "facts" (concept
    views) and/or "submissions" (filings view). Loop B's onboarder registers only "facts".
    ``discovery`` adds the sec_tickers discovery table and view (bootstrap CLI, or the live
    discovery adapter on first use); they are catalog reads, not evidence sources."""
    unknown = set(tables) - set(REGISTRABLE_TABLES)
    if unknown or not tables:
        raise ValueError(f"tables must be a non-empty subset of {REGISTRABLE_TABLES}")
    ua = _check(_USER_AGENT, sec_user_agent, "SEC_USER_AGENT")
    steps: list[tuple[str, str, str, str | None]] = [
        ("database", "TIRESIAS_SEC", render_file("00_database.sql", directory), None),
        ("container", "tiresias", render_file("01_container.sql", directory), None),
    ]
    if discovery:
        sql = render_file("13_tickers_table.sql", directory, sec_user_agent=ua)
        steps.append(("tickers_table", f"TIRESIAS_SEC.{TICKERS_TABLE}", sql, None))
        cache = render_file("12_cache_table.sql", directory, table=TICKERS_TABLE)
        steps.append(("cache", f"TIRESIAS_SEC.{TICKERS_TABLE}", cache, TICKERS_TABLE))
        view = render_file("22_tickers_view.sql", directory)
        steps.append(("tickers_view", f"tiresias.{TICKERS_VIEW}", view, TICKERS_TABLE))
    for e in entities:
        t, cik = ticker_of(e.ticker), _check(_CIK10, e.cik, "CIK")
        facts, subs = f"{t}_facts_raw", f"{t}_submissions_raw"
        for step, tmpl, table in (
            ("facts_table", "10_companyfacts_table.sql", facts),
            ("submissions_table", "11_submissions_table.sql", subs),
        ):
            if step.removesuffix("_table") not in tables:
                continue
            sql = render_file(tmpl, directory, ticker=t, cik10=cik, sec_user_agent=ua)
            steps.append((step, f"TIRESIAS_SEC.{table}", sql, None))
            cache = render_file("12_cache_table.sql", directory, table=table)
            steps.append(("cache", f"TIRESIAS_SEC.{table}", cache, table))
        for c in concepts if "facts" in tables else ():
            slug = concept_slug(c)
            sql = render_file("20_concept_view.sql", directory, ticker=t, slug=slug, concept=c)
            steps.append(("concept_view", f"tiresias.{t}_{slug}", sql, facts))
        if "submissions" in tables:
            sql = render_file("21_filings_view.sql", directory, ticker=t)
            steps.append(("filings_view", f"tiresias.{t}_filings", sql, subs))
    return steps


def bootstrap(
    client: ZetarisClient,
    *,
    sec_user_agent: str | None = None,
    entities: Sequence[Entity] = SHOWCASE_ENTITIES,
    concepts: Sequence[str] = tuple(CONCEPT_SLUGS),
    tables: Sequence[str] = REGISTRABLE_TABLES,
    discovery: bool = False,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    protect: Iterable[str] = (),
    cache: FactsCache | None = None,
) -> list[dict[str, Any]]:
    """Register the showcase sources (or one entity's ``tables``: the asked entity, or Loop B's
    discovered one). Safe to run twice: "already exists" counts as done.

    Before each companyfacts CACHE TABLE, ``cache`` (default ``FACTS_CACHE``) makes room under
    ZETARIS_MAX_CACHED_FACTS by uncaching the least recently used table that is not one of this
    call's entities, not in ``protect`` (object tickers, e.g. the run's asked entity) and not
    pinned; if nothing can be released the CACHE is not sent (the cap is never exceeded) and the
    entry is ``status: "capped"`` with ``reason: "cache_cap"``: the raw table stays registered and
    is read uncached.

    A concept the entity never reported gives status "absent" (no view); other failures are
    recorded and the run goes on; steps on a raw table that failed are skipped. Never drops.
    ``deadline`` (a ``clock()`` value, e.g. the end of the run budget) caps each statement's
    timeout at the time left and skips the remaining statements once it has passed.
    """
    ua = sec_user_agent or os.getenv("SEC_USER_AGENT", "")
    cache = cache if cache is not None else FACTS_CACHE
    failed_tables: set[str] = set()
    report: list[dict[str, Any]] = []
    plan = bootstrap_plan(ua, entities, concepts, tables=tables, discovery=discovery)
    keep = {facts_table(e.ticker) for e in entities} | {facts_table(t) for t in protect}
    for step, obj, sql, requires in plan:
        entry: dict[str, Any] = {"step": step, "object": obj}
        if requires in failed_tables:
            report.append({**entry, "status": "skipped", "duration_ms": 0})
            continue
        timeout = max(client.config.timeout_seconds, BOOTSTRAP_TIMEOUT_SECONDS)
        if deadline is not None:
            left = deadline - clock()
            if left < MIN_STATEMENT_SECONDS:
                report.append({**entry, "status": "skipped", "duration_ms": 0, "budget": True})
                continue
            timeout = min(timeout, left)
        start = time.perf_counter()
        counted = step == "cache" and requires is not None and is_facts_table(requires)
        if counted:
            assert requires is not None
            room = cache.make_room(
                client, requires, protect=keep, timeout=min(UNCACHE_TIMEOUT_SECONDS, timeout)
            )
            if not room:
                # Never cache past the cap: the table stays registered and is read uncached.
                report.append(
                    {**entry, "status": "capped", "reason": "cache_cap", "duration_ms": _ms(start)}
                )
                continue

        def classify(err: SourceError, step: str = step) -> str | None:
            return bootstrap_outcome(step, err)

        try:
            client.run_sql(
                sql, label=f"bootstrap:{step}", timeout=timeout, redact=[ua], expected=classify
            )
            # Views use CREATE OR REPLACE, so a re-run reports them as "replaced".
            entry["status"] = _DONE_STATUS.get(step, "created")
            if counted:
                assert requires is not None
                cache.add(requires)
        except SourceError as err:
            if outcome := classify(err):
                entry["status"] = outcome
            else:
                entry["status"] = "failed"
                entry["error"] = str(err)[:200]
                entry["recoverable"] = err.recoverable
                entry["cause"] = err.cause
                if step.endswith("_table"):
                    failed_tables.add(obj.split(".")[-1])
        entry["duration_ms"] = _ms(start)
        report.append(entry)
    return report


def lightning_tables(client: ZetarisClient, directory: Path | None = None) -> dict[str, str]:
    """Registered REST tables in TIRESIAS_SEC: lower-case name -> endpoint (32_entities.sql)."""
    result = client.run_sql(load_template("32_entities.sql", directory), label="list_tables")
    return {
        str(r.get("name") or "").lower(): str(r.get("source_table") or "")
        for r in result.rows
        if r.get("name")
    }


def endpoint_cik(endpoint: str) -> str | None:
    match = re.search(r"CIK(\d{10})", endpoint or "")
    return match.group(1) if match else None


# --- Cache warm-up (BACKLOG B-20) -------------------------------------------------------------

# Raw tables warm re-caches: companyfacts ("facts"), submissions and the discovery ticker map. An
# EDGAR full-text search table (fts_<hash>_raw) serves one past search only, so it is never
# refetched. Each cached companyfacts table holds Zetaris driver heap: on 2026-10-24 re-caching
# every registered table (63) ran the 2 GB driver heap out of memory after about ten companyfacts
# tables, which stopped its SparkContext until the driver was restarted. So warm takes at most
# ``WarmSettings.max_tables`` (ZETARIS_WARM_MAX_TABLES, default 11: the five default
# ZETARIS_WARM_ENTITIES' facts and submissions, then the ticker map) in ``warm_order``, and stops
# after ``WARM_MAX_CONSECUTIVE_FAILURES`` failures in a row (Zetaris itself is then likely down).
WARM_KINDS = ("facts", "submissions", "tickers")
WARM_MAX_CONSECUTIVE_FAILURES = 2


def check_warm_entities(entities: Iterable[str]) -> tuple[str, ...]:
    """Each ZETARIS_WARM_ENTITIES name checked against the object-ticker pattern used for SQL
    placeholders (``object_ticker`` output, e.g. ``brkb``); raises ValueError otherwise."""
    return tuple(_check(_TICKER, e, "ZETARIS_WARM_ENTITIES entry") for e in entities)


def warm_kind(table: str) -> str | None:
    """ "facts" / "submissions" / "tickers" for a raw table warm re-caches, else None."""
    if table == TICKERS_TABLE:
        return "tickers"
    for kind in ("facts", "submissions"):
        suffix = f"_{kind}_raw"
        if table.endswith(suffix) and _TICKER.fullmatch(table.removesuffix(suffix)):
            return kind
    return None


def warm_order(tables: Iterable[str], entities: Sequence[str] = WARM_ENTITIES_DEFAULT) -> list[str]:
    """Warmable tables, most useful first: ``entities`` in the given order (each one's facts,
    then its submissions; validated by ``check_warm_entities``), the ticker map, other
    registered entities' facts, their submissions, then submissions tables only discovery
    registered (scout candidates without a facts table)."""
    priority = {e: i for i, e in reversed(list(enumerate(check_warm_entities(entities))))}
    names = [t for t in tables if warm_kind(t)]
    registered = {t.removesuffix("_facts_raw") for t in names if warm_kind(t) == "facts"}

    def key(name: str) -> tuple[int, int, str]:
        kind = warm_kind(name)
        ticker = name.rsplit("_", 2)[0]
        if kind == "tickers":
            return (1, 0, name)
        if ticker in priority:
            return (0, priority[ticker] * 2 + (kind == "submissions"), name)
        if kind == "facts":
            return (2, 0, name)
        return (3 if ticker in registered else 4, 0, name)

    return sorted(names, key=key)


def warm_outcome(err: SourceError) -> str | None:
    """The benign outcome of a failed CACHE TABLE: the table was dropped after it was listed."""
    return "absent" if "TABLE_OR_VIEW_NOT_FOUND" in err.detail else None


# --- Bounded cache and liveness (BACKLOG B-24) ------------------------------------------------

UNCACHE_TIMEOUT_SECONDS = 10.0


def facts_table(ticker: str) -> str:
    """The companyfacts raw table of an entity (``<ticker>_facts_raw``)."""
    return f"{ticker_of(ticker)}_facts_raw"


def is_facts_table(table: str) -> bool:
    return warm_kind(table) == "facts"


def uncache_sql(table: str, directory: Path | None = None) -> str:
    """``UNCACHE TABLE`` for one raw table (15_uncache_table.sql); the name is validated."""
    return render_file(
        "15_uncache_table.sql", directory, table=_check(_TABLE, table, "raw table name")
    )


def uncache(
    client: ZetarisClient,
    table: str,
    *,
    reason: str,
    timeout: float = UNCACHE_TIMEOUT_SECONDS,
    directory: Path | None = None,
) -> bool:
    """Release one raw table's session cache. Never drops it, never raises: a failure is traced
    (the statement's ``uncache`` line, with ``table`` and ``reason``) and returns False. A table
    that no longer exists counts as released (``outcome: "absent"``)."""
    fields = {"table": f"TIRESIAS_SEC.{table}", "reason": reason}
    try:
        sql = uncache_sql(table, directory)
        client.run_sql(
            sql,
            label="uncache",
            timeout=max(MIN_STATEMENT_SECONDS, timeout),
            max_retries=0,
            expected=warm_outcome,
            trace_fields=fields,
        )
        return True
    except SourceError as err:
        return warm_outcome(err) is not None
    except Exception as exc:  # noqa: BLE001 - releasing a cache must never fail a run
        client.tracer.emit(
            agent=AGENT,
            action="uncache",
            status="error",
            source="zetaris",
            output_summary=f"uncache not sent: {type(exc).__name__}: {exc}"[:200],
            **fields,
        )
        return False


class FactsCache:
    """The companyfacts raw tables this process has cached in Zetaris, least recently used first.

    Each one holds about 150 MB of Zetaris driver heap while cached (BACKLOG B-24), so at most
    ``max_cached_facts()`` (ZETARIS_MAX_CACHED_FACTS, default 5) are kept: ``make_room`` runs
    before a CACHE TABLE of one more and uncaches the least recently used table that is neither
    protected by the caller nor pinned by a running run (``pin``: the asked entity, the Loop B
    counterparty). ``touch`` marks a table used (a query on it). Tables cached by another process,
    or before this one started, are not known here. Thread-safe; the UNCACHE itself is sent
    outside the lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lru: OrderedDict[str, None] = OrderedDict()
        self._pins: Counter[str] = Counter()

    def reset(self) -> None:
        with self._lock:
            self._lru.clear()
            self._pins.clear()

    def tables(self) -> list[str]:
        with self._lock:
            return list(self._lru)

    def pinned(self) -> set[str]:
        with self._lock:
            return {t for t, n in self._pins.items() if n > 0}

    def add(self, table: str) -> None:
        with self._lock:
            self._lru[table] = None
            self._lru.move_to_end(table)

    def touch(self, table: str) -> None:
        with self._lock:
            if table in self._lru:
                self._lru.move_to_end(table)

    def discard(self, table: str) -> None:
        with self._lock:
            self._lru.pop(table, None)

    def pin(self, table: str) -> None:
        with self._lock:
            self._pins[table] += 1

    def unpin(self, table: str) -> None:
        with self._lock:
            self._pins[table] -= 1
            if self._pins[table] <= 0:
                del self._pins[table]

    def snapshot(self, cap: int | None = None) -> dict[str, Any]:
        with self._lock:
            return {
                "cached_facts": list(self._lru),
                "max_cached_facts": cap if cap is not None else max_cached_facts(),
                "pinned": sorted(t for t, n in self._pins.items() if n > 0),
            }

    def make_room(
        self,
        client: ZetarisClient,
        incoming: str,
        *,
        protect: Iterable[str] = (),
        cap: int | None = None,
        timeout: float = UNCACHE_TIMEOUT_SECONDS,
        directory: Path | None = None,
    ) -> bool:
        """Before caching ``incoming``: uncache least recently used tables until one more fits
        under the cap. Returns True when it fits (or ``incoming`` is already counted), False when
        every cached table is protected or pinned or its UNCACHE failed. Never raises; each
        UNCACHE is traced with its reason; at most one attempt per cached table."""
        cap = cap if cap is not None else max_cached_facts()
        keep = {*protect, incoming}
        tried: set[str] = set()
        while True:
            with self._lock:
                if incoming in self._lru:
                    self._lru.move_to_end(incoming)
                    return True
                if len(self._lru) < cap:
                    return True
                pinned = {t for t, n in self._pins.items() if n > 0}
                victim = next(
                    (t for t in self._lru if t not in keep and t not in pinned and t not in tried),
                    None,
                )
                held = len(self._lru)
            if victim is None:
                return False
            tried.add(victim)
            reason = (
                f"ZETARIS_MAX_CACHED_FACTS={cap} reached ({held} cached): least recently used, "
                f"making room for {incoming}"
            )
            if uncache(client, victim, reason=reason, timeout=timeout, directory=directory):
                self.discard(victim)


FACTS_CACHE = FactsCache()


@dataclass(frozen=True)
class Liveness:
    """One liveness probe result: ``state`` ``alive`` / ``down`` / ``inconclusive``."""

    state: str
    reason: str
    checked_at: str
    at: float
    duration_ms: int
    dead_engine: bool = False

    @property
    def down(self) -> bool:
        return self.state == "down"


class LivenessProbe:
    """A cheap check that Zetaris can run a statement at all, before a live run.

    Sends ``42_liveness.sql`` (``SELECT 1 AS alive``: a constant, no table, no SEC fetch, but it
    needs a running SparkContext) with ``LivenessSettings.timeout_seconds`` (3 s) and no retry,
    and reuses the result for ``ttl_seconds`` (30 s) per Zetaris URL, so a dead or hung engine is
    found once instead of by every query's timeouts. ``down``: a timeout, a connection error, a
    5xx or a dead-engine reply (``dead_engine``); the run then sends nothing more to Zetaris and
    serves the sample (``ZetarisClient.mark_down``). ``inconclusive``: any other refusal (e.g.
    auth): the run goes ahead live and the real queries report it. Each check is traced as one
    ``liveness`` line (``cached`` true when the result was reused); ``snapshot`` is the last
    result, for GET /health. Never raises."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, Liveness] = {}
        self._latest: Liveness | None = None

    def reset(self) -> None:
        with self._lock:
            self._last.clear()
            self._latest = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latest = self._latest
        if latest is None:
            return {"state": "not-checked"}
        return {
            "state": latest.state,
            "reason": latest.reason,
            "checked_at": latest.checked_at,
            "duration_ms": latest.duration_ms,
        }

    def check(
        self,
        client: ZetarisClient,
        *,
        tracer: Tracer | None = None,
        settings: LivenessSettings | None = None,
        clock: Callable[[], float] = time.monotonic,
        directory: Path | None = None,
    ) -> Liveness:
        settings = settings or LivenessSettings.from_env()
        log = tracer or client.tracer
        key = client.config.rest_url
        with self._lock:
            last = self._last.get(key)
        cached = last is not None and clock() - last.at < settings.ttl_seconds
        if cached:
            assert last is not None
            result = last
        else:
            result = self._probe(client, settings, clock, directory)
            with self._lock:
                self._last[key] = result
                self._latest = result
        log.emit(
            agent=AGENT,
            action="liveness",
            status={"alive": "success", "down": "degraded"}.get(result.state, "error"),
            outcome=result.state,
            cached=cached,
            duration_ms=0 if cached else result.duration_ms,
            source="zetaris",
            output_summary=result.reason,
        )
        return result

    def _probe(
        self,
        client: ZetarisClient,
        settings: LivenessSettings,
        clock: Callable[[], float],
        directory: Path | None,
    ) -> Liveness:
        start = time.perf_counter()
        state, reason, dead = "alive", "Zetaris answered a constant query", False
        try:
            client.run_sql(
                load_template("42_liveness.sql", directory),
                label="liveness:probe",
                timeout=settings.timeout_seconds,
                max_retries=0,
            )
        except SourceError as err:
            dead = err.dead_engine
            # An unclassified reply is no proof that Zetaris is down: the run goes ahead live
            # and each read degrades on its own.
            if err.recoverable and err.cause != "unclassified":
                state = "down"
                reason = (
                    DEAD_ENGINE_HINT
                    if dead
                    else f"Zetaris unreachable ({err}); the run serves the offline sample"
                )
            else:
                state, reason = "inconclusive", f"probe refused, run goes ahead live: {err}"
        except Exception as exc:  # noqa: BLE001 - a probe never stops a run
            state, reason = "inconclusive", f"probe failed: {type(exc).__name__}"
        return Liveness(state, reason[:300], _utc_now(), clock(), _ms(start), dead)


LIVENESS = LivenessProbe()


def check_liveness(
    client: ZetarisClient,
    *,
    tracer: Tracer | None = None,
    probe: LivenessProbe | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Liveness:
    """``LIVENESS.check`` and, when Zetaris is down, ``client.mark_down`` with the reason: the
    run's statements then fail at once as an outage and every read is served from the sample."""
    result = (probe or LIVENESS).check(client, tracer=tracer, clock=clock)
    if result.down:
        client.mark_down(result.reason, dead=result.dead_engine)
    return result


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class WarmStatus:
    """Thread-safe progress of one warm pass, for GET /health (``zetaris_cache``).

    ``state``: ``off`` (sample mode, disabled or not configured; ``reason`` says which),
    ``not-started``, ``warming`` (``done`` of ``total`` tables), then ``warm`` (every table
    within the cap warmed), ``partial`` (some warmed, some failed or skipped) or ``failed``
    (none warmed, or the table list could not be read; ``error`` says why)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"state": "not-started"}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {k: dict(v) if isinstance(v, dict) else v for k, v in self._data.items()}

    def off(self, reason: str) -> None:
        with self._lock:
            self._data = {"state": "off", "reason": reason}

    def begin(self, total: int | None = None) -> None:
        with self._lock:
            self._data = {"state": "warming", "done": 0, "started_at": _utc_now()}
            if total is not None:
                self._data["total"] = total

    def advance(self) -> None:
        with self._lock:
            self._data["done"] = self._data.get("done", 0) + 1

    def finish(self, state: str, counts: Mapping[str, int], duration_ms: int) -> None:
        with self._lock:
            self._data.update(
                state=state, counts=dict(counts), duration_ms=duration_ms, finished_at=_utc_now()
            )

    def fail(self, error: str) -> None:
        with self._lock:
            self._data.update(state="failed", error=error[:200], finished_at=_utc_now())


def warm(
    client: ZetarisClient,
    *,
    settings: WarmSettings | None = None,
    status: WarmStatus | None = None,
    clock: Callable[[], float] = time.monotonic,
    directory: Path | None = None,
    cache: FactsCache | None = None,
) -> list[dict[str, Any]]:
    """Re-issue ``CACHE TABLE`` (template 12) for the registered raw tables, one at a time.

    Companyfacts tables count toward ZETARIS_MAX_CACHED_FACTS (``cache``, default
    ``FACTS_CACHE``): before each one, tables cached outside this pass (least recently used
    first, never a pinned one) are uncached to make room, never one this pass warmed; when no
    room is left the remaining companyfacts tables are ``capped`` with ``reason: "cache_cap"``.
    After the pass the warmed companyfacts tables are ordered so the first in ``warm_order`` is
    the last to be evicted.

    The cache is lost when Zetaris restarts and the first query then refetches from SEC
    (BACKLOG B-20). Tables are taken in ``warm_order`` with ``settings.entities``
    (ZETARIS_WARM_ENTITIES) first; an invalid name raises ValueError before any SQL is sent.
    At most ``settings.max_tables`` are re-cached (the
    rest are ``capped``: listed, not touched). Each statement may take
    ``settings.timeout_seconds``, with no retry (a timed-out refetch is reported; run warm
    again), capped at what is left of ``settings.budget_seconds``. One entry per table:
    ``warmed``, ``absent`` (dropped after it was listed), ``failed`` (with ``error`` and
    ``recoverable``), ``skipped`` (``reason`` ``budget``, with ``budget: true``, or ``halted``
    after WARM_MAX_CONSECUTIVE_FAILURES failures in a row) or ``capped``. Never creates,
    replaces or drops anything. Raises SourceError only if the table list cannot be read."""
    settings = settings or WarmSettings.from_env()
    status = status or WarmStatus()
    cache = cache if cache is not None else FACTS_CACHE
    start = time.perf_counter()
    deadline = clock() + settings.budget_seconds
    try:
        entities = check_warm_entities(settings.entities)  # before any SQL is sent
    except ValueError as err:
        status.fail(str(err))
        raise
    try:
        tables = warm_order(lightning_tables(client, directory), entities)
    except SourceError as err:
        status.fail(str(err))
        raise
    chosen, capped = tables[: settings.max_tables], tables[settings.max_tables :]
    status.begin(len(chosen))
    report: list[dict[str, Any]] = []
    failures_in_a_row = 0
    warmed_facts: list[str] = []
    for table in chosen:
        entry: dict[str, Any] = {"table": f"TIRESIAS_SEC.{table}", "kind": warm_kind(table)}
        left = deadline - clock()
        if failures_in_a_row >= WARM_MAX_CONSECUTIVE_FAILURES:
            report.append({**entry, "status": "skipped", "reason": "halted", "duration_ms": 0})
            status.advance()
            continue
        if left < MIN_STATEMENT_SECONDS:
            skip = {"status": "skipped", "reason": "budget", "budget": True, "duration_ms": 0}
            report.append({**entry, **skip})
            status.advance()
            continue
        sql = render_file("12_cache_table.sql", directory, table=table)
        t0 = time.perf_counter()
        facts = is_facts_table(table)
        if facts and not cache.make_room(
            client, table, protect=warmed_facts, timeout=min(UNCACHE_TIMEOUT_SECONDS, left)
        ):
            report.append({**entry, "status": "capped", "reason": "cache_cap", "duration_ms": 0})
            status.advance()
            continue
        try:
            client.run_sql(
                sql,
                label="warm:cache",
                timeout=min(settings.timeout_seconds, left),
                max_retries=0,
                expected=warm_outcome,
            )
            entry["status"] = "warmed"
            failures_in_a_row = 0
            if facts:
                cache.add(table)
                warmed_facts.append(table)
        except SourceError as err:
            if outcome := warm_outcome(err):
                entry["status"] = outcome
                failures_in_a_row = 0
            else:
                entry.update(status="failed", error=str(err)[:200], recoverable=err.recoverable)
                failures_in_a_row += 1
        entry["duration_ms"] = _ms(t0)
        report.append(entry)
        status.advance()
    for table in reversed(warmed_facts):  # highest priority = most recently used
        cache.touch(table)
    for table in capped:
        name = f"TIRESIAS_SEC.{table}"
        report.append({"table": name, "kind": warm_kind(table), "status": "capped"})
    counts = warm_counts(report)
    if not counts.get("failed") and not counts.get("skipped"):  # capped is by design
        state, trace_status = "warm", "success"
    elif counts.get("warmed"):
        state, trace_status = "partial", "degraded"
    else:
        state, trace_status = "failed", "error"
    duration = _ms(start)
    status.finish(state, counts, duration)
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no raw tables"
    client.tracer.emit(
        agent=AGENT,
        action="warm",
        status=trace_status,
        outcome=state,
        tables=len(report),
        counts=counts,
        duration_ms=duration,
        source="zetaris",
        output_summary=f"{state}: {summary}",
    )
    return report


def warm_counts(report: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Tables per status in a ``warm`` report."""
    counts: dict[str, int] = {}
    for entry in report:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return counts


def warm_on_start(
    *,
    sample_mode: bool,
    status: WarmStatus,
    env: Mapping[str, str] | None = None,
    make_client: Callable[[], ZetarisClient] | None = None,
    tracer: Tracer | None = None,
) -> threading.Thread | None:
    """Start ``warm`` in a daemon thread when the API starts in live mode with Zetaris
    configured (ZETARIS_REST_URL set) and ZETARIS_WARM_ON_START on (the default). Returns at
    once: the thread, or None (``status`` then says ``off`` and why). The thread never raises;
    any failure ends as ``status`` ``failed`` with a traced ``warm`` error line. Sample mode
    never touches Zetaris."""
    env = os.environ if env is None else env
    if sample_mode:
        status.off("sample mode")
        return None
    settings = WarmSettings.from_env(env)
    if not settings.on_start:
        status.off("ZETARIS_WARM_ON_START is off")
        return None
    if not (env.get("ZETARIS_REST_URL") or "").strip():
        status.off("ZETARIS_REST_URL is not set")
        return None
    config = ZetarisConfig.from_env(env)
    log = tracer or _NullTracer()

    def body() -> None:
        client: ZetarisClient | None = None
        try:
            client = make_client() if make_client else ZetarisClient(config, tracer=tracer)
            warm(client, settings=settings, status=status)
        except Exception as exc:  # noqa: BLE001 - never crash the app; /health reports it
            message = f"{type(exc).__name__}: {exc}"
            if config.api_key:
                message = message.replace(config.api_key, REDACTED)
            status.fail(message)
            log.emit(
                agent=AGENT,
                action="warm",
                status="error",
                outcome="failed",
                source="zetaris",
                output_summary=message[:200],
            )
        finally:
            if client is not None:
                client.close()

    status.begin()
    thread = threading.Thread(target=body, name="zetaris-warm", daemon=True)
    thread.start()
    return thread


# --- CLI --------------------------------------------------------------------------------------


class _PrintTracer:
    """Prints trace-shaped lines for the CLI (SQL shortened; the key is never in them)."""

    run_id = None

    def emit(self, *, agent: str, action: str, status: str = "success", **fields: Any) -> None:
        line = {"agent": agent, "action": action, "status": status, **fields}
        if isinstance(line.get("sql"), str):
            line["sql"] = " ".join(line["sql"].split())[:160]
        print(json.dumps(line, default=str), flush=True)


def _smoke(source: EvidenceSource) -> int:
    def show(label: str, result: Evidence[Any], **extra: Any) -> None:
        summary = {
            "check": label,
            "source": result.source,
            "rows": len(result),
            "duration_ms": result.duration_ms,
            "degraded": result.degraded,
            **({"note": result.note} if result.note else {}),
            **extra,
        }
        print(json.dumps(summary, default=str), flush=True)

    show("entities", source.entities())
    for e in SHOWCASE_ENTITIES:
        revenue = source.concept_series(e.ticker, "Revenues")
        show(f"{e.ticker} revenue facts", revenue, concepts=sorted({f.concept for f in revenue}))
        show(f"{e.ticker} 10-K/8-K filings", source.filings(e.ticker, ("10-K", "8-K")))
    ge = source.concept_series("ge", "Revenues")
    fy2022 = sorted(
        {
            f.val
            for f in ge
            if f.start == "2022-01-01" and f.end == "2022-12-31" and f.form == "10-K"
        }
    )
    print(json.dumps({"check": "GE FY2022 revenue as reported by 10-Ks", "values": fy2022}))
    return 0 if fy2022 else 1


def _warm(client: ZetarisClient) -> int:
    """CLI ``warm``: one JSON line per raw table, then the counts. Exit 1 unless all warmed."""
    start = time.perf_counter()
    try:
        report = warm(client)
    except (SourceError, ValueError) as err:
        print(json.dumps({"warm": "failed", "error": str(err)[:200]}), flush=True)
        return 1
    for entry in report:
        print(json.dumps(entry), flush=True)
    counts = warm_counts(report)
    summary = {"warm": counts, "tables": len(report), "duration_ms": _ms(start)}
    print(json.dumps(summary), flush=True)
    return 1 if counts.get("failed") or counts.get("skipped") else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tiresias_agent.adapters.zetaris")
    parser.add_argument("command", choices=["bootstrap", "smoke", "warm"])
    parser.add_argument("--sample", action="store_true", help="smoke against data/sample only")
    args = parser.parse_args(argv)
    tracer = _PrintTracer()
    if args.command == "smoke":
        return _smoke(make_source(args.sample, tracer))
    client = ZetarisClient(ZetarisConfig.from_env(), tracer=tracer)
    if args.command == "warm":
        return _warm(client)
    report = bootstrap(client, discovery=True)
    for entry in report:
        print(json.dumps(entry), flush=True)
    counts: dict[str, int] = {}
    for entry in report:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    print(json.dumps({"bootstrap": counts}), flush=True)
    core = {"database", "container", "facts_table", "submissions_table", "tickers_table"}
    return 1 if any(e["status"] == "failed" and e["step"] in core for e in report) else 0


__all__ = [
    "AUTH_SIGNATURES",
    "BEFORE_SENDING_ERRORS",
    "CAUSES",
    "CONCEPT_ALIASES",
    "DEAD_ENGINE_SIGNATURES",
    "FACTS_CACHE",
    "LIVENESS",
    "RECOVERABLE_CAUSES",
    "REFUSED_SIGNATURES",
    "SQL_SIGNATURES",
    "Entity",
    "Evidence",
    "EvidenceSource",
    "Fact",
    "FactsCache",
    "FallbackSource",
    "Filing",
    "LiveZetarisSource",
    "Liveness",
    "LivenessProbe",
    "QueryResult",
    "SampleSource",
    "SourceError",
    "TemplateError",
    "WarmStatus",
    "ZetarisClient",
    "ZetarisConfig",
    "bootstrap",
    "check_liveness",
    "classify_reply",
    "dead_engine",
    "endpoint_cik",
    "lightning_tables",
    "make_source",
    "object_ticker",
    "uncache",
    "unclassified",
    "warm",
    "warm_on_start",
]


if __name__ == "__main__":
    sys.exit(main())
