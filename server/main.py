import json
import time
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

STATUS_FILE = Path("/tmp/claude-status.json")
STALE_AFTER = 300  # seconds

# Tool name → display category (0=generic RUN, 1=BASH, 2=FILE, 3=WEB, 4=AGNT, 5=PLAN)
TOOL_TYPES = {
    "Bash": 1,
    "Read": 2, "Write": 2, "Edit": 2, "NotebookEdit": 2, "Glob": 2, "Grep": 2,
    "WebSearch": 3, "WebFetch": 3,
    "Agent": 4,
    "TodoWrite": 5, "TaskCreate": 5, "TaskUpdate": 5, "TaskGet": 5, "TaskList": 5,
}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

_default = {"sessions": 0, "running": 0, "waiting": 0, "msg": "Idle",
            "tool": "", "tool_type": 0, "tokens_today": 0, "ts": 0}


@app.get("/status")
def status():
    if not STATUS_FILE.exists():
        return JSONResponse(_default)
    try:
        data = json.loads(STATUS_FILE.read_text())
        if time.time() - data.get("ts", 0) > STALE_AFTER and data.get("running"):
            data["running"] = 0
            data["msg"] = "Idle"
            data["tool"] = ""
        tool = data.get("tool", "")
        data["tool_type"] = TOOL_TYPES.get(tool, 0) if data.get("running") else 0
        return JSONResponse(data)
    except Exception:
        return JSONResponse(_default)


@app.get("/health")
def health():
    return {"status": "ok"}
