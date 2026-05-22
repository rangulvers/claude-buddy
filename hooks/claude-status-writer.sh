#!/bin/bash
# Write /tmp/claude-status.json for the claude-buddy-server.
# Called by Stop, UserPromptSubmit, and PostToolUse hooks.
# Usage: claude-status-writer.sh --event=stop|prompt|tool
# Stdin: Claude Code hook JSON payload.

set -euo pipefail

EVENT="${1:-}"
STATUS_FILE="/tmp/claude-status.json"
TMP_FILE="/tmp/claude-status.json.tmp"
LOCK_FILE="/tmp/claude-status.lock"

# ── Validate event ────────────────────────────────────────────────────────────
# Unknown events are silently treated as stop (idle). This is intentional:
# an unrecognised event should not leave running=1 stuck on.
case "$EVENT" in
    --event=prompt|--event=tool|--event=stop) ;;
    *) EVENT="--event=stop" ;;
esac

# ── Read stdin safely ─────────────────────────────────────────────────────────
# cat will return empty string if stdin is closed or empty; guard downstream
# python3 calls against that case.
STDIN_JSON=$(cat) || true
[ -z "$STDIN_JSON" ] && STDIN_JSON="{}"

# ── Parse event-specific fields ───────────────────────────────────────────────
MSG=""
TOOL_NAME=""
NEW_TOKENS=0

if [ "$EVENT" = "--event=prompt" ]; then
    MSG=$(printf '%s' "$STDIN_JSON" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); p=d.get('prompt',''); print(p[:60].replace('\n',' '))" \
        2>/dev/null || true)

elif [ "$EVENT" = "--event=tool" ]; then
    TOOL_NAME=$(printf '%s' "$STDIN_JSON" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name','')[:60])" \
        2>/dev/null || true)
    MSG="$TOOL_NAME"

elif [ "$EVENT" = "--event=stop" ]; then
    NEW_TOKENS=$(printf '%s' "$STDIN_JSON" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); u=d.get('usage',{}); print(u.get('input_tokens',0)+u.get('output_tokens',0))" \
        2>/dev/null || echo 0)
fi

# Guard: NEW_TOKENS must be a non-negative integer; anything else (empty string,
# corrupted python3 output, non-numeric) is treated as 0 to prevent $(( ))
# throwing a fatal arithmetic error under set -e.
[[ "$NEW_TOKENS" =~ ^[0-9]+$ ]] || NEW_TOKENS=0

# ── Count active claude tmux panes ───────────────────────────────────────────
# grep -c exits 1 when there are no matches, which would kill the script under
# set -e even inside a $() subshell.  The || true makes the pipeline succeed;
# grep still prints "0" on stdout so SESSIONS is never an empty string here.
SESSIONS=$(tmux list-panes -a -F "#{pane_current_command}" 2>/dev/null \
    | grep -c "^claude$" || true)
# Defensive fallback in case some edge case produces an empty string anyway.
[[ "$SESSIONS" =~ ^[0-9]+$ ]] || SESSIONS=0

# ── Determine running/waiting state ──────────────────────────────────────────
if [ "$EVENT" = "--event=prompt" ] || [ "$EVENT" = "--event=tool" ]; then
    RUNNING=1
    WAITING=0
    [ -z "$MSG" ] && MSG="Working..."
else
    RUNNING=0
    WAITING=0
    MSG="Idle"
fi

# ── Token accumulation (with file lock to prevent concurrent write races) ─────
# Use flock so two simultaneous hook calls can't both read the old total and
# then each write back a value that drops the other's contribution.
(
    flock -x 9

    TOKENS_TODAY=0
    if [ -f "$STATUS_FILE" ]; then
        TOKENS_TODAY=$(python3 -c \
            "import json; d=json.load(open('$STATUS_FILE')); print(d.get('tokens_today',0))" \
            2>/dev/null || echo 0)
        # Guard against corrupted or non-numeric value in the existing file.
        [[ "$TOKENS_TODAY" =~ ^[0-9]+$ ]] || TOKENS_TODAY=0
    fi

    TOKENS_TODAY=$(( TOKENS_TODAY + NEW_TOKENS ))
    TS=$(date +%s)

    # Single python3 call: receives all values via environment variables so
    # there is no shell-expansion injection into Python source, and no extra
    # subprocess forks for JSON-encoding msg/tool strings.
    _SESSIONS="$SESSIONS" \
    _RUNNING="$RUNNING" \
    _WAITING="$WAITING" \
    _MSG="$MSG" \
    _TOOL="$TOOL_NAME" \
    _TOKENS="$TOKENS_TODAY" \
    _TS="$TS" \
    python3 - <<'PYEOF' > "$TMP_FILE" 2>/dev/null && mv "$TMP_FILE" "$STATUS_FILE" || true
import json, os
print(json.dumps({
    "sessions":     int(os.environ["_SESSIONS"]),
    "running":      int(os.environ["_RUNNING"]),
    "waiting":      int(os.environ["_WAITING"]),
    "msg":          os.environ["_MSG"],
    "tool":         os.environ["_TOOL"],
    "tokens_today": int(os.environ["_TOKENS"]),
    "ts":           int(os.environ["_TS"]),
}))
PYEOF

) 9>"$LOCK_FILE"

exit 0
