"""scripts/setup.sh: parses, documents itself, and --check-live reports unreachable services as
"not ready" (exit 1) without printing secret values. Never starts containers."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "setup.sh"
FAKE_KEY = "zk-test-NOT-A-REAL-KEY-7f3a9c"
FAKE_TOKEN = "ns-test-NOT-A-REAL-TOKEN-51be"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


def _run(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "SETUP_CURL_TIMEOUT": "2"}
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0


def test_help_exits_zero_and_names_the_modes():
    out = _run("--help")
    assert out.returncode == 0
    assert "--check-live" in out.stdout and "--env-file" in out.stdout


def test_unknown_option_is_refused():
    assert _run("--bogus").returncode == 2


@pytest.mark.skipif(not shutil.which("curl"), reason="needs curl")
def test_check_live_unreachable_is_not_ready_and_hides_secrets(tmp_path):
    env_file = tmp_path / "live.env"
    env_file.write_text(
        "SAMPLE_MODE=false\n"
        "ZETARIS_REST_URL=http://127.0.0.1:9/api/v1.0\n"
        "ZETARIS_ORG_ID=1\n"
        f"ZETARIS_API_KEY={FAKE_KEY}   # inline comment\n"
        "METERLESS_BASE_URL=http://127.0.0.1:9/v1\n"
        f'METERLESS_RUNTIME_TOKEN="{FAKE_TOKEN}"\n'
        "SEC_USER_AGENT=Tiresias test@example.com\n"
    )
    out = _run("--check-live", "--env-file", str(env_file))
    text = out.stdout + out.stderr
    assert out.returncode == 1
    assert "live mode not ready: 2 issue(s)" in out.stdout
    assert "FAIL     Zetaris REST" in out.stdout and "unreachable" in out.stdout
    assert "FAIL     Nightshift /models" in out.stdout
    assert "OK       ZETARIS_API_KEY" in out.stdout
    assert FAKE_KEY not in text and FAKE_TOKEN not in text
    assert "NOT-A-REAL" not in text


def test_check_live_placeholders_count_as_missing(tmp_path):
    env_file = tmp_path / "example.env"
    env_file.write_text((ROOT / ".env.example").read_text())
    out = _run("--check-live", "--env-file", str(env_file))
    assert out.returncode == 1
    assert "MISSING  ZETARIS_API_KEY" in out.stdout
    assert "MISSING  METERLESS_RUNTIME_TOKEN" in out.stdout
    assert "MISSING  SEC_USER_AGENT" in out.stdout
    assert "your-zetaris-api-key" not in out.stdout


def test_check_live_without_env_file(tmp_path):
    out = _run("--check-live", "--env-file", str(tmp_path / "absent.env"))
    assert out.returncode == 1
    assert "MISSING  .env" in out.stdout
