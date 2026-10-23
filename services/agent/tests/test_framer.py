"""Framer: one measured, like-for-like effect per question (sample source)."""

import pytest
from loop_helpers import RecordingTracer, request

from tiresias_agent.adapters.zetaris import SampleSource
from tiresias_agent.agents.common import LoopError
from tiresias_agent.agents.framer import FrameFailure, frame, parse_filing_ref


def test_parse_filing_ref():
    assert parse_filing_ref("10-K filed 2020-01-31") == ("10-K", "2020-01-31")
    assert parse_filing_ref("the 10-k/a filed 2021-03-01") == ("10-K/A", "2021-03-01")
    assert parse_filing_ref("2017 and prior-year values") is None
    assert parse_filing_ref(None) is None


def test_restatement_is_framed_from_the_two_named_filings():
    req = request("01")
    tracer = RecordingTracer()
    f = frame(req, SampleSource(), tracer)
    assert f.kind == "restatement"
    assert f.baseline.filed == "2023-02-10" and f.target.filed == "2024-02-02"
    assert f.baseline.accn != f.target.accn
    assert f.delta == f.target.value - f.baseline.value < 0
    assert f.window() == ("2022-12-31", "2024-02-02")
    assert f.evidence.sql.startswith("SELECT") and f.evidence.row_count > 0
    (line,) = [e for e in tracer.events if e["agent"] == "framer"]
    assert line["action"] == "frame" and line["target_agent"] == "hypothesiser"


def test_change_uses_the_comparative_from_the_same_filing():
    f = frame(request("03"), SampleSource(), RecordingTracer())
    assert f.kind == "change"
    assert f.target.accn == f.baseline.accn  # like-for-like: one filing, two periods
    assert f.baseline.end == "2017-12-31"
    assert f.window() == ("2017-12-31", "2018-12-31")


def test_unknown_filing_ref_falls_back_to_a_change():
    req = request("01", compare={"filing_a": "10-K filed 1990-01-01", "filing_b": "x"})
    f = frame(req, SampleSource(), RecordingTracer())
    assert f.kind == "change" and "framed as a change" in (f.evidence.note or "")


def test_metric_missing_from_sources_is_a_frame_failure():
    req = request("01", entity={"ticker": "ZZZZ"})
    with pytest.raises(FrameFailure) as err:
        frame(req, SampleSource(), RecordingTracer())
    assert err.value.error_type == "MetricNotFound"


def test_invalid_ticker_is_a_controlled_error():
    req = request("01", entity={"ticker": "not a ticker!"})
    with pytest.raises(LoopError) as err:
        frame(req, SampleSource(), RecordingTracer())
    assert err.value.error_type == "InvalidInput"
