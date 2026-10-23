"""Loop B: source scout, onboarder (source gate), reconcile, and run_investigation end to end."""

import json
import shutil
from pathlib import Path

import httpx
import pytest
from loop_helpers import EXAMPLES, ROOT, RecordingTracer, request

from tiresias_agent import orchestrator
from tiresias_agent.adapters import zetaris as z
from tiresias_agent.adapters.catalog import (
    CatalogError,
    FilingRow,
    Profile,
    SampleDiscovery,
    TickerRow,
    load_catalog,
    parse_catalog,
)
from tiresias_agent.adapters.zetaris import Entity, Evidence, Fact, SampleSource, SourceError
from tiresias_agent.agents import onboarder, reconciler, source_scout
from tiresias_agent.agents.common import Frame
from tiresias_agent.config import LoopBudgets
from tiresias_agent.gates import Gate
from tiresias_agent.models import (
    Asker,
    EntityHint,
    EvidenceItem,
    InformationNeed,
    Observation,
    Period,
    RunResult,
)
from tiresias_agent.orchestrator import run_investigation, run_loop_a

END_STATES = {"actionable", "branched", "cyclic", "evidence-frontier", "external-driver", "budget"}
SAMPLE = ROOT / "data" / "sample"
CATALOG = load_catalog()
LOOP_B_AGENTS = {"source_scout", "onboarder"}


def _need(prefix):
    req = request(prefix)
    req.options.sample_mode = True
    result = run_loop_a(req, tracer=RecordingTracer())
    assert result.end_state == "evidence-frontier"
    return result.information_need


def _investigate(prefix, tmp_path, **kw):
    req = request(prefix)
    req.options.sample_mode = True
    for key in ("hitl_mode", "decisions"):
        if key in kw:
            setattr(req.options, key, kw.pop(key))
    result = run_investigation(req, logs_dir=tmp_path, **kw)
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    return result, lines


def _copy_sample(tmp_path, leave_out):
    """A copy of data/sample/ without one file (nothing is deleted)."""
    out = tmp_path / "sample"
    out.mkdir()
    for f in SAMPLE.iterdir():
        if f.name != leave_out:
            shutil.copy(f, out / f.name)
    return out


# --- catalog -----------------------------------------------------------------------------------


def test_catalog_parses_all_templates_with_params_and_join_keys():
    assert {"sec_companyfacts", "sec_submissions", "sec_tickers"} <= set(CATALOG.ids())
    facts = CATALOG.get("sec_companyfacts")
    assert facts.params == ("cik10",) and "cik" in facts.joins_on
    assert facts.url(cik10="0000000001").endswith("CIK0000000001.json")
    with pytest.raises(CatalogError):
        facts.url(cik10="1; DROP")
    with pytest.raises(CatalogError):
        CATALOG.get("not_in_catalog")


def test_catalog_rejects_lines_outside_its_yaml_subset():
    with pytest.raises(CatalogError):
        parse_catalog("sources:\n  - id: x\n    nested:\n      deeper: 1\n")
    complete = "sources:\n  - id: x\n    endpoint: /x\n    params: []\n    joins_on: []\n"
    assert parse_catalog(complete).ids() == ["x"]
    # A nested mapping inside an otherwise complete entry must not parse silently.
    for nested in ("    nested: yes\n      deeper: 1\n", "    nested:\n      deeper: 1\n"):
        with pytest.raises(CatalogError):
            parse_catalog(complete + nested)
    with pytest.raises(CatalogError):
        parse_catalog("version: 1\n")  # no sources


# --- source scout ------------------------------------------------------------------------------


@pytest.mark.parametrize(("prefix", "cik"), [("01", "0001932393"), ("02", "0001996810")])
def test_scout_resolves_the_disposed_business_from_the_data(prefix, cik):
    need = _need(prefix)
    tracer = RecordingTracer()
    scout = source_scout.resolve(need, discovery=SampleDiscovery(CATALOG), tracer=tracer)
    assert scout.resolved and scout.entity.cik == cik
    assert scout.template.id == "sec_companyfacts"
    qualifying = [c for c in scout.candidates if c.qualifies]
    assert [c.cik10 for c in qualifying] == [cik]  # the other registrant fails a rule
    line = next(e for e in tracer.events if e["agent"] == "source_scout")
    assert line["status"] == "success" and line["entity"]["cik"] == cik
    assert line["template"] == "sec_companyfacts" and "FROM tiresias.sec_tickers" in line["sql"]


