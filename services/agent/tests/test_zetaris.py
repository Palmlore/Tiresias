"""Zetaris adapter: live client over httpx.MockTransport, SampleSource on data/sample, fallback."""

import json
import re

import httpx
import pytest

from tiresias_agent.adapters import zetaris as z

KEY = "zk-test-secret-0123456789abcdef"
UA = "Tiresias tester@example.com"


class RecordingTracer:
    run_id = "run-test-1"

    def __init__(self):
        self.events = []

    def emit(self, *, agent, action, status="success", **fields):
        self.events.append({"agent": agent, "action": action, "status": status, **fields})


def make_client(handler, *, tracer=None, max_retries=2, timeout=5.0):
    config = z.ZetarisConfig(
        rest_url="http://zetaris.test/api/v1.0",
        org_id="1",
        api_key=KEY,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )
    sleeps = []
    client = z.ZetarisClient(
        config, tracer=tracer, transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    return client, sleeps


def ok(headers, rows):
    return httpx.Response(200, json={"headers": headers, "data": rows, "total": len(rows)})


# --- live client --------------------------------------------------------------------------------


def test_run_sql_sends_auth_org_request_id_and_run_id_headers():
    seen = []

    def handler(request):
        seen.append(request)
        return ok(["one"], [["1"]])

    tracer = RecordingTracer()
    client, _ = make_client(handler, tracer=tracer)
    result = client.run_sql("SELECT 1 AS one", label="ping")
    request = seen[0]
    assert request.url.path == "/api/v1.0/sql-editor/sqls/run"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert request.headers["X-Org-ID"] == "1"
    assert re.fullmatch(r"[0-9a-f-]{36}", request.headers["X-Request-ID"])
    assert request.headers["X-Run-Id"] == "run-test-1"
    assert json.loads(request.content)["sql"] == "SELECT 1 AS one"
    assert result.rows == [{"one": "1"}] and result.columns == ["one"]
    assert result.source == "zetaris" and result.sql == "SELECT 1 AS one"
    event = tracer.events[-1]
    assert event["agent"] == "zetaris_adapter" and event["status"] == "success"
    assert {"sql", "rows", "duration_ms", "retry_count", "source"} <= event.keys()


def test_run_sql_sends_a_run_id_even_without_one_supplied():
    seen = []

    def handler(request):
        seen.append(request)
        return ok(["one"], [["1"]])

    client, _ = make_client(handler)
    client.run_sql("SELECT 1", label="a")
    client.run_sql("SELECT 1", label="b")
    run_ids = [request.headers.get("X-Run-Id") for request in seen]
    assert run_ids[0] and run_ids[0] == run_ids[1]


@pytest.mark.parametrize(
    "rows",
    [[{"other": "wrong"}], [["1", "2"]], [["1"], "scalar"]],
    ids=["dict-keys-mismatch", "list-length-mismatch", "not-a-row"],
)
def test_run_sql_rejects_rows_not_keyed_by_response_headers(rows):
    client, _ = make_client(lambda request: ok(["value"], rows))
    with pytest.raises(z.SourceError) as caught:
        client.run_sql("SELECT 1 AS value", label="shape")
    assert caught.value.recoverable is False


@pytest.mark.parametrize(
    "payload",
    [[{"headers": ["x"], "data": [["bad"]]}], "text", 42, {"headers": "x", "data": []}],
)
def test_run_sql_rejects_a_malformed_top_level_payload(payload):
    client, _ = make_client(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(z.SourceError) as caught:
        client.run_sql("SELECT 1", label="shape")
    assert caught.value.recoverable is False


def test_run_sql_retries_503_then_succeeds_with_fresh_request_ids():
    request_ids = []

    def handler(request):
        request_ids.append(request.headers["X-Request-ID"])
        if len(request_ids) == 1:
            return httpx.Response(503, text="busy")
        return ok(["one"], [["1"]])

    client, sleeps = make_client(handler)
    result = client.run_sql("SELECT 1", label="ping")
    assert len(request_ids) == 2 and request_ids[0] != request_ids[1]
    assert result.retry_count == 1 and len(sleeps) == 1


def test_run_sql_does_not_retry_400():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"msg": "bad sql"})

    client, sleeps = make_client(handler)
    with pytest.raises(z.SourceError) as info:
        client.run_sql("SELEC 1", label="bad")
    assert len(calls) == 1 and sleeps == []
    assert info.value.recoverable is False and info.value.status_code == 400


