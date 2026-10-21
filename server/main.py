"""
DevPulse — FastAPI HTTP server
Implements the MCP 2025-11-25 Streamable HTTP transport spec.

Endpoints:
  POST /mcp        — MCP protocol endpoint
  GET  /simulator  — Alexa+ voice simulator for demo
  GET  /health     — Health check
  GET  /           — Server info
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse

from config import settings
from mcp_server import dispatch, dispatch_batch

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("devpulse")


SIMULATOR_HTML = open(__file__.replace("main.py", "simulator.html"), encoding="utf-8").read() \
    if __import__("os").path.exists(__file__.replace("main.py", "simulator.html")) \
    else "<h1>simulator.html not found</h1>"


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = settings.validate()
    if missing:
        logger.warning(
            "Missing environment variables: %s — some features will be degraded.",
            ", ".join(missing),
        )
    else:
        logger.info("DevPulse MCP server starting on %s:%s", settings.host, settings.port)
    yield
    logger.info("DevPulse shutting down.")


app = FastAPI(
    title="DevPulse MCP Server",
    description="Voice-driven developer workflow assistant for Alexa+",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


@app.get("/simulator", response_class=HTMLResponse)
async def simulator():
    """Alexa+ voice simulator — open this in your browser to demo DevPulse."""
    return SIMULATOR_HTML


@app.post("/mcp")
async def mcp_endpoint(
    request: Request,
    body: Any = Body(
        ...,
        examples={
            "initialize": {
                "value": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "clientInfo": {"name": "test", "version": "1.0"}
                    }
                }
            },
            "tools/list": {
                "value": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {}
                }
            }
        }
    )
) -> Response:
    """
    MCP 2025-11-25 Streamable HTTP transport.

    Clients must send JSON-RPC 2.0 messages (single object or array for batch).
    The Accept header controls the response format:
      - application/json  -> direct JSON response
      - text/event-stream -> SSE stream
    """
    session_id: str = request.headers.get("mcp-session-id") or str(uuid.uuid4())
    accept: str = request.headers.get("accept", "application/json")
    response_headers = {"Mcp-Session-Id": session_id}

    if isinstance(body, list):
        if not body:
            return Response(status_code=400, content="Empty batch")
        responses = await dispatch_batch(body, session_id)
        if "text/event-stream" in accept:
            return _sse_response(responses, response_headers)
        return JSONResponse(content=responses, headers=response_headers)

    result = await dispatch(body, session_id)

    if result is None:
        return Response(status_code=202, headers=response_headers)

    if "text/event-stream" in accept:
        return _sse_response([result], response_headers)

    return JSONResponse(content=result, headers=response_headers)


def _sse_response(messages: list[dict], headers: dict) -> StreamingResponse:
    async def generate():
        for message in messages:
            payload = json.dumps(message, ensure_ascii=False)
            yield f"data: {payload}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "mcp_version": "2025-11-25"}


@app.get("/")
async def root():
    return {
        "name": "DevPulse MCP Server",
        "version": "1.0.0",
        "mcp_endpoint": "/mcp",
        "simulator": "/simulator",
        "spec": "2025-11-25",
        "transport": "Streamable HTTP",
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )