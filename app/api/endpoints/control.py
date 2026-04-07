import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app import config
from app.api.limiter import limiter
from app.core.database import log_event
from app.core.models import to_naive_utc

logger = logging.getLogger(__name__)
router = APIRouter()

# Maximum lookahead for the `until` parameter on light control. A typo
# (e.g. wrong year) shouldn't be able to leave the lights on for years.
_MAX_UNTIL_DAYS = 30
_VALID_ACTIONS = {"on", "off", "off_all"}


class LightControlRequest(BaseModel):
    light_ids: list[int] = []
    action: str  # "on" | "off" | "off_all"
    until: Optional[datetime] = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in _VALID_ACTIONS:
            raise ValueError(f"action must be one of: {sorted(_VALID_ACTIONS)}")
        return v

    @field_validator("light_ids")
    @classmethod
    def validate_light_ids(cls, v: list[int]) -> list[int]:
        if len(v) > 10:
            raise ValueError("light_ids must have 10 or fewer entries")
        for lid in v:
            if lid < 1 or lid > 10:
                raise ValueError(f"light_id must be between 1 and 10, got {lid}")
        return v

    @field_validator("until")
    @classmethod
    def validate_until(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        v = to_naive_utc(v)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if v <= now:
            raise ValueError("'until' must be in the future")
        if v - now > timedelta(days=_MAX_UNTIL_DAYS):
            raise ValueError(f"'until' must be within {_MAX_UNTIL_DAYS} days from now")
        return v


@router.post("/door")
@limiter.limit("5/minute")
def remote_door(request: Request):
    door_relay = request.app.state.door_relay
    buzzer = request.app.state.buzzer
    duration = config.DOOR_UNLOCK_DURATION

    door_relay.pulse(duration)
    buzzer.beep_success()
    log_event("REMOTE_DOOR", actor="api", details=f"duration={duration}s")

    logger.info("Remote door unlock triggered")
    return {"status": "unlocked", "duration": duration}


@router.post("/lights")
@limiter.limit("20/minute")
def remote_lights(request: Request, body: LightControlRequest):
    light_manager = request.app.state.light_manager

    if body.action == "on":
        if body.until is None:
            raise HTTPException(status_code=400, detail="'until' is required when action is 'on'")
        for lid in body.light_ids:
            light_manager.turn_on(lid, body.until)
        log_event(
            "REMOTE_LIGHT",
            light_ids=json.dumps(body.light_ids),
            actor="api",
            details=f"on until {body.until}",
        )
    elif body.action == "off":
        for lid in body.light_ids:
            light_manager.turn_off(lid)
        log_event(
            "REMOTE_LIGHT",
            light_ids=json.dumps(body.light_ids),
            actor="api",
            details="off",
        )
    else:  # "off_all" — validated by the model
        light_manager.turn_off_all()
        log_event("REMOTE_LIGHT", actor="api", details="off_all")

    status = light_manager.get_status()
    # Serialize datetimes in status
    lights_resp = {}
    for lid, info in status.items():
        lights_resp[str(lid)] = {
            "on": info["on"],
            "until": info["until"].isoformat() if info["until"] else None,
        }

    return {"status": "ok", "lights": lights_resp}


@router.get("/status")
def relay_status(request: Request):
    door_relay = request.app.state.door_relay
    light_manager = request.app.state.light_manager

    light_status = light_manager.get_status()
    lights_resp = {}
    for lid, info in light_status.items():
        lights_resp[str(lid)] = {
            "on": info["on"],
            "until": info["until"].isoformat() if info["until"] else None,
        }

    return {
        "door": {"locked": not door_relay.is_on()},
        "lights": lights_resp,
    }
