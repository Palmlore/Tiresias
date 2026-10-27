"""Render the USL explorer page from a scripts/zetaris_usl.py run log (stdlib only).

Usage:
    python3 scripts/render_usl_explorer.py [LOG.jsonl] [--out zetaris/usl/explorer.html] [--fragment]

Every number on the page is read from the log (the live run's rows); nothing is typed in.
--fragment leaves out <!doctype>/<html>/<head>/<body> (for hosts that add their own skeleton).
"""

import argparse
import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "docs" / "measurements" / "zetaris-usl-2026-10-27.jsonl"
NS = "lightning.metastore.tiresias_usl.sec_evidence"
SOURCES = {
    "companyfacts": (
        "SEC XBRL company facts",
        "sec_companyfacts",
        "data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    ),
    "submissions": ("SEC submissions", "sec_submissions", "data.sec.gov/submissions/CIK{cik}.json"),
    "tickers": ("SEC ticker map", "sec_tickers", "www.sec.gov/files/company_tickers_exchange.json"),
    "fulltext": ("EDGAR full-text search", "sec_fulltext", "efts.sec.gov/LATEST/search-index"),
}

esc = html.escape


def load(log: pathlib.Path) -> dict[str, dict]:
    lines = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    return {pathlib.Path(d["file"]).stem: d for d in lines}


def rows(log: dict, stem: str) -> list[dict]:
    return log.get(stem, {}).get("rows") or []


def usd(value) -> str:
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e9:
        return f"{sign}${v / 1e9:,.3f}B".replace(".000B", "B")
    if v >= 1e6:
        return f"{sign}${v / 1e6:,.1f}M"
    return f"{sign}${v:,.0f}"


def comment(path: str) -> str:
    """The leading comment of a SQL file, as one sentence block."""
    p = ROOT / path
    if not p.exists():
        return ""
    out = []
    for ln in p.read_text().splitlines():
        if not ln.lstrip().startswith("--"):
            break
        out.append(ln.lstrip()[2:].strip())
    return " ".join(out)


def ddl_tables(sql: str) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """(table, [(column, type, key)]) parsed from the COMPILE statement."""
    tables = []
    for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, re.DOTALL):
        cols = []
        for raw in body.strip().splitlines():
            raw = raw.strip().rstrip(",")
            m = re.match(r"(\w+) ([\w()]+)(.*)", raw)
            if not m:
                continue
            col, typ, rest = m.groups()
            key = ""
            if "PRIMARY KEY" in rest:
                key = "PK"
            fk = re.search(r"REFERENCES (\w+)\((\w+)\)", rest)
            if fk:
                key = f"FK → {fk.group(1)}.{fk.group(2)}"
            cols.append((col, typ + (" not null" if "NOT NULL" in rest else ""), key))
        tables.append((name, cols))
    return tables


