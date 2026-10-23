"""Zetaris adapter: Lightning SQL over REST, a sample-mode twin, and a fallback (AGENTS.md rule 8).

Live mode runs SQL rendered from the templates in zetaris/sql/. Sample mode reads data/sample/
and reports the SQL it would have run, so traces show the query either way. In live mode a
SourceError after retries falls back to the sample for that call, traced as "degraded".

CLI: python -m tiresias_agent.adapters.zetaris {bootstrap,smoke} [--sample]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

import httpx

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


class SourceError(Exception):
    """A Zetaris call failed. recoverable=True for timeouts, connection errors and 5xx."""

    def __init__(
        self,
        message: str,
        *,
        recoverable: bool,
        status_code: int | None = None,
        retry_count: int = 0,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.recoverable = recoverable
        self.status_code = status_code
        self.retry_count = retry_count
        self.detail = detail


# --- SQL templates ----------------------------------------------------------------------------


def sql_dir() -> Path:
    return Path(os.getenv("ZETARIS_SQL_DIR") or _REPO_ROOT / "zetaris" / "sql")


def sample_dir() -> Path:
    return Path(os.getenv("SAMPLE_DATA_DIR") or _REPO_ROOT / "data" / "sample")


def load_template(name: str, directory: Path | None = None) -> str:
    """Template text without comment lines or a trailing semicolon."""
    text = ((directory or sql_dir()) / name).read_text(encoding="utf-8")
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
    ) -> QueryResult:
        """Run one statement. `redact` values are masked in the returned/traced SQL and errors."""
        url = f"{self.config.rest_url.rstrip('/')}/sql-editor/sqls/run"
        run_id = run_id or getattr(self.tracer, "run_id", None) or self._default_run_id
        shown_sql = self._scrub(sql, redact)
        wait = timeout or self.config.timeout_seconds
        start = time.perf_counter()
        error: SourceError | None = None
        for attempt in range(self.config.max_retries + 1):
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
            try:
                response = self._http.post(url, json=body, headers=headers, timeout=wait)
            except httpx.TimeoutException:
                error = SourceError(
                    f"Zetaris {label}: timed out after {wait:g} s", recoverable=True
                )
            except httpx.TransportError as exc:
                error = SourceError(
                    f"Zetaris {label}: connection failed ({type(exc).__name__})", recoverable=True
                )
            else:
                if response.status_code < 400:
                    result = self._parse(response, label, shown_sql, start, attempt)
                    if isinstance(result, QueryResult):
                        return result
                    error = result
                else:
                    detail = self._scrub(response.text[:500], redact)
                    error = SourceError(
                        f"Zetaris {label}: HTTP {response.status_code}: {detail[:200]}",
                        recoverable=response.status_code >= 500,
                        status_code=response.status_code,
                        detail=detail,
                    )
            error.retry_count = attempt
            if not error.recoverable or attempt >= self.config.max_retries:
                break
            self._sleep(self.BACKOFF_SECONDS * 2**attempt)
        assert error is not None
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
        )
        raise error

    def _parse(
        self, response: httpx.Response, label: str, sql: str, start: float, attempt: int
    ) -> QueryResult | SourceError:
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            return SourceError(f"Zetaris {label}: response is not JSON", recoverable=False)
        # An empty body (DDL) parses to {} and means no rows; anything that is not an
        # object with list-valued headers and data is malformed, not an empty result.
        headers = payload.get("headers") or [] if isinstance(payload, dict) else None
        data = payload.get("data") or [] if isinstance(payload, dict) else None
        if not isinstance(headers, list) or not isinstance(data, list):
            return SourceError(
                f"Zetaris {label}: response is not an object with headers and data lists",
                recoverable=False,
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
                return SourceError(
                    f"Zetaris {label}: row does not match response headers {columns}",
                    recoverable=False,
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
        )
        return result


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


# --- Evidence sources -------------------------------------------------------------------------


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
        result = self.client.run_sql(sql, label="concept_series")
        facts = [
            Fact(
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
            for r in result.rows
            if r.get("val") is not None
        ]
        return Evidence(
            _dedupe(facts, aliases), sql=sql, source=self.name, duration_ms=result.duration_ms
        )

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS) -> Evidence[Filing]:
        ticker = ticker_of(entity)
        sql = filings_sql(ticker, forms, self.directory)
        if f"{ticker}_filings" not in self.registered_views():
            return Evidence(sql=sql, source=self.name, note=f"no registered view {ticker}_filings")
        result = self.client.run_sql(sql, label="filings")
        rows = [
            Filing(
                accession_number=str(r["accession_number"]),
                filing_date=str(r["filing_date"]),
                report_date=_opt_str(r.get("report_date")),
                form=str(r["form"]),
                items=str(r.get("items") or ""),
                primary_document=_opt_str(r.get("primary_document")),
            )
            for r in result.rows
        ]
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
    """Live first; on a recoverable SourceError (outage, timeout, 5xx, after the client's retries)
    serve the sample, traced degraded. A non-recoverable error (bad SQL, auth, malformed response)
    is a bug or misconfiguration, not an outage: it is raised, never papered over with sample rows."""

    def __init__(
        self, primary: EvidenceSource, fallback: EvidenceSource, tracer: Tracer | None = None
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.tracer = tracer or _NullTracer()
        self.name = primary.name

    def _call(self, method: str, *args: Any) -> Evidence[Any]:
        try:
            return getattr(self.primary, method)(*args)
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
) -> list[dict[str, Any]]:
    """Register the showcase sources (or one entity's ``tables``: the asked entity, or Loop B's
    discovered one). Safe to run twice: "already exists" counts as done.

    A concept the entity never reported gives status "absent" (no view); other failures are
    recorded and the run goes on; steps on a raw table that failed are skipped. Never drops.
    ``deadline`` (a ``clock()`` value, e.g. the end of the run budget) caps each statement's
    timeout at the time left and skips the remaining statements once it has passed.
    """
    ua = sec_user_agent or os.getenv("SEC_USER_AGENT", "")
    failed_tables: set[str] = set()
    report: list[dict[str, Any]] = []
    plan = bootstrap_plan(ua, entities, concepts, tables=tables, discovery=discovery)
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
        try:
            client.run_sql(sql, label=f"bootstrap:{step}", timeout=timeout, redact=[ua])
            # Views use CREATE OR REPLACE, so a re-run reports them as "replaced".
            entry["status"] = _DONE_STATUS.get(step, "created")
        except SourceError as err:
            if "already exist" in err.detail.lower():
                entry["status"] = "exists"
            elif step == "concept_view" and "FIELD_NOT_FOUND" in err.detail:
                entry["status"] = "absent"  # the entity never reported this concept
            else:
                entry["status"] = "failed"
                entry["error"] = str(err)[:200]
                entry["recoverable"] = err.recoverable
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tiresias_agent.adapters.zetaris")
    parser.add_argument("command", choices=["bootstrap", "smoke"])
    parser.add_argument("--sample", action="store_true", help="smoke against data/sample only")
    args = parser.parse_args(argv)
    tracer = _PrintTracer()
    if args.command == "smoke":
        return _smoke(make_source(args.sample, tracer))
    client = ZetarisClient(ZetarisConfig.from_env(), tracer=tracer)
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
    "CONCEPT_ALIASES",
    "Entity",
    "Evidence",
    "EvidenceSource",
    "Fact",
    "FallbackSource",
    "Filing",
    "LiveZetarisSource",
    "QueryResult",
    "SampleSource",
    "SourceError",
    "ZetarisClient",
    "ZetarisConfig",
    "bootstrap",
    "endpoint_cik",
    "lightning_tables",
    "make_source",
    "object_ticker",
]


if __name__ == "__main__":
    sys.exit(main())
