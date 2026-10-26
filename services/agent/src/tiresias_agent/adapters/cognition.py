"""Client for the cognition service (Meterless H-MEM memory, contracts/openapi/cognition.yaml).

Memory is optional for a run (AGENTS.md rule 8): when cognition is down, recall returns no
lessons with ``available=False`` and remember/feedback report nothing stored. Purge is the
exception: a purge that did not happen raises, so it can never be reported as done.
Each endpoint's one trace line also records what went over the wire on its last attempt
(``exchange_fields``: endpoint, method, service_host, http_status, request_bytes, response_bytes).
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, Self

import httpx

AGENT = "memory_client"
SUMMARY_MAX = 160
FEEDBACK_KINDS = frozenset({"helpful", "not_helpful", "wrong"})


class Tracer(Protocol):
    def emit(self, *, agent: str, action: str, status: str = "success", **fields: Any) -> None: ...


class _NoopTracer:
    def emit(self, *, agent: str, action: str, status: str = "success", **fields: Any) -> None:
        return None


class CognitionError(Exception):
    """A cognition call failed. Raised only where silence would be wrong (purge, list, ledger)."""


class CognitionUnavailable(CognitionError):
    """Cognition was unreachable, timed out or answered 5xx."""


class CognitionRequestError(CognitionError):
    """Cognition rejected the request (4xx): a caller bug or an unknown memory id."""

    def __init__(self, status_code: int, error_type: str, message: str) -> None:
        super().__init__(f"{status_code} {error_type}: {message}")
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class CognitionConfig:
    url: str = "http://localhost:8100"
    timeout_s: float = 10.0
    retries: int = 1  # extra attempts on connect errors, idempotent calls only

    @classmethod
    def from_env(cls) -> CognitionConfig:
        return cls(
            url=os.getenv("COGNITION_URL", cls.url).rstrip("/"),
            timeout_s=float(os.getenv("COGNITION_TIMEOUT_S", str(cls.timeout_s))),
            retries=max(0, int(os.getenv("COGNITION_RETRIES", str(cls.retries)))),
        )


@dataclass(frozen=True)
class Gate:
    """Memory-gate decision; serialised with the server's camelCase ``decidedBy``."""

    mode: str
    decision: str
    decided_by: str
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mode": self.mode,
            "decision": self.decision,
            "decidedBy": self.decided_by,
        }
        if self.reason is not None:
            out["reason"] = self.reason
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> Gate | None:
        if not data:
            return None
        return cls(
            mode=str(data.get("mode", "")),
            decision=str(data.get("decision", "")),
            decided_by=str(data.get("decidedBy", "")),
            reason=data.get("reason"),
        )


@dataclass(frozen=True)
class Lesson:
    id: str
    content: str
    namespace: str
    confidence: float
    tags: list[str] = field(default_factory=list)
    run_id: str | None = None
    relevance: float | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Lesson:
        return cls(
            id=str(data["id"]),
            content=str(data.get("content", "")),
            namespace=str(data.get("namespace", "")),
            confidence=float(data.get("confidence", 0.0)),
            tags=[str(t) for t in data.get("tags") or []],
            run_id=data.get("runId"),
            relevance=data.get("relevance"),
        )


@dataclass(frozen=True)
class Health:
    available: bool
    version: str | None = None
    engine: dict[str, Any] = field(default_factory=dict)
    lessons: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class RecallResult:
    lessons: list[Lesson]
    context: str = ""
    trace: dict[str, Any] = field(default_factory=dict)  # server's reinjection trace, verbatim
    available: bool = True
    error: str | None = None

    @property
    def memory_ids(self) -> list[str]:
        return [lesson.id for lesson in self.lessons]


@dataclass(frozen=True)
class RememberResult:
    stored: bool
    lesson: Lesson | None = None
    gate: Gate | None = None
    available: bool = True
    error: str | None = None


@dataclass(frozen=True)
class FeedbackResult:
    applied: bool
    lesson: Lesson | None = None
    available: bool = True
    error: str | None = None


@dataclass(frozen=True)
class PurgeResult:
    purged: int
    namespace: str | None = None


@dataclass(frozen=True)
class LedgerEntry:
    memory_id: str
    action: str
    actor: str
    timestamp: int
    details: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LedgerEntry:
        return cls(
            memory_id=str(data.get("memoryId", "")),
            action=str(data.get("action", "")),
            actor=str(data.get("actor", "")),
            timestamp=int(data.get("timestamp", 0)),
            details=dict(data.get("details") or {}),
            raw=data,
        )


def exchange_fields(request: httpx.Request, response: httpx.Response | None) -> dict[str, Any]:
    """What went over the wire on one attempt, for its trace line: the path only (no query
    string), the method, host:port (never userinfo), the HTTP status (None when no reply came
    back) and the bytes sent and received."""
    url = request.url
    return {
        "endpoint": url.path,
        "method": request.method,
        "service_host": f"{url.host}:{url.port}" if url.port else url.host,
        "http_status": response.status_code if response is not None else None,
        "request_bytes": len(request.content),
        "response_bytes": len(response.content) if response is not None else None,
    }


