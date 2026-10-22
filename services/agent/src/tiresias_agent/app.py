"""HTTP entry point. Only /health is implemented so far; /run returns a structured 501."""

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .config import settings

app = FastAPI(title="Tiresias agent", version=__version__)


@app.get("/health")
def health() -> dict:
    return {
        "service": "agent",
        "version": __version__,
        "sample_mode": settings.sample_mode,
        "hitl_mode": settings.hitl_mode,
    }


@app.post("/run")
def run() -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={
            "status": "error",
            "error": {
                "type": "NotImplemented",
                "message": "The investigation pipeline is under construction; see docs/STATUS.md.",
                "recoverable": False,
            },
        },
    )


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("AGENT_PORT", "8000")))


if __name__ == "__main__":
    main()
