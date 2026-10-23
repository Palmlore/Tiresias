"""Scope check before Loop A: forecast / what-if questions and non-SEC companies end at once
with plain reasons and runnable alternatives; the Super Micro-style frontier asks for a
breakdown instead of the same total."""

import json
from datetime import date
from pathlib import Path

import pytest
from fake_zetaris import FakeZetaris
from loop_helpers import EXAMPLES, RecordingTracer, request

from tiresias_agent.adapters import zetaris as z
from tiresias_agent.agents import scope
from tiresias_agent.agents.entity_resolver import Resolution
from tiresias_agent.models import RunRequest
from tiresias_agent.orchestrator import run_investigation
from tiresias_agent.trace import Tracer

UA = "Tiresias test test@example.com"

# --- the forecast / what-if rule ----------------------------------------------------------------

FORWARD = [
    ("What would Gigabyte's server throughput be if Nvidia doubled GPU supply?", "What would"),
    ("What will GE's revenue be in FY2027?", "will"),
    ("Will revenue keep growing?", "Will"),
    ("What if the company had not spun off its power business?", "What if"),
    ("Forecast next year's revenue", "Forecast"),
    ("Do analysts expect revenue to grow?", "expect revenue to"),
    ("How much would net income change if rates rise?", "How much would"),
    ("Suppose margins fall: what happens to net income?", "Suppose"),
    ("Is revenue going to fall?", "going to"),
]
IN_SCOPE = [
    "Why did revenue fall even if orders rose?",
    "Why did net income come in below what analysts expected?",
    "Why did the company miss its forecast in 2018?",
    "Why did revenue fall short of its outlook?",
    "Why did goodwill impairment rise?",
    "Why would the company restate its 2022 revenue?",
    "How would revenue have changed without the disposal?",
    "What would revenue have been without the spin-off?",
    "What caused the expected-to-be-temporary drop in revenue?",
    "Why did revenue fall in the quarter after the spin-off?",
]


@pytest.mark.parametrize(("question", "cue"), FORWARD)
def test_forward_questions_are_caught_with_their_cue(question, cue):
    assert scope.forward_cue(question) == cue


@pytest.mark.parametrize("question", IN_SCOPE)
def test_past_why_questions_are_not_false_positives(question):
    assert scope.forward_cue(question) is None


def test_the_input_examples_are_in_scope():
    for path in EXAMPLES:
        assert scope.forward_cue(json.loads(path.read_text())["question"]) is None


def test_a_period_after_today_is_a_forecast_even_without_a_cue():
    req = request("01", question="Why did revenue fall?")
    req.period.start, req.period.end = date(2030, 1, 1), date(2030, 12, 31)
    res = Resolution(None, False, "x")
    out = scope.check(
        req,
        res,
        sample=True,
        source=z.SampleSource(),
        discovery=lambda: None,
        tracer=RecordingTracer(),
        today=date(2026, 10, 23),
    )
    assert not out.in_scope and out.reasons[0].type == "forecast"
    assert "after today" in out.reasons[0].cue


def test_name_phrases_skip_question_words_periods_acronyms_and_the_asked_company():
    q = "What would Gigabyte's server throughput be if Nvidia doubled GPU supply in FY2026?"
    assert scope.name_phrases(q, ["Gigabyte"]) == ["Nvidia"]
    assert scope.name_phrases("Why did General revenue fall?", []) == []


# --- sample mode -------------------------------------------------------------------------------


def _lines(result):
    return [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]


def test_sample_forecast_for_a_registrant_offers_past_period_and_same_sic_peer(tmp_path):
    req = request("01", question="What will GE's revenue be in FY2027?")
    req.options.sample_mode = True
    result = run_investigation(req, logs_dir=tmp_path)
    assert result.end_state == "evidence-frontier" and result.status == "partial"
    assert result.error is None and result.effect is None and result.information_need is None
    s = result.scope
    assert not s.in_scope and [r.type for r in s.reasons] == ["forecast"]
    assert (
        result.root_cause.startswith("Out of scope:") and "does not forecast" in result.root_cause
    )
    bases = [a.basis for a in s.alternatives]
    assert bases[0] == "same entity, past period"
    assert any(b.startswith("same SIC peer (") for b in bases)  # peers share the SIC code
    assert 2 <= len(s.alternatives) <= scope.MAX_ALTERNATIVES
    assert all(a.request.question in result.action for a in s.alternatives)
    lines = _lines(result)
    assert not any(x["agent"] in {"framer", "hypothesiser", "analyst"} for x in lines)
    check = next(x for x in lines if x["agent"] == "scope" and x["action"] == "check")
    assert check["status"] == "rejected" and check["reasons"] == ["forecast"]
    # Each alternative is runnable as-is and in scope.
    for alt in s.alternatives:
        assert alt.registered and len(alt.checks) == 4
        run = run_investigation(alt.request, logs_dir=tmp_path)
        assert run.error is None and run.scope is None and run.effect is not None


def test_sample_non_sec_what_if_says_both_reasons_and_why_no_peers(tmp_path):
    req = RunRequest(
        question="What would Gigabyte's server throughput be if Nvidia doubled GPU supply?",
        entity={"name": "Gigabyte"},
        metric="Revenues",
        period={"start": "2025-01-01", "end": "2025-12-31"},
        asker={"role": "analyst"},
        options={"sample_mode": True},
    )
    result = run_investigation(req, logs_dir=tmp_path)
    assert result.end_state == "evidence-frontier" and result.error is None
    assert [r.type for r in result.scope.reasons] == ["forecast", "not-in-sample"]
    assert result.scope.alternatives == []
    assert "no industry (SIC)" in result.scope.note and "model" in result.scope.note
    assert "Run live" in result.action