def test_scout_reads_only_catalog_objects_as_zetaris_sql():
    tracer = RecordingTracer()
    source_scout.resolve(
        _need("01"), discovery=SampleDiscovery(CATALOG, tracer=tracer), tracer=tracer
    )
    reads = [e for e in tracer.events if e["agent"] == "catalog"]
    assert {r["action"] for r in reads} == {"tickers", "submissions"}
    for r in reads:  # the SQL live mode runs through Zetaris, over catalog-backed objects
        assert r["sql"].startswith("SELECT ")
        assert "FROM tiresias.sec_tickers" in r["sql"] or "_submissions_raw" in r["sql"]


def test_scout_refuses_a_hint_without_a_disposal_filing():
    need = _need("01").model_copy(
        update={"entity_hint": EntityHint(relation="same entity", description="finer detail")}
    )
    scout = source_scout.resolve(need, discovery=SampleDiscovery(CATALOG), tracer=RecordingTracer())
    assert not scout.resolved and "disposal filing" in scout.reason


def test_scout_refuses_when_the_catalog_has_no_registrable_facts_template():
    text = (ROOT / "catalog" / "sources.yaml").read_text().replace("sec_companyfacts", "other_x")
    discovery = SampleDiscovery(parse_catalog(text))
    scout = source_scout.resolve(_need("01"), discovery=discovery, tracer=RecordingTracer())
    assert not scout.resolved and scout.template is None


class FakeDiscovery:
    name = "fake"

    def __init__(self, tickers, profiles):
        self.catalog = CATALOG
        self._tickers, self._profiles = tickers, profiles

    def _rows(self, keep):
        return Evidence([t for t in self._tickers if keep(t)], sql="SELECT tickers", source="fake")

    def lookup(self, *, tickers=(), ciks=()):
        up = {t.upper() for t in tickers}
        return self._rows(lambda t: t.ticker.upper() in up or t.cik10 in set(ciks))

    def brand(self, words):
        return self._rows(lambda t: t.title.split()[0].lower() in set(words))

    def search(self, name):
        return self._rows(lambda t: name.lower() in t.title.lower())

    def submissions(self, cik10, ticker):
        p = self._profiles.get(cik10)
        return Evidence([p] if p else [], sql=f"GET {cik10}", source=self.name)


def _hint_need(date="2030-06-01"):
    return InformationNeed(
        node_id="n0.1",
        variable="Revenues",
        entity_hint=EntityHint(
            relation="disposed business",
            source_ticker="AAA",
            source_cik="0000000001",
            filing_date=date,
            filing_items=["2.01", "9.01"],
            description="test",
        ),
        period=Period(start="2029-01-01", end="2029-12-31"),
        join_key=["cik", "concept", "period_start", "period_end"],
        test="add back",
        reason="test",
    )


def _profile(cik, since, filings):
    return Profile(
        cik10=cik,
        name=cik,
        tickers=(),
        former_names=({"from": since + "T00:00:00.000Z"},),
        filings=tuple(FilingRow(f, d, i, f"a-{d}") for f, d, i in filings),
    )