def test_timeout_becomes_recoverable_source_error_after_bounded_retries():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    client, sleeps = make_client(handler, max_retries=2)
    with pytest.raises(z.SourceError) as info:
        client.run_sql("SELECT 1", label="slow")
    assert len(calls) == 3 and len(sleeps) == 2
    assert info.value.recoverable is True and info.value.retry_count == 2
    assert "timed out" in str(info.value)


def test_api_key_never_in_errors_reprs_or_trace_fields():
    def handler(request):
        echoed = request.headers["Authorization"]  # a server that echoes the credential
        return httpx.Response(500 if "500" in request.content.decode() else 401, text=echoed)

    tracer = RecordingTracer()
    client, _ = make_client(handler, tracer=tracer, max_retries=1)
    texts = [repr(client), repr(client.config)]
    for sql in ("SELECT 500", "SELECT 401"):
        with pytest.raises(z.SourceError) as info:
            client.run_sql(sql, label="leak")
        texts += [str(info.value), repr(info.value), info.value.detail]
    texts += [json.dumps(event) for event in tracer.events]
    assert tracer.events and all(KEY not in text for text in texts)


def test_config_from_env_reads_bounds_and_hides_key():
    config = z.ZetarisConfig.from_env(
        {
            "ZETARIS_REST_URL": "http://localhost:8888/api/v1.0/",
            "ZETARIS_ORG_ID": "7",
            "ZETARIS_API_KEY": KEY,
            "SOURCE_TIMEOUT_SECONDS": "12",
            "MAX_RETRIES": "3",
        }
    )
    assert config.rest_url == "http://localhost:8888/api/v1.0"
    assert (config.org_id, config.timeout_seconds, config.max_retries) == ("7", 12.0, 3)
    assert KEY not in repr(config)


# --- live source --------------------------------------------------------------------------------


def test_live_source_types_rows_and_unions_registered_alias_views():
    sent = []

    def handler(request):
        sql = json.loads(request.content)["sql"]
        sent.append(sql)
        if sql.startswith("SHOW DATASOURCE VIEWS"):
            return ok(["table_identifier"], [["GE_REVENUES"], ["GE_REVENUE_CONTRACT"]])
        cols = ["concept", "unit", "val", "period_start", "period_end", "fy", "fp", "form"]
        cols += ["filed", "accn", "frame"]
        row = ["Revenues", "USD", "76555000000", "2022-01-01", "2022-12-31", "2022", "FY"]
        row += ["10-K", "2023-02-10", "0000040545-23-000023", None]
        return ok(cols, [row])

    source = z.LiveZetarisSource(make_client(handler)[0])
    facts = source.concept_series("GE", "Revenues")
    assert facts.source == "zetaris" and "UNION ALL" in facts.sql
    assert "tiresias.ge_revenues" in facts.sql and "tiresias.ge_revenue_contract" in facts.sql
    assert facts[0].val == 76555000000 and facts[0].fy == 2022 and facts[0].frame is None
    assert source.concept_series("ge", "NetIncomeLoss").note  # no view: not queried
    assert sum(s.startswith("SHOW DATASOURCE VIEWS") for s in sent) == 1


def test_identifiers_are_validated_before_sql_is_built():
    source = z.SampleSource()
    with pytest.raises(ValueError):
        source.concept_series("ge; DROP", "Revenues")
    with pytest.raises(ValueError):
        source.concept_series("ge", "Revenues' OR '1'='1")
    with pytest.raises(ValueError):
        source.filings("ge", ["10-K') OR ('x"])


# --- sample source ------------------------------------------------------------------------------


