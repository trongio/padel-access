import logging
from datetime import datetime, timezone
from typing import Generator, Optional

from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_URL
from app.core.models import AuditLog

logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create all tables if they don't exist."""
    SQLModel.metadata.create_all(engine)
    logger.info("Database initialized at %s", DATABASE_URL)


def get_session() -> Generator[Session, None, None]:
    """Yield a database session (FastAPI dependency)."""
    with Session(engine) as session:
        yield session


def log_event(
    event: str,
    code: Optional[str] = None,
    light_ids: Optional[str] = None,
    actor: str = "system",
    details: Optional[str] = None,
) -> None:
    """Write an audit log entry."""
    entry = AuditLog(
        event=event,
        code=code,
        light_ids=light_ids,
        actor=actor,
        timestamp=datetime.now(timezone.utc),
        details=details,
    )
    with Session(engine) as session:
        session.add(entry)
        session.commit()
    logger.debug("Audit: %s actor=%s code=%s", event, actor, code)
