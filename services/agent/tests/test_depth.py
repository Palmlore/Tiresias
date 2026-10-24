"""Investigation depth (ADR-0011): the user picks quick / thorough / deep per question and is told
the expected wait before the run starts. No network: Zetaris is the stateful fake, Nightshift the
mocked Runtime of tests/test_model_lanes.py (httpx.MockTransport) behind the real resolver path.
"""

import json
from pathlib import Path

import httpx
import pytest
from fake_zetaris import FakeZetaris
from fastapi.testclient import TestClient
from loop_helpers import EXAMPLES, request
from test_any_company import ACME_ALIASES, ACME_TICKERS, UA
from test_entities_api import _live as live_directory
from test_model_lanes import TOKEN, FakeNightshift

from tiresias_agent import app as app_module
from tiresias_agent import entities
from tiresias_agent.adapters import nightshift
from tiresias_agent.adapters import zetaris as z
from tiresias_agent.agents import model_lanes
from tiresias_agent.config import DEPTHS, FIRST_RUN_SECONDS, MODEL_SECONDS, expected_seconds
from tiresias_agent.models import EntityList, RunDepth
from tiresias_agent.orchestrator import depth_plan, run_investigation
from tiresias_agent.trace import Tracer

REVIEW = {"decision": "accept", "label": "breakdown", "rationale": "ok"}


@pytest.fixture
def runtime(monkeypatch):
    """A configured Nightshift Runtime answered by a FakeNightshift through the real resolver
    (orchestrator._lanes -> model_lanes.resolve -> ModelLanes -> NightshiftClient)."""
    monkeypatch.setenv("METERLESS_BASE_URL", "http://vm.test:8088/v1")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", TOKEN)
    monkeypatch.delenv("MODEL_LANES", raising=False)
    fake = FakeNightshift(
        propose={"candidates": [{"type": "concept", "name": "GoodwillImpairmentLoss"}]},
        review=REVIEW,
        wording=None,  # {} -> the template stays
    )
    real = nightshift.NightshiftClient

    def client(config, **kw):
        return real(config, **{**kw, "transport": httpx.MockTransport(fake)})

    monkeypatch.setattr(model_lanes, "NightshiftClient", client)
    return fake


