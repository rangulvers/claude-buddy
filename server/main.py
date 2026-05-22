import fcntl
import json
import logging
import math
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("claude-buddy")

STATUS_FILE  = Path("/tmp/claude-status.json")
BUDDIES_FILE = Path.home() / ".claude-buddy" / "buddies.json"
BUDDIES_FILE.parent.mkdir(exist_ok=True)

STALE_AFTER  = 300
ADMIN_TOKEN  = os.environ.get("BUDDY_ADMIN_TOKEN", "buddy-admin-changeme")
TOKENS_BASE = 1_000

# Warn operators who are running with the default insecure token.
if ADMIN_TOKEN == "buddy-admin-changeme":
    print(
        "WARNING: BUDDY_ADMIN_TOKEN is set to the default value 'buddy-admin-changeme'. "
        "Set the BUDDY_ADMIN_TOKEN environment variable to a strong secret before exposing "
        "this server outside your local machine.",
        file=sys.stderr,
    )

BUDDY_TYPES    = ["BOT","CAT","OWL","GHOST","ALIEN","BEAR","FOX","DRAGON","BUNNY","CRYSTAL"]
BUDDY_RARITIES = ["Common","Common","Uncommon","Rare","Epic","Common","Rare","Legendary","Uncommon","Mystical"]
BUDDY_NAMES    = {
    0: ["Bolt","Chip","Circuit","Cog","Gear","Pixel","Spark","Static","Volt","Wire"],
    1: ["Byte","Cache","Claw","Cursor","Hiss","Mew","Patch","Purr","Tab","Tail"],
    2: ["Binary","Codec","Hoot","Lumen","Null","Query","Sage","Sigma","Twig","Woo"],
    3: ["Async","Boo","Daemon","Echo","Flicker","Glitch","Phantom","Vapor","Void","Zero"],
    4: ["Alpha","Delta","Flux","Gamma","Helix","Nova","Orb","Pulse","Qubit","Zeta"],
    5: ["Blob","Buff","Chunk","Dense","Fuzzy","Hash","Heap","Stack","Stub","Thick"],
    6: ["Cache","Clever","Debug","Delta","Fleet","Parse","Quick","Sharp","Swift","Trace"],
    7: ["Blaze","Crypt","Forge","Glyph","Hex","Kernel","Root","Rune","Shell","Smog"],
    8: ["Buffer","Hop","Jump","Loop","Nibble","Ping","Quick","Skip","Sprint","Tick"],
    9: ["Array","Core","Facet","Grid","Index","Lattice","Matrix","Node","Prism","Vector"],
}

SPAWN_WEIGHTS = [
    (0, 200),   # BOT      Common    20%
    (1, 370),   # CAT      Common    17%
    (5, 500),   # BEAR     Common    13%
    (8, 620),   # BUNNY    Uncommon  12%
    (2, 745),   # OWL      Uncommon  12.5%
    (6, 870),   # FOX      Rare      12.5%
    (3, 945),   # GHOST    Rare      7.5%
    (4, 975),   # ALIEN    Epic      3%
    (7, 995),   # DRAGON   Legendary 2%
    (9, 1000),  # CRYSTAL  Mystical  0.5%
]

TOOL_TYPES = {
    "Bash": 1,
    "Read": 2, "Write": 2, "Edit": 2, "NotebookEdit": 2, "Glob": 2, "Grep": 2,
    "WebSearch": 3, "WebFetch": 3,
    "Agent": 4,
    "TodoWrite": 5, "TaskCreate": 5, "TaskUpdate": 5, "TaskGet": 5, "TaskList": 5,
}

# Allowed characters for buddy names assigned via the admin API.
_BUDDY_NAME_RE = re.compile(r'^[A-Za-z0-9 _\-]+$')