def test_sample_ge_fy2022_revenue_includes_restated_values():
    tracer = RecordingTracer()
    facts = z.SampleSource(tracer=tracer).concept_series("ge", "Revenues")
    fy2022 = {
        f.val: f.filed
        for f in facts
        if f.start == "2022-01-01" and f.end == "2022-12-31" and f.form == "10-K"
    }
    # As first reported (filed 2023-02-10), then restated after the GEHC and GEV spin-offs.
    assert fy2022[76555000000] == "2023-02-10"
    assert fy2022[58100000000] == "2024-02-02"
    assert facts.source == "sample" and "FROM tiresias.ge_revenues" in facts.sql
    assert tracer.events[-1]["source"] == "sample" and tracer.events[-1]["rows"] == len(facts)


def test_sample_revenue_alias_resolves_gehc_contract_revenue():
    facts = z.SampleSource().concept_series("gehc", "Revenues")
    assert facts and {f.concept for f in facts} == {
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    }
    assert "tiresias.gehc_revenue_contract" in facts.sql


def test_sample_filings_filter_forms_newest_first():
    filings = z.SampleSource().filings("ge", ["10-K"])
    assert filings and {f.form for f in filings} == {"10-K"}
    dates = [f.filing_date for f in filings]
    assert dates == sorted(dates, reverse=True) and "2024-02-02" in dates
    assert "WHERE form IN ('10-K')" in filings.sql


def test_sample_entities_list_showcase_ciks():
    # The GE family (the entities bootstrap registers live) plus the 3M -> Solventum pair.
    entities = {e.ticker: e.cik for e in z.SampleSource().entities()}
    assert entities == {
        **{e.ticker: e.cik for e in z.SHOWCASE_ENTITIES},
        "mmm": "0000066740",
        "solv": "0001964738",
    }


# --- fallback -----------------------------------------------------------------------------------


def test_live_outage_falls_back_to_sample_and_is_traced_degraded():
    tracer = RecordingTracer()
    client, _ = make_client(lambda request: httpx.Response(503, text="down"), tracer=tracer)
    source = z.make_source(False, tracer, client=client)
    facts = source.concept_series("ge", "Revenues")
    assert facts and facts.source == "sample" and facts.degraded and "HTTP 503" in facts.error
    degraded = [e for e in tracer.events if e["status"] == "degraded"]
    assert len(degraded) == 1 and degraded[0]["source"] == "sample"
    assert degraded[0]["retry_count"] == 2 and "tiresias.ge_revenues" in degraded[0]["sql"]
    assert any(e["status"] == "error" and e["source"] == "zetaris" for e in tracer.events)


def test_live_non_recoverable_error_is_raised_not_served_from_sample():
    tracer = RecordingTracer()
    client, _ = make_client(
        lambda request: httpx.Response(400, json={"msg": "bad sql"}), tracer=tracer
    )
    source = z.make_source(False, tracer, client=client)
    with pytest.raises(z.SourceError) as excinfo:
        source.concept_series("ge", "Revenues")
    assert not excinfo.value.recoverable
    assert not [e for e in tracer.events if e["status"] == "degraded"]


def test_sample_mode_never_touches_the_network():
    assert isinstance(z.make_source(True), z.SampleSource)


# --- templates and bootstrap ------------------------------------------------------------------


def test_every_template_renders_without_unreplaced_placeholders():
    statements = [sql for _, _, sql, _ in z.bootstrap_plan(UA)]
    for e in z.SHOWCASE_ENTITIES:
        statements.append(z.concept_series_sql(e.ticker, list(z.CONCEPT_SLUGS)))
        statements.append(z.filings_sql(e.ticker, z.DEFAULT_FORMS))
    statements += [z.render_file("32_entities.sql"), z.render_file("33_views.sql")]
    names = {p.name for p in z.sql_dir().glob("*.sql")}
    assert len(names) >= 12
    for sql in statements:
        assert "{{" not in sql and "}}" not in sql and "--" not in sql
    with pytest.raises(KeyError):
        z.render_file("20_concept_view.sql", ticker="ge")


