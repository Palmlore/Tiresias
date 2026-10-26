"""The agent image carries what sample mode needs (BACKLOG B-1), and never .env."""

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NEEDED = ["data/sample", "zetaris/sql", "catalog", "input_examples"]


def _ignore_patterns() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    return [x.strip() for x in lines if x.strip() and not x.strip().startswith("#")]


def _in_context(rel: str) -> bool:
    """Docker's rule: the last pattern matching the path (or one of its parents) wins."""
    parts = rel.split("/")
    candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    included = True
    for pattern in _ignore_patterns():
        negate = pattern.startswith("!")
        pat = pattern[1:] if negate else pattern
        anywhere = pat.startswith("**/")  # Docker anchors patterns at the root unless **/
        pat = pat.removeprefix("**/")
        names = [c.split("/")[-1] if anywhere else c for c in candidates]
        if any(fnmatch.fnmatch(n, pat) for n in names):
            included = negate
    return included


def test_compose_builds_the_agent_from_the_repo_root():
    compose = (ROOT / "compose.yaml").read_text()
    agent = compose.split("\n  cognition:")[0]
    assert re.search(
        r"build:\s*\{context:\s*\.,\s*dockerfile:\s*services/agent/Dockerfile\}", agent
    )


def test_dockerfile_copies_sample_data_sql_catalog_and_examples():
    dockerfile = (ROOT / "services/agent/Dockerfile").read_text()
    for path in NEEDED:
        assert re.search(
            rf"^COPY {re.escape(path)} /app/{re.escape(path)}$", dockerfile, re.MULTILINE
        )
    assert "SAMPLE_DATA_DIR=/app/data/sample" in dockerfile
    assert "ZETARIS_SQL_DIR=/app/zetaris/sql" in dockerfile


def test_build_context_includes_needed_files_and_excludes_secrets():
    for path in NEEDED:
        files = [p for p in (ROOT / path).rglob("*") if p.is_file()]
        assert files, path
        for f in files:
            assert _in_context(str(f.relative_to(ROOT))), f
    assert _in_context("data/sample/PROVENANCE.json")
    assert _in_context("services/agent/src/tiresias_agent/app.py")
    for secret in (".env", "logs/trace-x.jsonl", ".git/config", ".claude/worktrees/x/.env"):
        assert not _in_context(secret), secret
    assert not _in_context("assets/brand/logo-1024.png")
    assert not _in_context("assets/characters/tiresias.svg")
