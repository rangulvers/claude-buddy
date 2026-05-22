#!/bin/bash
# Write /tmp/claude-status.json for the claude-buddy-server.
# Called by Stop and UserPromptSubmit hooks.
# Usage: claude-status-writer.sh --event stop|prompt
# Stdin: Claude Code hook JSON payload.

set -euo pipefail

EVENT="${1:-}"
STATUS_FILE="/tmp/claude-status.json"
TMP_FILE="/tmp/claude-status.json.tmp"

# Parse stdin for msg
STDIN_JSON=$(cat)
MSG=""
TOOL_NAME=""
NEW_TOKENS=0
if [ "$EVENT" = "--event=prompt" ]; then
    MSG=$(echo "$STDIN_JSON" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); p=d.get('prompt',''); print(p[:60].replace('\n',' '))" \
        2>/dev/null || true)
elif [ "$EVENT" = "--event=tool" ]; then
    TOOL_NAME=$(echo "$STDIN_JSON" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name','')[:60])" \
        2>/dev/null || true)
    MSG="$TOOL_NAME"
elif [ "$EVENT" = "--event=stop" ]; then
    NEW_TOKENS=$(echo "$STDIN_JSON" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); u=d.get('usage',{}); print(u.get('input_tokens',0)+u.get('output_tokens',0))" \
        2>/dev/null || echo 0)
fi

# Count active claude tmux panes
SESSIONS=$(tmux list-panes -a -F "#{pane_current_command}" 2>/dev/null \
    | grep -c "^claude$" || true)
[ -z "$SESSIONS" ] && SESSIONS=0

# Determine running/waiting from event
if [ "$EVENT" = "--event=prompt" ] || [ "$EVENT" = "--event=tool" ]; then
    RUNNING=1
    WAITING=0
    [ -z "$MSG" ] && MSG="Working..."
else
    RUNNING=0
    WAITING=0
    MSG="Idle"
fi

# Tokens today: accumulate; Stop event adds NEW_TOKENS to running total
TOKENS_TODAY=0
if [ -f "$STATUS_FILE" ]; then
    TOKENS_TODAY=$(python3 -c \
        "import json; d=json.load(open('$STATUS_FILE')); print(d.get('tokens_today',0))" \
        2>/dev/null || echo 0)
fi
TOKENS_TODAY=$(( TOKENS_TODAY + NEW_TOKENS ))

TS=$(date +%s)

python3 -c "
import json
d = {
    'sessions': $SESSIONS,
    'running': $RUNNING,
    'waiting': $WAITING,
    'msg': $(echo "$MSG" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    'tool': $(echo "$TOOL_NAME" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))"),
    'tokens_today': $TOKENS_TODAY,
    'ts': $TS
}
print(json.dumps(d))
" > "$TMP_FILE" 2>/dev/null && mv "$TMP_FILE" "$STATUS_FILE" || true

exit 0
