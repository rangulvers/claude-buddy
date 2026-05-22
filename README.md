# Claude Buddy

A tiny status display for your desk that shows what Claude Code is doing — built on a D1 Mini (ESP8266) with a 128×64 OLED. Idle shows your home energy data. When Claude is active, the screen switches to a full-screen mascot that reacts in real time.

```
Active / Running                 Active / Idle
┌───────────────────────────┐    ┌───────────────────────────┐
│    o*        │  CLAUDE    │    │    o         │  CLAUDE    │
│   /|\        │ ─────────  │    │   /|\        │ ─────────  │
│  (◉ ◉)  ╱╲  │     1      │    │  (-_-)   z   │     1      │
│   \U/        │  session   │    │   \~~/  z Z  │  session   │
│              │  RUN /     │    │              │  IDL ..    │
└───────────────────────────┘    └───────────────────────────┘

No active sessions
┌───────────────────────────┐
│                           │
│          Claude           │
│          Buddy            │
│          ready            │
└───────────────────────────┘
```

## How it works

```
Claude Code hooks                 ESP8266
─────────────────                 ───────
UserPromptSubmit  ─┐              keller-example.yaml
PostToolUse (beat) ├─► status ──► polls /status every 3s
Stop              ─┘  writer      draws mascot on OLED
                       .sh
                         │
                         ▼
                  /tmp/claude-status.json
                         │
                         ▼
                  server/main.py  (FastAPI :3003)
```

Three components:

| Component | Path | Purpose |
|-----------|------|---------|
| Status server | `server/` | FastAPI — exposes `/status` JSON for the ESP to poll |
| Hook script | `hooks/claude-status-writer.sh` | Called by Claude Code hooks; writes `/tmp/claude-status.json` |
| ESPHome config | `esphome/keller-example.yaml` | D1 Mini firmware — display logic, HTTP poll, mascot |

---

## Status server

Minimal FastAPI app. Reads `/tmp/claude-status.json` and serves it at `GET /status`.

```json
{
  "sessions": 1,
  "running": 1,
  "waiting": 0,
  "msg": "Read",
  "tokens_today": 0,
  "ts": 1748000000
}
```

Status older than 5 minutes is treated as idle (catches the case where Claude exits without firing the Stop hook).

```bash
cd server
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 3003
```

---

## Hook script

`hooks/claude-status-writer.sh` is called by three Claude Code hooks:

| Hook | Event | What it writes |
|------|-------|----------------|
| `UserPromptSubmit` | User sends a message | `running=1`, captures first 60 chars of prompt |
| `PostToolUse` | Any tool call completes | `running=1`, captures tool name (heartbeat) |
| `Stop` | Claude finishes responding | `running=0`, `msg=Idle` |

Install in `~/.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [
    {
      "hooks": [
        {"type": "command", "command": "/path/to/claude-status-writer.sh --event=prompt"}
      ]
    }
  ],
  "PostToolUse": [
    {
      "matcher": "*",
      "hooks": [
        {"type": "command", "command": "/path/to/claude-status-writer.sh --event=tool"}
      ]
    }
  ],
  "Stop": [
    {
      "hooks": [
        {"type": "command", "command": "/path/to/claude-status-writer.sh --event=stop"}
      ]
    }
  ]
}
```

---

## ESPHome display

Hardware: **D1 Mini (ESP8266)** + **SSD1306 128×64 OLED** (I2C, SDA=D2, SCL=D1)

`esphome/keller-example.yaml` — adapt to your setup:

- Replace `http://YOUR_SERVER_IP:3003/status` with your status server address
- Replace the four `homeassistant` sensor entity IDs with your own (or remove them if you don't use HA)
- Set your WiFi credentials in ESPHome secrets

### Display modes

**No active Claude sessions** — standby screen:
```
┌─────────────────────┐
│                     │
│       Claude        │
│       Buddy         │
│       ready         │
└─────────────────────┘
```

**Active Claude session** — full-screen mascot:
- Left half: animated robot face
  - Running: raised eyebrows, open eyes with pupils, wide smile, pulsing antenna, energy rays
  - Idle: droopy eyebrows, half-closed eyes, small smile, floating Zzz
  - Blinks every 8 seconds regardless of state
- Right half: session count + RUN/IDL status with spinner animation

### Known gotchas (ESP8266-specific)

- **Do not set `min_auth_mode: WPA2`** — causes "Auth Expired" loop on WPA/WPA2 mixed-mode routers
- **HTTP timeout must be ≤ 2s** — ESP8266 hardware watchdog fires at ~3.2s; 8s timeout causes crash loop
- **I2C frequency must be 400kHz** — default 50kHz means ~160ms blocked per frame, starves WiFi stack
- **`wifi.connected` condition on HTTP interval** — prevents request attempts during reconnect phase

### Flashing

OTA via ESPHome dashboard (once WiFi is working), or serial:
```bash
# Via ESPHome dashboard → INSTALL → "Plug into computer running ESPHome Dashboard"
# Device appears as /dev/ttyACM0 (CH340 chip)
```

---

## Mascot design

Face drawn entirely with ESPHome display primitives on 64×64 pixels (left half of 128×64 screen):

```
Running state              Idle state
   o*                         o
  /|\                         |
 (◉ ◉)  ← pupils          (-_-)  ← droopy lids + flat brows
  \U/   ← wide smile        \~/   ← small smile
╱    ╲  ← energy rays    z z Z    ← floating Zzz
```

Primitives used: `circle`, `filled_circle`, `filled_rectangle`, `line`, `printf`

Animation driven by an 8-step frame counter (1s per step at `update_interval: 1s`):
- Blink at frame 7 (every 8 seconds)
- Antenna pulse every even frame when running
- Energy rays every even frame when running
- Zzz appear progressively: z at frame≥1, second z at frame≥3, Z at frame≥5 when idle
- Spinner/dots on right panel cycle through frame % 4