def test_bootstrap_is_idempotent_and_never_drops():
    existing, sent = set(), []

    def handler(request):
        sql = json.loads(request.content)["sql"]
        sent.append(sql)
        if sql.startswith(("CACHE", "CREATE OR REPLACE")):
            if "gev_goodwill" in sql:
                msg = "[FIELD_NOT_FOUND] No such struct field GoodwillImpairmentLoss"
                return httpx.Response(400, json={"msg": msg})
            return ok([], [])
        name = re.search(r"(?:DATABASE|CONTAINER|TABLE) (\w+)", sql).group(1)
        if name in existing:
            return httpx.Response(400, json={"msg": f'A table named "{name}" already exists.'})
        existing.add(name)
        return ok([], [])

    client, _ = make_client(handler)
    first = z.bootstrap(client, sec_user_agent=UA)
    second = z.bootstrap(client, sec_user_agent=UA)
    for report in (first, second):
        assert not [e for e in report if e["status"] in {"failed", "skipped"}]
        assert [e["object"] for e in report if e["status"] == "absent"] == [
            "tiresias.gev_goodwill_impairment"
        ]
    assert {e["status"] for e in first if e["step"].endswith("_table")} == {"created"}
    assert {e["status"] for e in second if e["step"] in {"database", "container"}} == {"exists"}
    assert {e["status"] for e in second if e["step"].endswith("_table")} == {"exists"}
    assert not any(re.match(r"\s*(DROP|DELETE|TRUNCATE)", s, re.IGNORECASE) for s in sent)


def _rerun_handler(sent=None):
    """Every CREATE of a database/container/table already exists; gev goodwill is absent."""

    def handler(request):
        sql = json.loads(request.content)["sql"]
        if sent is not None:
            sent.append(sql)
        if "gev_goodwill" in sql:
            msg = "[FIELD_NOT_FOUND] No such struct field GoodwillImpairmentLoss"
            return httpx.Response(400, json={"msg": msg})
        if sql.startswith(("CACHE", "CREATE OR REPLACE")):
            return ok([], [])
        name = re.search(r"(?:DATABASE|CONTAINER|TABLE) (\w+)", sql).group(1)
        return httpx.Response(400, json={"msg": f'A table named "{name}" already exists.'})

    return handler


def test_bootstrap_traces_already_exists_as_success_with_outcome_exists():
    # B-9: an "already exists" reply is a benign outcome, never a red error line.
    tracer = RecordingTracer()
    client, _ = make_client(_rerun_handler(), tracer=tracer)
    report = z.bootstrap(client, sec_user_agent=UA, entities=z.SHOWCASE_ENTITIES[:1])
    assert {e["status"] for e in report if e["step"] in {"database", "container"}} == {"exists"}
    assert {e["status"] for e in report if e["step"].endswith("_table")} == {"exists"}
    assert not [e for e in tracer.events if e["status"] == "error"]
    for step in ("database", "container", "facts_table", "submissions_table"):
        (line,) = [e for e in tracer.events if e["action"] == f"bootstrap:{step}"]
        assert line["status"] == "success" and line["outcome"] == "exists"
        assert line["rows"] == 0 and "already exists" in line["output_summary"]


def test_bootstrap_traces_field_not_found_view_as_success_with_outcome_absent():
    tracer = RecordingTracer()
    client, _ = make_client(_rerun_handler(), tracer=tracer)
    report = z.bootstrap(client, sec_user_agent=UA, entities=z.SHOWCASE_ENTITIES[2:3])
    assert z.SHOWCASE_ENTITIES[2].ticker == "gev"
    assert [e["object"] for e in report if e["status"] == "absent"] == [
        "tiresias.gev_goodwill_impairment"
    ]
    assert not [e for e in tracer.events if e["status"] == "error"]
    (line,) = [
        e
        for e in tracer.events
        if e["action"] == "bootstrap:concept_view" and "gev_goodwill" in e["sql"]
    ]
    assert line["status"] == "success" and line["outcome"] == "absent"
    assert UA not in json.dumps(tracer.events)


