import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.core.database import get_session, log_event
from app.core.models import AccessCode, AccessCodeCreate, AccessCodeGenerate, AccessCodeRead, AccessCodeUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


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


def _generate_unique_code(session: Session, length: int = 6) -> str:
    """Generate a random numeric code that doesn't exist in the database."""
    for _ in range(100):
        code = "".join([str(secrets.randbelow(10)) for _ in range(length)])
        existing = session.exec(select(AccessCode).where(AccessCode.code == code)).first()
        if not existing:
            return code
    raise HTTPException(status_code=500, detail="Could not generate unique code")


@router.post("", status_code=201, response_model=AccessCodeRead)
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

    # If code is already in its validity window, activate lights now
    now = datetime.now(timezone.utc)
    if ac.valid_from <= now < ac.valid_until:
        light_manager = request.app.state.light_manager
        for lid in body.light_ids:
            light_manager.turn_on(lid, ac.valid_until)

    logger.info("Created access code id=%d label=%s", ac.id, ac.label)
    return _to_read(ac)


@router.post("/generate", status_code=201, response_model=AccessCodeRead)
def generate_code(
    body: AccessCodeGenerate,
    request: Request,
    session: Session = Depends(get_session),
):
    code = _generate_unique_code(session, body.code_length)

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

    now = datetime.now(timezone.utc)
    if ac.valid_from <= now < ac.valid_until:
        light_manager = request.app.state.light_manager
        for lid in body.light_ids:
            light_manager.turn_on(lid, ac.valid_until)

    logger.info("Generated access code id=%d code=%s label=%s", ac.id, code, ac.label)
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
    if "light_ids" in update_data:
        update_data["light_ids"] = json.dumps(update_data["light_ids"])

    for key, value in update_data.items():
        setattr(ac, key, value)

    session.add(ac)
    session.commit()
    session.refresh(ac)

    # Reschedule light jobs if valid_until changed
    if "valid_until" in update_data and ac.is_active:
        light_manager = request.app.state.light_manager
        now = datetime.now(timezone.utc)
        if ac.valid_from <= now < ac.valid_until:
            for lid in ac.light_ids_list:
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

    # Cancel any pending light jobs for this code's lights
    scheduler = request.app.state.scheduler
    for lid in ac.light_ids_list:
        job_id = f"light_off_{lid}"
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

    log_event("LIGHT_OFF", code=ac.code, light_ids=ac.light_ids, actor="api", details="code deactivated")
    logger.info("Deactivated access code id=%d", ac.id)
    return _to_read(ac)
