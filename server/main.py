import json
import time
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

STATUS_FILE = Path("/tmp/claude-status.json")
STALE_AFTER = 300  # seconds

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

_default = {"sessions": 0, "running": 0, "waiting": 0, "msg": "Idle", "tokens_today": 0, "ts": 0}


@app.get("/status")
def status():
    if not STATUS_FILE.exists():
        return JSONResponse(_default)
    try:
        data = json.loads(STATUS_FILE.read_text())
        # If stale, report idle (Claude stopped without firing hook)
        if time.time() - data.get("ts", 0) > STALE_AFTER and data.get("running"):
            data["running"] = 0
            data["msg"] = "Idle"
        return JSONResponse(data)
    except Exception:
        return JSONResponse(_default)


@app.get("/health")
def health():
    return {"status": "ok"}