def test_scout_assessment_rules_on_synthetic_registrants():
    tickers = [
        TickerRow("AAA", "0000000001", "Alpha Corp"),
        TickerRow("AAAX", "0000000005", "Alpha Old Inc"),  # already filed a 10-K
        TickerRow("AAAN", "0000000006", "Alpha Late Inc"),  # registered after the disposal
        TickerRow("AAAS", "0000000007", "Alpha Spin Inc"),  # qualifies
        TickerRow("BBB", "0000000008", "Beta Inc"),  # other brand: never a candidate
    ]
    profiles = {
        "0000000005": _profile(
            "0000000005", "2020-01-01", [("10-K", "2029-02-01", ""), ("8-K", "2030-06-01", "5.01")]
        ),
        "0000000006": _profile("0000000006", "2030-07-01", [("8-K", "2030-07-02", "")]),
        "0000000007": _profile("0000000007", "2030-01-15", [("8-K", "2030-06-02", "5.01,9.01")]),
        "0000000008": _profile("0000000008", "2030-01-15", [("8-K", "2030-06-01", "5.01")]),
    }
    scout = source_scout.resolve(
        _hint_need(), discovery=FakeDiscovery(tickers, profiles), tracer=RecordingTracer()
    )
    assert scout.resolved and scout.entity.cik == "0000000007"
    assert {c.cik10 for c in scout.candidates} == {"0000000005", "0000000006", "0000000007"}
    reasons = {c.cik10: c.reason for c in scout.candidates}
    assert "annual report" in reasons["0000000005"] and "not a registrant" in reasons["0000000006"]


def test_scout_refuses_a_tie_and_an_unmatched_disposal():
    tickers = [TickerRow("AAA", "0000000001", "Alpha Corp")] + [
        TickerRow(f"AAA{c}", f"000000000{c}", f"Alpha {c}") for c in (2, 3)
    ]
    same = [("8-K", "2030-06-01", "5.01")]
    profiles = {c: _profile(c, "2030-01-01", same) for c in ("0000000002", "0000000003")}
    tie = source_scout.resolve(
        _hint_need(), discovery=FakeDiscovery(tickers, profiles), tracer=RecordingTracer()
    )
    assert not tie.resolved and "ambiguous" in tie.reason
    none = source_scout.resolve(
        _hint_need("2035-01-01"),
        discovery=FakeDiscovery(tickers, profiles),
        tracer=RecordingTracer(),
    )
    assert not none.resolved and "no candidate" in none.reason


# --- onboarder ---------------------------------------------------------------------------------


def _scout(prefix="01"):
    return source_scout.resolve(
        _need(prefix), discovery=SampleDiscovery(CATALOG), tracer=RecordingTracer()
    )


def _onboard(scout, *, gate=None, cap=2, sample=True, registry=None, **kw):
    tracer = RecordingTracer()
    need = _need("01")
    registry = registry or onboarder.Registry(SampleSource(), "ge")
    gate = gate or Gate("auto", tracer=tracer)
    gate.tracer = tracer
    reg = onboarder.onboard(
        scout, need, gate=gate, registry=registry, cap=cap, sample=sample, tracer=tracer, **kw
    )
    return reg, registry, tracer.events


def test_onboarder_auto_gate_registers_in_sample_mode_and_says_so():
    scout = _scout()
    reg, registry, events = _onboard(scout)
    assert reg.status == "registered" and reg.mode == "sample" and reg.gate_decided
    assert scout.entity.ticker in registry.allowed and registry.onboarded == [scout.entity]
    gate = next(e for e in events if e["agent"] == "gate")
    assert (gate["gate"], gate["gate_mode"], gate["decided_by"]) == (
        "source",
        "auto",
        "policy:source",
    )
    line = next(e for e in events if e["agent"] == "onboarder" and e["action"] == "register")
    assert line["registration"] == "sample" and "Zetaris not called" in line["output_summary"]
    dry = next(e for e in events if e["action"] == "dry_run")
    assert dry["sql"].startswith("CREATE LIGHTNING REST TABLE") and dry["rows"] >= 3


def test_onboarder_scripted_deny_registers_nothing():
    scout = _scout()
    gate = Gate("scripted", tracer=RecordingTracer(), scripted={"source": "deny"})
    reg, registry, events = _onboard(scout, gate=gate)
    assert reg.status == "denied" and not registry.onboarded
    decide = next(e for e in events if e["agent"] == "gate")
    assert (decide["gate_mode"], decide["decided_by"]) == ("scripted", "script")
    with pytest.raises(SourceError):
        registry.concept_series(scout.entity.ticker, "Revenues")  # still not visible


