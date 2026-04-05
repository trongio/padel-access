import logging
import threading
from datetime import datetime

from app.hardware.relay import RelayController

logger = logging.getLogger(__name__)

# Module-level reference set by LightManager.__init__
# Used by the scheduler callback to avoid pickling the instance
_instance: "LightManager | None" = None


def _scheduled_turn_off(light_id: int) -> None:
    """Standalone function called by APScheduler — avoids pickling LightManager."""
    if _instance is not None:
        relay = _instance._relays.get(light_id)
        if relay and relay.is_on():
            relay.off()
            logger.info("Light %d auto-OFF (scheduler)", light_id)


class LightManager:
    """Manages light zone relays with scheduled auto-off via APScheduler."""

    def __init__(self, light_relays: dict[int, RelayController], scheduler) -> None:
        global _instance
        self._relays = light_relays
        self._scheduler = scheduler
        self._lock = threading.Lock()
        _instance = self

    def turn_on(self, light_id: int, until: datetime) -> None:
        with self._lock:
            relay = self._relays.get(light_id)
            if relay is None:
                logger.warning("Unknown light_id %d", light_id)
                return

            relay.on()
            logger.info("Light %d ON until %s", light_id, until)

            # Cancel existing job for this light
            job_id = f"light_off_{light_id}"
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

            # Schedule turn-off using module-level function (picklable)
            self._scheduler.add_job(
                _scheduled_turn_off,
                "date",
                run_date=until,
                args=[light_id],
                id=job_id,
                replace_existing=True,
            )

    def turn_off(self, light_id: int) -> None:
        with self._lock:
            relay = self._relays.get(light_id)
            if relay is None:
                return

            relay.off()
            logger.info("Light %d OFF", light_id)

            job_id = f"light_off_{light_id}"
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

    def turn_off_all(self) -> None:
        for light_id in list(self._relays.keys()):
            self.turn_off(light_id)

    def get_status(self) -> dict:
        status = {}
        for light_id, relay in self._relays.items():
            until = None
            job_id = f"light_off_{light_id}"
            try:
                job = self._scheduler.get_job(job_id)
                if job is not None:
                    until = job.next_run_time
            except Exception:
                pass
            status[light_id] = {"on": relay.is_on(), "until": until}
        return status
