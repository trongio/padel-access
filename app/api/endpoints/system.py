"""System administration routes: settings, mode, reboot.

All routes here are mounted under no prefix because the user-facing paths
straddle two namespaces — the settings PATCH lives at `/api/settings` while
the rest live under `/api/system/*`. Declaring full paths inline keeps the
mount in `app/api/router.py` to a single line.
"""

import logging
import subprocess
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app import config
from app.api.limiter import limiter
from app.core import runtime_settings
from app.core.database import log_event
from app.core.models import (
    RebootRequest,
    SettingsRead,
    SettingsUpdate,
    SystemModeRead,
    SystemModeUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _current_settings() -> SettingsRead:
    """Snapshot the live values from `app.config` into the read schema."""
    return SettingsRead(
        door_unlock_duration=config.DOOR_UNLOCK_DURATION,
        mask_code_input=config.MASK_CODE_INPUT,
        buzzer_enabled=config.BUZZER_ENABLED,
        door_open_alarm_enabled=config.DOOR_OPEN_ALARM_ENABLED,
        door_open_alarm_seconds=config.DOOR_OPEN_ALARM_SECONDS,
        display_idle_text=config.DISPLAY_IDLE_TEXT,
        display_idle_subtext=config.DISPLAY_IDLE_SUBTEXT,
        app_lang=config.APP_LANG,
        log_level=config.LOG_LEVEL,
        code_length=config.CODE_LENGTH,
    )


# ─── Settings ─────────────────────────────────────


@router.get("/api/settings", response_model=SettingsRead, tags=["system"])
@limiter.limit("30/minute")
def get_settings(request: Request):
    return _current_settings()


@router.patch("/api/settings", response_model=SettingsRead, tags=["system"])
@limiter.limit("10/minute")
def update_settings(body: SettingsUpdate, request: Request):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _current_settings()

    changed: list[str] = []
    for key, value in updates.items():
        try:
            runtime_settings.apply_single(key, value, request.app.state)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        changed.append(key)

    # Audit: never include the API key or any code values. Booleans, ints,
    # display text and language are all safe to record.
    safe_pairs = [f"{k}={updates[k]!r}" for k in changed]
    log_event("SETTINGS_UPDATE", actor="api", details=", ".join(safe_pairs))
    logger.info("Settings updated: %s", ", ".join(changed))

    return _current_settings()


# ─── System mode ─────────────────────────────────


@router.get("/api/system/mode", response_model=SystemModeRead, tags=["system"])
@limiter.limit("30/minute")
def get_system_mode(request: Request):
    sm = getattr(request.app.state, "system_mode", None)
    if sm is None:
        raise HTTPException(status_code=503, detail="system mode controller not ready")
    return SystemModeRead(mode=sm.mode)


@router.post("/api/system/mode", response_model=SystemModeRead, tags=["system"])
@limiter.limit("10/minute")
def set_system_mode(body: SystemModeUpdate, request: Request):
    sm = getattr(request.app.state, "system_mode", None)
    if sm is None:
        raise HTTPException(status_code=503, detail="system mode controller not ready")
    try:
        sm.set_mode(body.mode, actor="api")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return SystemModeRead(mode=sm.mode)


# ─── Reboot ──────────────────────────────────────


def _reboot_worker() -> None:
    # Tiny delay so the HTTP response has a chance to flush before systemd
    # tears the process down.
    time.sleep(1.0)
    try:
        subprocess.Popen(["/sbin/reboot"])
    except FileNotFoundError:
        # Some distros put it under /usr/sbin — try the unqualified name as
        # a last resort. The systemd service runs as root so PATH lookups
        # for `reboot` should be fine.
        try:
            subprocess.Popen(["reboot"])
        except Exception:
            logger.exception("Failed to invoke reboot")
    except Exception:
        logger.exception("Failed to invoke reboot")


@router.post("/api/system/reboot", status_code=202, tags=["system"])
@limiter.limit("1/minute")
def reboot(body: RebootRequest, request: Request):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required")
    log_event("SYSTEM_REBOOT", actor="api", details="requested")
    logger.warning("Reboot requested via API")
    threading.Thread(target=_reboot_worker, daemon=True).start()
    return JSONResponse(status_code=202, content={"status": "rebooting"})
