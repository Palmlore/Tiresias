"""HTTP entry point (contracts/openapi/agent.yaml).

POST /run runs one investigation synchronously (the judge path). POST /runs starts one in the
background; GET /runs/{run_id} polls it and GET /runs/{run_id}/events streams its trace lines
as Server-Sent Events. Errors always use the contract's Error shape, never a stack trace.
"""

import json
import os

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .config import settings
from .models import ErrorDetail, ErrorResponse, RunRequest
from .runs import RegistryBusy, RunConflict, RunRecord, RunRegistry, valid_run_id
from .runs import wait_budget_seconds as _wait_budget

app = FastAPI(title="Tiresias agent", version=__version__)
registry = RunRegistry()

_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
if _origins:  # off by default; set CORS_ALLOW_ORIGINS for a browser UI on another origin
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Run-Id"],
        expose_headers=["X-Run-Id"],
    )


def error_response(
    status_code: int,
    error_type: str,
    message: str,
    *,
    recoverable: bool = False,
    run_id: str | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        run_id=run_id,
        error=ErrorDetail(type=error_type, message=message[:1000], recoverable=recoverable),
    )
    headers = {"X-Run-Id": run_id} if run_id else None
    return JSONResponse(
        status_code=status_code, content=body.model_dump(exclude_none=True), headers=headers
    )


def _header_run_id(request: Request) -> str | None:
    value = request.headers.get("x-run-id")
    return value if value and valid_run_id(value) else None


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = []
    for err in exc.errors()[:10]:
        where = ".".join(str(p) for p in err.get("loc", ()) if p != "body") or "body"
        problems.append(f"{where}: {err.get('msg', 'invalid')}")
    return error_response(
        422, "ValidationError", "; ".join(problems), run_id=_header_run_id(request)
    )


@app.exception_handler(Exception)
async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    return error_response(
        500, type(exc).__name__, "Unexpected server error.", run_id=_header_run_id(request)
    )


@app.get("/health")
def health() -> dict:
    return {
        "service": "agent",
        "version": __version__,
        "sample_mode": settings.sample_mode,
        "hitl_mode": settings.hitl_mode,
    }


def _start(body: RunRequest, x_run_id: str | None) -> RunRecord | JSONResponse:
    if x_run_id is not None and not valid_run_id(x_run_id):
        return error_response(422, "ValidationError", "X-Run-Id must match [A-Za-z0-9._-]{1,100}.")
    try:
        return registry.start(body, run_id=x_run_id)
    except RunConflict:
        return error_response(
            409, "RunConflict", "A run with this X-Run-Id already exists.", run_id=x_run_id
        )
    except RegistryBusy:
        return error_response(
            429,
            "TooManyRuns",
            f"{registry.max_concurrent} runs are already running; retry shortly.",
            recoverable=True,
            run_id=x_run_id,
        )


@app.post("/run")
def run(body: RunRequest, x_run_id: str | None = Header(default=None)) -> JSONResponse:
    record = _start(body, x_run_id)
    if isinstance(record, JSONResponse):
        return record
    rid = record.run_id
    if not record.wait(_wait_budget()):
        return error_response(
            504,
            "RunTimeout",
            f"The run did not finish within the budget; poll GET /runs/{rid}.",
            recoverable=True,
            run_id=rid,
        )
    if record.error is not None:
        return error_response(
            500,
            record.error.type,
            record.error.message,
            recoverable=record.error.recoverable,
            run_id=rid,
        )
    content = record.result.model_dump(mode="json")
    return JSONResponse(content=content, headers={"X-Run-Id": rid})


@app.post("/runs", status_code=202)
def start_run(body: RunRequest, x_run_id: str | None = Header(default=None)) -> JSONResponse:
    record = _start(body, x_run_id)
    if isinstance(record, JSONResponse):
        return record
    rid = record.run_id
    return JSONResponse(
        status_code=202,
        content={"run_id": rid, "status": record.status, "events": f"/runs/{rid}/events"},
        headers={"X-Run-Id": rid},
    )


def _unknown(run_id: str) -> JSONResponse:
    return error_response(
        404, "RunNotFound", "No such run (unknown, or evicted from the registry)."
    )


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    record = registry.get(run_id)
    if record is None:
        return _unknown(run_id)
    return JSONResponse(content=record.summary(), headers={"X-Run-Id": run_id})


def _sse(record: RunRecord):
    max_seconds = _wait_budget()
    for line in record.follow(max_seconds=max_seconds):
        if line is None:
            yield ": keepalive\n\n"
        else:
            yield f"event: trace\ndata: {line}\n\n"
    end = {"run_id": record.run_id, "status": record.status}
    if record.result is not None:
        end.update(run_status=record.result.status, end_state=record.result.end_state)
    if record.error is not None:
        end["error"] = record.error.model_dump()
    yield f"event: end\ndata: {json.dumps(end)}\n\n"


@app.get("/runs/{run_id}/events")
def run_events(run_id: str):
    record = registry.get(run_id)
    if record is None:
        return _unknown(run_id)
    return StreamingResponse(
        _sse(record),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": run_id},
    )


def _not_implemented(what: str) -> JSONResponse:
    return error_response(
        501, "NotImplemented", f"{what} is planned; see docs/STATUS.md.", recoverable=False
    )


@app.post("/runs/{run_id}/branch")
def branch(run_id: str) -> JSONResponse:
    return _not_implemented("Branching")


@app.post("/memory/purge")
def purge_memory() -> JSONResponse:
    return _not_implemented("Memory purge")


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("AGENT_PORT", "8000")))


if __name__ == "__main__":
    main()
