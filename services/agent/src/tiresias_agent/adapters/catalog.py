"""The bounded source catalog (catalog/sources.yaml) and discovery reads over it (Loop B, and
the asked-entity resolver).

The source scout may only read and instantiate templates listed in the catalog (ADR-0003,
docs/architecture.md). This module loads the catalog and serves the discovery reads, each from
a catalog template and, in live mode, **only through Zetaris** (ADR-0003; BACKLOG B-4 closed):
- ``lookup(tickers=, ciks=)``, ``brand(words)``, ``search(name)``: rows of the ``sec_tickers``
  map (ticker, CIK, name), queried from the Zetaris view ``tiresias.sec_tickers``
  (zetaris/sql/13_tickers_table.sql, 22_tickers_view.sql, 34..36_tickers_by_*.sql);
- ``submissions(cik10, ticker)``: the ``sec_submissions`` profile and filing history of one
  registrant, read from its REST table ``TIRESIAS_SEC.<ticker>_submissions_raw``
  (11_submissions_table.sql), registered on demand (37_submissions_profile.sql);
- ``filers_naming(phrase, forms, start, end)``: the filers whose filings of those forms in that
  date range contain the phrase (catalog ``sec_fulltext``, EDGAR full-text search), one
  ``TIRESIAS_SEC.fts_<hash>_raw`` table per search registered on demand (14_fulltext_table.sql,
  40_fulltext_total.sql, 41_fulltext_hits.sql). Sample mode serves the searches cached in
  ``data/sample/fts_<hash>.json`` (the same hash as the live table, so the same search finds
  the same file) and aggregates their hits as 41_fulltext_hits.sql does
  (``fulltext_hit_rows``); any other search returns no rows and says so.

Sample mode reads ``data/sample/`` (company_tickers.json, <ticker>_submissions.json,
fts_<hash>.json) and reports the SQL live mode would have run. Live mode falls back to the sample per call on an outage
(recoverable SourceError), traced ``degraded``; a non-recoverable error (bad SQL, auth, a table
bound to another CIK) is raised, never served from the sample.

Discovery objects are not evidence sources: the ``sec_tickers`` table/view (registered once), the
per-candidate submissions tables (at most MAX_CANDIDATES per scout run) and the full-text search
tables (at most two per scout run) never count toward MAX_NEW_SOURCES_PER_RUN. Only the onboarder registers evidence sources. Nothing in this module
opens an HTTP connection of its own: every live read is a Lightning SQL statement sent by
``adapters.zetaris.ZetarisClient``, and Zetaris fetches SEC with ``SEC_USER_AGENT``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .zetaris import (
    BOOTSTRAP_TIMEOUT_SECONDS,
    TICKERS_VIEW,
    Evidence,
    QueryResult,
    SourceError,
    ZetarisClient,
    ZetarisConfig,
    _NullTracer,
    already_exists,
    bootstrap,
    endpoint_cik,
    lightning_tables,
    load_template,
    object_ticker,
    render_file,
    sample_dir,
    uncache,
)

AGENT = "catalog"
_REPO_ROOT = Path(__file__).resolve().parents[5]
_CIK10 = re.compile(r"^\d{10}$")
_USER_AGENT = re.compile(r"^[A-Za-z0-9 .@_+-]{3,200}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,11}$")
_WORD = re.compile(r"^[a-z0-9&.-]{1,40}$")
_NAME = re.compile(r"^[a-z0-9 &.,()-]{2,80}$")
_PHRASE = re.compile(r"^[a-z0-9][a-z0-9 &.,'-]{0,79}$")
_FORM = re.compile(r"^[0-9A-Z][0-9A-Z/-]{0,15}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LINE = re.compile(
    r"^(?P<indent> *)(?P<dash>- )?(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?: (?P<val>.*))?$"
)

TICKERS = "sec_tickers"
SUBMISSIONS = "sec_submissions"
FULLTEXT = "sec_fulltext"
SYMBOL_LIMIT = 20
BRAND_LIMIT = 50
NAME_LIMIT = 20
FULLTEXT_LIMIT = 20  # filers per search (one SEC page holds at most 100 hits)


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
    report_date: str = ""


@dataclass(frozen=True)
class Profile:
    """One registrant's sec_submissions profile, as the scout needs it."""

    cik10: str
    name: str
    tickers: tuple[str, ...]
    former_names: tuple[dict[str, str], ...]
    filings: tuple[FilingRow, ...] = field(default_factory=tuple)
    sic: str = ""  # SEC standard industry code ("" when the registrant has none)


@dataclass(frozen=True)
class NamingRow:
    """One filer in a full-text search: how many of its documents contain the phrase."""

    cik10: str
    name: str
    hits: int
    first_date: str
    last_date: str
    forms: tuple[str, ...]
    items: tuple[str, ...]
    total: int  # hits of the whole search (all filers)


