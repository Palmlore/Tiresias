"""Build and query the Tiresias USL (Zetaris Unified Semantic Layer) and run the per-source queries.

Usage (needs a running Zetaris with case 04 registered: MMM and SOLV raw tables, the
`mmm_*`/`solv_revenues` views and the 3M full-text search, i.e. one live run of
input_examples/04_3m_2023_revenue_restated.json):

    cd services/agent && uv run python ../../scripts/zetaris_usl.py \
        [--steps build,query,explore] [--teardown] [--out FILE.jsonl] [--env-file PATH]

Runs zetaris/usl/NN_*.sql (build: 00-39, query: 40-89) and zetaris/explore/*.sql in name order,
one statement per file, through the agent's Zetaris adapter (timeouts, key masked).
Prints one JSON line per statement (file, status, duration, columns, first rows); --out also
writes them to a file. A re-run is safe: "already exists" is reported as `exists`, not fatal.
--teardown runs zetaris/usl/99_teardown.sql only (REMOVE USL).
"""

import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
USL = ROOT / "zetaris" / "usl"
EXPLORE = ROOT / "zetaris" / "explore"
ROW_CAP = 50


def load_env(env: pathlib.Path) -> None:
    """Same rule as zetaris_probe.py: .env values never override the environment."""
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                value = value.split(" #")[0].strip()
                if value:
                    os.environ.setdefault(key.strip(), value)


def statement(path: pathlib.Path) -> str:
    """The file's SQL without comment lines (one statement per file)."""
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def files(steps: list[str], teardown: bool) -> list[tuple[str, pathlib.Path]]:
    if teardown:
        return [("teardown", USL / "99_teardown.sql")]
    out: list[tuple[str, pathlib.Path]] = []
    for path in sorted(USL.glob("[0-9][0-9]_*.sql")):
        n = int(path.name[:2])
        if "build" in steps and n < 40:
            out.append(("build", path))
        elif "query" in steps and 40 <= n < 90:
            out.append(("query", path))
    if "explore" in steps:
        out += [("explore", p) for p in sorted(EXPLORE.glob("*.sql"))]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zetaris_usl.py")
    parser.add_argument("--steps", default="build,query,explore", help="comma list")
    parser.add_argument("--teardown", action="store_true", help="REMOVE USL only")
    parser.add_argument("--out", default=None, help="also write the JSON lines to this file")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per statement")
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="default: <repo>/.env")
    args = parser.parse_args(argv)

    load_env(pathlib.Path(args.env_file))
    if not os.environ.get("ZETARIS_API_KEY"):
        print("ZETARIS_API_KEY is required (.env)", file=sys.stderr)
        return 2
    os.environ["ZETARIS_REST_URL"] = os.environ.get(
        "ZETARIS_PROBE_URL", "http://localhost:8888/api/v1.0"
    ).rstrip("/")

    from tiresias_agent.adapters.zetaris import SourceError, ZetarisClient, ZetarisConfig

    base = ZetarisConfig.from_env()
    client = ZetarisClient(
        ZetarisConfig(
            rest_url=base.rest_url,
            org_id=base.org_id,
            api_key=base.api_key,
            timeout_seconds=args.timeout,
            max_retries=0,
        )
    )
    out = open(args.out, "w") if args.out else None  # noqa: SIM115
    failed = 0
    try:
        for phase, path in files([s.strip() for s in args.steps.split(",")], args.teardown):
            sql = statement(path)
            line: dict = {"file": str(path.relative_to(ROOT)), "phase": phase, "sql": sql}
            try:
                result = client.run_sql(sql, label=f"usl:{path.stem}")
                line |= {
                    "status": "ok",
                    "duration_ms": result.duration_ms,
                    "columns": result.columns,
                    "row_count": len(result.rows),
                    "rows": result.rows[:ROW_CAP],
                }
            except SourceError as err:
                detail = err.detail or str(err)
                exists = re.search(r"already exist", detail, re.IGNORECASE)
                failed += 0 if exists else 1
                line |= {"status": "exists" if exists else "error", "error": detail[:600]}
            text = json.dumps(line, default=str)
            print(text, flush=True)
            if out:
                out.write(text + "\n")
    finally:
        client.close()
        if out:
            out.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
