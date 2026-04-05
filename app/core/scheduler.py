import logging
from datetime import datetime, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.core.database import engine
from app.core.models import AccessCode

logger = logging.getLogger(__name__)


def create_scheduler(db_url: str) -> BackgroundScheduler:
    """Create a BackgroundScheduler with SQLAlchemy-backed job store."""
    jobstores = {"default": SQLAlchemyJobStore(url=db_url)}
    scheduler = BackgroundScheduler(jobstores=jobstores)
    logger.info("Scheduler created with SQLAlchemy job store")
    return scheduler


def restore_light_jobs(scheduler: BackgroundScheduler, light_manager) -> None:
    """On startup, re-schedule turn-off jobs for all active codes still in their validity window."""
    now = datetime.now(timezone.utc)
    count = 0

    with Session(engine) as session:
        statement = select(AccessCode).where(
            AccessCode.is_active == True,  # noqa: E712
            AccessCode.valid_until > now,
        )
        codes = session.exec(statement).all()

        for code in codes:
            for light_id in code.light_ids_list:
                light_manager.turn_on(light_id, code.valid_until)
                count += 1

    logger.info("Restored %d light jobs from %d active codes", count, len(codes))