class Discovery(Protocol):
    name: str
    catalog: Catalog

    def lookup(
        self, *, tickers: Sequence[str] = (), ciks: Sequence[str] = ()
    ) -> Evidence[TickerRow]: ...

    def brand(self, words: Sequence[str]) -> Evidence[TickerRow]: ...

    def search(self, name: str) -> Evidence[TickerRow]: ...

    def submissions(self, cik10: str, ticker: str) -> Evidence[Profile]: ...

    def registrant(self, cik10: str, ticker: str) -> Evidence[Profile]: ...

    def filers_naming(
        self, phrase: str, forms: Sequence[str], start: str, end: str
    ) -> Evidence[NamingRow]: ...


# --- SQL (the same text in sample and live mode) -----------------------------------------------


def _symbols(tickers: Sequence[str]) -> list[str]:
    out = []
    for t in tickers:
        t = str(t or "").strip().upper()
        if t and not _SYMBOL.match(t):
            raise ValueError(f"invalid ticker {t!r}")
        if t:
            out.append(t)
    return out


def _ciks(ciks: Sequence[str]) -> list[int]:
    out = []
    for c in ciks:
        c = str(c or "").strip()
        if c and not c.isdigit():
            raise ValueError(f"invalid CIK {c!r}")
        if c:
            out.append(int(c))
    return out


def _brand_words(words: Sequence[str]) -> list[str]:
    return sorted({w.strip().lower() for w in words if _WORD.match(w.strip().lower())})


def name_pattern(name: str) -> str:
    """A lower-case LIKE pattern for a company name; an apostrophe becomes the one-character
    wildcard (Spark SQL has no doubled-quote escape). Raises ValueError for anything else."""
    text = " ".join(str(name or "").lower().replace("'", "_").split())
    if not _NAME.match(text.replace("_", "")) or "%" in text:
        raise ValueError(f"invalid company name {name!r}")
    return f"%{text}%"


def tickers_by_symbol_sql(
    tickers: Sequence[str], ciks: Sequence[str], directory: Path | None = None
) -> str:
    syms, nums = _symbols(tickers), _ciks(ciks)
    if not syms and not nums:
        raise ValueError("a ticker or a CIK is required")
    return render_file(
        "34_tickers_by_symbol.sql",
        directory,
        tickers=", ".join(f"'{s}'" for s in syms) or "NULL",
        ciks=", ".join(str(n) for n in nums) or "NULL",
        limit=str(SYMBOL_LIMIT),
    )


def tickers_by_brand_sql(words: Sequence[str], directory: Path | None = None) -> str:
    clean = _brand_words(words)
    if not clean:
        raise ValueError("no usable brand word")
    return render_file(
        "35_tickers_by_brand.sql",
        directory,
        words=", ".join(f"'{w}'" for w in clean),
        limit=str(BRAND_LIMIT),
    )


def tickers_by_name_sql(name: str, directory: Path | None = None) -> str:
    return render_file(
        "36_tickers_by_name.sql",
        directory,
        pattern=f"'{name_pattern(name)}'",
        limit=str(NAME_LIMIT),
    )


def submissions_table(ticker: str) -> str:
    return f"{object_ticker(ticker)}_submissions_raw"


def submissions_sql(ticker: str, directory: Path | None = None) -> str:
    return render_file("37_submissions_profile.sql", directory, table=submissions_table(ticker))


def registrant_sql(ticker: str, directory: Path | None = None) -> str:
    """The submissions profile with ``sic`` and per-filing ``report_date`` (agents/scope.py)."""
    return render_file("38_registrant_profile.sql", directory, table=submissions_table(ticker))


def _first_word(text: str) -> str:
    parts = text.strip().split()
    return parts[0].lower() if parts else ""


def fulltext_endpoint(catalog: Catalog, phrase: str, forms: Sequence[str], start: str, end: str):
    """The sec_fulltext URL for an exact-phrase search, percent-encoded (no quote or space can
    reach the SQL string). Raises ValueError for a phrase, form or date outside its pattern."""
    text = " ".join(str(phrase or "").lower().split())
    if not _PHRASE.match(text) or len(text.replace(" ", "")) < 2:
        raise ValueError(f"invalid search phrase {phrase!r}")
    clean = [str(f).strip().upper() for f in forms]
    if not clean or not all(_FORM.match(f) for f in clean):
        raise ValueError(f"invalid forms {forms!r}")
    # Root forms only: the search matches amendments (10-12B/A) under their root form, and a
    # form with "/A" in the list makes it return nothing at all (measured 2026-10-23).
    clean = list(dict.fromkeys(f.split("/")[0] for f in clean))
    for day in (start, end):
        if not _DAY.match(str(day)):
            raise ValueError(f"invalid date {day!r}")
    return catalog.get(FULLTEXT).url(
        phrase=quote(f'"{text}"', safe=""),
        forms=",".join(clean),
        start=start,
        end=end,
    )