def test_in_scope_examples_trace_the_check_and_carry_no_scope(tmp_path):
    req = request("03")
    req.options.sample_mode = True
    result = run_investigation(req, logs_dir=tmp_path)
    assert result.end_state == "actionable" and result.scope is None
    check = next(x for x in _lines(result) if x["agent"] == "scope")
    assert check["action"] == "check" and check["in_scope"] is True


# --- live, with a fake Zetaris -----------------------------------------------------------------

TICKERS = [
    ("ACME", 42, "ACME CORP"),
    ("NVDX", 1045810, "NVDX CORP"),
    ("PEER", 1996810, "PEER Power Inc."),
]


def _live(tmp_path, monkeypatch, req, **kw):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    fake = FakeZetaris(
        TICKERS,
        {"acme": "ge", "nvdx": "gev", "peer": "gev"},
        tables={
            "acme_facts_raw": "https://example.test/companyfacts/CIK0000000042.json",
            "peer_facts_raw": "https://example.test/companyfacts/CIK0001996810.json",
        },
        views={
            "sec_tickers",
            "acme_filings",
            "acme_revenues",
            "acme_income_cont_ops",
            "peer_filings",
            "peer_revenue_contract",
        },
        **kw,
    )
    req.options.sample_mode = False
    tracer = Tracer("run-scope-test", logs_dir=tmp_path, defaults={"mode": "live"})
    client = fake.client(tracer)
    source = z.make_source(False, tracer, client=client)
    result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
    return fake, result, _lines(result)


def _what_if(**entity):
    return RunRequest(
        question="What would Gigabyte's server throughput be if Nvdx doubled GPU supply?",
        entity=entity,
        metric="Revenues",
        period={"start": "2025-01-01", "end": "2025-12-31"},
        asker={"role": "analyst", "levers": ["estimates"]},
    )


def test_live_non_sec_what_if_offers_the_named_registrant_validated_from_its_filings(
    tmp_path, monkeypatch
):
    fake, result, _ = _live(tmp_path, monkeypatch, _what_if(name="Gigabyte"))
    assert result.end_state == "evidence-frontier" and result.error is None
    assert [r.type for r in result.scope.reasons] == ["forecast", "not-sec-registrant"]
    assert "not an SEC registrant" in result.scope.reasons[1].message
    [alt] = result.scope.alternatives
    assert alt.basis == "named in the question" and alt.entity.ticker == "NVDX"
    assert alt.registered is False  # registrable: the run registers it first
    assert (alt.request.period.start.isoformat(), alt.request.period.end.isoformat()) == (
        "2025-01-01",
        "2025-12-31",
    )  # between its two latest 10-K report dates
    assert any("sec_tickers" in c for c in alt.checks) and any("15-21 s" in c for c in alt.checks)
    assert "no industry (SIC)" in result.scope.note
    # Nothing was registered as an evidence source by the scope check.
    assert result.stats.sources_onboarded == 0
    assert not [s for s in fake.sql if s.startswith("CREATE LIGHTNING REST TABLE nvdx_facts")]
    # The alternative runs as-is: it registers the registrant and frames the change.
    run = run_investigation(
        alt.request,
        source=z.make_source(False, None, client=fake.client()),
        zetaris_client=fake.client(),
        logs_dir=tmp_path,
    )
    assert run.error is None and run.scope is None and run.effect is not None
    assert run.stats.sources_onboarded == 1


def test_live_forecast_for_a_registrant_offers_its_past_period_and_a_sic_peer(
    tmp_path, monkeypatch
):
    req = request(
        "01", question="What will ACME's revenue be next year?", entity={"ticker": "ACME"}
    )
    _, result, lines = _live(tmp_path, monkeypatch, req)
    assert [r.type for r in result.scope.reasons] == ["forecast"]
    bases = [a.basis for a in result.scope.alternatives]
    assert bases == ["same entity, past period", "same SIC peer (3600)"]
    assert all(a.registered for a in result.scope.alternatives)
    regs = [x for x in lines if x["agent"] == "catalog" and x["action"] == "registrant"]
    assert regs and all("sic" in x["sql"] and "report_date" in x["sql"] for x in regs)


def test_live_outage_never_claims_not_sec(tmp_path, monkeypatch):
    _, result, _ = _live(
        tmp_path, monkeypatch, request("03", entity={"ticker": "NOPE"}), fail_all=True
    )
    assert result.scope is None
    assert "Zetaris unreachable" in result.information_need.reason


# --- the frontier when every candidate is a ruled-out accounting event ------------------------


def test_all_events_ruled_out_asks_for_a_breakdown_not_the_same_total(tmp_path):
    req = request(
        "01",
        question="Why did revenue rise in FY2025?",
        period={"start": "2025-01-01", "end": "2025-12-31"},
        compare=None,
    )
    req.options.sample_mode = True
    result = run_investigation(req, logs_dir=tmp_path)
    assert result.end_state == "evidence-frontier" and result.error is None
    tested = [n for n in result.why_tree if n.verdict != "root"]
    assert tested and all(n.verdict == "pruned" for n in tested)
    need = result.information_need
    assert need.variable == "Revenues by segment or product"
    assert need.entity_hint.relation == "same entity, breakdown"
    assert result.root_cause.startswith("Ruled out (none explains the ")
    assert "acquisition or disposal (8-K item 2.01)" in result.root_cause
    assert "segment or product detail isn't in SEC company facts" in result.root_cause
    assert "Onboard a source for Revenues (" not in result.action
    skip = [x for x in _lines(result) if x["agent"] == "orchestrator" and x["action"] == "loop_b"]
    assert skip and skip[0]["status"] == "rejected" and "breakdown" in skip[0]["output_summary"]
