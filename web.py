"""Web UI for AI Room — live-streaming negotiation viewer."""

import asyncio
import json
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from engine import Session, load_config
from scenario import DEFAULT_SCENARIO

app = FastAPI(title="AI Room — Negotiation Simulator")

BASE_DIR = Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Global state
active_session: Session | None = None
connected_clients: set[WebSocket] = set()


async def broadcast(event_type: str, data: dict):
    """Send event to all connected WebSocket clients."""
    msg = json.dumps({"type": event_type, **data}, ensure_ascii=False)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def get_status():
    if active_session:
        return {
            "status": active_session.status,
            "session_id": active_session.session_id,
            "current_round": active_session.current_round,
            "max_rounds": active_session.scenario.max_rounds,
            "scenario": active_session.scenario.title,
        }
    return {"status": "idle"}


@app.get("/api/sessions")
async def list_sessions():
    """List all saved sessions."""
    sessions = []
    if SESSIONS_DIR.exists():
        for d in sorted(SESSIONS_DIR.iterdir(), reverse=True):
            if d.is_dir():
                log_file = d / "full_log.json"
                if log_file.exists():
                    try:
                        with open(log_file) as f:
                            data = json.load(f)
                        sessions.append({
                            "id": d.name,
                            "scenario": data.get("scenario", "?"),
                            "status": data.get("status", "?"),
                            "rounds": len(data.get("rounds", [])),
                            "created_at": data.get("created_at", "?"),
                        })
                    except Exception:
                        pass
    return sessions


@app.get("/api/sessions/{session_id}/transcript")
async def get_transcript(session_id: str):
    """Get Markdown transcript of a session."""
    path = SESSIONS_DIR / session_id / "transcript.md"
    if path.exists():
        return HTMLResponse(content=path.read_text(), media_type="text/markdown")
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/sessions/{session_id}/log")
async def get_log(session_id: str):
    """Get full JSON log of a session."""
    path = SESSIONS_DIR / session_id / "full_log.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/start")
async def start_session():
    """Start a new negotiation session."""
    global active_session
    if active_session and active_session.status == "running":
        return JSONResponse({"error": "Session already running"}, status_code=409)

    config = load_config()
    active_session = Session(config=config)
    active_session.set_event_callback(broadcast)

    # Run in background
    asyncio.create_task(_run_and_save())
    return {"status": "started", "session_id": active_session.session_id}


@app.post("/api/stop")
async def stop_session():
    """Stop the active session."""
    global active_session
    if active_session and active_session.status == "running":
        active_session.stop()
        return {"status": "stopped"}
    return {"status": "no_active_session"}


async def _run_and_save():
    """Run session and save results when done."""
    global active_session
    if not active_session:
        return

    try:
        await active_session.run()
    except Exception as e:
        print(f"Session error: {e}")

    # Save
    out_dir = SESSIONS_DIR / active_session.session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "transcript.md", "w") as f:
        f.write(active_session.to_transcript_md())

    with open(out_dir / "full_log.json", "w") as f:
        json.dump(active_session.to_full_log(), f, ensure_ascii=False, indent=2)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)

    # Send current state if session is running
    if active_session:
        await websocket.send_text(json.dumps({
            "type": "status",
            "status": active_session.status,
            "session_id": active_session.session_id,
            "current_round": active_session.current_round,
        }, ensure_ascii=False))

        # Send existing rounds
        for rd in active_session.rounds:
            await websocket.send_text(json.dumps({
                "type": "round",
                **rd
            }, ensure_ascii=False))

        if active_session.epilogue:
            await websocket.send_text(json.dumps({
                "type": "epilogue",
                "text": active_session.epilogue
            }, ensure_ascii=False))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        connected_clients.discard(websocket)


if __name__ == "__main__":
    import uvicorn
    config = load_config()
    web_config = config.get("web", {})
    uvicorn.run(app, host=web_config.get("host", "0.0.0.0"),
                port=web_config.get("port", 8091))
