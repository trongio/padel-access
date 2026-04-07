import logging
from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.core.database import engine
from app.core.models import AccessCode, AuditLog

LIGHT_OFF_JOB_PREFIX = "light_off_"

logger = logging.getLogger(__name__)

# Retention periods
CODE_RETENTION_DAYS = 30
LOG_RETENTION_DAYS = 90


def _utcnow_naive() -> datetime:
    """All stored datetimes are naive UTC — keep comparisons consistent."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_scheduler(db_url: str) -> BackgroundScheduler:
    """Create a BackgroundScheduler with SQLAlchemy-backed job store."""
    jobstores = {"default": SQLAlchemyJobStore(url=db_url)}
    scheduler = BackgroundScheduler(jobstores=jobstores)
    logger.info("Scheduler created with SQLAlchemy job store")
    return scheduler


def restore_light_jobs(scheduler: BackgroundScheduler, light_manager) -> None:
    """
    On startup, re-enable lights that were actively in use when the service
    stopped. We detect this via persisted `light_off_<id>` jobs in the
    APScheduler store: each one represents a light that the keypad turned on
    and that has not yet reached its scheduled auto-off time.

    A code being merely active and within its validity window does NOT cause
    its lights to come on — that only happens when a client enters the code
    on the keypad.
    """
    count = 0
    for job in scheduler.get_jobs():
        if not job.id.startswith(LIGHT_OFF_JOB_PREFIX):
            continue
        try:
            light_id = int(job.id[len(LIGHT_OFF_JOB_PREFIX):])
        except ValueError:
            logger.warning("Skipping malformed light job id %r", job.id)
            continue
        if job.next_run_time is None:
            continue
        light_manager.turn_on(light_id, job.next_run_time)
        count += 1

    logger.info("Restored %d in-progress light(s) from scheduler store", count)


def cleanup_old_data() -> None:
    """Delete expired/deactivated codes and old audit logs past retention."""
    now = _utcnow_naive()
    code_cutoff = now - timedelta(days=CODE_RETENTION_DAYS)
    log_cutoff = now - timedelta(days=LOG_RETENTION_DAYS)

    with Session(engine) as session:
        # Auto-deactivate expired codes
        expired = session.exec(
            select(AccessCode).where(
                AccessCode.is_active == True,  # noqa: E712
                AccessCode.valid_until < now,
            )
        ).all()
        for code in expired:
            code.is_active = False
            session.add(code)
        if expired:
            logger.info("Auto-deactivated %d expired codes", len(expired))

        # Delete old inactive codes (expired or deactivated > 30 days ago)
        old_codes = session.exec(
            select(AccessCode).where(
                AccessCode.is_active == False,  # noqa: E712
                AccessCode.valid_until < code_cutoff,
            )
        ).all()
        for code in old_codes:
            session.delete(code)
        if old_codes:
            logger.info("Deleted %d old inactive codes (>%d days)", len(old_codes), CODE_RETENTION_DAYS)

        # Delete old audit logs
        old_logs = session.exec(
            select(AuditLog).where(AuditLog.timestamp < log_cutoff)
        ).all()
        for log in old_logs:
            session.delete(log)
        if old_logs:
            logger.info("Deleted %d old audit logs (>%d days)", len(old_logs), LOG_RETENTION_DAYS)

        session.commit()


def schedule_cleanup(scheduler: BackgroundScheduler) -> None:
    """Schedule daily cleanup at 3:00 AM."""
    scheduler.add_job(
        cleanup_old_data,
        "cron",
        hour=3,
        minute=0,
        id="daily_cleanup",
        replace_existing=True,
    )
    logger.info("Scheduled daily cleanup at 03:00 (codes: %dd, logs: %dd retention)",
                CODE_RETENTION_DAYS, LOG_RETENTION_DAYS)