@pytest.fixture
def no_real_http(monkeypatch):
    def refuse(self, req):
        raise AssertionError(f"real HTTP request to {req.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


def _live(tmp_path, monkeypatch, prefix="03", **options):
    """One live run (fake Zetaris) of input example ``prefix`` asked about ACME."""
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    req = request(prefix, entity={"ticker": "ACME"})
    req.options.sample_mode = False
    for k, v in options.items():
        setattr(req.options, k, v)
    tracer = Tracer(f"run-depth-{prefix}", logs_dir=tmp_path, defaults={"mode": "live"})
    client = fake.client(tracer)
    source = z.make_source(False, tracer, client=client)
    result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    return result, lines


def _shape(result):
    return result.end_state, [(lk.node_id, lk.verdict, lk.rule, lk.label) for lk in result.why_tree]


def _roles(fake):
    return {role for role, _model in fake.calls}


# --- live: the depth picks the lanes -------------------------------------------------------------


def test_default_depth_is_quick_and_makes_zero_model_calls_live(
    tmp_path, monkeypatch, runtime, no_real_http
):
    result, lines = _live(tmp_path, monkeypatch)
    assert result.error is None and result.end_state == "actionable"
    assert runtime.calls == [] and result.stats.model_calls == 0
    d = result.depth
    assert (d.requested, d.requested_by, d.effective, d.lanes) == ("quick", "default", "quick", [])
    assert not d.degraded and "rule lanes only" in d.note
    mode = next(x for x in lines if x["agent"] == "model_lanes" and x["action"] == "mode")
    assert mode["lanes"] == "off" and mode["depth"] == "quick"
    assert not [x for x in lines if x.get("phase") == "model_call"]
    assert all(lk.model_opinion is None and lk.proposed_by == "rules" for lk in result.why_tree)


def test_thorough_calls_only_the_critic_lane(tmp_path, monkeypatch, runtime, no_real_http):
    monkeypatch.setenv("MAX_BRANCHES_PER_LEVEL", "4")  # a free slot the hypothesiser could fill
    quick, _ = _live(tmp_path / "quick", monkeypatch)
    result, lines = _live(tmp_path, monkeypatch, depth="thorough")
    assert runtime.calls and _roles(runtime) == {"review"}
    assert all(m == "meterless/deep" for _, m in runtime.calls)
    assert result.stats.model_calls == len(runtime.calls)
    assert result.depth.effective == "thorough" and result.depth.lanes == ["critic"]
    assert _shape(result) == _shape(quick)  # the second opinion never decides
    assert any(lk.model_opinion for lk in result.why_tree)
    assert result.action_source == "template"
    assert {x["target_agent"] for x in lines if x.get("phase") == "model_call"} == {"critic"}


def test_deep_calls_all_three_lanes(tmp_path, monkeypatch, runtime, no_real_http):
    monkeypatch.setenv("MAX_BRANCHES_PER_LEVEL", "4")
    result, _ = _live(tmp_path, monkeypatch, depth="deep")
    assert _roles(runtime) == {"propose", "review", "wording"}
    assert result.depth.effective == "deep"
    assert result.depth.lanes == ["hypothesiser", "critic", "reporter"]
    assert result.stats.model_calls == len(runtime.calls)
    assert any(lk.proposed_by == "model" for lk in result.why_tree)


def test_model_lanes_off_forces_quick_even_at_deep(tmp_path, monkeypatch, runtime, no_real_http):
    monkeypatch.setenv("MODEL_LANES", "off")
    result, lines = _live(tmp_path, monkeypatch, depth="deep")
    assert runtime.calls == [] and result.stats.model_calls == 0
    assert (result.depth.requested, result.depth.effective) == ("deep", "quick")
    assert "MODEL_LANES=off" in result.depth.note and not result.depth.degraded
    assert not [x for x in lines if x["action"] == "degrade"]


def test_model_lanes_on_with_no_depth_is_deep(tmp_path, monkeypatch, runtime, no_real_http):
    monkeypatch.setenv("MODEL_LANES", "on")
    result, _ = _live(tmp_path, monkeypatch)
    assert result.depth.requested_by == "MODEL_LANES" and result.depth.effective == "deep"
    assert "review" in _roles(runtime)


def test_deprecated_model_lanes_option_still_works(tmp_path, monkeypatch, runtime, no_real_http):
    result, _ = _live(tmp_path, monkeypatch, model_lanes="on")
    assert result.depth.requested_by == "options.model_lanes" and result.depth.effective == "deep"
    assert runtime.calls
    runtime.calls.clear()
    result, _ = _live(tmp_path / "depth-wins", monkeypatch, model_lanes="on", depth="quick")
    assert result.depth.requested_by == "options.depth" and runtime.calls == []


def test_unconfigured_runtime_at_thorough_degrades_to_the_rules(
    tmp_path, monkeypatch, runtime, no_real_http
):
    monkeypatch.delenv("METERLESS_BASE_URL")
    quick, _ = _live(tmp_path / "quick", monkeypatch)
    result, lines = _live(tmp_path, monkeypatch, depth="thorough")
    assert runtime.calls == [] and _shape(result) == _shape(quick)
    d = result.depth
    assert (d.requested, d.effective, d.degraded) == ("thorough", "quick", True)
    assert "not configured" in d.note
    degraded = [x for x in lines if x["action"] == "degrade"]
    assert len(degraded) == 1 and degraded[0]["status"] == "degraded"
    assert d.expected_wait.max_seconds == FIRST_RUN_SECONDS["searchable"][1]  # quick's wait


def test_runtime_down_mid_run_is_reported_degraded(tmp_path, monkeypatch, no_real_http):
    monkeypatch.setenv("METERLESS_BASE_URL", "http://vm.test:8088/v1")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", TOKEN)

    def down(req):
        raise httpx.ConnectError("refused")

    real = nightshift.NightshiftClient
    monkeypatch.setattr(
        model_lanes,
        "NightshiftClient",
        lambda config, **kw: real(config, **{**kw, "transport": httpx.MockTransport(down)}),
    )
    result, _ = _live(tmp_path, monkeypatch, depth="deep")
    assert result.end_state == "actionable" and result.stats.model_calls == 1
    assert result.depth.effective == "deep" and result.depth.degraded
    assert "model unavailable" in result.depth.note


# --- sample mode: no model call at any depth -----------------------------------------------------


@pytest.fixture(scope="module")
def sample_quick(tmp_path_factory):
    out = {}
    for path in EXAMPLES:
        req = request(path.name[:2])
        req.options.sample_mode = True
        out[path.name[:2]] = run_investigation(req, logs_dir=tmp_path_factory.mktemp("quick"))
    return out


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("prefix", ["01", "02", "03"])
def test_sample_mode_zero_model_calls_at_every_depth(
    prefix, depth, sample_quick, tmp_path, monkeypatch, runtime
):
    monkeypatch.setenv("MODEL_LANES", "on")  # even the operator's on does not call in sample
    req = request(prefix)
    req.options.sample_mode = True
    req.options.depth = depth
    result = run_investigation(req, logs_dir=tmp_path)
    assert runtime.calls == [] and result.stats.model_calls == 0
    assert result.end_state == sample_quick[prefix].end_state == "actionable"
    assert _shape(result) == _shape(sample_quick[prefix])
    d = result.depth
    assert (d.requested, d.effective, d.lanes, d.degraded) == (depth, "quick", [], False)
    assert "sample mode" in d.note and "no model call" in d.note
    assert d.expected_wait.max_seconds <= 1.0 and "milliseconds" in d.expected_wait.basis


# --- expected wait -------------------------------------------------------------------------------


def test_expected_wait_scales_with_depth_and_sample_is_milliseconds():
    for tier in (None, "ready", "searchable"):
        waits = [expected_seconds(tier, d, sample=False) for d in DEPTHS]
        assert waits[0][0] < waits[1][0] < waits[2][0]
        assert waits[0][1] < waits[1][1] < waits[2][1]
        for d, (lo, hi, basis) in zip(DEPTHS, waits, strict=True):
            mlo, mhi, mbasis = MODEL_SECONDS[d]
            assert mbasis in basis and "2026-10-24" in MODEL_SECONDS["deep"][2]
            if tier:
                assert (lo, hi) == (
                    FIRST_RUN_SECONDS[tier][0] + mlo,
                    FIRST_RUN_SECONDS[tier][1] + mhi,
                )
    assert MODEL_SECONDS["quick"][:2] == (0.0, 0.0)
    assert MODEL_SECONDS["thorough"][:2] == (91.0, 94.0)  # three deep critic calls, case 01
    assert MODEL_SECONDS["deep"][:2] == (142.0, 145.0)  # + the ~51 s wording call
    for d in DEPTHS:
        lo, hi, basis = expected_seconds("ready", d, sample=True)
        assert (lo, hi) == FIRST_RUN_SECONDS["sample"][:2] and "no model call" in basis


def test_measured_model_seconds_match_the_adr():
    adr = (
        EXAMPLES[0].parents[1] / "docs" / "decisions" / "ADR-0011-investigation-depth.md"
    ).read_text()
    for needle in ("91–94 s", "about 51 s", "169–173 s", "3–11 s", "15–21 s"):
        assert needle in adr, needle


class _Record:
    run_id = "depth-202"
    status = "running"


class _Registry:
    max_concurrent = 4

    def start(self, body, run_id=None):
        return _Record()


@pytest.mark.parametrize(
    ("sample", "depth", "effective", "low"),
    [
        (False, None, "quick", 3.0),
        (False, "thorough", "thorough", 94.0),
        (False, "deep", "deep", 145.0),
        (True, "deep", "quick", 0.0),
    ],
)
def test_runs_202_body_carries_the_depth_and_expected_wait(
    sample, depth, effective, low, monkeypatch, runtime
):
    monkeypatch.setattr(app_module, "registry", _Registry())  # nothing actually runs
    body = json.loads(EXAMPLES[2].read_text())
    body["options"] = {"sample_mode": sample, **({"depth": depth} if depth else {})}
    r = TestClient(app_module.app).post("/runs", json=body)
    assert r.status_code == 202, r.text
    d = RunDepth.model_validate(r.json()["depth"])
    assert d.effective == effective and d.requested == (depth or "quick")
    lo, hi, _ = expected_seconds(None, effective, sample)
    assert (d.expected_wait.min_seconds, d.expected_wait.max_seconds) == (lo, hi)
    assert d.expected_wait.min_seconds >= low and runtime.calls == []
    if sample:
        assert d.expected_wait.max_seconds <= 1.0 and "sample mode" in d.note


def test_depth_plan_matches_the_result_of_the_run(tmp_path, monkeypatch, runtime, no_real_http):
    req = request("03", entity={"ticker": "ACME"})
    req.options.sample_mode = False
    req.options.depth = "thorough"
    planned = depth_plan(req)
    result, _ = _live(tmp_path, monkeypatch, depth="thorough")
    assert planned == result.depth


def test_entities_carry_the_expected_wait_per_depth(monkeypatch):
    _fake, tracer, source, discovery = live_directory(monkeypatch)
    body = entities.list_entities(
        "acme", sample=False, source=source, discovery=discovery, tracer=tracer
    )
    assert {e.tier for e in body.entities} == {"ready", "searchable"}
    for e in body.entities:
        by = e.expected_first_run_by_depth
        assert (by.quick.min_seconds, by.quick.max_seconds) == (
            e.expected_first_run.min_seconds,
            e.expected_first_run.max_seconds,
        )
        assert by.quick.max_seconds < by.thorough.max_seconds < by.deep.max_seconds
        lo, hi, _ = expected_seconds(e.tier, "deep", sample=False)
        assert (by.deep.min_seconds, by.deep.max_seconds) == (lo, hi)


def test_sample_entities_say_every_depth_is_milliseconds(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    r = TestClient(app_module.app).get("/entities?sample_mode=true")
    body = EntityList.model_validate(r.json())
    for e in body.entities:
        for d in DEPTHS:
            w = getattr(e.expected_first_run_by_depth, d)
            assert w.max_seconds <= 1.0 and "no model call" in w.basis


def test_cli_depth_flag_sets_the_depth(tmp_path, capsys, runtime):
    from tiresias_agent import orchestrator

    args = [str(EXAMPLES[2]), "--depth", "thorough", "--logs-dir", str(tmp_path)]
    assert orchestrator.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["depth"]["requested"] == "thorough" and out["depth"]["effective"] == "quick"
    assert out["stats"]["model_calls"] == 0 and runtime.calls == []  # the CLI defaults to sample