def test_onboarder_respects_the_source_cap_without_asking_the_gate():
    scout = _scout()
    reg, registry, events = _onboard(scout, cap=0)
    assert reg.status == "capped" and "MAX_NEW_SOURCES_PER_RUN=0" in reg.reason
    assert not any(e["agent"] == "gate" for e in events) and not registry.onboarded
    full = onboarder.Registry(SampleSource(), "ge")
    full.add(Entity("zz", "0000000009", "x"))
    reg, _, _ = _onboard(scout, cap=1, registry=full)
    assert reg.status == "capped"


def test_onboarder_fails_when_the_sample_slice_lacks_the_entity(tmp_path):
    scout = _scout()
    data = _copy_sample(tmp_path, f"{scout.entity.ticker}_companyfacts.json")
    reg, registry, _ = _onboard(scout, data_dir=data)
    assert reg.status == "failed" and not registry.onboarded


def _client(handler):
    cfg = z.ZetarisConfig(rest_url="http://zetaris.test/api/v1.0", api_key="k", max_retries=0)
    return z.ZetarisClient(cfg, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_onboarder_live_registers_one_statement_at_a_time_and_refreshes_views():
    statements = []

    def ok(req):
        statements.append(json.loads(req.content)["sql"])
        return httpx.Response(200, json={})

    class Src(SampleSource):
        refreshed = 0

        def refresh(self):
            Src.refreshed += 1

    scout = _scout()
    registry = onboarder.Registry(Src(), "ge")
    reg, _, _ = _onboard(
        scout, sample=False, registry=registry, client=_client(ok), sec_user_agent="Test t@x.io"
    )
    assert reg.status == "registered" and reg.mode == "zetaris" and Src.refreshed == 1
    assert len(statements) == reg.evidence[0].row_count  # every planned statement, one per call
    assert any("CREATE LIGHTNING REST TABLE" in s for s in statements)
    assert not any("submissions" in s for s in statements)  # only the picked template


def test_onboarder_live_outage_degrades_but_a_bad_request_never_uses_the_sample():
    scout = _scout()
    down, _, _ = _onboard(
        scout,
        sample=False,
        client=_client(lambda r: httpx.Response(503, text="down")),
        sec_user_agent="Test t@x.io",
    )
    assert down.status == "degraded" and down.mode == "sample" and down.evidence[0].degraded
    bad, registry, _ = _onboard(
        scout,
        sample=False,
        client=_client(lambda r: httpx.Response(400, text="syntax")),
        sec_user_agent="Test t@x.io",
    )
    assert bad.status == "failed" and bad.mode == "zetaris" and not registry.onboarded


# --- reconcile ---------------------------------------------------------------------------------


def _frame(original, restated, kind="restatement"):
    obs = lambda v, filed: Observation(
        value=v,
        concept="Revenues",
        start="2029-01-01",
        end="2029-12-31",
        form="10-K",
        filed=filed,
        accn=f"a-{filed}",
    )
    return Frame(
        question="q",
        ticker="aaa",
        cik="0000000001",
        metric="Revenues",
        aliases=("Revenues",),
        period_start="2029-01-01",
        period_end="2029-12-31",
        kind=kind,
        baseline=obs(original, "2030-02-01"),
        target=obs(restated, "2031-02-01"),
        asker=Asker(role="analyst"),
        evidence=EvidenceItem(sql="s", source="t", row_count=0),
    )


class OneSeries:
    name = "fake"

    def __init__(self, values):
        self.values = values

    def concept_series(self, entity, concept):
        facts = [
            Fact(
                "Revenues",
                "USD",
                v,
                "2029-01-01",
                "2029-12-31",
                2029,
                "FY",
                "10-K",
                f"203{i}-03-01",
                f"b{i}",
            )
            for i, v in enumerate(self.values)
        ]
        return Evidence(facts, sql="SELECT ... FROM tiresias.bbb_revenues", source="fake")


def _reconcile(original, restated, values, kind="restatement"):
    frame = _frame(original, restated, kind)
    need = _hint_need()
    r = reconciler.measure(
        frame,
        need,
        Entity("bbb", "0000000007", "B"),
        OneSeries(values),
        node_id="n0.1.1",
        tracer=RecordingTracer(),
    )
    return r, *reconciler.judge(frame, r)


def test_reconcile_add_back_math_share_and_residual():
    r, v, residual = _reconcile(150.0, 100.0, [48.0])
    assert r.counterfactual == 148.0 and r.effect == -48.0 and r.residual_amount == -2.0
    assert v.decision == "accept" and v.label == "causal" and v.evidence_status == "evidenced"
    assert v.share == pytest.approx(0.96) and residual == pytest.approx(0.04)


def test_reconcile_over_explained_within_tolerance_has_a_negative_residual():
    _, v, residual = _reconcile(1000.0, 900.0, [102.0])
    assert v.decision == "accept" and v.share == 1.0 and residual == pytest.approx(-0.02)
    assert not v.escalation


def test_reconcile_over_explained_beyond_tolerance_escalates():
    _, v, _ = _reconcile(150.0, 100.0, [80.0])
    assert v.escalation and v.escalation["proposal"] == "accept-capped"


def test_reconcile_small_share_is_pruned_and_missing_data_is_a_frontier():
    assert _reconcile(150.0, 100.0, [5.0])[1].decision == "prune"
    assert _reconcile(150.0, 100.0, [])[1].decision == "frontier"
    assert _reconcile(150.0, 100.0, [48.0], kind="change")[1].decision == "frontier"


def test_reconcile_uses_the_first_annual_report_of_the_period():
    r, _, _ = _reconcile(150.0, 100.0, [48.0, 47.0])
    assert r.value == 48.0 and r.others == [47.0]


# --- run_investigation -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    return {
        p.name[:2]: _investigate(p.name[:2], tmp_path_factory.mktemp(p.name[:2])) for p in EXAMPLES
    }


