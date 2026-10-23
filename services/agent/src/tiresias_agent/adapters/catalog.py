"""The bounded source catalog (catalog/sources.yaml) and discovery reads over it (Loop B).

The source scout may only read and instantiate templates listed in the catalog (ADR-0003,
docs/architecture.md). This module loads the catalog and serves the two discovery reads the
scout needs, each from a catalog template:
- ``tickers()``: the ``sec_tickers`` map (ticker, CIK, name);
- ``submissions(cik10)``: the ``sec_submissions`` profile and filing history of one registrant.

Sample mode reads ``data/sample/`` (company_tickers.json, <ticker>_submissions.json). Live mode
GETs the catalog endpoint with ``SEC_USER_AGENT`` (timeout, bounded retries, <= the catalog's
rate limit) and falls back to the sample per call on an outage, traced ``degraded``; a
non-recoverable error (bad request, missing user agent, malformed body) is raised, never served
from the sample. Discovery reads are not registered sources: they never count toward
MAX_NEW_SOURCES_PER_RUN (only the onboarder registers sources).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from .zetaris import Evidence, SourceError, _NullTracer, sample_dir

AGENT = "catalog"
_REPO_ROOT = Path(__file__).resolve().parents[5]
_CIK10 = re.compile(r"^\d{10}$")
_USER_AGENT = re.compile(r"^[A-Za-z0-9 .@_+-]{3,200}$")
_LINE = re.compile(
    r"^(?P<indent> *)(?P<dash>- )?(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?: (?P<val>.*))?$"
)

TICKERS = "sec_tickers"
SUBMISSIONS = "sec_submissions"


class CatalogError(ValueError):
    """The catalog file is missing, malformed, or lacks a template the caller needs."""


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    endpoint: str
    params: tuple[str, ...]
    joins_on: tuple[str, ...]
    description: str = ""
    rate_limit_per_second: float | None = None

    def url(self, **params: str) -> str:
        """The endpoint with its {params} filled; every declared param is required."""
        missing = [p for p in self.params if p not in params]
        if missing:
            raise CatalogError(f"{self.id}: missing params {missing}")
        out = self.endpoint
        for name in self.params:
            value = str(params[name])
            if name == "cik10" and not _CIK10.match(value):
                raise CatalogError(f"{self.id}: invalid cik10 {value!r}")
            out = out.replace("{" + name + "}", value)
        return out


@dataclass(frozen=True)
class Catalog:
    entries: tuple[CatalogEntry, ...]
    user_agent_env: str = "SEC_USER_AGENT"
    path: str = ""

    def get(self, entry_id: str) -> CatalogEntry:
        for e in self.entries:
            if e.id == entry_id:
                return e
        raise CatalogError(f"template {entry_id!r} is not in the catalog ({self.path})")

    def ids(self) -> list[str]:
        return [e.id for e in self.entries]


def catalog_path() -> Path:
    return Path(os.getenv("CATALOG_PATH") or _REPO_ROOT / "catalog" / "sources.yaml")


def _scalar(text: str) -> Any:
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [] if not inner else [_scalar(x) for x in inner.split(",")]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def parse_catalog(text: str, path: str = "") -> Catalog:
    """Parse the catalog's YAML subset: top-level scalars and a ``sources:`` list of flat
    mappings whose values are scalars or [flow, lists]. Anything else is a CatalogError, so a
    catalog edit that leaves the subset fails loudly instead of being half-read."""
    top: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    in_sources = False
    key_indent = -1
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw.rstrip())
        if not m:
            raise CatalogError(f"{path}:{n}: unsupported catalog line: {raw.strip()[:60]!r}")
        indent, dash, key, val = len(m["indent"]), m["dash"], m["key"], m["val"]
        if indent == 0 and not dash:
            in_sources = key == "sources"
            if in_sources:
                if val not in (None, ""):
                    raise CatalogError(f"{path}:{n}: 'sources:' must start a list")
                continue
            top[key] = _scalar(val or "")
        elif in_sources and dash:
            if val in (None, ""):
                raise CatalogError(f"{path}:{n}: {key!r} needs a scalar value (no nesting)")
            key_indent = indent + 2
            sources.append({key: _scalar(val)})
        elif in_sources and sources and not dash and indent == key_indent:
            # Flat mappings only: an empty value would open a nested block, and a key at any
            # other indentation belongs to one.
            if val in (None, ""):
                raise CatalogError(f"{path}:{n}: {key!r} needs a scalar value (no nesting)")
            sources[-1][key] = _scalar(val)
        else:
            raise CatalogError(f"{path}:{n}: unexpected indentation: {raw.strip()[:60]!r}")
    entries = []
    for s in sources:
        missing = {"id", "endpoint", "params", "joins_on"} - set(s)
        if missing or not isinstance(s.get("params"), list) or not isinstance(s["joins_on"], list):
            raise CatalogError(f"{path}: source {s.get('id')!r} lacks {sorted(missing) or 'lists'}")
        rate = s.get("rate_limit_per_second")
        entries.append(
            CatalogEntry(
                id=str(s["id"]),
                endpoint=str(s["endpoint"]),
                params=tuple(str(p) for p in s["params"]),
                joins_on=tuple(str(j) for j in s["joins_on"]),
                description=str(s.get("description") or ""),
                rate_limit_per_second=float(rate) if rate else None,
            )
        )
    if not entries:
        raise CatalogError(f"{path}: no sources")
    return Catalog(tuple(entries), str(top.get("user_agent_env") or "SEC_USER_AGENT"), path)


def load_catalog(path: Path | None = None) -> Catalog:
    path = Path(path or catalog_path())
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise CatalogError(f"catalog not readable: {path} ({type(err).__name__})") from err
    return parse_catalog(text, str(path))


# --- Discovery reads ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TickerRow:
    ticker: str
    cik10: str
    title: str


@dataclass(frozen=True)
class FilingRow:
    form: str
    filing_date: str
    items: str
    accession_number: str


@dataclass(frozen=True)
class Profile:
    """One registrant's sec_submissions profile, as the scout needs it."""

    cik10: str
    name: str
    tickers: tuple[str, ...]
    former_names: tuple[dict[str, str], ...]
    filings: tuple[FilingRow, ...] = field(default_factory=tuple)


