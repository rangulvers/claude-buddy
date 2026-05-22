# Claude Buddy

A tiny status display for your desk that shows what Claude Code is doing — built on a D1 Mini (ESP8266) with a 128×64 OLED. When Claude is active, a mascot takes over the screen and reacts in real time. When idle, a standby screen is shown.

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

## Install

Clone the repo, open it in Claude Code, and say **"install this repo"** — Claude reads `CLAUDE.md` and runs every step automatically:

- Creates a Python venv and installs server dependencies
- Writes and enables a systemd user service (port 3003)
- Copies the hook script to `~/.local/bin/`
- Merges the three hooks into `~/.claude/settings.json`
- Verifies everything is working

Manual step: flash the ESPHome YAML to your D1 Mini (Claude will tell you exactly what to edit and how to wire it).

---

## Hardware

| Part | Spec |
|------|------|
| Microcontroller | Wemos D1 Mini (ESP8266, CH340 USB chip) |
| Display | 0.96" SSD1306 OLED, 128×64px, I2C, 3.3V |

### Wiring

```
D1 Mini        SSD1306 OLED
─────────      ────────────
3V3       ──►  VCC
GND       ──►  GND
D1 (GPIO5)──►  SCL
D2 (GPIO4)──►  SDA
```

Both parts are available on AliExpress / Amazon for a few euros each. No resistors or other components needed — the OLED has onboard pull-ups.

---

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

FastAPI app on port 3003. Reads `/tmp/claude-status.json` and serves it at `GET /status`.

```bash
cd server
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 3003
```

### Buddy hatch system

First time a device connects (via `?device_id=<chip_hex>`), the server rolls a random buddy and stores it permanently in `~/.claude-buddy/buddies.json`. Same device always gets the same buddy. 10 types, 6 rarity tiers:

| Rarity | Types | Chance |
|--------|-------|--------|
| Common | BOT, CAT, BEAR | 50% |
| Uncommon | BUNNY, OWL | 25% |
| Rare | FOX, GHOST | 15% |
| Epic | ALIEN | 3% |
| Legendary | DRAGON | 2% |
| Mystical | CRYSTAL | 0.5% |

`GET /status?device_id=AABBCCDD` returns `buddy_type`, `buddy_name`, `buddy_rarity` alongside the Claude status fields.

Admin endpoints (require `Authorization: Bearer <BUDDY_ADMIN_TOKEN>`):

```bash
# Force-assign a specific buddy
POST /admin/assign
{"device_id": "AABBCCDD", "buddy_type": 9, "buddy_name": "Prism"}

# List all devices and their buddies
GET /admin/buddies
```

Set `BUDDY_ADMIN_TOKEN` env var (defaults to `buddy-admin-changeme` — change it).

Status older than 5 minutes is treated as idle (catches the case where Claude exits without firing the Stop hook).

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

- Replace `YOUR_SERVER_IP` with your status server address
- Set your WiFi credentials in ESPHome secrets
- The chip ID is read automatically (`ESP.getChipId()`) — no manual device ID needed

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
  - Running: raised eyebrows, open eyes with pupils, wide smile, pulsing antenna, activity rays
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

Each device hatches one of 10 buddy types, drawn entirely with ESPHome display primitives on the left 64×64 pixels. All types share running / idle / blink states.

```
# BOT (Common)          # CAT (Common)          # OWL (Uncommon)
    o*                    /\ /\                    /\_/\
   /|\                  (o . o)                  (O) (O)
  (◉ ◉)                  ~ ~ ~                     ^
   \U/                     w                     )   (

# GHOST (Rare)          # ALIEN (Epic)           # BEAR (Common)
  _____                 () ()                     (.) (.)
 /     \               /  _  \                   /     \
| o   o |             | (o o) |                 | . . . |
|       |             |   v   |                 | (U)   |
 \_/\_/               \_____/                    \_____/

# FOX (Rare)            # DRAGON (Legendary)     # BUNNY (Uncommon)
  /\ /\                  /  /                      | |
 (^   ^)               /____\                      \_/
  ---                 |= o o=|                    (^ ^)
 ( w )                |  ~~~  |                    w

# CRYSTAL (Mystical)
     /\
    /  \
   | ◆  |
    \  /
     \/
   (o o)
```

All drawing uses: `circle`, `filled_circle`, `filled_rectangle`, `line`, `printf`

Animation: 8-step frame counter (1s per tick at `update_interval: 1s`):
- Blink at frame 7 (every 8 seconds)
- Running: raised eyebrows/alert eyes, pulsing details every even frame
- Idle: droopy eyes, floating Zzz (BOT), swaying tail (CAT, FOX), ear wiggle (BUNNY), bobbing (GHOST)
- DRAGON: fire breath lines when running on even frames
- CRYSTAL: sparkle facet lines pulse when running
- Spinner/dots on right panel cycle through `frame % 4`
