"""Pontis Web Frontend - FastAPI server with SSE streaming."""
import json
import os
import uuid
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Ensure project root is importable
import sys
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agent.agent import PontusAgent

app = FastAPI(title="Pontis Agent")

# --- Session Management ---
_sessions: dict[str, PontusAgent] = {}


def _validate_project_path(path: str) -> Optional[str]:
    """Validate project path. Returns error message or None."""
    if not os.path.isabs(path):
        return "路径必须是绝对路径"
    if ".." in path:
        return "路径不能包含 .."
    if not os.path.isdir(path):
        return f"路径不存在或不是目录: {path}"
    pontis_dir = os.path.join(path, ".pontis")
    if not os.path.isdir(pontis_dir):
        return f"未找到 .pontis 目录: {pontis_dir}\n请先运行 extractor 提取知识图谱"
    return None


def _get_or_create_agent(session_id: str, project_path: str) -> PontusAgent:
    key = f"{session_id}:{project_path}"
    if key not in _sessions:
        _sessions[key] = PontusAgent(project_path)
    return _sessions[key]


# --- Static Files ---
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# --- Routes ---

class ChatRequest(BaseModel):
    session_id: str
    project_path: str
    message: str


@app.get("/", response_class=HTMLResponse)
async def index():
    return RedirectResponse(url="/static/index.html")


@app.get("/api/validate")
async def validate(project_path: str = Query(..., alias="project_path")):
    error = _validate_project_path(project_path)
    if error:
        return JSONResponse({"valid": False, "error": error}, status_code=400)
    return {"valid": True, "project_path": project_path}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    error = _validate_project_path(req.project_path)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    agent = _get_or_create_agent(req.session_id, req.project_path)

    def event_generator():
        try:
            for event in agent.chat_stream(req.message):
                yield json.dumps(event, ensure_ascii=False)
        except Exception as e:
            yield json.dumps({"type": "error", "content": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

    return EventSourceResponse(event_generator())


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, project_path: str = Query(...)):
    key = f"{session_id}:{project_path}"
    if key in _sessions:
        del _sessions[key]
    return {"ok": True}


@app.get("/api/session-id")
async def new_session_id():
    """Generate a new session ID."""
    return {"session_id": str(uuid.uuid4())}
