# CLAUDE.md — Claude Buddy Install Instructions

This file tells Claude Code exactly how to install Claude Buddy on this machine.
When the user says "install this repo" or "set up claude-buddy", follow every step below.

---

## What gets installed

1. **Status server** — FastAPI app (port 3003) that tracks Claude Code activity
2. **Hook script** — shell script called by Claude Code hooks to write activity to the server
3. **ESPHome firmware** — YAML config for a D1 Mini + SSD1306 OLED (user flashes manually)

---

## Step 1 — Determine repo path

The repo is wherever the user cloned it. Use the current working directory as `REPO_DIR`.
All paths below use `$REPO_DIR` as that root.

---

## Step 2 — Server: create venv and install dependencies

```bash
cd $REPO_DIR/server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Verify:
```bash
$REPO_DIR/server/.venv/bin/python -c "import fastapi, uvicorn; print('ok')"
```

---

## Step 3 — Server: create systemd user service

Write the file `~/.config/systemd/user/claude-buddy-server.service` with this exact content,
replacing `REPO_DIR` with the absolute path to the server directory:

```ini
[Unit]
Description=Claude Buddy Status Server
After=network.target

[Service]
Type=simple
WorkingDirectory=REPO_DIR/server
ExecStart=REPO_DIR/server/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 3003
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=claude-buddy-server

[Install]
WantedBy=default.target
```

**Optional but recommended:** set a custom admin token by adding this line under `[Service]`:
```ini
Environment=BUDDY_ADMIN_TOKEN=your-secret-token-here
```

Then enable and start:
```bash
systemctl --user daemon-reload
systemctl --user enable claude-buddy-server
systemctl --user start claude-buddy-server
sleep 2
systemctl --user is-active claude-buddy-server
```

Verify the server is up:
```bash
curl -s http://localhost:3003/health
```

Expected: `{"status":"ok"}`

---

## Step 4 — Hook script: install

```bash
mkdir -p ~/.local/bin
cp $REPO_DIR/hooks/claude-status-writer.sh ~/.local/bin/claude-status-writer.sh
chmod +x ~/.local/bin/claude-status-writer.sh
```

---

## Step 5 — Hook script: wire into Claude Code settings

Read `~/.claude/settings.json`. If it does not exist, create it as `{}`.

**Merge** the following hooks into the existing `hooks` object — do NOT overwrite any existing hooks, append to each event's array:

```json
"UserPromptSubmit": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "/home/USER/.local/bin/claude-status-writer.sh --event=prompt"
      }
    ]
  }
],
"PostToolUse": [
  {
    "matcher": "*",
    "hooks": [
      {
        "type": "command",
        "command": "/home/USER/.local/bin/claude-status-writer.sh --event=tool"
      }
    ]
  }
],
"Stop": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "/home/USER/.local/bin/claude-status-writer.sh --event=stop"
      }
    ]
  }
]
```

Replace `/home/USER` with the actual home directory (`echo $HOME`).

**Important:** `settings.json` may already have entries under `UserPromptSubmit`, `PostToolUse`, and `Stop`. Add the new hook objects to those arrays — do not replace what is already there.

---

## Step 6 — Verify hooks fire

Run this to simulate a Stop event and confirm tokens are tracked:

```bash
echo '{"usage":{"input_tokens":100,"output_tokens":50}}' \
  | ~/.local/bin/claude-status-writer.sh --event=stop

python3 -c "import json; d=json.load(open('/tmp/claude-status.json')); print('tokens_today:', d['tokens_today'])"
```

Expected: `tokens_today: 150`

---

## Step 7 — ESPHome firmware (manual)

The user must flash the ESP device themselves via the ESPHome dashboard or CLI.

Tell the user:
1. Open `$REPO_DIR/esphome/keller-example.yaml`
2. Replace `YOUR_SERVER_IP` with the IP address of this machine (run `hostname -I | awk '{print $1}'` to find it)
3. Add WiFi credentials to their ESPHome `secrets.yaml`
4. Flash via ESPHome dashboard → Install → select device
5. On first boot the device shows a boot splash with the assigned buddy name and rarity

Hardware required:
- Wemos D1 Mini (ESP8266)
- SSD1306 0.96" OLED 128×64 I2C
- Wiring: D1→SCL, D2→SDA, 3V3→VCC, GND→GND

---

## Step 8 — Final check

```bash
# Server health
curl -s http://localhost:3003/health

# Status endpoint (no device, just Claude activity)
curl -s http://localhost:3003/status | python3 -m json.tool

# Admin: list all hatched buddies (use your token from Step 3)
curl -s http://localhost:3003/admin/buddies \
  -H "Authorization: Bearer buddy-admin-changeme" | python3 -m json.tool
```

If the server is not running:
```bash
systemctl --user status claude-buddy-server
journalctl --user -u claude-buddy-server -n 20
```

---

## tmux requirement

The hook script counts active Claude Code sessions by looking for `claude` processes in tmux panes. If the user does not run Claude Code inside tmux, `sessions` will always be 0 on the display. This is cosmetic only — all other features (running state, tool display, tokens, buddy) work regardless.