class Discovery(Protocol):
    name: str
    catalog: Catalog

    def tickers(self) -> Evidence[TickerRow]: ...

    def submissions(self, cik10: str) -> Evidence[Profile]: ...


def _ticker_rows(data: Any) -> list[TickerRow]:
    rows = data.values() if isinstance(data, Mapping) else (data or [])
    out = []
    for r in rows:
        if not isinstance(r, Mapping) or "cik_str" not in r or "ticker" not in r:
            raise SourceError("sec_tickers: row without cik_str/ticker", recoverable=False)
        out.append(
            TickerRow(str(r["ticker"]), f"{int(r['cik_str']):010d}", str(r.get("title", "")))
        )
    return out


def _profile(data: Any, cik10: str) -> Profile:
    if not isinstance(data, Mapping):
        raise SourceError("sec_submissions: body is not an object", recoverable=False)
    recent = (data.get("filings") or {}).get("recent") or {}
    items = recent.get("items") or []
    try:
        filings = tuple(
            FilingRow(
                form=str(recent["form"][i]),
                filing_date=str(recent["filingDate"][i]),
                items=str((items[i] if i < len(items) else "") or ""),
                accession_number=str(accn),
            )
            for i, accn in enumerate(recent.get("accessionNumber") or [])
        )
    except (KeyError, IndexError, TypeError) as err:
        raise SourceError(f"sec_submissions: malformed filings ({err})", recoverable=False) from err
    former = tuple(
        {k: str(v) for k, v in f.items()}
        for f in data.get("formerNames") or []
        if isinstance(f, Mapping)
    )
    return Profile(
        cik10=cik10,
        name=str(data.get("name") or ""),
        tickers=tuple(str(t) for t in data.get("tickers") or []),
        former_names=former,
        filings=filings,
    )


