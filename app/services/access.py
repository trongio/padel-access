import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlmodel import Session, select

from app import config
from app.core.database import engine
from app.core.models import AccessCode

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    success: bool
    code_id: Optional[int] = None
    light_ids: list[int] = field(default_factory=list)
    valid_until: Optional[datetime] = None
    reason: Optional[str] = None


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_code(code: str) -> ValidationResult:
    """Validate an access code against the database.

    Uses optimistic locking on `use_count` to prevent a one-time code from
    being redeemed twice by concurrent callers (e.g., keypad + API).
    """
    with Session(engine) as session:
        access_code = session.exec(
            select(AccessCode).where(
                AccessCode.code == code,
                AccessCode.is_active == True,  # noqa: E712
            )
        ).first()

        if access_code is None:
            logger.info("Code validation failed: invalid code")
            return ValidationResult(success=False, reason=config.LANG["invalid_code"])

        now = _utcnow_naive()

        if now < access_code.valid_from:
            logger.info("Code validation failed: not yet valid")
            return ValidationResult(success=False, reason=config.LANG["code_not_valid"])

        if now > access_code.valid_until:
            logger.info("Code validation failed: expired")
            return ValidationResult(success=False, reason=config.LANG["code_expired"])

        if access_code.max_uses is not None and access_code.use_count >= access_code.max_uses:
            logger.info("Code validation failed: max uses reached")
            return ValidationResult(success=False, reason=config.LANG["code_used"])

        # Atomic increment with optimistic concurrency control:
        # only succeed if use_count and is_active have not changed since the
        # SELECT above. This blocks concurrent double-redeem of one-time codes.
        prior_use_count = access_code.use_count
        new_use_count = prior_use_count + 1
        new_active = not (
            access_code.max_uses is not None and new_use_count >= access_code.max_uses
        )

        result = session.execute(
            update(AccessCode)
            .where(
                AccessCode.id == access_code.id,
                AccessCode.use_count == prior_use_count,
                AccessCode.is_active == True,  # noqa: E712
            )
            .values(use_count=new_use_count, is_active=new_active)
        )
        session.commit()

        if result.rowcount == 0:
            # Lost the optimistic-lock race — another caller already redeemed.
            logger.warning("Code validation lost optimistic lock race for id=%d", access_code.id)
            return ValidationResult(success=False, reason=config.LANG["code_used"])

        if not new_active:
            logger.info("Code auto-deactivated after %d uses", new_use_count)
        logger.info(
            "Code validated successfully (id=%d, label=%s, use %d/%s)",
            access_code.id, access_code.label, new_use_count,
            access_code.max_uses or "unlimited",
        )
        return ValidationResult(
            success=True,
            code_id=access_code.id,
            light_ids=access_code.light_ids_list,
            valid_until=access_code.valid_until,
        )
