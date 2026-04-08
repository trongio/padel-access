import logging
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from sqlmodel import Session, col, select

from app import config
from app.api.endpoints import codes, control, system
from app.api.limiter import limiter
from app.core.database import get_session
from app.core.models import AuditLog, AuditLogRead

logger = logging.getLogger(__name__)

_start_time = time.time()


# ─── Auth dependency ──────────────────────────────

async def verify_api_key(authorization: Optional[str] = Header(default=None)) -> None:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing API key")
    expected = f"Bearer {config.API_KEY}"
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


# ─── Build main router ───────────────────────────

api_router = APIRouter()

# Codes — requires auth
api_router.include_router(
    codes.router,
    prefix="/api/codes",
    tags=["codes"],
    dependencies=[Depends(verify_api_key)],
)

# Control — requires auth
api_router.include_router(
    control.router,
    prefix="/api/control",
    tags=["control"],
    dependencies=[Depends(verify_api_key)],
)

# System (settings, mode, reboot) — requires auth. Routes declare their full
# path internally because they straddle /api/settings and /api/system/*.
api_router.include_router(
    system.router,
    dependencies=[Depends(verify_api_key)],
)


# ─── Health (no auth) ────────────────────────────

@api_router.get("/api/health", tags=["system"])
@limiter.limit("60/minute")
def health(request: Request):
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _start_time),
    }


# ─── Audit logs (auth required) ──────────────────

@api_router.get("/api/logs", tags=["system"], response_model=list[AuditLogRead])
@limiter.limit("30/minute")
def get_logs(
    request: Request,
    limit: int = Query(default=50, le=500),
    event: Optional[str] = None,
    _auth: None = Depends(verify_api_key),
    session: Session = Depends(get_session),
):
    statement = select(AuditLog).order_by(col(AuditLog.timestamp).desc())
    if event:
        statement = statement.where(AuditLog.event == event)
    statement = statement.limit(limit)
    logs = session.exec(statement).all()
    return logs
