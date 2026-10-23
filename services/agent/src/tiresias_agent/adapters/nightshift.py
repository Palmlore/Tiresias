"""Meterless Nightshift Runtime client (OpenAI-compatible chat completions).

Nightshift routes by the app's mode, so the model named in a request is only a lane
(``meterless/fast`` or ``meterless/deep``). The model that actually answered is read from the
response's receipt (``meterless.routed_model``, ``winning_route.provider``) and is ``None`` when
the receipt does not say. When Nightshift is offline the client raises ``ModelUnavailable`` so the
orchestrator can fall back to the rule-based path (ADR-0007).

CLI: ``uv run python -m tiresias_agent.adapters.nightshift probe [--env-file PATH]``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

Lane = Literal["fast", "deep"]

TOKEN_PLACEHOLDERS = frozenset({"", "your-nightshift-runtime-token", "rt-local"})
OFFLINE_MARKERS = (
    "offline",
    "not signed in",
    "sign in",
    "signed out",
    "not logged in",
    "no provider",
    "unauthorized",
    "unavailable",
)
_SECRET_KEY_RE = re.compile(
    r"token|secret|api[_-]?key|authorization|password|bearer", re.IGNORECASE
)
_SUMMARY_CHARS = 160
MAX_ATTEMPTS = 2  # one bounded retry on connection error or 5xx


class Tracer(Protocol):
    def emit(self, *, agent: str, action: str, status: str = "success", **fields: Any) -> Any: ...


class _NoopTracer:
    def emit(self, *, agent: str, action: str, status: str = "success", **fields: Any) -> None:
        return None


class ModelError(Exception):
    """Base class for model client errors. Messages never contain the runtime token."""


class ModelUnavailable(ModelError):
    """Nightshift cannot answer now (offline, unauthorized, timeout, 5xx)."""

    def __init__(self, reason: str, recoverable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.recoverable = recoverable


class ModelProtocolError(ModelError):
    """Nightshift answered, but not in a shape we can use."""


def normalize_base_url(url: str) -> str:
    """Normalise a pasted URL to ``<host>/v1`` (never ``/v1/v1``)."""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    for suffix in ("/chat/completions", "/models", "/healthz"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


@dataclass(frozen=True)
class NightshiftConfig:
    base_url: str = ""
    token: str = field(default="", repr=False)
    app: str = "tiresias"
    model_fast: str = "meterless/fast"
    model_deep: str = "meterless/deep"
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> NightshiftConfig:
        e = os.environ if env is None else env
        timeout_raw = (e.get("MODEL_TIMEOUT_SECONDS") or "").strip()
        try:
            timeout = float(timeout_raw) if timeout_raw else 180.0
        except ValueError:
            timeout = 180.0
        return cls(
            base_url=normalize_base_url(e.get("METERLESS_BASE_URL", "")),
            token=e.get("METERLESS_RUNTIME_TOKEN", "").strip(),
            app=e.get("METERLESS_APP", "").strip() or "tiresias",
            model_fast=e.get("METERLESS_MODEL_FAST", "").strip() or "meterless/fast",
            model_deep=e.get("METERLESS_MODEL_DEEP", "").strip() or "meterless/deep",
            timeout_seconds=timeout if timeout > 0 else 180.0,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url) and self.token not in TOKEN_PLACEHOLDERS

    def model_for(self, lane: Lane) -> str:
        if lane == "fast":
            return self.model_fast
        if lane == "deep":
            return self.model_deep
        raise ValueError(f"unknown lane {lane!r}; expected 'fast' or 'deep'")


@dataclass(frozen=True)
class ModelReply:
    text: str
    lane: Lane
    requested_model: str
    routed_model: str | None
    provider: str | None
    receipt: dict[str, Any]
    usage: dict[str, Any]
    duration_ms: int


def _redact(value: Any, token: str) -> Any:
    """Drop secret-looking keys and mask the token anywhere in a JSON-like value."""
    if isinstance(value, dict):
        return {
            k: _redact(v, token)
            for k, v in value.items()
            if not (isinstance(k, str) and _SECRET_KEY_RE.search(k))
        }
    if isinstance(value, list):
        return [_redact(v, token) for v in value]
    if isinstance(value, str) and token and token in value:
        return value.replace(token, "***")
    return value


def _dig(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _first_str(*candidates: Any) -> str | None:
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def extract_receipt(body: dict[str, Any], headers: httpx.Headers | None = None) -> dict[str, Any]:
    """Collect receipt material from the body (``meterless``, ``receipt``, ``winning_route``) and
    any ``meterless-*`` response headers. The shape is parsed defensively (see PR notes)."""
    receipt: dict[str, Any] = {}
    for key in ("meterless", "receipt", "winning_route"):
        if key in body:
            receipt[key] = body[key]
    if headers is not None:
        meta = {
            k.lower(): v
            for k, v in headers.items()
            if k.lower().startswith(("meterless-", "x-meterless-"))
        }
        if meta:
            receipt["headers"] = meta
    return receipt


def routed_from_receipt(receipt: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (routed_model, provider) from a receipt, or None for each one it does not state."""
    m = receipt.get("meterless")
    hdr = receipt.get("headers") or {}
    routed = _first_str(
        _dig(m, "routed_model"),
        _dig(m, "receipt", "routed_model"),
        _dig(receipt, "receipt", "routed_model"),
        _dig(receipt, "receipt", "meterless", "routed_model"),
        hdr.get("meterless-routed-model"),
        hdr.get("x-meterless-routed-model"),
    )
    provider = _first_str(
        _dig(m, "winning_route", "provider"),
        _dig(m, "receipt", "winning_route", "provider"),
        _dig(receipt, "receipt", "winning_route", "provider"),
        _dig(receipt, "winning_route", "provider"),
        hdr.get("meterless-provider"),
        hdr.get("x-meterless-provider"),
    )
    return routed, provider


