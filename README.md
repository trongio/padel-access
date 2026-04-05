# Padel Facility Access Control System

Raspberry Pi 4 (Ubuntu Server 24) based access control for a padel facility. Controls a door lock (12V relay) and lights (220V relays) via keypad input and REST API, exposed publicly via Cloudflare Tunnel.

**Repository:** https://github.com/trongio/padel-access.git

## Features

- 4x4 keypad code entry with OLED display feedback
- Door lock relay (12V) with configurable unlock duration
- 2 light zone relays (220V) with scheduled auto-off
- One-time and multi-use access codes
- Auto-generate random codes for booking integration
- REST API for remote control and code management
- Cloudflare Tunnel for secure public access
- Audit logging for all door and light events
- Graceful hardware degradation (runs API-only without hardware)

## Hardware Wiring

| # | Item | Connection | Purpose |
|---|------|------------|---------|
| 1 | 1-Ch 5V Relay Module (AR0310) | GPIO 17 | Door lock — 12V |
| 2 | 12-Ch 5V Relay Module — Relay 1 | GPIO 27 | Light zone 1 — 220V |
| 3 | 12-Ch 5V Relay Module — Relay 2 | GPIO 22 | Light zone 2 — 220V |
| 4 | 4x4 Matrix Keypad | GPIO 5,6,13,19 (rows) / 12,16,20,21 (cols) | Code input |
| 5 | 0.96" OLED I2C SSD1306 (128x64) | I2C SDA=GPIO2, SCL=GPIO3 | Display |
| 6 | Exit Button (NO momentary) | GPIO 26 (pull-up) | Door release from inside |
| 7 | Active Buzzer (5V) | GPIO 24 | Audio feedback |

> All relay modules are **active-LOW** (GPIO LOW = relay ON).

### Buzzer Wiring

```
GPIO 24 → 1kΩ resistor → NPN transistor base (2N2222/BC547)
                          collector → buzzer negative
                          buzzer positive → 5V
                          emitter → GND
```

GPIO HIGH = buzzer ON.

## Installation

```bash
# 1. Clone to Pi
git clone https://github.com/trongio/padel-access.git /opt/padel-access
cd /opt/padel-access

# 2. Run interactive setup (installs deps, I2C, .env wizard, systemd)
sudo bash scripts/init.sh

# 3. Reboot if I2C was just enabled
sudo reboot

# 4. Verify I2C (should show 3c for the OLED)
i2cdetect -y 1

# 5. Start
sudo bash scripts/start.sh

# 6. Check status
sudo bash scripts/status.sh
```

## Management

```bash
sudo bash scripts/start.sh     # Start and enable services
sudo bash scripts/stop.sh      # Stop and disable services
sudo bash scripts/restart.sh   # Restart both services
sudo bash scripts/status.sh    # Show status + tunnel URL + health
```

## API Usage

All endpoints (except `/api/health`) require: `Authorization: Bearer <API_KEY>`

A [Postman collection](Padel_Access_API.postman_collection.json) is included for easy API testing and client handoff.

### Health Check

```bash
curl https://padel.hackerman.ge/api/health
```

### Create Access Code

```bash
curl -X POST https://padel.hackerman.ge/api/codes \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "1234",
    "light_ids": [1, 2],
    "valid_from": "2026-04-05T08:00:00",
    "valid_until": "2026-04-05T22:00:00",
    "label": "Court 1 - John",
    "max_uses": null
  }'
```

Set `max_uses` to `1` for a one-time code, or `null` for unlimited uses.

### Generate Random Code

```bash
curl -X POST https://padel.hackerman.ge/api/codes/generate \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "light_ids": [1, 2],
    "valid_from": "2026-04-05T08:00:00",
    "valid_until": "2026-04-05T22:00:00",
    "label": "Walk-in Customer",
    "max_uses": 1,
    "code_length": 6
  }'
```

Returns the generated code in the response. Ideal for booking system integration.

### List Codes

```bash
curl https://padel.hackerman.ge/api/codes?active_only=true \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Remote Door Unlock

```bash
curl -X POST https://padel.hackerman.ge/api/control/door \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Remote Light Control

```bash
# Turn on (until = auto-off time)
curl -X POST https://padel.hackerman.ge/api/control/lights \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"light_ids": [1, 2], "action": "on", "until": "2026-04-05T22:00:00"}'

# Turn off specific zones
curl -X POST https://padel.hackerman.ge/api/control/lights \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"light_ids": [1], "action": "off"}'

# Emergency: turn off all lights
curl -X POST https://padel.hackerman.ge/api/control/lights \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": "off_all"}'
```

### Relay Status

```bash
curl https://padel.hackerman.ge/api/control/status \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Audit Logs

```bash
curl "https://padel.hackerman.ge/api/logs?limit=50&event=DOOR_OPEN" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

**Event types:** `DOOR_OPEN`, `LIGHT_ON`, `LIGHT_OFF`, `CODE_FAIL`, `REMOTE_DOOR`, `REMOTE_LIGHT`

## Project Structure

```
padel-access/
├── main.py                  # Entry point: startup, keypad flow, shutdown
├── app/
│   ├── config.py            # Environment config loader
│   ├── api/
│   │   ├── router.py        # FastAPI router, auth, health, logs
│   │   └── endpoints/
│   │       ├── codes.py     # Codes CRUD + generate
│   │       └── control.py   # Door/light remote control
│   ├── core/
│   │   ├── database.py      # SQLite engine, sessions, migrations
│   │   ├── models.py        # SQLModel tables + Pydantic schemas
│   │   └── scheduler.py     # APScheduler with job persistence
│   ├── hardware/
│   │   ├── relay.py         # Thread-safe GPIO relay controller
│   │   ├── keypad.py        # 4x4 matrix keypad (pad4pi)
│   │   ├── display.py       # OLED SSD1306 queue-based display
│   │   ├── buzzer.py        # Active buzzer with beep patterns
│   │   └── button.py        # Exit button with edge detection
│   └── services/
│       ├── access.py        # Code validation + use tracking
│       └── light_manager.py # Light zones with auto-off scheduling
├── scripts/                 # init, start, stop, restart, status
├── systemd/                 # padel-access.service, padel-tunnel.service
├── Padel_Access_API.postman_collection.json
├── .env.example
└── requirements.txt
```

## Environment Variables

See [`.env.example`](.env.example) for all configuration options.

Key settings:
- `API_KEY` — Bearer token for API authentication
- `TZ` — Timezone for display (e.g. `Asia/Tbilisi`)
- `DOOR_UNLOCK_DURATION` — Seconds to keep door unlocked (default: 5)
- `CF_TUNNEL_TOKEN` — Cloudflare Tunnel token for public access

## Keypad Operation

1. Enter code digits on keypad (shown as dots on display)
2. Press `#` to submit
3. Press `*` to clear input
4. On success: door unlocks, lights turn on, display shows "Access Granted"
5. On failure: buzzer error tone, display shows error message
6. Input auto-clears after 15 seconds of inactivity
7. One-time codes auto-deactivate after use

## Logs

```bash
# Application logs
journalctl -u padel-access -f

# Tunnel logs
journalctl -u padel-tunnel -f
```
