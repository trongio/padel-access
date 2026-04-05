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


def _migrate_db() -> None:
    """Add columns that may be missing from older database versions."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(access_code)")
    columns = {row[1] for row in cursor.fetchall()}
    if "max_uses" not in columns:
        cursor.execute("ALTER TABLE access_code ADD COLUMN max_uses INTEGER DEFAULT NULL")
        logger.info("Migration: added max_uses column")
    if "use_count" not in columns:
        cursor.execute("ALTER TABLE access_code ADD COLUMN use_count INTEGER DEFAULT 0 NOT NULL")
        logger.info("Migration: added use_count column")
    conn.commit()
    conn.close()


def init_db() -> None:
    """Create all tables if they don't exist."""
    SQLModel.metadata.create_all(engine)
    _migrate_db()
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