def fulltext_table(endpoint: str) -> str:
    """One REST table per search, named after its endpoint (the same search reuses it)."""
    return f"fts_{hashlib.sha256(endpoint.encode()).hexdigest()[:16]}_raw"


def fulltext_sample_file(table: str) -> str:
    """The data/sample/ file that caches one search: the live table's name without ``_raw``."""
    return f"{table.removesuffix('_raw')}.json"


def fulltext_total(data: Any) -> int:
    """40_fulltext_total.sql over a search response (hits.total.value)."""
    try:
        return int(((data.get("hits") or {}).get("total") or {}).get("value") or 0)
    except (AttributeError, TypeError, ValueError) as err:
        raise SourceError(
            f"{FULLTEXT}: malformed total ({type(err).__name__})", recoverable=False
        ) from err


def fulltext_hit_rows(data: Any, limit: int = FULLTEXT_LIMIT) -> list[dict[str, Any]]:
    """41_fulltext_hits.sql over a search response, in Python (sample mode): one row per filer
    CIK of each hit, grouped by CIK, most hits first (then CIK descending), at most ``limit``.
    The columns are the SQL's: cik, name (first display name), hits, first/last date, forms and
    items (sorted, distinct, comma-joined), total."""
    try:
        total = fulltext_total(data)
        groups: dict[str, dict[str, Any]] = {}
        for h in (data.get("hits") or {}).get("hits") or []:
            s = h["_source"]
            names = s.get("display_names") or []
            for i, cik in enumerate(s.get("ciks") or []):
                g = groups.setdefault(
                    str(cik),
                    {
                        "cik": str(cik),
                        "name": names[i] if i < len(names) else None,
                        "hits": 0,
                        "dates": [],
                        "forms": set(),
                        "items": set(),
                    },
                )
                g["hits"] += 1
                if s.get("file_date"):
                    g["dates"].append(str(s["file_date"]))
                if s.get("form"):
                    g["forms"].add(str(s["form"]))
                g["items"].update(str(x) for x in s.get("items") or [])
    except (AttributeError, KeyError, TypeError) as err:
        raise SourceError(
            f"{FULLTEXT}: malformed hits ({type(err).__name__})", recoverable=False
        ) from err
    ordered = sorted(groups.values(), key=lambda g: (g["hits"], g["cik"]), reverse=True)
    return [
        {
            "cik": g["cik"],
            "name": g["name"],
            "hits": g["hits"],
            "first_date": min(g["dates"], default=None),
            "last_date": max(g["dates"], default=None),
            "forms": ",".join(sorted(g["forms"])),
            "items": ",".join(sorted(g["items"])),
            "total": total,
        }
        for g in ordered[:limit]
    ]


def fulltext_total_sql(table: str, directory: Path | None = None) -> str:
    return render_file("40_fulltext_total.sql", directory, table=table)


def fulltext_hits_sql(table: str, directory: Path | None = None) -> str:
    return render_file("41_fulltext_hits.sql", directory, table=table, limit=str(FULLTEXT_LIMIT))


def _naming_rows(rows: Sequence[Mapping[str, Any]]) -> list[NamingRow]:
    """41_fulltext_hits.sql rows (Zetaris, or ``fulltext_hit_rows``) -> NamingRow, in order."""
    out = []
    for r in rows:
        try:
            out.append(
                NamingRow(
                    cik10=f"{int(r['cik']):010d}",
                    name=str(r.get("name") or ""),
                    hits=int(r.get("hits") or 0),
                    first_date=str(r.get("first_date") or ""),
                    last_date=str(r.get("last_date") or ""),
                    forms=tuple(f for f in str(r.get("forms") or "").split(",") if f),
                    items=tuple(i for i in str(r.get("items") or "").split(",") if i),
                    total=int(r.get("total") or 0),
                )
            )
        except (KeyError, TypeError, ValueError) as err:
            raise SourceError(
                f"{FULLTEXT}: malformed row ({type(err).__name__})", recoverable=False
            ) from err
    return out


# --- row parsing ---------------------------------------------------------------------------------


def _ticker_rows(data: Any) -> list[TickerRow]:
    """company_tickers.json (object of {cik_str, ticker, title}) -> rows."""
    rows = data.values() if isinstance(data, Mapping) else (data or [])
    out = []
    for r in rows:
        if not isinstance(r, Mapping) or "cik_str" not in r or "ticker" not in r:
            raise SourceError("sec_tickers: row without cik_str/ticker", recoverable=False)
        out.append(
            TickerRow(str(r["ticker"]), f"{int(r['cik_str']):010d}", str(r.get("title", "")))
        )
    return out


