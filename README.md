# Padel Facility Access Control System

Raspberry Pi 4 (Ubuntu Server 24) based access control for a padel facility. Controls a door lock (12V relay) and lights (220V relays) via keypad input and REST API, exposed publicly via Cloudflare Tunnel.

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
git clone <repo> /opt/padel-access
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

### Health Check

```bash
curl http://localhost:8000/api/health
```

### Create Access Code

```bash
curl -X POST http://localhost:8000/api/codes \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "1234",
    "light_ids": [1, 2],
    "valid_from": "2025-06-01T08:00:00",
    "valid_until": "2025-06-01T22:00:00",
    "label": "Court 1 - John"
  }'
```

### List Codes

```bash
curl http://localhost:8000/api/codes?active_only=true \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Remote Door Unlock

```bash
curl -X POST http://localhost:8000/api/control/door \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Remote Light Control

```bash
# Turn on
curl -X POST http://localhost:8000/api/control/lights \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"light_ids": [1], "action": "on", "until": "2025-06-01T22:00:00"}'

# Turn off
curl -X POST http://localhost:8000/api/control/lights \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"light_ids": [1], "action": "off"}'
```

### Relay Status

```bash
curl http://localhost:8000/api/control/status \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Audit Logs

```bash
curl "http://localhost:8000/api/logs?limit=50&event=DOOR_OPEN" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Environment Variables

See [`.env.example`](.env.example) for all configuration options.

## Keypad Operation

1. Enter code digits on keypad (shown as dots on display)
2. Press `#` to submit
3. Press `*` to clear input
4. On success: door unlocks, lights turn on, display shows "Access Granted"
5. On failure: buzzer error tone, display shows error message
6. Input auto-clears after 15 seconds of inactivity

## Logs

```bash
# Application logs
journalctl -u padel-access -f

# Tunnel logs
journalctl -u padel-tunnel -f
```