def _summary(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _SUMMARY_CHARS else text[: _SUMMARY_CHARS - 1] + "…"


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or err)[:300]
    if isinstance(err, str):
        return err[:300]
    return str(body)[:300]


def _looks_offline(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in OFFLINE_MARKERS)


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_text(text: str) -> Any:
    """Parse model output as JSON, tolerating one fenced ```json block. No silent defaults."""
    candidate = text.strip()
    match = _FENCE_RE.search(candidate)
    if match:
        candidate = match.group(1).strip()
    try:
        return json.loads(candidate)
    except ValueError as exc:
        raise ModelProtocolError(f"model output is not JSON: {_summary(text)!r}") from exc


class NightshiftClient:
    """Synchronous client for the Meterless Runtime. Pass ``transport`` to test it offline."""

    def __init__(
        self,
        config: NightshiftConfig | None = None,
        *,
        tracer: Tracer | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or NightshiftConfig.from_env()
        self.tracer: Tracer = tracer or _NoopTracer()
        self._transport = transport

    def __repr__(self) -> str:
        return f"NightshiftClient(base_url={self.config.base_url!r}, app={self.config.app!r})"

    def _headers(self, run_id: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "meterless-app": self.config.app,
            "meterless-scope": "user",
            "X-Run-Id": run_id or str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _safe(self, text: str) -> str:
        token = self.config.token
        return text.replace(token, "***") if token else text

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        lane: Lane,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        run_id: str | None = None,
    ) -> ModelReply:
        requested = self.config.model_for(lane)
        prompt_text = " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
        trace = {
            "model": requested,
            "lane": lane,
            "input_summary": _summary(self._safe(prompt_text)),
        }
        start = time.monotonic()
        retries = 0
        try:
            if not self.config.configured:
                raise ModelUnavailable("nightshift not configured", recoverable=False)
            payload: dict[str, Any] = {"model": requested, "messages": messages, "stream": False}
            if response_format is not None:
                payload["response_format"] = response_format
            if temperature is not None:
                payload["temperature"] = temperature
            response, retries = self._post(payload, run_id)
            reply = self._parse(response, lane, requested, start)
        except ModelError as exc:
            self.tracer.emit(
                agent="model_client",
                action="chat",
                status="error",
                routed_model=None,
                provider=None,
                retry_count=retries,
                duration_ms=int((time.monotonic() - start) * 1000),
                output_summary=_summary(self._safe(f"{type(exc).__name__}: {exc}")),
                **trace,
            )
            raise
        self.tracer.emit(
            agent="model_client",
            action="chat",
            status="success",
            routed_model=reply.routed_model,
            provider=reply.provider,
            retry_count=retries,
            duration_ms=reply.duration_ms,
            output_summary=_summary(self._safe(reply.text)),
            **trace,
        )
        return reply

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        lane: Lane,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        run_id: str | None = None,
    ) -> tuple[Any, ModelReply]:
        """Ask for a single JSON value and parse it. Returns (parsed, reply)."""
        instruction = {
            "role": "system",
            "content": "Reply with one JSON value only. No prose before or after it.",
        }
        reply = self.chat(
            [instruction, *messages],
            lane=lane,
            response_format=response_format,
            temperature=temperature,
            run_id=run_id,
        )
        return parse_json_text(reply.text), reply

    def _post(self, payload: dict[str, Any], run_id: str | None) -> tuple[httpx.Response, int]:
        url = f"{self.config.base_url}/chat/completions"
        headers = self._headers(run_id)
        last: ModelUnavailable | None = None
        with httpx.Client(timeout=self.config.timeout_seconds, transport=self._transport) as http:
            for attempt in range(MAX_ATTEMPTS):
                try:
                    response = http.post(url, json=payload, headers=headers)
                except httpx.TimeoutException:
                    # No retry: a timeout already spent the full model budget (180 s by default).
                    raise ModelUnavailable(
                        f"nightshift timed out after {self.config.timeout_seconds:g}s",
                        recoverable=True,
                    ) from None
                except httpx.TransportError as exc:
                    last = ModelUnavailable(
                        f"nightshift unreachable ({type(exc).__name__})", recoverable=True
                    )
                    continue
                if response.status_code >= 500:
                    msg = self._safe(_error_message(response))
                    last = ModelUnavailable(
                        f"nightshift HTTP {response.status_code}: {msg}", recoverable=True
                    )
                    continue
                if response.status_code in (401, 403):
                    raise ModelUnavailable(
                        f"nightshift unauthorized (HTTP {response.status_code}); check the token",
                        recoverable=False,
                    )
                if response.status_code >= 400:
                    msg = self._safe(_error_message(response))
                    if _looks_offline(msg):
                        raise ModelUnavailable(
                            f"nightshift HTTP {response.status_code}: {msg}", recoverable=True
                        )
                    raise ModelProtocolError(
                        f"nightshift rejected the request (HTTP {response.status_code}): {msg}"
                    )
                return response, attempt
        assert last is not None
        raise last

    def _parse(
        self, response: httpx.Response, lane: Lane, requested: str, start: float
    ) -> ModelReply:
        try:
            body = response.json()
        except ValueError:
            raise ModelProtocolError("nightshift returned a non-JSON body") from None
        if not isinstance(body, dict):
            raise ModelProtocolError("nightshift returned a JSON body that is not an object")
        if "error" in body and not body.get("choices"):
            msg = self._safe(_error_message(response))
            if _looks_offline(msg):
                raise ModelUnavailable(f"nightshift: {msg}", recoverable=True)
            raise ModelProtocolError(f"nightshift error: {msg}")
        text = _dig((body.get("choices") or [None])[0], "message", "content")
        if not isinstance(text, str):
            raise ModelProtocolError("nightshift response has no choices[0].message.content string")
        receipt = _redact(extract_receipt(body, response.headers), self.config.token)
        routed, provider = routed_from_receipt(receipt)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        # Mask values only: the secret-key filter would drop `*_tokens` counts.
        usage = {k: self._safe(v) if isinstance(v, str) else v for k, v in usage.items()}
        return ModelReply(
            text=self._safe(text),
            lane=lane,
            requested_model=requested,
            routed_model=routed,
            provider=provider,
            receipt=receipt,
            usage=usage,
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def mask(token: str) -> str:
    return f"set ({len(token)} chars, masked)" if token else "missing"


def _read_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0].strip().strip("'\"")
            env[key.strip()] = value
    return env