def _query_rows(result: QueryResult) -> list[TickerRow]:
    """tiresias.sec_tickers rows (cik, name, ticker, exchange) -> rows."""
    out = []
    for r in result.rows:
        try:
            out.append(TickerRow(str(r["ticker"]), f"{int(r['cik']):010d}", str(r.get("name", ""))))
        except (KeyError, TypeError, ValueError) as err:
            raise SourceError(
                f"sec_tickers: malformed row ({type(err).__name__})", recoverable=False
            ) from err
    return out


def _profile(data: Any, cik10: str) -> Profile:
    """A submissions JSON document (sample) -> Profile."""
    if not isinstance(data, Mapping):
        raise SourceError("sec_submissions: body is not an object", recoverable=False)
    recent = (data.get("filings") or {}).get("recent") or {}
    items = recent.get("items") or []
    reports = recent.get("reportDate") or []
    try:
        filings = tuple(
            FilingRow(
                form=str(recent["form"][i]),
                filing_date=str(recent["filingDate"][i]),
                items=str((items[i] if i < len(items) else "") or ""),
                accession_number=str(accn),
                report_date=str((reports[i] if i < len(reports) else "") or ""),
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
        sic=str(data.get("sic") or ""),
    )


def _json_list(text: Any) -> list[Any]:
    if isinstance(text, list):
        return text
    try:
        value = json.loads(text) if text not in (None, "", "null") else []
    except (TypeError, ValueError):
        raise SourceError("sec_submissions: column is not JSON", recoverable=False) from None
    return value if isinstance(value, list) else []


def _profile_rows(result: QueryResult, cik10: str) -> Profile | None:
    """37_submissions_profile.sql rows (profile columns repeated per filing) -> Profile."""
    if not result.rows:
        return None
    head = result.rows[0]
    former = tuple(
        {k: str(v) for k, v in f.items()}
        for f in _json_list(head.get("former_names"))
        if isinstance(f, Mapping)
    )
    filings = tuple(
        FilingRow(
            form=str(r.get("form") or ""),
            filing_date=str(r.get("filing_date") or ""),
            items=str(r.get("items") or ""),
            accession_number=str(r["accession_number"]),
            report_date=str(r.get("report_date") or ""),
        )
        for r in result.rows
        if r.get("accession_number") not in (None, "", "null")
    )
    return Profile(
        cik10=cik10,
        name=str(head.get("name") or ""),
        tickers=tuple(str(t) for t in _json_list(head.get("tickers"))),
        former_names=former,
        filings=filings,
        sic=str(head.get("sic") or ""),
    )


# --- sample ------------------------------------------------------------------------------------


class SampleDiscovery:
    """Discovery served from data/sample/ (sample mode, and the live fallback). Filters the
    same way as the live SQL and reports that SQL."""

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

    def _tickers(self, sql: str, keep: Callable[[TickerRow], bool], limit: int, start: float):
        self.catalog.get(TICKERS)
        data = self._read("company_tickers.json")
        rows = [r for r in (_ticker_rows(data) if data is not None else []) if keep(r)]
        rows.sort(key=lambda r: r.cik10, reverse=True)
        note = (
            "served from data/sample/company_tickers.json"
            if data is not None
            else "not in sample: company_tickers.json"
        )
        return self._emit(
            "tickers", Evidence(rows[:limit], sql=sql, source=self.name, note=note), start
        )

    def lookup(
        self, *, tickers: Sequence[str] = (), ciks: Sequence[str] = ()
    ) -> Evidence[TickerRow]:
        start = time.perf_counter()
        sql = tickers_by_symbol_sql(tickers, ciks)
        syms, nums = set(_symbols(tickers)), set(_ciks(ciks))
        return self._tickers(
            sql,
            lambda r: r.ticker.upper() in syms or int(r.cik10) in nums,
            SYMBOL_LIMIT,
            start,
        )

    def brand(self, words: Sequence[str]) -> Evidence[TickerRow]:
        start = time.perf_counter()
        sql = tickers_by_brand_sql(words)
        wanted = set(_brand_words(words))
        return self._tickers(sql, lambda r: _first_word(r.title) in wanted, BRAND_LIMIT, start)

    def search(self, name: str) -> Evidence[TickerRow]:
        start = time.perf_counter()
        sql = tickers_by_name_sql(name)
        rx = re.compile(re.escape(name_pattern(name).strip("%")).replace("_", "."))
        return self._tickers(sql, lambda r: bool(rx.search(r.title.lower())), NAME_LIMIT, start)

    def submissions(self, cik10: str, ticker: str) -> Evidence[Profile]:
        start = time.perf_counter()
        if not _CIK10.match(cik10):
            raise CatalogError(f"{SUBMISSIONS}: invalid cik10 {cik10!r}")
        self.catalog.get(SUBMISSIONS)
        sql = submissions_sql(ticker)
        found: list[Profile] = []
        note = f"not in sample: submissions for CIK {cik10}"
        for path in sorted(self.data_dir.glob("*_submissions.json")):
            data = self._read(path.name)
            if isinstance(data, Mapping) and str(data.get("cik", "")).zfill(10) == cik10:
                found = [_profile(data, cik10)]
                note = f"served from data/sample/{path.name}"
                break
        return self._emit(
            "submissions", Evidence(found, sql=sql, source=self.name, note=note), start
        )

    def registrant(self, cik10: str, ticker: str) -> Evidence[Profile]:
        """The same profile (with sic and report dates), reporting registrant_sql."""
        result = self.submissions(cik10, ticker)
        result.sql = registrant_sql(ticker)
        return result

    def filers_naming(
        self, phrase: str, forms: Sequence[str], start: str, end: str
    ) -> Evidence[NamingRow]:
        """The same search as live mode, served from its cached response in data/sample/
        (``fts_<hash>.json``, named after the live table) with the 40/41_fulltext_*.sql logic in
        Python. A search the slice does not cache returns no rows and the note says so. The SQL
        is what live mode would run."""
        t0 = time.perf_counter()
        endpoint = fulltext_endpoint(self.catalog, phrase, forms, start, end)
        table = fulltext_table(endpoint)
        sql = fulltext_hits_sql(table)
        name = fulltext_sample_file(table)
        data = self._read(name)
        if data is None:
            note = (
                "not in sample: this full-text search (data/sample/ caches only the searches "
                "scripts/fetch_sample.py lists; live mode runs any)"
            )
            return self._emit("fulltext", Evidence([], sql=sql, source=self.name, note=note), t0)
        total = fulltext_total(data)
        rows = _naming_rows(fulltext_hit_rows(data)) if total else []
        note = (
            f"{total} matching document(s); {len(rows)} filer(s) shown; "
            f"served from data/sample/{name}"
        )
        return self._emit("fulltext", Evidence(rows, sql=sql, source=self.name, note=note), t0)


# --- live: Zetaris only --------------------------------------------------------------------------


class _Released:
    """Discovery tables this process uncached (``ZetarisDiscovery.release_cache``): a later read
    re-caches them first. Bounded: the oldest names are forgotten past ``limit``."""

    def __init__(self, limit: int = 512) -> None:
        self._lock = threading.Lock()
        self._names: dict[str, None] = {}
        self._limit = limit

    def add(self, table: str) -> None:
        with self._lock:
            self._names.pop(table, None)
            self._names[table] = None
            while len(self._names) > self._limit:
                self._names.pop(next(iter(self._names)))

    def discard(self, table: str) -> None:
        with self._lock:
            self._names.pop(table, None)

    def has(self, table: str) -> bool:
        with self._lock:
            return table in self._names

    def clear(self) -> None:
        with self._lock:
            self._names.clear()


RELEASED = _Released()


class ZetarisDiscovery:
    """Discovery reads as Lightning SQL through ``ZetarisClient`` (no HTTP of its own).

    - The ``sec_tickers`` table and view are registered on first use if bootstrap has not
      registered them (catalog objects, not evidence sources; never counted in the cap).
    - ``submissions(cik10, ticker)`` registers ``<ticker>_submissions_raw`` on demand (CREATE +
      CACHE, the same template as bootstrap) and refuses a table bound to another CIK.
      Statements that make Zetaris fetch SEC are paced at the catalog rate limit.
    - ``release_cache(keep=)`` uncaches the submissions and full-text tables this instance
      cached, except ``keep``'s (BACKLOG B-24; the orchestrator calls it after the source scout
      and at the end of a live run). They stay registered; a released table read again by a
      later scout is re-cached on demand (``RELEASED`` remembers which, per process).
    """

    name = "zetaris"

    def __init__(
        self,
        catalog: Catalog,
        client: ZetarisClient,
        *,
        tracer: Any = None,
        user_agent: str | None = None,
        directory: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.catalog = catalog
        self.client = client
        self.tracer = tracer or _NullTracer()
        self.user_agent = (
            user_agent if user_agent is not None else os.getenv(catalog.user_agent_env, "")
        )
        self.directory = directory
        self._sleep = sleep
        self._clock = clock
        self._last = 0.0
        self._tickers_ready = False
        self._tables: dict[str, str] | None = None
        self.registered: list[str] = []  # discovery objects this instance registered
        self.cached: list[str] = []  # submissions / full-text tables this instance cached

    def _check_ua(self, label: str) -> None:
        if not _USER_AGENT.match(self.user_agent or ""):
            raise SourceError(
                f"{label}: {self.catalog.user_agent_env} is missing or invalid (SEC fair access)",
                recoverable=False,
            )

    def _pace(self, entry_id: str) -> None:
        rate = self.catalog.get(entry_id).rate_limit_per_second or 10
        wait = self._last + 1.0 / rate - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()

    def _emit(self, action: str, result: Evidence[Any], start: float, **extra: Any) -> None:
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
            **extra,
        )

    def _ensure_tickers(self) -> None:
        if self._tickers_ready:
            return
        views = self.client.run_sql(
            load_template("33_views.sql", self.directory), label="list_views"
        )
        names = {str(r.get("table_identifier") or "").lower() for r in views.rows}
        if TICKERS_VIEW not in names:
            self._check_ua(TICKERS)
            self._pace(TICKERS)
            start = time.perf_counter()
            report = bootstrap(
                self.client, sec_user_agent=self.user_agent, entities=(), discovery=True
            )
            bad = [e for e in report if e["status"] in {"failed", "skipped"}]
            self.tracer.emit(
                agent=AGENT,
                action="register_tickers",
                status="error" if bad else "success",
                source=self.name,
                rows=len(report),
                duration_ms=round((time.perf_counter() - start) * 1000),
                output_summary="; ".join(
                    f"{e['step']} {e['object']}: {e['status']}" for e in report
                )[:400],
            )
            if bad:
                raise SourceError(
                    f"{TICKERS}: registration failed ({bad[0].get('error', bad[0]['status'])})",
                    recoverable=bool(bad[0].get("recoverable")),
                )
            self.registered.append(TICKERS_VIEW)
        self._tickers_ready = True

    def _tickers(self, sql: str, start: float) -> Evidence[TickerRow]:
        self._ensure_tickers()
        result = self.client.run_sql(sql, label="discovery_tickers")
        out = Evidence(_query_rows(result), sql=sql, source=self.name)
        self._emit("tickers", out, start)
        return out

    def lookup(
        self, *, tickers: Sequence[str] = (), ciks: Sequence[str] = ()
    ) -> Evidence[TickerRow]:
        start = time.perf_counter()
        return self._tickers(tickers_by_symbol_sql(tickers, ciks, self.directory), start)

    def brand(self, words: Sequence[str]) -> Evidence[TickerRow]:
        start = time.perf_counter()
        return self._tickers(tickers_by_brand_sql(words, self.directory), start)

    def search(self, name: str) -> Evidence[TickerRow]:
        start = time.perf_counter()
        return self._tickers(tickers_by_name_sql(name, self.directory), start)

    def _ensure_submissions(self, cik10: str, ticker: str) -> str:
        table = submissions_table(ticker)
        if self._tables is None:
            self._tables = lightning_tables(self.client, self.directory)
        if table in self._tables:
            bound = endpoint_cik(self._tables[table])
            if bound != cik10:
                raise SourceError(
                    f"{SUBMISSIONS}: {table} is bound to CIK {bound}, not {cik10}",
                    recoverable=False,
                )
            self._recache(table, SUBMISSIONS)
            return table
        self._check_ua(SUBMISSIONS)
        ua = self.user_agent
        start = time.perf_counter()
        create = render_file(
            "11_submissions_table.sql",
            self.directory,
            ticker=object_ticker(ticker),
            cik10=cik10,
            sec_user_agent=ua,
        )
        cache = render_file("12_cache_table.sql", self.directory, table=table)
        timeout = max(self.client.config.timeout_seconds, BOOTSTRAP_TIMEOUT_SECONDS)
        for label, sql in (("discovery_submissions_table", create), ("discovery_cache", cache)):
            self._pace(SUBMISSIONS)
            try:
                self.client.run_sql(
                    sql, label=label, timeout=timeout, redact=[ua], expected=already_exists
                )
            except SourceError as err:
                if not already_exists(err):
                    raise
        self._tables[table] = self.catalog.get(SUBMISSIONS).url(cik10=cik10)
        self.registered.append(table)
        self._cached(table)
        self.tracer.emit(
            agent=AGENT,
            action="register_submissions",
            status="success",
            source=self.name,
            sql=create.replace(ua, "***") if ua else create,
            rows=0,
            duration_ms=round((time.perf_counter() - start) * 1000),
            output_summary=f"discovery table TIRESIAS_SEC.{table} for CIK {cik10} "
            "(catalog read, not counted in MAX_NEW_SOURCES_PER_RUN)",
        )
        return table

    def submissions(self, cik10: str, ticker: str) -> Evidence[Profile]:
        start = time.perf_counter()
        if not _CIK10.match(cik10):
            raise CatalogError(f"{SUBMISSIONS}: invalid cik10 {cik10!r}")
        self._ensure_submissions(cik10, ticker)
        sql = submissions_sql(ticker, self.directory)
        result = self.client.run_sql(sql, label="discovery_submissions")
        profile = _profile_rows(result, cik10)
        note = None if profile else f"no submissions for CIK {cik10}"
        out = Evidence([profile] if profile else [], sql=sql, source=self.name, note=note)
        self._emit("submissions", out, start)
        return out

    def registrant(self, cik10: str, ticker: str) -> Evidence[Profile]:
        """The profile with sic and report dates (38_registrant_profile.sql); same table."""
        start = time.perf_counter()
        if not _CIK10.match(cik10):
            raise CatalogError(f"{SUBMISSIONS}: invalid cik10 {cik10!r}")
        self._ensure_submissions(cik10, ticker)
        sql = registrant_sql(ticker, self.directory)
        result = self.client.run_sql(sql, label="discovery_registrant")
        profile = _profile_rows(result, cik10)
        note = None if profile else f"no submissions for CIK {cik10}"
        out = Evidence([profile] if profile else [], sql=sql, source=self.name, note=note)
        self._emit("registrant", out, start)
        return out

    def _ensure_fulltext(self, endpoint: str) -> str:
        table = fulltext_table(endpoint)
        if self._tables is None:
            self._tables = lightning_tables(self.client, self.directory)
        if table in self._tables:
            self._recache(table, FULLTEXT)
            return table
        self._check_ua(FULLTEXT)
        ua = self.user_agent
        start = time.perf_counter()
        create = render_file(
            "14_fulltext_table.sql",
            self.directory,
            table=table,
            endpoint=endpoint,
            sec_user_agent=ua,
        )
        cache = render_file("12_cache_table.sql", self.directory, table=table)
        timeout = max(self.client.config.timeout_seconds, BOOTSTRAP_TIMEOUT_SECONDS)
        for label, sql in (("discovery_fulltext_table", create), ("discovery_cache", cache)):
            self._pace(FULLTEXT)
            try:
                self.client.run_sql(
                    sql, label=label, timeout=timeout, redact=[ua], expected=already_exists
                )
            except SourceError as err:
                if not already_exists(err):
                    raise
        self._tables[table] = endpoint
        self.registered.append(table)
        self._cached(table)
        self.tracer.emit(
            agent=AGENT,
            action="register_fulltext",
            status="success",
            source=self.name,
            sql=create.replace(ua, "***") if ua else create,
            rows=0,
            duration_ms=round((time.perf_counter() - start) * 1000),
            output_summary=f"discovery table TIRESIAS_SEC.{table} for one full-text search "
            "(catalog read, not counted in MAX_NEW_SOURCES_PER_RUN)",
        )
        return table

    def _cached(self, table: str) -> None:
        if table not in self.cached:
            self.cached.append(table)
        RELEASED.discard(table)

    def _recache(self, table: str, entry_id: str) -> None:
        """An existing table this process released earlier: CACHE it again before reading it.
        Either way the read below caches it, so this instance tracks it for ``release_cache``."""
        if not RELEASED.has(table):
            self._cached(table)  # registered by an earlier run; this read caches it
            return
        self._pace(entry_id)
        cache = render_file("12_cache_table.sql", self.directory, table=table)
        timeout = max(self.client.config.timeout_seconds, BOOTSTRAP_TIMEOUT_SECONDS)
        try:
            self.client.run_sql(
                cache,
                label="discovery_cache",
                timeout=timeout,
                trace_fields={"reason": "re-cached on demand after an earlier release"},
            )
        except SourceError as err:
            if err.recoverable:
                raise  # an outage: the fallback serves the sample
            return  # traced; the read below refetches the table instead
        self._cached(table)

    def release_cache(self, keep: Sequence[str] = ()) -> list[dict[str, Any]]:
        """UNCACHE TABLE the discovery tables this instance cached (candidate submissions tables,
        full-text search tables), except the submissions tables of ``keep`` (tickers: the asked
        entity, the chosen counterparty). Never drops, never raises: one traced ``uncache`` line
        per table with its reason; a failure is logged and the table is not retried. Returns one
        entry per table (``table``, ``released`` bool)."""
        kept: set[str] = set()
        for ticker in keep:
            try:
                kept.add(submissions_table(ticker))
            except ValueError:
                continue
        report: list[dict[str, Any]] = []
        for table in list(self.cached):
            if table in kept:
                continue
            reason = (
                "full-text search table: one search, re-cached on demand"
                if table.startswith("fts_")
                else "discovery candidate not chosen: re-cached on demand"
            )
            ok = uncache(self.client, table, reason=reason, directory=self.directory)
            self.cached.remove(table)
            if ok:
                RELEASED.add(table)
            report.append({"table": table, "released": ok})
        return report

    def filers_naming(
        self, phrase: str, forms: Sequence[str], start: str, end: str
    ) -> Evidence[NamingRow]:
        t0 = time.perf_counter()
        endpoint = fulltext_endpoint(self.catalog, phrase, forms, start, end)
        table = self._ensure_fulltext(endpoint)
        head = self.client.run_sql(
            fulltext_total_sql(table, self.directory), label="discovery_fulltext_total"
        )
        total = int((head.rows[0].get("total") if head.rows else 0) or 0)
        sql = fulltext_hits_sql(table, self.directory)
        rows: list[NamingRow] = []
        if total:
            rows = _naming_rows(self.client.run_sql(sql, label="discovery_fulltext").rows)
        note = f"{total} matching document(s); {len(rows)} filer(s) shown"
        out = Evidence(rows, sql=sql, source=self.name, note=note)
        self._emit("fulltext", out, t0)
        return out


class FallbackDiscovery:
    """Live first; on a recoverable error serve the sample, traced degraded. A non-recoverable
    error is raised, never papered over with sample rows (same contract as FallbackSource)."""

    def __init__(self, primary: Discovery, fallback: Discovery, tracer: Any = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.catalog = primary.catalog
        self.tracer = tracer or _NullTracer()
        self.name = primary.name

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Evidence[Any]:
        try:
            return getattr(self.primary, method)(*args, **kwargs)
        except SourceError as err:
            if not err.recoverable:
                raise
            result = getattr(self.fallback, method)(*args, **kwargs)
            result.degraded = True
            result.error = str(err)
            self.tracer.emit(
                agent=AGENT,
                action={"lookup": "tickers", "brand": "tickers", "search": "tickers"}.get(
                    method, "fulltext" if method == "filers_naming" else method
                ),
                status="degraded",
                sql=result.sql,
                rows=len(result),
                retry_count=err.retry_count,
                source=result.source,
                output_summary=f"Zetaris discovery read failed, served from sample: {err}",
            )
            return result

    def lookup(
        self, *, tickers: Sequence[str] = (), ciks: Sequence[str] = ()
    ) -> Evidence[TickerRow]:
        return self._call("lookup", tickers=tickers, ciks=ciks)

    def brand(self, words: Sequence[str]) -> Evidence[TickerRow]:
        return self._call("brand", words)

    def search(self, name: str) -> Evidence[TickerRow]:
        return self._call("search", name)

    def submissions(self, cik10: str, ticker: str) -> Evidence[Profile]:
        return self._call("submissions", cik10, ticker)

    def registrant(self, cik10: str, ticker: str) -> Evidence[Profile]:
        return self._call("registrant", cik10, ticker)

    def filers_naming(
        self, phrase: str, forms: Sequence[str], start: str, end: str
    ) -> Evidence[NamingRow]:
        return self._call("filers_naming", phrase, forms, start, end)

    def release_cache(self, keep: Sequence[str] = ()) -> list[dict[str, Any]]:
        """The live side's ``release_cache`` (the sample has no cache to release)."""
        release = getattr(self.primary, "release_cache", None)
        return release(keep) if callable(release) else []


def make_discovery(
    sample_mode: bool,
    tracer: Any = None,
    *,
    catalog: Catalog | None = None,
    data_dir: Path | None = None,
    client: ZetarisClient | None = None,
) -> Discovery:
    """Sample mode: SampleDiscovery. Live: Zetaris (the given client, else one from the
    environment), falling back to the sample per call on an outage."""
    catalog = catalog or load_catalog()
    sample = SampleDiscovery(catalog, data_dir, tracer=tracer)
    if sample_mode:
        return sample
    client = client or ZetarisClient(ZetarisConfig.from_env(), tracer=tracer)
    return FallbackDiscovery(ZetarisDiscovery(catalog, client, tracer=tracer), sample, tracer)


__all__ = [
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "Discovery",
    "FallbackDiscovery",
    "FilingRow",
    "NamingRow",
    "Profile",
    "SampleDiscovery",
    "TickerRow",
    "ZetarisDiscovery",
    "fulltext_endpoint",
    "fulltext_hit_rows",
    "fulltext_sample_file",
    "fulltext_table",
    "fulltext_total",
    "load_catalog",
    "make_discovery",
    "name_pattern",
    "parse_catalog",
]
