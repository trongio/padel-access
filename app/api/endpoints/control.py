import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import config
from app.api.limiter import limiter
from app.core.database import log_event

logger = logging.getLogger(__name__)
router = APIRouter()


class LightControlRequest(BaseModel):
    light_ids: list[int] = []
    action: str  # "on" | "off" | "off_all"
    until: Optional[datetime] = None


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
    elif body.action == "off_all":
        light_manager.turn_off_all()
        log_event("REMOTE_LIGHT", actor="api", details="off_all")
    else:
        raise HTTPException(status_code=400, detail="action must be 'on', 'off', or 'off_all'")

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
