# CLAUDE.md

Project-specific notes for Claude Code working on this repo. Read once per session, then trust the code over this file when they disagree.

## What this is

A Raspberry Pi 4 (Ubuntu Server 24, ARM64) access-control system for a padel
facility. Single Python process — `main.py` — that owns the GPIO hardware
**and** runs a FastAPI server in a daemon thread. There is no separate worker
process, no celery, no broker. APScheduler handles delayed light turn-offs and
persists its jobs to the same SQLite DB the app uses.

The systemd service runs as **root** from `/opt/padel-access` and is named
`padel-access`. The dev Pi at the user's site is at `192.168.0.119` (creds
in the user's notes, not here). Use `sshpass` for non-interactive ssh — it's
already installed locally.

## Where things live

- `main.py` — process entry: hardware init, keypad event loop, shutdown.
  All hardware globals (`_door_relay`, `_buzzer`, `_display`, `_light_manager`,
  `_keypad`, `_exit_button`, `_door_sensor`, `_system_mode`) are module-level
  and assigned inside `main()`.
- `app/config.py` — loaded once from `.env` at import time. Treated as a
  mutable namespace at runtime: `runtime_settings.apply_single` writes new
  values back into `config.X` after the PATCH endpoint validates them.
- `app/core/runtime_settings.py` — JSON overlay at `data/runtime_settings.json`.
  `apply_overrides()` runs at startup before hardware exists; `apply_single()`
  is the runtime path used by `PATCH /api/settings` and dispatches per-key
  side effects (buzzer toggle, log level, display refresh, language re-derive).
- `app/services/system_mode.py` — `SystemModeController` owns the operating
  mode (`normal` / `keypad_disabled` / `free`) and is the **single funnel**
  for door unlocks via `unlock_door()`. Every site that used to call
  `_door_relay.pulse(...)` now calls this so mode-aware suppression lives in
  one place.
- `app/api/endpoints/{codes,control,system}.py` — one file per route group.
  Routes are mounted by `app/api/router.py`.
- `app/api/limiter.py` — slowapi limiter; per-route `@limiter.limit("N/minute")`
  decorators are the rate-limit source of truth.
- `app/core/models.py` — SQLModel tables (`AccessCode`, `AuditLog`) and every
  Pydantic request/response schema. New schemas go here, not in the endpoint
  files.
- `app/core/database.py` — `engine`, `get_session` (FastAPI dep), `log_event`.
  All datetimes stored as **naive UTC**.

## Hard project conventions

1. **Never log a code's secret value.** `AuditLog.code` stores the access_code
   row id as a string, not the plaintext. The keypad failure path deliberately
   logs no code at all to avoid aiding brute force.
2. **Naive UTC everywhere.** Use `datetime.now(timezone.utc).replace(tzinfo=None)`
   or `to_naive_utc(...)` from `app/core/models.py`. Never mix tz-aware and
   naive in the same comparison.
3. **Soft delete.** `DELETE /api/codes/{id}` flips `is_active=False`, it does
   not remove the row. Auto-deactivation by use_count is also soft.
4. **Active-LOW relays.** Every relay (door, light 1, light 2) is active-LOW.
   `RelayController.on()` drives the pin LOW. Tests on the bench have already
   bitten people who flipped this.
5. **Door pulse funnel.** Do not call `_door_relay.pulse(...)` directly from
   new code — go through `_system_mode.unlock_door(actor=...)` so free mode
   is honored. The keypad, exit button, and `POST /api/control/door` already
   route through it.
6. **Free mode invariants.** When mode is `free`: door relay is held energized,
   both light relays are held energized with **no APScheduler jobs**, the
   door-open alarm is suppressed, the keypad is silent, and the buzzer
   chirps are skipped. `LightManager.turn_on_indefinite_all()` is the
   sanctioned way to energize lights without scheduling a turn-off.
7. **Settings extras forbidden.** `SettingsUpdate.model_config["extra"] =
   "forbid"`. Do not relax this — silently dropping unknown keys hid a
   `system_mode` smuggle bug during testing.

## Running and testing

- Local dev box does **not** have `RPi.GPIO`, `luma.oled`, etc. Anything that
  imports from `app/hardware/*` will fail outside the Pi. For smoke tests of
  pure-Python modules (`models.py`, `runtime_settings.py`, `system_mode.py`,
  `api/endpoints/codes.py`), spin up a temporary venv:
  ```bash
  python3 -m venv /tmp/smoke && /tmp/smoke/bin/pip install -q pydantic sqlmodel fastapi python-dotenv slowapi
  ```
  Validate everything you can locally before deploying.

- **End-to-end testing** must hit the real Pi:
  ```bash
  sshpass -p '<PASS>' ssh azael@192.168.0.119 '...'
  curl -H "Authorization: Bearer $API_KEY" http://192.168.0.119:8000/api/...
  ```
  The API key lives in `/opt/padel-access/.env` (root-readable).

- **Deployment** (the install at `/opt/padel-access` is a stale `git status`
  with door-sensor changes applied directly — do not assume `git pull` will
  work cleanly there). Stage to `/tmp/deploy/...` over scp, then sudo-cp into
  `/opt/padel-access`, then `systemctl restart padel-access`. Always snapshot
  with `cp -a /opt/padel-access /opt/padel-access.backup-$(date +%Y%m%d-%H%M%S)`
  before overwriting.

- After `systemctl restart padel-access`, uvicorn needs ~1s to bind. Poll
  `/api/health` rather than sleeping a fixed amount.

## Gotchas worth remembering

- `Buzzer.__init__` skips `GPIO.setup` when `enabled=False`. If you later flip
  it on, you must call `Buzzer.set_enabled(True)` (which lazily configures
  the pin) — do not poke `_enabled` directly.
- `FastAPI` matches routes in declaration order. `@router.get("/with-status")`
  **must** appear before `@router.get("/{code_id}")` in `codes.py` or path
  capture eats it. There is a load-bearing comment about this above the route.
- `APScheduler` job IDs for lights are `light_off_<id>`. `restore_light_jobs`
  on startup re-runs any jobs whose `run_date` is in the past, which is how
  in-progress bookings survive a reboot. Free mode deliberately removes these
  jobs when entering, and does not recreate them on leaving — the lights just
  go off.
- `runtime_settings.json` and `padel_access.db` both live in `data/`. The
  systemd service runs from `/opt/padel-access`, so the live paths are
  `/opt/padel-access/data/*`.
- The Postman collection (`Padel_Access_API.postman_collection.json`) is the
  user-facing API spec. Keep it in sync when adding routes.

## Style

- Match the existing terse, comment-light style. Add a comment **only** when
  the next reader would otherwise have to reverse-engineer *why* (e.g. the
  free-mode pulse suppression, the route ordering trap). Line counts matter
  less than the load-bearing reasoning being captured at the right spot.
- Imperative commit messages with a `subsystem:` prefix matching the area
  touched (`api:`, `display:`, `buzzer:`, `door-sensor:`, `init:`). See
  `git log --oneline` for the pattern.
- The user prefers small, focused PRs/commits. When a change spans multiple
  subsystems (this happened with the settings/mode/reboot rollout), one
  commit covering the whole vertical slice is fine — call out the
  cross-cutting nature in the commit body.
