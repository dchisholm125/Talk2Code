from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import AsyncIterator, Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse
from sse_starlette import EventSourceResponse
from uvicorn import Config, Server

from core.events import ProgressUpdate, SessionID
from observability.hub import get_observability_hub
from telemetry import TelemetryEvent, get_event_ledger

OBSERVABILITY_HOST = "0.0.0.0"
OBSERVABILITY_PORT = 8765
SESSION_STATE_PATH = Path.home() / ".voice-to-code" / "sessions-state.json"
SESSION_EVENT_LIMIT = 200

app = FastAPI(
    title="Voice-to-Code observability",
    description="Streams progress updates emitted by the orchestrator and brainstorming services.",
)

_hub = get_observability_hub()


def _serialize_event(update: ProgressUpdate) -> str:
    payload = jsonable_encoder(update)
    payload["timestamp"] = time.time()
    return json.dumps(payload)


@app.get("/observability/progress", response_class=EventSourceResponse)
async def progress_stream() -> EventSourceResponse:
    queue = _hub.subscribe()

    async def server_events() -> AsyncIterator[str]:
        try:
            while True:
                update = await queue.get()
                yield f"data: {_serialize_event(update)}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            _hub.unsubscribe(queue)

    return EventSourceResponse(server_events())


@app.get("/observability/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


def _load_session_states() -> Dict[str, Any]:
    if not SESSION_STATE_PATH.exists():
        return {}
    try:
        with open(SESSION_STATE_PATH, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, OSError):
        return {}


def _serialize_telemetry_event(event: TelemetryEvent) -> Dict[str, Any]:
    return {
        "session_id": int(event.session_id),
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "payload": event.payload,
        "reason": event.reason,
    }


@app.get("/observability/sessions/{session_id}")
async def session_details(session_id: int) -> JSONResponse:
    sessions = _load_session_states()
    state = sessions.get(str(session_id))
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    ledger = get_event_ledger()
    events = ledger.get_events(SessionID(session_id))
    if len(events) > SESSION_EVENT_LIMIT:
        events = events[-SESSION_EVENT_LIMIT:]

    payload = {
        "session_id": session_id,
        "state": state,
        "events": [_serialize_telemetry_event(evt) for evt in events],
    }

    return JSONResponse(jsonable_encoder(payload))


async def start_observability_server(
    host: str = OBSERVABILITY_HOST, port: int = OBSERVABILITY_PORT
) -> None:
    import logging as _logging
    import socket

    _obs_logger = _logging.getLogger("voice-to-code")

    # Try the configured port, then up to 3 alternates, so a stale daemon
    # never prevents the new one from serving observability data.
    bound_port: int | None = None
    for attempt_port in range(port, port + 4):
        try:
            # Probe the port before handing it to uvicorn; this gives us a
            # clean OSError instead of a fatal sys.exit(1) from uvicorn.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((host, attempt_port))
            bound_port = attempt_port
            break
        except OSError:
            _obs_logger.warning(
                f"[OBSERVABILITY] Port {attempt_port} is in use, trying next..."
            )

    if bound_port is None:
        _obs_logger.error(
            f"[OBSERVABILITY] Could not bind on ports {port}–{port + 3}. "
            "Observability HTTP server will NOT start. The bot continues normally."
        )
        return

    if bound_port != port:
        _obs_logger.warning(
            f"[OBSERVABILITY] Configured port {port} busy; using {bound_port} instead."
        )

    _obs_logger.info(f"[OBSERVABILITY] Starting server on {host}:{bound_port}")
    try:
        config = Config(app=app, host=host, port=bound_port, loop="asyncio", lifespan="on")
        server = Server(config=config)
        await server.serve()
    except SystemExit as exc:
        # Uvicorn calls sys.exit(1) on startup failures — absorb it so the
        # unhandled task exception never corrupts the bot's event loop.
        _obs_logger.error(
            f"[OBSERVABILITY] Server exited with code {exc.code}. "
            "Observability disabled for this session."
        )
    except Exception as exc:
        _obs_logger.error(f"[OBSERVABILITY] Unexpected server error: {exc}", exc_info=True)