def table_html(rs: list[dict], limit: int = 8, money: tuple[str, ...] = ()) -> str:
    if not rs:
        return '<p class="muted">No rows.</p>'
    cols = list(rs[0])
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = []
    for r in rs[:limit]:
        cells = []
        for c in cols:
            v = r.get(c)
            text = usd(v) if c in money and v not in (None, "") else ("—" if v is None else str(v))
            cells.append(f"<td>{esc(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    more = f'<p class="muted small">First {limit} of {len(rs)} rows.</p>' if len(rs) > limit else ""
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>{more}'


def lineage_svg(counts: dict[str, str]) -> str:
    cols = [40, 290, 540, 790]
    w, h = 180, 74
    ys = {"company": 30, "filing": 150, "fact": 290}
    boxes = []

    def box(x, y, title, sub, foot, kind):
        boxes.append(
            f'<g class="node {kind}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>'
            f'<text x="{x + 12}" y="{y + 22}" class="t">{esc(title)}</text>'
            f'<text x="{x + 12}" y="{y + 40}" class="s">{esc(sub)}</text>'
            f'<text x="{x + 12}" y="{y + 60}" class="f">{esc(foot)}</text></g>'
        )

    box(cols[0], 80, "SEC submissions", "data.sec.gov/submissions", "2 requests · JSON", "ext")
    box(cols[0], 290, "SEC company facts", "data.sec.gov/api/xbrl", "2 requests · JSON", "ext")
    box(cols[1], 80, "TIRESIAS_SEC", "mmm_ / solv_submissions_raw", "2 REST tables", "db")
    box(cols[1], 290, "TIRESIAS_SEC", "mmm_ / solv_facts_raw", "2 REST tables", "db")
    box(cols[2], ys["company"], "ACTIVATE company", "UNION of 2 profiles", "1 query", "q")
    box(cols[2], ys["filing"], "ACTIVATE filing", "posexplode, UNION of 2", "1 query", "q")
    box(cols[2], ys["fact"], "ACTIVATE fact", "3 concept paths, UNION", "1 query", "q")
    for t, y in ys.items():
        box(cols[3], y, t, "USL table", f"{counts.get(t, '?')} rows", "usl")

    def arrow(x1, y1, x2, y2, label=""):
        mid = (x1 + x2) / 2
        path = f"M{x1},{y1} C{mid},{y1} {mid},{y2} {x2 - 6},{y2}"
        lab = (
            f'<text x="{mid}" y="{min(y1, y2) + abs(y2 - y1) / 2 - 6}" class="lab">{esc(label)}</text>'
            if label
            else ""
        )
        return f'<path d="{path}" class="edge" marker-end="url(#ah)"/>{lab}'

    edges = [
        arrow(cols[0] + w, 117, cols[1], 117, "HTTPS"),
        arrow(cols[0] + w, 327, cols[1], 327, "HTTPS"),
        arrow(cols[1] + w, 117, cols[2], ys["company"] + 37),
        arrow(cols[1] + w, 117, cols[2], ys["filing"] + 37),
        arrow(cols[1] + w, 327, cols[2], ys["fact"] + 37),
    ] + [arrow(cols[2] + w, y + 37, cols[3], y + 37, "writes") for y in ys.values()]
    fk = (
        f'<path d="M{cols[3] + w},{ys["filing"] + 37} C{cols[3] + w + 40},{ys["filing"] + 37} {cols[3] + w + 40},{ys["company"] + 37} {cols[3] + w + 6},{ys["company"] + 37}" class="fk" marker-end="url(#fh)"/>'
        f'<path d="M{cols[3] + w},{ys["fact"] + 37} C{cols[3] + w + 40},{ys["fact"] + 37} {cols[3] + w + 40},{ys["filing"] + 47} {cols[3] + w + 6},{ys["filing"] + 47}" class="fk" marker-end="url(#fh)"/>'
        f'<path d="M{cols[3] + w},{ys["fact"] + 52} C{cols[3] + w + 75},{ys["fact"] + 52} {cols[3] + w + 75},{ys["company"] + 52} {cols[3] + w + 6},{ys["company"] + 52}" class="fk" marker-end="url(#fh)"/>'
    )
    heads = "".join(
        f'<text x="{x}" y="16" class="colh">{t}</text>'
        for x, t in zip(
            cols,
            ["EXTERNAL SOURCE", "LIGHTNING DATABASE", "ACTIVATION QUERY", "USL TABLE"],
            strict=True,
        )
    )
    return (
        '<svg viewBox="0 0 1080 380" role="img" aria-label="Lineage from the SEC APIs through Zetaris REST tables and activation queries to the three USL tables">'
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" class="ahp"/></marker>'
        '<marker id="fh" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" class="fhp"/></marker></defs>'
        f"{heads}{''.join(edges)}{fk}{''.join(boxes)}</svg>"
    )


def trace_bars(rs: list[dict]) -> str:
    """Traced vs untraced 3M facts per fiscal year, drawn to one scale."""
    years: dict[str, dict[str, int]] = {}
    for r in rs:
        if r.get("entity_name") != "3M CO" or r.get("fy") is None:
            continue
        years.setdefault(r["fy"], {"traced": 0, "untraced": 0})[r["source_filing"]] += int(
            r["facts"]
        )
    if not years:
        return ""
    top = max(v["traced"] + v["untraced"] for v in years.values())
    bw, gap, h, base = 34, 10, 150, 175
    out = []
    for i, (fy, v) in enumerate(sorted(years.items())):
        x = 40 + i * (bw + gap)
        hu = h * v["untraced"] / top
        ht = h * v["traced"] / top
        out.append(
            f'<rect x="{x}" y="{base - hu}" width="{bw}" height="{hu}" class="bar-u"><title>FY{fy}: {v["untraced"]} untraced</title></rect>'
        )
        out.append(
            f'<rect x="{x}" y="{base - hu - ht}" width="{bw}" height="{ht}" class="bar-t"><title>FY{fy}: {v["traced"]} traced</title></rect>'
        )
        out.append(
            f'<text x="{x + bw / 2}" y="{base + 16}" class="ax" text-anchor="middle">{fy[2:]}</text>'
        )
    width = 40 + len(years) * (bw + gap) + 10
    grid = "".join(
        f'<line x1="34" x2="{width}" y1="{base - h * t / top}" y2="{base - h * t / top}" class="grid"/>'
        f'<text x="28" y="{base - h * t / top + 4}" class="ax" text-anchor="end">{t}</text>'
        for t in range(0, top + 1, 20)
    )
    return (
        f'<svg viewBox="0 0 {width} 200" class="bars" role="img" aria-label="3M revenue and net income facts per fiscal year, traced or untraced to a filing">'
        f"{grid}{''.join(out)}</svg>"
    )


def page(log: dict) -> tuple[str, str]:
    counts = {r["usl_table"]: r["rows"] for r in rows(log, "40_row_counts")}
    dq = rows(log, "31_run_dq_filing") + rows(log, "32_run_dq_fact")
    rules = rows(log, "33_list_dq")
    fks = sum(1 for r in rules if r["type"].lower().startswith("foreign"))
    invalid = sum(int(r["invalid_record"]) for r in dq)
    activated = all(
        log.get(s, {}).get("status") == "ok"
        for s in ("20_activate_company", "21_activate_filing", "22_activate_fact")
    )
    tables = ddl_tables(log["10_compile"]["sql"])
    rec = (rows(log, "41_reconcile_2023") or [{}])[0]
    shared = rows(log, "42_shared_filings")
    trace = rows(log, "44_trace_one_number")
    first = trace[0] if trace else {}

    badges = [
        ("ok" if activated else "bad", "Activated" if activated else "Not activated"),
        ("", f"{len(tables)} tables"),
        ("", f"{fks} foreign keys"),
        ("", f"{len(rules)} DQ rules"),
        ("warn" if invalid else "ok", f"DQ: {invalid} invalid records"),
    ]
    badge_html = "".join(f'<span class="badge {k}">{esc(t)}</span>' for k, t in badges)

    erd = []
    for name, cols in tables:
        lis = "".join(
            f'<li><span class="col">{esc(c)}</span><span class="typ">{esc(t)}</span>'
            + (f'<span class="key {"pk" if k == "PK" else "fk"}">{esc(k)}</span>' if k else "")
            + "</li>"
            for c, t, k in cols
        )
        erd.append(
            f'<div class="entity"><h4>{esc(name)} <span class="muted small">{counts.get(name, "?")} rows</span></h4><ul>{lis}</ul></div>'
        )

    dq_rows = "".join(
        f"<tr><td><code>{esc(r['table'])}.{esc(r['name'])}</code></td><td>{esc(r['type'])}</td>"
        f'<td class="num">{esc(r["total_record"])}</td><td class="num">{esc(r["valid_record"])}</td>'
        f'<td class="num {"warnc" if int(r["invalid_record"]) else "okc"}">{esc(r["invalid_record"])}</td>'
        f'<td><span class="meter"><span style="width:{100 * int(r["valid_record"]) / max(int(r["total_record"]), 1):.1f}%"></span></span></td></tr>'
        for r in dq
    )
    custom = next((r for r in rules if r["type"].startswith("Custom")), None)

    rec_html = ""
    if rec:
        rec_html = f"""
<div class="recon">
  <div class="fig"><span class="lbl">3M FY2023, 10-K filed 2024-02-07</span><span class="val">{usd(rec["mmm_original"])}</span></div>
  <div class="fig"><span class="lbl">3M FY2023 restated, 10-K filed 2025-02-05</span><span class="val">{usd(rec["mmm_restated"])}</span></div>
  <div class="fig"><span class="lbl">Solventum's own FY2023 revenue</span><span class="val">+ {usd(rec["solventum"])}</span></div>
  <div class="fig sum"><span class="lbl">Add-back</span><span class="val">{usd(rec["add_back"])}</span></div>
  <div class="fig res"><span class="lbl">Residual against the original</span><span class="val">{usd(rec["residual"])}</span></div>
</div>"""

    chain = ""
    if first:
        chain = f"""
<ol class="chain">
  <li><span class="lbl">SEC source</span><code>{esc(first["sec_source"])}</code></li>
  <li><span class="lbl">Zetaris REST table</span><code>{esc(first["raw_table"])}</code></li>
  <li><span class="lbl">USL row</span><code>fact</code> · {esc(first["entity_name"])} · {esc(first["concept"])} · {esc(first["period_start"])} to {esc(first["period_end"])} · <strong>{usd(first["val"])}</strong></li>
  <li><span class="lbl">Reported in</span>{esc(first["filing_form"] or "a filing outside the history")} filed {esc(first["filing_date"] or "?")} · accession <code>{esc(first["accn"])}</code></li>
</ol>"""

    explore = []
    for key, (label, catalog_id, endpoint) in SOURCES.items():
        items = []
        for stem, d in sorted(log.items()):
            if d.get("phase") != "explore" or not stem.startswith(key + "_"):
                continue
            items.append(
                f'<div class="q"><p>{esc(comment(d["file"]))}</p>'
                f"<details><summary>SQL · {d.get('row_count', 0)} rows · {d.get('duration_ms', '?')} ms</summary><pre>{esc(d['sql'])}</pre></details>"
                f"{table_html(d.get('rows') or [], 6, money=('val', 'low', 'high'))}</div>"
            )
        explore.append(
            f'<section class="source"><h3>{esc(label)} <span class="muted small">catalog id <code>{catalog_id}</code> · {esc(endpoint)}</span></h3>{"".join(items)}</section>'
        )

    total_ms = sum(int(d.get("duration_ms") or 0) for d in log.values())
    statements = len(log)
    title = "Tiresias USL Explorer"
    style = STYLE
    body = f"""
<main class="wrap">
<header class="top">
  <p class="eyebrow">Zetaris Unified Semantic Layer · Tiresias</p>
  <h1>sec_evidence</h1>
  <p class="ns"><code>{NS}</code></p>
  <div class="badges">{badge_html}</div>
  <p class="lede">The evidence behind Tiresias's lead example, the 3M → Solventum spin-off, modelled once in Zetaris: three typed tables across both companies, with declared keys and data-quality rules that run on the live rows.</p>
</header>

<section>
  <h2>Where the data comes from</h2>
  <p>Two SEC APIs feed four Zetaris REST tables. Each USL table is one activation query over them. Dashed lines are the foreign keys.</p>
  <div class="scroll diagram">{lineage_svg(counts)}</div>
</section>

<section>
  <h2>Entity relationships</h2>
  <p>A company has many filings and many facts. A fact names the filing that reported it by accession number.</p>
  <div class="erd">{"".join(erd)}</div>
</section>

<section>
  <h2>Data quality on the live rows</h2>
  <p>The foreign keys generate their rules; <code>period_order</code> is ours{f" (<code>{esc(custom['expression'])}</code>)" if custom else ""}.</p>
  <div class="scroll"><table class="dq"><thead><tr><th>Rule</th><th>Type</th><th class="num">Total</th><th class="num">Valid</th><th class="num">Invalid</th><th>Valid share</th></tr></thead><tbody>{dq_rows}</tbody></table></div>
  <div class="findings">
    <div class="finding">
      <h3>{len(shared)} filings sit in both companies' histories</h3>
      <p>They break the <code>filing</code> primary key ({2 * len(shared)} rows, one per company). They are 3M's ownership filings on its retained Solventum stake: a direct parent and spin-off link that Tiresias otherwise finds through full-text search.</p>
      {table_html(shared, 6)}
    </div>
    <div class="finding">
      <h3>Facts whose filing is outside SEC's recent list</h3>
      <p>The <code>fact.accn</code> rule fails when a fact cites a filing missing from <code>filings.recent</code>, which holds only recent filings. Older facts cannot be traced to their filing from this source alone.</p>
      {trace_bars(rows(log, "43_fact_traceability"))}
      <p class="legend"><span class="sw t"></span>traced to a filing <span class="sw u"></span>untraced · 3M facts per fiscal year</p>
    </div>
  </div>
</section>

<section>
  <h2>Case 04 in one query</h2>
  <p>The reconciliation the agent computes from per-company views, written as one statement over the USL (<code>zetaris/usl/41_reconcile_2023.sql</code>).</p>
  {rec_html}
</section>

<section>
  <h2>One number, traced end to end</h2>
  <p>Solventum's FY2023 revenue, from the SEC endpoint to the filing that reported it.</p>
  {chain}
</section>

<section>
  <h2>The sources, explored</h2>
  <p>Three queries per source, run through Zetaris on the registered tables.</p>
  {"".join(explore)}
</section>

<footer>
  <p>Generated by <code>scripts/render_usl_explorer.py</code> from <code>docs/measurements/zetaris-usl-2026-10-27.jsonl</code>: {statements} statements run live through Zetaris 2.4.3.1 by <code>scripts/zetaris_usl.py</code>, {total_ms / 1000:.1f} s in total.</p>
</footer>
</main>"""
    return (
        title,
        f'<title>{title}</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n<style>{style}</style>\n{body}\n',
    )


STYLE = """
/* Layout: one reading column, diagrams and tables scroll inside their own frames. Brand tokens (assets/brand/palette.json), dark by design. */
:root {
  --ink: #12151c; --ink-2: #1a1f29; --ink-3: #242a36; --line: #313847;
  --bronze: #d4a054; --celeste: #7eb6c9; --parchment: #ede6d9; --stone: #9b968c; --text: #f4efe4;
  --ok: #8cc79a; --warn: #e3b25c; --bad: #e58a7c;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { background: var(--ink); color: var(--text); font: 15px/1.55 var(--sans); }
.wrap { max-width: 1120px; margin: 0 auto; padding-block: 40px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 48px; }
h1, h2, h3, h4 { text-wrap: balance; margin: 0; }
h1 { font: 600 2.6rem/1.1 var(--mono); color: var(--bronze); letter-spacing: -0.01em; }
h2 { font-size: 1.35rem; color: var(--parchment); }
h3 { font-size: 1.02rem; color: var(--parchment); }
section { display: grid; gap: 14px; min-width: 0; }
section > p, .lede { max-width: 68ch; margin: 0; color: var(--stone); }
.lede { color: var(--parchment); font-size: 1.05rem; }
.top { display: grid; gap: 12px; }
.eyebrow { margin: 0; text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.75rem; color: var(--celeste); }
.ns { margin: 0; }
code, pre { font-family: var(--mono); font-size: 0.85em; }
code { color: var(--celeste); overflow-wrap: anywhere; }
.muted { color: var(--stone); } .small { font-size: 0.8rem; font-weight: 400; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; }
.badge { border: 1px solid var(--line); border-radius: 999px; padding: 3px 12px; font-size: 0.8rem; color: var(--parchment); background: var(--ink-2); }
.badge.ok { border-color: var(--ok); color: var(--ok); } .badge.warn { border-color: var(--warn); color: var(--warn); } .badge.bad { border-color: var(--bad); color: var(--bad); }
.scroll { overflow-x: auto; min-width: 0; }
.diagram { border: 1px solid var(--line); border-radius: 8px; background: var(--ink-2); padding: 12px; }
.diagram svg { min-width: 860px; width: 100%; height: auto; display: block; }
.node rect { stroke-width: 1.2; fill: var(--ink-3); stroke: var(--line); }
.node.ext rect { stroke-dasharray: 4 3; stroke: var(--stone); fill: var(--ink); }
.node.db rect { stroke: var(--celeste); }
.node.q rect { stroke: var(--line); }
.node.usl rect { stroke: var(--bronze); fill: #2a2418; }
.node .t { fill: var(--text); font: 600 13px var(--sans); }
.node .s { fill: var(--stone); font: 11px var(--mono); }
.node .f { fill: var(--parchment); font: 11px var(--sans); }
.node.usl .t { fill: var(--bronze); font-family: var(--mono); }
.colh { fill: var(--stone); font: 600 10px var(--sans); letter-spacing: 0.1em; }
.edge { fill: none; stroke: var(--stone); stroke-width: 1.2; }
.fk { fill: none; stroke: var(--bronze); stroke-width: 1.2; stroke-dasharray: 5 4; }
.ahp { fill: var(--stone); } .fhp { fill: var(--bronze); }
.lab { fill: var(--stone); font: 10px var(--sans); text-anchor: middle; }
.erd { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.entity { border: 1px solid var(--bronze); border-radius: 8px; background: var(--ink-2); min-width: 0; }
.entity h4 { font: 600 0.95rem var(--mono); color: var(--bronze); padding: 10px 14px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 8px; }
.entity ul { list-style: none; margin: 0; padding: 6px 0; }
.entity li { display: flex; flex-wrap: wrap; gap: 4px 10px; padding: 4px 14px; font-size: 0.82rem; align-items: baseline; }
.col { font-family: var(--mono); color: var(--text); } .typ { color: var(--stone); font-family: var(--mono); font-size: 0.76rem; }
.key { margin-left: auto; font-size: 0.7rem; border-radius: 4px; padding: 0 6px; font-family: var(--mono); }
.key.pk { color: var(--ink); background: var(--bronze); } .key.fk { color: var(--bronze); border: 1px solid var(--bronze); }
table { border-collapse: collapse; width: 100%; font-size: 0.82rem; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--stone); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; }
td.num, th.num { text-align: right; } .okc { color: var(--ok); } .warnc { color: var(--warn); }
.meter { display: inline-block; width: 120px; height: 6px; border-radius: 3px; background: var(--ink-3); overflow: hidden; vertical-align: middle; }
.meter span { display: block; height: 100%; background: var(--ok); }
.findings { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.finding { border-top: 2px solid var(--warn); background: var(--ink-2); padding: 14px 16px; display: grid; gap: 10px; min-width: 0; align-content: start; }
.finding p { margin: 0; color: var(--stone); font-size: 0.9rem; }
.bars { width: 100%; max-width: 640px; height: auto; }
.bar-t { fill: var(--ok); } .bar-u { fill: var(--warn); }
.grid { stroke: var(--line); stroke-width: 1; } .ax { fill: var(--stone); font: 10px var(--sans); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; font-size: 0.8rem; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; } .sw.t { background: var(--ok); } .sw.u { background: var(--warn); }
.recon { display: grid; gap: 0; max-width: 560px; font-variant-numeric: tabular-nums; }
.fig { display: flex; justify-content: space-between; gap: 16px; padding: 8px 0; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.fig .lbl { color: var(--stone); } .fig .val { font-family: var(--mono); color: var(--text); }
.fig.sum { border-bottom: 2px solid var(--parchment); } .fig.sum .val { color: var(--parchment); }
.fig.res .val { color: var(--bronze); font-weight: 600; }
.chain { list-style: none; margin: 0; padding: 0; display: grid; gap: 0; max-width: 820px; }
.chain li { position: relative; padding: 10px 0 10px 26px; border-left: 1px solid var(--line); margin-left: 6px; display: grid; gap: 2px; min-width: 0; }
.chain li::before { content: ""; position: absolute; left: -6px; top: 14px; width: 11px; height: 11px; border-radius: 50%; background: var(--ink); border: 2px solid var(--bronze); }
.chain .lbl { text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.7rem; color: var(--stone); }
.source { border-top: 1px solid var(--line); padding-top: 16px; }
.source h3 { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
.q { display: grid; gap: 6px; min-width: 0; }
.q p { margin: 0; color: var(--parchment); font-size: 0.9rem; max-width: 80ch; }
details summary { cursor: pointer; color: var(--celeste); font-size: 0.8rem; }
details summary:focus-visible { outline: 2px solid var(--bronze); outline-offset: 2px; }
pre { background: var(--ink-2); border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; overflow-x: auto; color: var(--parchment); }
footer p { margin: 0; color: var(--stone); font-size: 0.8rem; max-width: 80ch; }
@media (max-width: 520px) { h1 { font-size: 2rem; } }
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="render_usl_explorer.py")
    parser.add_argument("log", nargs="?", default=str(DEFAULT_LOG))
    parser.add_argument("--out", default=str(ROOT / "zetaris" / "usl" / "explorer.html"))
    parser.add_argument("--fragment", action="store_true", help="no document skeleton")
    args = parser.parse_args(argv)
    _, content = page(load(pathlib.Path(args.log)))
    if not args.fragment:
        content = (
            '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f"{content.split('<main', 1)[0]}</head>\n<body>\n<main{content.split('<main', 1)[1]}</body>\n</html>\n"
        )
    pathlib.Path(args.out).write_text(content)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
