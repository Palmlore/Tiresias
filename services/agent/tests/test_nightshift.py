"""Nightshift client against a fake Runtime (httpx.MockTransport); no network."""

import json

import httpx
import pytest

from tiresias_agent.adapters.nightshift import (
    ModelProtocolError,
    ModelUnavailable,
    NightshiftClient,
    NightshiftConfig,
    normalize_base_url,
    parse_json_text,
    probe,
)

TOKEN = "rt-test-0123456789abcdef-SECRET"
CFG = NightshiftConfig(base_url="http://vm.test:8088/v1", token=TOKEN, timeout_seconds=5)


class RecordingTracer:
    def __init__(self):
        self.events = []

    def emit(self, *, agent, action, status="success", **fields):
        self.events.append({"agent": agent, "action": action, "status": status, **fields})


def completion(text="hello", meterless=None, **extra):
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "meterless/fast",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        **extra,
    }
    if meterless is not None:
        body["meterless"] = meterless
    return body


RECEIPT = {
    "routed_model": "nvidia/nemotron-super-49b",
    "receipt_id": "rcpt-42",
    "winning_route": {"provider": "nvidia", "model": "nvidia/nemotron-super-49b"},
    "signature": "ed25519:abc",
    "proxy_token": TOKEN,
}


def make_client(handler, tracer=None, config=CFG):
    return NightshiftClient(config, tracer=tracer, transport=httpx.MockTransport(handler))


def assert_no_token(*things):
    for thing in things:
        assert TOKEN not in json.dumps(thing, default=str)


def test_sends_meterless_headers_and_lane_model_without_leaking_token():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion(meterless=RECEIPT))

    tracer = RecordingTracer()
    reply = make_client(handler, tracer).chat(
        [{"role": "user", "content": "hi"}], lane="deep", run_id="run-7"
    )
    assert seen["url"] == "http://vm.test:8088/v1/chat/completions"
    assert seen["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert seen["headers"]["meterless-app"] == "tiresias"
    assert seen["headers"]["meterless-scope"] == "user"
    assert seen["headers"]["x-run-id"] == "run-7"
    assert seen["body"]["model"] == "meterless/deep"
    assert reply.requested_model == "meterless/deep"
    [event] = tracer.events
    assert event["agent"] == "model_client" and event["lane"] == "deep"
    assert event["model"] == "meterless/deep" and event["retry_count"] == 0
    assert_no_token(
        tracer.events, reply.receipt, repr(reply), repr(CFG), repr(make_client(handler))
    )


def test_echoed_token_is_masked_in_reply_text_usage_and_repr():
    def handler(request):
        return httpx.Response(
            200, json=completion("echo " + TOKEN, usage={"note": TOKEN, "total_tokens": 4})
        )

    tracer = RecordingTracer()
    reply = make_client(handler, tracer).chat([{"role": "user", "content": "x"}], lane="fast")
    assert reply.text == "echo ***"
    assert_no_token(reply.text, reply.usage, repr(reply), tracer.events)


def test_token_in_message_content_is_masked_in_trace_input_summary():
    def handler(request):
        return httpx.Response(200, json=completion("ok"))

    tracer = RecordingTracer()
    make_client(handler, tracer).chat([{"role": "user", "content": TOKEN}], lane="fast")
    [event] = tracer.events
    assert "***" in event["input_summary"]
    assert_no_token(tracer.events)


def test_routed_model_and_provider_come_from_receipt():
    def handler(request):
        return httpx.Response(200, json=completion("ok", meterless=RECEIPT))

    tracer = RecordingTracer()
    reply = make_client(handler, tracer).chat([{"role": "user", "content": "x"}], lane="fast")
    assert reply.text == "ok"
    assert reply.routed_model == "nvidia/nemotron-super-49b"
    assert reply.provider == "nvidia"
    assert reply.usage["total_tokens"] == 4
    assert reply.receipt["meterless"]["receipt_id"] == "rcpt-42"
    assert "proxy_token" not in reply.receipt["meterless"]
    assert tracer.events[0]["routed_model"] == "nvidia/nemotron-super-49b"
    assert tracer.events[0]["provider"] == "nvidia"


def test_missing_receipt_gives_none_not_the_requested_model():
    def handler(request):
        return httpx.Response(200, json=completion("ok"))

    reply = make_client(handler).chat([{"role": "user", "content": "x"}], lane="fast")
    assert reply.routed_model is None
    assert reply.provider is None
    assert reply.receipt == {}


def test_retries_once_on_503_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "warming up"}})
        return httpx.Response(200, json=completion("ok", meterless=RECEIPT))

    tracer = RecordingTracer()
    reply = make_client(handler, tracer).chat([{"role": "user", "content": "x"}], lane="fast")
    assert reply.text == "ok"
    assert len(calls) == 2
    assert tracer.events[0]["retry_count"] == 1


def test_persistent_503_raises_recoverable_unavailable_after_two_attempts():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"error": {"message": "runtime offline"}})

    with pytest.raises(ModelUnavailable) as info:
        make_client(handler).chat([{"role": "user", "content": "x"}], lane="fast")
    assert info.value.recoverable is True
    assert len(calls) == 2


