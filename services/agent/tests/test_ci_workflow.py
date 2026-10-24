"""CI stays what AGENTS.md and README say it is: sample mode only, the judge quickstart, four jobs."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CI = (ROOT / ".github/workflows/ci.yml").read_text()
JOBS = ["agent", "cognition", "repo", "quickstart"]


def _quickstart() -> str:
    return CI[CI.index("\n  quickstart:") :]


def test_four_jobs_and_agents_md_names_each():
    names = re.findall(r"^  (\w+):\n    name:", CI, flags=re.MULTILINE)
    assert names == JOBS
    agents = (ROOT / "AGENTS.md").read_text()
    ci_line = next(x for x in agents.splitlines() if "**CI**" in x)
    for phrase in [
        "agent pytest + ruff",
        "cognition tests + H-MEM conformance",
        "secrets and metadata checks",
        "judge quickstart",
    ]:
        assert phrase in ci_line


def test_no_secret_live_mode_or_sponsor_endpoint():
    # `secrets.` as the Actions context, not the `check_secrets.sh` script name
    for banned in [r"(?<!\w)secrets\.", "--live", "SAMPLE_MODE=false", "8888", "8088"]:
        assert not re.search(banned, CI), banned


def test_quickstart_follows_readme():
    readme = (ROOT / "README.md").read_text()
    assert "cp .env.example .env" in readme and "docker compose up --build" in readme
    job = _quickstart()
    assert "cp .env.example .env" in job
    assert re.search(r"docker compose up (-d )?--build", job)
    assert "input_examples/*.json" in job


def test_quickstart_runs_the_memory_demo_in_sample_mode():
    job = _quickstart()
    assert "python3 scripts/memory_demo.py --agent http://localhost:8000" in job
    demo = (ROOT / "scripts/memory_demo.py").read_text()
    assert '"sample_mode"] = True' in demo and "--live" in demo  # sample unless asked


def _assertion_script() -> str:
    body = _quickstart().split("<<'EOF'\n", 1)[1].split("\n          EOF", 1)[0]
    return "\n".join(line.removeprefix("          ") for line in body.splitlines())


@pytest.mark.parametrize(
    "result, ok",
    [
        ('{"end_state": "root-cause", "status": "done"}', True),
        ('{"status": "done"}', False),
        ('{"end_state": "evidence-frontier", "status": "error", "error": "x"}', False),
    ],
)
def test_quickstart_assertion_rejects_a_failing_example(tmp_path, result, ok):
    f = tmp_path / "r.json"
    f.write_text(result)
    proc = subprocess.run(
        [sys.executable, "-c", _assertion_script(), str(f)], check=False, capture_output=True
    )
    assert (proc.returncode == 0) is ok
