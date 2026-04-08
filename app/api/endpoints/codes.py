import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from app import config
from app.api.limiter import limiter
from app.core.database import get_session, log_event
from app.core.models import (
    AccessCode,
    AccessCodeCreate,
    AccessCodeGenerate,
    AccessCodeRead,
    AccessCodeStatus,
    AccessCodeUpdate,
    AccessCodeWithStatus,
)


logger = logging.getLogger(__name__)
router = APIRouter()


def _utcnow() -> datetime:
    """Return current UTC time as naive datetime (matches SQLite storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CodeCheckRequest(BaseModel):
    code: str


def _to_read(ac: AccessCode) -> AccessCodeRead:
    return AccessCodeRead(
        id=ac.id,
        code=ac.code,
        light_ids=ac.light_ids_list,
        valid_from=ac.valid_from,
        valid_until=ac.valid_until,
        label=ac.label,
        max_uses=ac.max_uses,
        use_count=ac.use_count,
        is_active=ac.is_active,
        created_at=ac.created_at,
    )


def _compute_status(ac: AccessCode, now: datetime) -> tuple[str, "int | None"]:
    """Derive a code's status string and remaining uses from its row state."""
    if not ac.is_active:
        if ac.max_uses is not None and ac.use_count >= ac.max_uses:
            return "used", 0
        return "inactive", None
    if now < ac.valid_from:
        status = "not_yet_valid"
    elif now > ac.valid_until:
        status = "expired"
    else:
        status = "active"
    uses_remaining = None
    if ac.max_uses is not None:
        uses_remaining = max(0, ac.max_uses - ac.use_count)
    return status, uses_remaining


def _generate_unique_code(session: Session, length: int = 6) -> str:
    """Generate a random numeric code that doesn't exist in the database."""
    for _ in range(100):
        code = "".join([str(secrets.randbelow(10)) for _ in range(length)])
        existing = session.exec(select(AccessCode).where(AccessCode.code == code)).first()
        if not existing:
            return code
    raise HTTPException(status_code=500, detail="Could not generate unique code")