def probe(config: NightshiftConfig, *, transport: httpx.BaseTransport | None = None) -> int:
    """Send a tiny prompt to each lane and print what answered. Returns a process exit code."""
    print(f"base_url  {config.base_url or '(missing)'}")
    print(f"app       {config.app}")
    print(f"token     {mask(config.token)}")
    if not config.configured:
        print(
            "✖ Nightshift is not configured: set METERLESS_BASE_URL and a real "
            "METERLESS_RUNTIME_TOKEN (not the .env.example placeholder).",
            file=sys.stderr,
        )
        return 2
    client = NightshiftClient(config, transport=transport)
    failed = False
    for lane in ("fast", "deep"):
        try:
            reply = client.chat(
                [{"role": "user", "content": "Reply with the single word: ok"}], lane=lane
            )
        except ModelError as exc:
            failed = True
            print(f"✖ {lane:<4}  {client._safe(str(exc))}", file=sys.stderr)
            continue
        print(
            f"✔ {lane:<4}  requested={reply.requested_model}  "
            f"routed_model={reply.routed_model or 'unknown (no receipt)'}  "
            f"provider={reply.provider or 'unknown'}  latency={reply.duration_ms} ms"
        )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "probe":
        print("usage: python -m tiresias_agent.adapters.nightshift probe [--env-file PATH]")
        return 2
    env = dict(os.environ)
    if "--env-file" in args:
        idx = args.index("--env-file")
        if idx + 1 >= len(args):
            print("✖ --env-file needs a path", file=sys.stderr)
            return 2
        env = {**_read_env_file(args[idx + 1]), **os.environ}
    return probe(NightshiftConfig.from_env(env))


if __name__ == "__main__":
    raise SystemExit(main())