class SampleDiscovery:
    """Catalog reads served from data/sample/ (sample mode, and the live fallback)."""

    name = "sample"

    def __init__(
        self, catalog: Catalog, data_dir: Path | None = None, *, tracer: Any = None
    ) -> None:
        self.catalog = catalog
        self.data_dir = Path(data_dir or sample_dir())
        self.tracer = tracer or _NullTracer()

    def _read(self, name: str) -> Any:
        path = self.data_dir / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            raise SourceError(f"sample {name}: unreadable ({err})", recoverable=False) from err

    def _emit(self, action: str, result: Evidence[Any], start: float) -> Evidence[Any]:
        result.duration_ms = round((time.perf_counter() - start) * 1000)
        self.tracer.emit(
            agent=AGENT,
            action=action,
            status="success",
            sql=result.sql,
            rows=len(result),
            duration_ms=result.duration_ms,
            source=self.name,
            output_summary=result.note,
        )
        return result

    def tickers(self) -> Evidence[TickerRow]:
        start = time.perf_counter()
        url = self.catalog.get(TICKERS).url()
        data = self._read("company_tickers.json")
        rows = _ticker_rows(data) if data is not None else []
        note = (
            "served from data/sample/company_tickers.json"
            if data is not None
            else ("not in sample: company_tickers.json")
        )
        return self._emit(
            "tickers", Evidence(rows, sql=f"GET {url}", source=self.name, note=note), start
        )

    def submissions(self, cik10: str) -> Evidence[Profile]:
        start = time.perf_counter()
        url = self.catalog.get(SUBMISSIONS).url(cik10=cik10)
        found: list[Profile] = []
        note = f"not in sample: submissions for CIK {cik10}"
        for path in sorted(self.data_dir.glob("*_submissions.json")):
            data = self._read(path.name)
            if isinstance(data, Mapping) and str(data.get("cik", "")).zfill(10) == cik10:
                found = [_profile(data, cik10)]
                note = f"served from data/sample/{path.name}"
                break
        return self._emit(
            "submissions", Evidence(found, sql=f"GET {url}", source=self.name, note=note), start
        )