@router.post("", status_code=201, response_model=AccessCodeRead)
@limiter.limit("20/minute")
def create_code(
    body: AccessCodeCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    # Check uniqueness
    existing = session.exec(
        select(AccessCode).where(AccessCode.code == body.code)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Code already exists")

    ac = AccessCode(
        code=body.code,
        light_ids=json.dumps(body.light_ids),
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        label=body.label,
        max_uses=body.max_uses,
    )
    session.add(ac)
    session.commit()
    session.refresh(ac)

    logger.info("Created access code id=%d label=%s", ac.id, ac.label)
    return _to_read(ac)


@router.post("/generate", status_code=201, response_model=AccessCodeRead)
@limiter.limit("20/minute")
def generate_code(
    body: AccessCodeGenerate,
    request: Request,
    session: Session = Depends(get_session),
):
    length = body.code_length if body.code_length is not None else config.CODE_LENGTH
    code = _generate_unique_code(session, length)

    ac = AccessCode(
        code=code,
        light_ids=json.dumps(body.light_ids),
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        label=body.label,
        max_uses=body.max_uses,
    )
    session.add(ac)
    session.commit()
    session.refresh(ac)

    logger.info("Generated access code id=%d label=%s", ac.id, ac.label)
    return _to_read(ac)


@router.get("", response_model=list[AccessCodeRead])
def list_codes(
    active_only: bool = False,
    session: Session = Depends(get_session),
):
    statement = select(AccessCode)
    if active_only:
        statement = statement.where(AccessCode.is_active == True)  # noqa: E712
    codes = session.exec(statement).all()
    return [_to_read(ac) for ac in codes]


# IMPORTANT: this route MUST be declared before `get_code` (`/{code_id}`)
# below — otherwise FastAPI matches the path parameter first and tries to
# parse "with-status" as an integer code id.
@router.get("/with-status", response_model=list[AccessCodeWithStatus])
@limiter.limit("30/minute")
def list_codes_with_status(
    request: Request,
    active_only: bool = False,
    session: Session = Depends(get_session),
):
    """List every code with its derived status and uses_remaining.

    Computed in one round-trip for the operator dashboard. `active_only`
    filters by the `is_active` flag (a soft-delete marker), not by the
    derived status — keep parity with `list_codes`.
    """
    statement = select(AccessCode)
    if active_only:
        statement = statement.where(AccessCode.is_active == True)  # noqa: E712
    now = _utcnow()
    out: list[AccessCodeWithStatus] = []
    for ac in session.exec(statement).all():
        status, remaining = _compute_status(ac, now)
        out.append(
            AccessCodeWithStatus(
                id=ac.id,
                code=ac.code,
                status=status,
                label=ac.label,
                light_ids=ac.light_ids_list,
                valid_from=ac.valid_from,
                valid_until=ac.valid_until,
                created_at=ac.created_at,
                max_uses=ac.max_uses,
                use_count=ac.use_count,
                uses_remaining=remaining,
                is_active=ac.is_active,
            )
        )
    return out


@router.post("/check", response_model=AccessCodeStatus)
@limiter.limit("10/minute")
def check_code(body: CodeCheckRequest, request: Request, session: Session = Depends(get_session)):
    """Check the status of a code.

    Uses POST + body so the code never appears in URL paths or access logs.
    """
    code = body.code
    ac = session.exec(select(AccessCode).where(AccessCode.code == code)).first()
    if not ac:
        return AccessCodeStatus(code=code, status="not_found")

    status, uses_remaining = _compute_status(ac, _utcnow())

    return AccessCodeStatus(
        code=ac.code,
        status=status,
        label=ac.label,
        light_ids=ac.light_ids_list,
        valid_from=ac.valid_from,
        valid_until=ac.valid_until,
        max_uses=ac.max_uses,
        use_count=ac.use_count,
        uses_remaining=uses_remaining,
    )


@router.get("/{code_id}", response_model=AccessCodeRead)
def get_code(code_id: int, session: Session = Depends(get_session)):
    ac = session.get(AccessCode, code_id)
    if not ac:
        raise HTTPException(status_code=404, detail="Code not found")
    return _to_read(ac)


@router.patch("/{code_id}", response_model=AccessCodeRead)
def update_code(
    code_id: int,
    body: AccessCodeUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    ac = session.get(AccessCode, code_id)
    if not ac:
        raise HTTPException(status_code=404, detail="Code not found")

    update_data = body.model_dump(exclude_unset=True)

    # If renaming the code, ensure the new value isn't already taken.
    if "code" in update_data and update_data["code"] != ac.code:
        clash = session.exec(
            select(AccessCode).where(AccessCode.code == update_data["code"])
        ).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail="Code already exists")

    if "light_ids" in update_data:
        update_data["light_ids"] = json.dumps(update_data["light_ids"])

    for key, value in update_data.items():
        setattr(ac, key, value)

    session.add(ac)
    session.commit()
    session.refresh(ac)

    # If valid_until changed AND a light is currently on for this code (i.e.
    # the keypad already activated it), reschedule its existing turn-off job
    # to the new time. Do NOT force the light on if it isn't already on —
    # creating/editing a code must never light up the room by itself.
    if "valid_until" in update_data and ac.is_active:
        light_manager = request.app.state.light_manager
        for lid in ac.light_ids_list:
            status = light_manager.get_status().get(lid)
            if status and status["on"]:
                light_manager.turn_on(lid, ac.valid_until)

    logger.info("Updated access code id=%d", ac.id)
    return _to_read(ac)


@router.delete("/{code_id}", response_model=AccessCodeRead)
def delete_code(
    code_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    ac = session.get(AccessCode, code_id)
    if not ac:
        raise HTTPException(status_code=404, detail="Code not found")

    ac.is_active = False
    session.add(ac)
    session.commit()
    session.refresh(ac)

    # Actually turn the lights off (LightManager.turn_off also cancels its
    # auto-off scheduler job, so we don't need to do that separately).
    light_manager = request.app.state.light_manager
    for lid in ac.light_ids_list:
        light_manager.turn_off(lid)

    # NOTE: code id, not the secret value.
    log_event(
        "LIGHT_OFF",
        code=str(ac.id),
        light_ids=ac.light_ids,
        actor="api",
        details="code deactivated",
    )
    logger.info("Deactivated access code id=%d", ac.id)
    return _to_read(ac)