app = FastAPI(title="Claude Buddy Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
security = HTTPBearer()

_default = {
    "sessions": 0, "running": 0, "waiting": 0, "msg": "Idle",
    "tool": "", "tool_type": 0,
    "buddy_type": 0, "buddy_name": "", "buddy_rarity": "",
    "buddy_level": 1, "buddy_tokens": 0, "levelup": False,
    "tokens_today": 0, "ts": 0,
}


def load_buddies() -> dict:
    """Load buddies.json under an advisory read lock.

    If the file is missing, returns {}.
    If the file is present but unreadable or contains invalid JSON, logs the
    error at WARNING level and returns {} rather than silently swallowing the
    problem.
    """
    if not BUDDIES_FILE.exists():
        return {}
    try:
        with BUDDIES_FILE.open("r") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            try:
                return json.load(fh)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except json.JSONDecodeError as exc:
        logger.warning("buddies.json is corrupt (%s); returning empty dict. "
                       "Inspect %s before next write.", exc, BUDDIES_FILE)
        return {}
    except OSError as exc:
        logger.warning("Could not read buddies.json: %s", exc)
        return {}


def save_buddies(data: dict):
    """Write buddies.json atomically under an advisory write lock.

    Writes to a sibling temp file first, then uses os.replace() so the live
    file is never in a partially-written state.  An exclusive fcntl lock on
    the temp file serialises concurrent writers.
    """
    dir_ = BUDDIES_FILE.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".buddies-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        os.replace(tmp_path, BUDDIES_FILE)
    except Exception:
        # Clean up the temp file if anything went wrong before the rename.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def roll_buddy() -> tuple[int, str]:
    roll = random.randint(0, 999)
    for type_id, upper in SPAWN_WEIGHTS:
        if roll < upper:
            return type_id, random.choice(BUDDY_NAMES[type_id])
    return 9, random.choice(BUDDY_NAMES[9])


def _compute_level(tokens_total: int) -> int:
    """Exponential ×2 progression: LV N→N+1 costs TOKENS_BASE × 2^(N-1).
    Total to reach level N = TOKENS_BASE × (2^(N-1) - 1)."""
    if tokens_total <= 0:
        return 1
    return max(1, int(math.log2(tokens_total / TOKENS_BASE + 1)) + 1)


def _tokens_for_level(level: int) -> int:
    """Total tokens required to reach `level` (cumulative threshold)."""
    return TOKENS_BASE * (2 ** (level - 1) - 1)


def _sanitise_tokens_today(raw) -> int:
    """Return a non-negative integer from whatever tokens_today the status file
    contains.  Rejects floats, strings, negatives, and None."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        logger.warning("tokens_today has unexpected type %s (%r); treating as 0",
                       type(raw).__name__, raw)
        return 0
    if raw < 0:
        logger.warning("tokens_today is negative (%d); treating as 0", raw)
        return 0
    return raw


def get_or_assign(device_id: str, tokens_today: int = 0) -> tuple[dict, bool]:
    buddies = load_buddies()
    if device_id not in buddies:
        type_id, name = roll_buddy()
        buddies[device_id] = {
            "type":           type_id,
            "name":           name,
            "type_name":      BUDDY_TYPES[type_id],
            "rarity":         BUDDY_RARITIES[type_id],
            "assigned_at":    int(time.time()),
            "tokens_total":   0,
            "tokens_last":    0,
            "level":          1,
            "level_notified": 1,
        }

    buddy = buddies[device_id]

    # Accumulate token delta (handles daily reset: tokens_today < tokens_last)
    last  = buddy.get("tokens_last", 0)
    total = buddy.get("tokens_total", 0)
    if tokens_today >= last:
        total += tokens_today - last
    else:
        total += tokens_today
    buddy["tokens_total"] = total
    buddy["tokens_last"]  = tokens_today

    # Level progression (exponential ×2: LV N→N+1 costs TOKENS_BASE × 2^(N-1))
    new_level     = _compute_level(total)
    old_notified  = buddy.get("level_notified", 1)
    levelup       = new_level > old_notified
    buddy["level"] = new_level
    if levelup:
        buddy["level_notified"] = new_level

    buddy["last_seen"] = int(time.time())
    buddies[device_id] = buddy
    save_buddies(buddies)
    return buddy, levelup


def require_admin(creds: HTTPAuthorizationCredentials = Depends(security)):
    if creds.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")


@app.get("/status")
def status(device_id: str = ""):
    try:
        data = json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else dict(_default)
    except Exception:
        data = dict(_default)

    raw_tokens = data.get("tokens_today", 0)
    tokens_today = _sanitise_tokens_today(raw_tokens)

    buddy, levelup = None, False
    if device_id:
        buddy, levelup = get_or_assign(device_id, tokens_today)

    if time.time() - data.get("ts", 0) > STALE_AFTER and data.get("running"):
        data["running"] = 0
        data["msg"] = "Idle"
        data["tool"] = ""

    tool = data.get("tool", "")
    data["tool_type"] = TOOL_TYPES.get(tool, 0) if data.get("running") else 0

    if buddy:
        lvl       = buddy["level"]
        cost      = TOKENS_BASE * (2 ** (lvl - 1))
        in_level  = buddy["tokens_total"] - _tokens_for_level(lvl)
        level_pct = min(100, max(0, int(in_level * 100 / cost))) if cost > 0 else 0
        data["buddy_type"]      = buddy["type"]
        data["buddy_name"]      = buddy["name"]
        data["buddy_rarity"]    = buddy["rarity"]
        data["buddy_level"]     = lvl
        data["buddy_level_pct"] = level_pct
        data["buddy_tokens"]    = buddy["tokens_total"]
        data["levelup"]         = levelup
    else:
        data.setdefault("buddy_type",   0)
        data.setdefault("buddy_name",   "")
        data.setdefault("buddy_rarity", "")
        data.setdefault("buddy_level",  1)
        data.setdefault("buddy_tokens", 0)
        data.setdefault("levelup",      False)

    return JSONResponse(data)


class AssignRequest(BaseModel):
    device_id:  str = Field(..., max_length=64)
    buddy_type: int
    buddy_name: str = Field(..., min_length=1, max_length=32)

    @field_validator("buddy_name")
    @classmethod
    def name_safe_chars(cls, v: str) -> str:
        if not _BUDDY_NAME_RE.match(v):
            raise ValueError(
                "buddy_name may only contain letters, digits, spaces, hyphens, and underscores"
            )
        return v

    @field_validator("device_id")
    @classmethod
    def device_id_safe_chars(cls, v: str) -> str:
        if not re.match(r'^[A-Za-z0-9_\-:.]+$', v):
            raise ValueError(
                "device_id may only contain letters, digits, underscores, hyphens, colons, and dots"
            )
        return v


@app.post("/admin/assign", dependencies=[Depends(require_admin)])
def admin_assign(req: AssignRequest):
    if req.buddy_type < 0 or req.buddy_type > 9:
        raise HTTPException(status_code=400, detail="buddy_type must be 0-9")
    buddies = load_buddies()
    prev = buddies.get(req.device_id, {})
    buddies[req.device_id] = {
        "type":           req.buddy_type,
        "name":           req.buddy_name,
        "type_name":      BUDDY_TYPES[req.buddy_type],
        "rarity":         BUDDY_RARITIES[req.buddy_type],
        "assigned_at":    int(time.time()),
        "last_seen":      prev.get("last_seen", 0),
        "tokens_total":   prev.get("tokens_total", 0),
        "tokens_last":    prev.get("tokens_last", 0),
        "level":          prev.get("level", 1),
        "level_notified": prev.get("level_notified", 1),
        "admin_assigned": True,
    }
    save_buddies(buddies)
    return {"ok": True, "device_id": req.device_id, **buddies[req.device_id]}


@app.get("/admin/buddies", dependencies=[Depends(require_admin)])
def admin_buddies():
    return load_buddies()


@app.get("/health")
def health():
    return {"status": "ok"}
