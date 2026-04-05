import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.core.database import engine
from app.core.models import AccessCode

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    success: bool
    light_ids: list[int] = field(default_factory=list)
    valid_until: Optional[datetime] = None
    reason: Optional[str] = None


def validate_code(code: str) -> ValidationResult:
    """Validate an access code against the database."""
    with Session(engine) as session:
        statement = select(AccessCode).where(
            AccessCode.code == code,
            AccessCode.is_active == True,  # noqa: E712
        )
        access_code = session.exec(statement).first()

        if access_code is None:
            logger.info("Code validation failed: invalid code")
            return ValidationResult(success=False, reason="Invalid code")

        now = datetime.now(timezone.utc)

        if now < access_code.valid_from:
            logger.info("Code validation failed: not yet valid")
            return ValidationResult(success=False, reason="Code not yet valid")

        if now > access_code.valid_until:
            logger.info("Code validation failed: expired")
            return ValidationResult(success=False, reason="Code expired")

        logger.info("Code validated successfully (label=%s)", access_code.label)
        return ValidationResult(
            success=True,
            light_ids=access_code.light_ids_list,
            valid_until=access_code.valid_until,
        )