class LiveDiscovery:
    """GETs the catalog endpoints (SEC) with SEC_USER_AGENT, a timeout and bounded retries."""

    name = "sec"
    BACKOFF_SECONDS = 0.5

    def __init__(
        self,
        catalog: Catalog,
        *,
        tracer: Any = None,
        user_agent: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.catalog = catalog
        self.tracer = tracer or _NullTracer()
        self.user_agent = (
            user_agent if user_agent is not None else os.getenv(catalog.user_agent_env, "")
        )
        self.timeout = timeout_seconds or float(os.getenv("SOURCE_TIMEOUT_SECONDS") or 20)
        self.max_retries = max(0, min(max_retries, 5))
        self._http = httpx.Client(timeout=self.timeout, transport=transport)
        self._sleep = sleep
        self._clock = clock
        self._last = 0.0

    def _get(self, entry: CatalogEntry, url: str, label: str) -> Any:
        if not _USER_AGENT.match(self.user_agent or ""):
            raise SourceError(
                f"{label}: {self.catalog.user_agent_env} is missing or invalid (SEC fair access)",
                recoverable=False,
            )
        gap = 1.0 / (entry.rate_limit_per_second or 10)
        error: SourceError | None = None
        for attempt in range(self.max_retries + 1):
            wait = self._last + gap - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            try:
                response = self._http.get(url, headers={"User-Agent": self.user_agent})
            except httpx.TimeoutException:
                error = SourceError(
                    f"{label}: timed out after {self.timeout:g} s", recoverable=True
                )
            except httpx.TransportError as exc:
                error = SourceError(
                    f"{label}: connection failed ({type(exc).__name__})", recoverable=True
                )
            else:
                if response.status_code == 404:
                    return None
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError:
                        raise SourceError(f"{label}: body is not JSON", recoverable=False) from None
                error = SourceError(
                    f"{label}: HTTP {response.status_code}",
                    recoverable=response.status_code >= 500 or response.status_code == 429,
                    status_code=response.status_code,
                )
            error.retry_count = attempt
            if not error.recoverable:
                break
            if attempt < self.max_retries:
                self._sleep(self.BACKOFF_SECONDS * 2**attempt)
        assert error is not None
        raise error

    def _emit(self, action: str, result: Evidence[Any], start: float) -> Evidence[Any]:
        result.duration_ms = round((time.perf_counter() - start) * 1000)
        self.tracer.emit(
            agent=AGENT,
            action=action,
            status="success",
            sql=result.sql,
            rows=len(result),
            duration_ms=result.duration_ms,
            source=self.name,
        )
        return result

    def tickers(self) -> Evidence[TickerRow]:
        start = time.perf_counter()
        entry = self.catalog.get(TICKERS)
        url = entry.url()
        data = self._get(entry, url, TICKERS)
        rows = _ticker_rows(data) if data is not None else []
        return self._emit("tickers", Evidence(rows, sql=f"GET {url}", source=self.name), start)

    def submissions(self, cik10: str) -> Evidence[Profile]:
        start = time.perf_counter()
        entry = self.catalog.get(SUBMISSIONS)
        url = entry.url(cik10=cik10)
        data = self._get(entry, url, SUBMISSIONS)
        found = [_profile(data, cik10)] if data is not None else []
        note = None if found else f"no submissions for CIK {cik10}"
        return self._emit(
            "submissions", Evidence(found, sql=f"GET {url}", source=self.name, note=note), start
        )


class FallbackDiscovery:
    """Live first; on a recoverable error serve the sample, traced degraded. A non-recoverable
    error is raised, never papered over with sample rows (same contract as FallbackSource)."""

    def __init__(self, primary: Discovery, fallback: Discovery, tracer: Any = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.catalog = primary.catalog
        self.tracer = tracer or _NullTracer()
        self.name = primary.name

    def _call(self, method: str, *args: Any) -> Evidence[Any]:
        try:
            return getattr(self.primary, method)(*args)
        except SourceError as err:
            if not err.recoverable:
                raise
            result = getattr(self.fallback, method)(*args)
            result.degraded = True
            result.error = str(err)
            self.tracer.emit(
                agent=AGENT,
                action=method,
                status="degraded",
                sql=result.sql,
                rows=len(result),
                retry_count=err.retry_count,
                source=result.source,
                output_summary=f"live catalog read failed, served from sample: {err}",
            )
            return result

    def tickers(self) -> Evidence[TickerRow]:
        return self._call("tickers")

    def submissions(self, cik10: str) -> Evidence[Profile]:
        return self._call("submissions", cik10)


def make_discovery(
    sample_mode: bool,
    tracer: Any = None,
    *,
    catalog: Catalog | None = None,
    data_dir: Path | None = None,
) -> Discovery:
    """Sample mode: SampleDiscovery. Live: the catalog endpoints, falling back to the sample."""
    catalog = catalog or load_catalog()
    sample = SampleDiscovery(catalog, data_dir, tracer=tracer)
    if sample_mode:
        return sample
    return FallbackDiscovery(LiveDiscovery(catalog, tracer=tracer), sample, tracer)


__all__ = [
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "Discovery",
    "FallbackDiscovery",
    "FilingRow",
    "LiveDiscovery",
    "Profile",
    "SampleDiscovery",
    "TickerRow",
    "load_catalog",
    "make_discovery",
    "parse_catalog",
]