def _short(text: str) -> str:
    return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 1] + "…"


class CognitionClient:
    """One method per endpoint. Every request carries ``X-Run-Id`` (minted if none is given)."""

    def __init__(
        self,
        config: CognitionConfig | None = None,
        *,
        run_id: str | None = None,
        tracer: Tracer | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or CognitionConfig.from_env()
        self.tracer: Tracer = tracer if tracer is not None else _NoopTracer()
        # Every request carries X-Run-Id: mint one for the client's lifetime if none is known.
        self.run_id: str = (
            run_id or getattr(tracer, "run_id", None) or f"run-{uuid.uuid4().hex[:12]}"
        )
        self._http = httpx.Client(
            base_url=self.config.url,
            timeout=self.config.timeout_s,
            transport=transport,
        )
        self._exchange: dict[str, Any] = {}  # the last attempt of the current call

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        headers = {"X-Run-Id": self.run_id}
        attempts = 1 + (self.config.retries if idempotent else 0)
        for attempt in range(attempts):
            request = self._http.build_request(
                method, path, json=json, params=params, headers=headers
            )
            self._exchange = exchange_fields(request, None)  # replaced when a reply arrives
            try:
                response = self._http.send(request)
                self._exchange = exchange_fields(request, response)
                break
            except httpx.ConnectError as exc:
                if attempt + 1 < attempts:
                    continue
                raise CognitionUnavailable(f"{type(exc).__name__}: {exc}") from exc
            except httpx.TransportError as exc:  # timeouts, protocol errors
                raise CognitionUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise CognitionUnavailable(f"HTTP {response.status_code}: {_error_message(response)}")
        if response.status_code >= 400:
            err = _error_body(response)
            raise CognitionRequestError(
                response.status_code, err.get("type", "HTTPError"), err.get("message", "")
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise CognitionUnavailable(f"non-JSON response from {path}") from exc
        if not isinstance(body, dict):
            raise CognitionUnavailable(f"unexpected response shape from {path}")
        return body

    def _emit(self, action: str, started: float, status: str = "success", **fields: Any) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000)
        exchange, self._exchange = self._exchange, {}
        self.tracer.emit(
            agent=AGENT,
            action=action,
            status=status,
            duration_ms=duration_ms,
            **{**exchange, **fields},
        )

    # -- endpoints ---------------------------------------------------------------------------

    def health(self) -> Health:
        """GET /health. Never raises: an unreachable service is ``available=False``."""
        started = time.perf_counter()
        try:
            body = self._request("GET", "/health", idempotent=True)
        except CognitionError as exc:
            self._emit("health", started, "degraded", output_summary=_short(str(exc)))
            return Health(available=False, error=str(exc))
        self._emit("health", started, output_summary=f"{body.get('lessons')} lesson(s)")
        return Health(
            available=True,
            version=body.get("version"),
            engine=dict(body.get("engine") or {}),
            lessons=body.get("lessons"),
        )

    def recall(
        self,
        query: str,
        *,
        namespace: str | None = None,
        top_n: int | None = None,
        threshold: float | None = None,
    ) -> RecallResult:
        """POST /memory/recall. Relevance filtering is the server's (RECALL_THRESHOLD)."""
        started = time.perf_counter()
        payload: dict[str, Any] = {"query": query}
        if namespace is not None:
            payload["namespace"] = namespace
        if top_n is not None:
            payload["top_n"] = top_n
        if threshold is not None:
            payload["threshold"] = threshold
        try:
            body = self._request("POST", "/memory/recall", json=payload, idempotent=True)
        except CognitionError as exc:
            status = "error" if isinstance(exc, CognitionRequestError) else "degraded"
            self._emit(
                "recall",
                started,
                status,
                memory_ids=[],
                input_summary=_short(query),
                output_summary=_short(f"no memory: {exc}"),
            )
            return RecallResult(lessons=[], available=False, error=str(exc))
        result = RecallResult(
            lessons=[Lesson.from_json(item) for item in body.get("lessons") or []],
            context=str(body.get("context") or ""),
            trace=dict(body.get("trace") or {}),
        )
        self._emit(
            "recall",
            started,
            memory_ids=result.memory_ids,
            input_summary=_short(query),
            output_summary=f"{len(result.lessons)} lesson(s)",
        )
        return result

    def remember(
        self,
        content: str,
        *,
        namespace: str | None = None,
        agent_id: str | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
        gate: Gate | None = None,
    ) -> RememberResult:
        """POST /memory/remember. The run id travels in X-Run-Id (the server prefers it)."""
        started = time.perf_counter()
        payload: dict[str, Any] = {"content": content, "run_id": self.run_id}
        if namespace is not None:
            payload["namespace"] = namespace
        if agent_id is not None:
            payload["agent_id"] = agent_id
        if tags is not None:
            payload["tags"] = list(tags)
        if confidence is not None:
            payload["confidence"] = confidence
        if gate is not None:
            payload["gate"] = gate.to_json()
        try:
            body = self._request("POST", "/memory/remember", json=payload)
        except CognitionError as exc:
            status = "error" if isinstance(exc, CognitionRequestError) else "degraded"
            self._emit(
                "remember",
                started,
                status,
                memory_ids=[],
                input_summary=_short(content),
                output_summary=_short(f"not stored: {exc}"),
            )
            return RememberResult(stored=False, gate=gate, available=False, error=str(exc))
        lesson = Lesson.from_json(body["lesson"]) if body.get("lesson") else None
        result = RememberResult(
            stored=bool(body.get("stored")), lesson=lesson, gate=Gate.from_json(body.get("gate"))
        )
        self._emit(
            "remember",
            started,
            "success" if result.stored else "rejected",
            memory_ids=[lesson.id] if lesson else [],
            input_summary=_short(content),
            output_summary=f"stored {lesson.id}" if lesson else "rejected at memory gate",
        )
        return result

    def feedback(self, memory_id: str, kind: str) -> FeedbackResult:
        """POST /memory/feedback with kind helpful, not_helpful or wrong."""
        if kind not in FEEDBACK_KINDS:
            raise ValueError(f"kind must be one of {sorted(FEEDBACK_KINDS)}, got {kind!r}")
        started = time.perf_counter()
        try:
            body = self._request(
                "POST", "/memory/feedback", json={"memory_id": memory_id, "kind": kind}
            )
        except CognitionError as exc:
            status = "error" if isinstance(exc, CognitionRequestError) else "degraded"
            self._emit(
                "feedback",
                started,
                status,
                memory_ids=[memory_id],
                input_summary=f"{memory_id}: {kind}",
                output_summary=_short(f"not applied: {exc}"),
            )
            return FeedbackResult(applied=False, available=False, error=str(exc))
        lesson = Lesson.from_json(body["lesson"])
        self._emit(
            "feedback",
            started,
            memory_ids=[memory_id],
            input_summary=f"{memory_id}: {kind}",
            output_summary=f"confidence {lesson.confidence}",
        )
        return FeedbackResult(applied=True, lesson=lesson)

    def purge(
        self, *, namespace: str | None = None, requested_by: str | None = None
    ) -> PurgeResult:
        """POST /memory/purge (all lessons, or one namespace).

        Raises CognitionError when the purge was not confirmed by the server: a cold run must
        never start on the belief that memory was cleared when it was not.
        """
        started = time.perf_counter()
        payload: dict[str, Any] = {}
        if namespace is not None:
            payload["namespace"] = namespace
        if requested_by is not None:
            payload["requested_by"] = requested_by
        target = f"namespace {namespace or '*'}"
        try:
            body = self._request("POST", "/memory/purge", json=payload)
            purged = body.get("purged")
            if not isinstance(purged, int) or isinstance(purged, bool):
                raise CognitionUnavailable("purge response has no integer 'purged' count")
        except CognitionError as exc:
            self._emit(
                "purge",
                started,
                "error",
                input_summary=target,
                output_summary=_short(f"purge NOT done: {exc}"),
            )
            raise
        self._emit("purge", started, input_summary=target, output_summary=f"purged {purged}")
        return PurgeResult(purged=purged, namespace=namespace)

    def list_memory(self, namespace: str | None = None) -> list[Lesson]:
        """GET /memory. Raises CognitionError on failure (an empty list would look like a purge)."""
        started = time.perf_counter()
        params = {"namespace": namespace} if namespace is not None else None
        try:
            body = self._request("GET", "/memory", params=params, idempotent=True)
        except CognitionError as exc:
            self._emit("list", started, "error", output_summary=_short(str(exc)))
            raise
        lessons = [Lesson.from_json(item) for item in body.get("lessons") or []]
        self._emit(
            "list",
            started,
            memory_ids=[lesson.id for lesson in lessons],
            input_summary=f"namespace {namespace or '*'}",
            output_summary=f"{len(lessons)} lesson(s)",
        )
        return lessons

    def ledger(self, memory_id: str | None = None) -> list[LedgerEntry]:
        """GET /ledger (optionally one memory's history). Raises CognitionError on failure."""
        started = time.perf_counter()
        params = {"memory_id": memory_id} if memory_id is not None else None
        try:
            body = self._request("GET", "/ledger", params=params, idempotent=True)
        except CognitionError as exc:
            self._emit("ledger", started, "error", output_summary=_short(str(exc)))
            raise
        entries = [LedgerEntry.from_json(item) for item in body.get("entries") or []]
        self._emit(
            "ledger",
            started,
            memory_ids=[memory_id] if memory_id else [],
            output_summary=f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}",
        )
        return entries


def _error_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    err = body.get("error") if isinstance(body, dict) else None
    return err if isinstance(err, dict) else {}


def _error_message(response: httpx.Response) -> str:
    return str(_error_body(response).get("message") or response.reason_phrase)