def test_field_not_found_outside_a_concept_view_is_still_an_error():
    # FIELD_NOT_FOUND is only benign on a concept view; on a filings view it is a failure.
    def handler(request):
        sql = json.loads(request.content)["sql"]
        if "_filings" in sql:
            return httpx.Response(400, json={"msg": "[FIELD_NOT_FOUND] No such struct field x"})
        return ok([], [])

    tracer = RecordingTracer()
    client, _ = make_client(handler, tracer=tracer)
    report = z.bootstrap(client, sec_user_agent=UA, entities=z.SHOWCASE_ENTITIES[:1])
    (entry,) = [e for e in report if e["step"] == "filings_view"]
    assert entry["status"] == "failed" and entry["recoverable"] is False
    (line,) = [e for e in tracer.events if e["action"] == "bootstrap:filings_view"]
    assert line["status"] == "error" and "outcome" not in line


def test_genuine_sql_error_still_traces_error_and_raises_unchanged():
    def handler(request):
        return httpx.Response(400, json={"msg": "syntax error at SELEC"})

    tracer = RecordingTracer()
    client, sleeps = make_client(handler, tracer=tracer)
    with pytest.raises(z.SourceError) as info:
        client.run_sql("SELEC 1", label="bad", expected=z.already_exists)
    assert info.value.recoverable is False and info.value.status_code == 400 and sleeps == []
    (line,) = tracer.events
    assert line["status"] == "error" and "outcome" not in line
    report = z.bootstrap(client, sec_user_agent=UA, entities=z.SHOWCASE_ENTITIES[:1])
    assert {e["status"] for e in report} <= {"failed", "skipped"}
    assert all(e["recoverable"] is False for e in report if e["status"] == "failed")
    assert {e["status"] for e in tracer.events} == {"error"}


def test_expected_outcome_still_raises_so_callers_decide_as_before():
    def handler(request):
        return httpx.Response(400, json={"msg": "An object named X already exists."})

    tracer = RecordingTracer()
    client, _ = make_client(handler, tracer=tracer)
    with pytest.raises(z.SourceError) as info:
        client.run_sql("CREATE DATABASE X", label="db", expected=z.already_exists)
    assert info.value.recoverable is False and info.value.status_code == 400
    assert [e["status"] for e in tracer.events] == ["success"]
    # Without a classifier the client keeps its old contract: the line is an error.
    with pytest.raises(z.SourceError):
        client.run_sql("CREATE DATABASE X", label="db")
    assert [e["status"] for e in tracer.events] == ["success", "error"]


def test_server_error_with_expected_classifier_still_traces_error_after_retries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="unavailable")

    tracer = RecordingTracer()
    client, sleeps = make_client(handler, tracer=tracer)
    with pytest.raises(z.SourceError) as info:
        client.run_sql("CREATE DATABASE X", label="db", expected=z.already_exists)
    assert info.value.recoverable is True and len(calls) == 3 and len(sleeps) == 2
    assert [e["status"] for e in tracer.events] == ["error"]


def test_server_error_saying_already_exists_is_still_an_error():
    tracer = RecordingTracer()
    client, _ = make_client(
        lambda request: httpx.Response(503, text="Database X already exists"), tracer=tracer
    )
    with pytest.raises(z.SourceError) as info:
        client.run_sql("CREATE DATABASE X", label="db", expected=z.already_exists)
    assert info.value.recoverable is True
    assert [e["status"] for e in tracer.events] == ["error"]
    assert "outcome" not in tracer.events[0]


def test_bootstrap_masks_sec_user_agent_in_traces():
    tracer = RecordingTracer()
    client, _ = make_client(lambda request: ok([], []), tracer=tracer)
    z.bootstrap(client, sec_user_agent=UA, entities=z.SHOWCASE_ENTITIES[:1])
    assert tracer.events and all(UA not in json.dumps(e) for e in tracer.events)