@pytest.mark.parametrize("prefix", ["01", "02", "03"])
def test_investigation_reaches_a_valid_end_state(runs, prefix):
    result, lines = runs[prefix]
    assert result.end_state in END_STATES and result.status in {"success", "partial"}
    assert RunResult.model_validate(result.model_dump(mode="json")) == result
    assert all(line["mode"] == "sample" for line in lines)


@pytest.mark.parametrize("prefix", ["01", "02"])
def test_restatements_continue_into_loop_b_and_reconcile(runs, prefix):
    result, lines = runs[prefix]
    agents = {line["agent"] for line in lines}
    assert LOOP_B_AGENTS <= agents
    assert any(x["agent"] == "gate" and x.get("gate") == "source" for x in lines)
    b = [x for x in lines if x.get("loop") == "B"]
    assert {"analyst", "critic"} <= {x["agent"] for x in b}
    verdict = next(x for x in b if x["agent"] == "critic")
    assert verdict["sql"] and verdict["rows"] >= 1 and verdict["label"] == "causal"
    assert verdict["share_explained"] is not None and verdict["residual"] is not None
    assert result.end_state == "actionable" and result.information_need is None
    link = next(n for n in result.why_tree if n.rule == "reconcile")
    assert link.verdict == "accepted" and link.evidence_status == "evidenced"
    assert link.evidence and all(ev.sql for ev in link.evidence)
    assert result.residual is not None and abs(result.residual) < 0.05
    assert result.residual == link.residual and result.stats.sources_onboarded == 1


def test_net_loss_case_does_not_enter_loop_b(runs):
    result, lines = runs["03"]
    assert result.end_state == "actionable"
    assert not ({line["agent"] for line in lines} & LOOP_B_AGENTS)
    assert not any(line.get("loop") == "B" for line in lines)
    assert result.stats.sources_onboarded == 0


def test_investigation_outputs_differ_across_cases(runs):
    results = [runs[p][0] for p in ("01", "02", "03")]
    assert len({r.action for r in results}) == 3 and len({r.root_cause for r in results}) == 3
    assert len({r.residual for r in results}) == 3