def test_400_is_not_retried_and_is_a_protocol_error():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": f"bad request {TOKEN}"}})

    tracer = RecordingTracer()
    with pytest.raises(ModelProtocolError) as info:
        make_client(handler, tracer).chat([{"role": "user", "content": "x"}], lane="fast")
    assert len(calls) == 1
    assert TOKEN not in str(info.value)
    assert tracer.events[0]["status"] == "error"
    assert_no_token(tracer.events)


def test_401_is_unauthorized_not_recoverable_and_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "invalid token"}})

    with pytest.raises(ModelUnavailable) as info:
        make_client(handler).chat([{"role": "user", "content": "x"}], lane="fast")
    assert info.value.recoverable is False
    assert "unauthorized" in info.value.reason
    assert len(calls) == 1


def test_offline_style_4xx_is_unavailable():
    def handler(request):
        return httpx.Response(409, json={"error": {"message": "Nightshift: not signed in"}})

    with pytest.raises(ModelUnavailable):
        make_client(handler).chat([{"role": "user", "content": "x"}], lane="fast")


def test_connection_error_retries_once_then_unavailable_without_token():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("connection refused", request=request)

    tracer = RecordingTracer()
    with pytest.raises(ModelUnavailable) as info:
        make_client(handler, tracer).chat([{"role": "user", "content": "x"}], lane="fast")
    assert info.value.recoverable is True
    assert len(calls) == 2
    assert TOKEN not in str(info.value) and TOKEN not in repr(info.value)
    assert tracer.events[0]["status"] == "error"
    assert_no_token(tracer.events)


def test_timeout_is_unavailable_and_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ModelUnavailable) as info:
        make_client(handler).chat([{"role": "user", "content": "x"}], lane="fast")
    assert info.value.recoverable is True
    assert len(calls) == 1


def test_malformed_body_is_protocol_error():
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(ModelProtocolError):
        make_client(handler).chat([{"role": "user", "content": "x"}], lane="fast")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"METERLESS_BASE_URL": "http://vm:8088/v1"},
        {
            "METERLESS_BASE_URL": "http://host.docker.internal:8088/v1",
            "METERLESS_RUNTIME_TOKEN": "your-nightshift-runtime-token",
        },
        {"METERLESS_BASE_URL": "http://vm:8088", "METERLESS_RUNTIME_TOKEN": "rt-local"},
        {"METERLESS_RUNTIME_TOKEN": TOKEN},
    ],
)
def test_placeholder_or_missing_config_is_not_configured(env):
    assert NightshiftConfig.from_env(env).configured is False


def test_real_config_from_env():
    cfg = NightshiftConfig.from_env(
        {"METERLESS_BASE_URL": "192.168.64.4:8088", "METERLESS_RUNTIME_TOKEN": TOKEN}
    )
    assert cfg.configured is True
    assert cfg.base_url == "http://192.168.64.4:8088/v1"
    assert cfg.timeout_seconds == 180.0
    assert TOKEN not in repr(cfg)


def test_unconfigured_chat_raises_without_calling_network():
    def handler(request):
        raise AssertionError("must not call the network")

    with pytest.raises(ModelUnavailable) as info:
        make_client(handler, config=NightshiftConfig()).chat(
            [{"role": "user", "content": "x"}], lane="fast"
        )
    assert info.value.recoverable is False


@pytest.mark.parametrize(
    "raw",
    [
        "http://h:8088",
        "http://h:8088/",
        "http://h:8088/v1",
        "http://h:8088/v1/chat/completions",
        "http://h:8088/v1/models",
        "h:8088/v1",
    ],
)
def test_normalize_base_url_never_doubles_v1(raw):
    assert normalize_base_url(raw) == "http://h:8088/v1"


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Here you go:\n```\n{"a": 1}\n```\nDone.',
    ],
)
def test_chat_json_accepts_fenced_and_unfenced(text):
    def handler(request):
        return httpx.Response(200, json=completion(text))

    parsed, reply = make_client(handler).chat_json([{"role": "user", "content": "x"}], lane="deep")
    assert parsed == {"a": 1}
    assert reply.lane == "deep"


def test_chat_json_garbage_raises_protocol_error():
    def handler(request):
        return httpx.Response(200, json=completion("I think the answer is maybe 1"))

    with pytest.raises(ModelProtocolError):
        make_client(handler).chat_json([{"role": "user", "content": "x"}], lane="fast")


def test_parse_json_text_rejects_empty():
    with pytest.raises(ModelProtocolError):
        parse_json_text("")


def test_probe_not_configured_exits_non_zero(capsys):
    assert probe(NightshiftConfig()) == 2
    assert "✖" in capsys.readouterr().err


def test_probe_prints_lanes_and_masks_token(capsys):
    def handler(request):
        return httpx.Response(200, json=completion("ok", meterless=RECEIPT))

    assert probe(CFG, transport=httpx.MockTransport(handler)) == 0
    out = capsys.readouterr()
    assert "routed_model=nvidia/nemotron-super-49b" in out.out
    assert "fast" in out.out and "deep" in out.out
    assert TOKEN not in out.out + out.err


def test_probe_unreachable_exits_non_zero(capsys):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    assert probe(CFG, transport=httpx.MockTransport(handler)) == 1
    out = capsys.readouterr()
    assert "✖" in out.err and TOKEN not in out.out + out.err
