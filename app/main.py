"""
FastAPI entrypoint.

Production-oriented touches:
- Per-request `request_id` injected into log records and returned to client.
- Structured error responses with stable shape: {error, message, request_id}.
- CORS allowed for the Streamlit UI (configurable).
- /health reports memory stats so external probes can monitor session count.

Endpoints:
    POST /ask         -> {question, session_id}            -> {answer, request_id}
    POST /reset       -> {session_id}                      -> {status}
    GET  /health      -> {status, memory: {sessions, total_messages}}
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agent import ask as run_agent
from app.errors import CountryAgentError
from app.memory import session_memory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s - %(message)s",
)


# Inject `request_id` into every log record (defaults to "-" when not set).
class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


for h in logging.getLogger().handlers:
    h.addFilter(_RequestIdFilter())


logger = logging.getLogger(__name__)

app = FastAPI(title="Country Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten this in prod (Streamlit origin only).
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Middleware: assign a request_id and time the request
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start = time.perf_counter()

    # Attach request_id to log records emitted during this request.
    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.request_id = request_id
        return record

    logging.setLogRecordFactory(factory)
    try:
        response = await call_next(request)
    finally:
        logging.setLogRecordFactory(old_factory)

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    response.headers["x-request-id"] = request_id
    logger.info(
        "http.request method=%s path=%s status=%s latency_ms=%d",
        request.method, request.url.path, response.status_code, elapsed_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    session_id: str = Field(..., min_length=1, max_length=128)


class AskResponse(BaseModel):
    answer: str
    session_id: str
    request_id: str


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)


class ErrorResponse(BaseModel):
    error: str
    message: str
    request_id: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "memory": session_memory.stats()}


@app.post("/ask")
def ask(req: AskRequest, request: Request):
    rid = request.state.request_id
    try:
        answer = run_agent(req.question, req.session_id)
    except CountryAgentError as e:
        # Known, recoverable error from our own code.
        logger.warning("agent.handled_error err=%s", e)
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                error=type(e).__name__, message=str(e), request_id=rid
            ).model_dump(),
        )
    except Exception as e:  # noqa: BLE001
        # Unknown error — log full stack, return generic message.
        logger.exception("agent.unhandled_error")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="InternalError",
                message="The agent encountered an unexpected error.",
                request_id=rid,
            ).model_dump(),
        )

    return AskResponse(answer=answer, session_id=req.session_id, request_id=rid)


@app.post("/reset")
def reset(req: ResetRequest, request: Request):
    session_memory.clear(req.session_id)
    return {
        "status": "cleared",
        "session_id": req.session_id,
        "request_id": request.state.request_id,
    }