@pytest.mark.parametrize("leave_out", ["gehc_companyfacts.json", "gehc_submissions.json"])
def test_missing_sample_data_for_the_target_leaves_an_evidence_frontier(
    tmp_path, monkeypatch, leave_out
):
    data = _copy_sample(tmp_path, leave_out)
    monkeypatch.setenv("SAMPLE_DATA_DIR", str(data))
    result, _ = _investigate("01", tmp_path)
    assert result.status == "partial" and result.error is None
    assert result.end_state == "evidence-frontier" and "Loop B" in result.information_need.reason
    assert not any(n.rule == "reconcile" and n.verdict == "accepted" for n in result.why_tree)


def test_scripted_source_deny_keeps_the_frontier(tmp_path):
    result, _ = _investigate("01", tmp_path, hitl_mode="scripted", decisions={"source": "deny"})
    assert result.end_state == "evidence-frontier" and result.stats.sources_onboarded == 0
    assert "denied" in result.information_need.reason


def test_source_cap_zero_ends_in_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_NEW_SOURCES_PER_RUN", "0")
    result, _ = _investigate("01", tmp_path)
    assert result.end_state == "budget" and "MAX_NEW_SOURCES_PER_RUN=0" in result.action


def test_wall_clock_budget_applies_to_loop_b(tmp_path):
    now = [0.0]

    class Tracer(RecordingTracer):
        def emit(self, **kw):
            if kw.get("action") == "loop_b":
                now[0] = 1e6  # the run budget is spent by the time Loop B starts its work
            super().emit(**kw)

    req = request("01")
    req.options.sample_mode = True
    result = run_investigation(
        req, tracer=Tracer(), budgets=LoopBudgets(run_seconds=60), clock=lambda: now[0]
    )
    assert result.end_state == "budget"


class NoNewEntity(SampleSource):
    """A source that fails non-recoverably for any entity but the asked one."""

    def concept_series(self, entity, concept):
        if entity.lower() != "ge":
            raise SourceError("FIELD_NOT_FOUND", recoverable=False)
        return super().concept_series(entity, concept)


def test_non_recoverable_source_error_is_not_turned_into_sample_evidence(tmp_path):
    req = request("01")
    req.options.sample_mode = True
    tracer = RecordingTracer()
    result = run_investigation(req, source=NoNewEntity(), tracer=tracer)
    assert result.end_state == "evidence-frontier" and result.error is None
    link = next(n for n in result.why_tree if n.rule == "reconcile")
    assert link.verdict == "pruned" and "not testable" in link.reason
    measure = next(e for e in tracer.events if e["agent"] == "analyst" and e.get("loop") == "B")
    assert measure["status"] == "error"


def test_unframed_effect_skips_loop_b_with_a_trace_line(tmp_path):
    tracer = RecordingTracer()
    result = run_investigation(request("01", entity={"ticker": "ZZZZ"}), tracer=tracer)
    assert result.end_state == "evidence-frontier"
    skip = next(e for e in tracer.events if e["action"] == "loop_b")
    assert skip["status"] == "rejected"


def test_cli_runs_the_investigation_and_loop_a_only_on_request(tmp_path, capsys):
    example = next(p for p in EXAMPLES if p.name.startswith("01"))
    assert orchestrator.main([str(example), "--logs-dir", str(tmp_path)]) == 0
    assert RunResult.model_validate_json(capsys.readouterr().out).end_state == "actionable"
    assert orchestrator.main([str(example), "--loop-a", "--logs-dir", str(tmp_path)]) == 0
    assert RunResult.model_validate_json(capsys.readouterr().out).end_state == "evidence-frontier"


def test_bootstrap_plan_can_register_only_the_facts_table():
    e = Entity("bbb", "0000000007", "B")
    plan = z.bootstrap_plan("Test t@x.io", [e], ["Revenues"], tables=("facts",))
    steps = [s for s, _, _, _ in plan]
    assert steps == ["database", "container", "facts_table", "cache", "concept_view"]
    assert len(z.bootstrap_plan("Test t@x.io", [e], ["Revenues"])) == len(plan) + 3
    with pytest.raises(ValueError):
        z.bootstrap_plan("Test t@x.io", [e], ["Revenues"], tables=("bogus",))
