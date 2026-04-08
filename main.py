import json
import logging
import signal
import sys
import threading
import time

import uvicorn
from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import config
from app.api.limiter import limiter
from app.api.router import api_router
from app.core.database import init_db, log_event
from app.core.scheduler import create_scheduler, restore_light_jobs, schedule_cleanup
from app.hardware.button import ExitButton
from app.hardware.buzzer import Buzzer
from app.hardware.display import DisplayManager
from app.hardware.door_sensor import DoorSensor
from app.hardware.keypad import KeypadManager
from app.hardware.relay import RelayController
from app.services.access import validate_code
from app.services.light_manager import LightManager

# ─── Logging ──────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("padel-access")

# ─── Keypad state ─────────────────────────────────

_input_buffer = ""
_input_lock = threading.Lock()
_input_timer: threading.Timer | None = None

# Brute-force protection (generous to avoid locking out real users)
_MAX_FAILED_ATTEMPTS = 20
_LOCKOUT_SECONDS = 30
_failed_attempts = 0
_lockout_until: float = 0.0
_failed_lock = threading.Lock()

# Globals set during init
_door_relay: RelayController
_buzzer: Buzzer
_display: DisplayManager
_light_manager: LightManager
_keypad: KeypadManager
_exit_button: ExitButton
_door_sensor: DoorSensor

# Door-open alarm state
_door_alarm_lock = threading.Lock()
_door_alarm_timer: threading.Timer | None = None
_door_alarm_active = False
_door_alarm_display_shown = False


def _reset_input() -> None:
    global _input_buffer, _input_timer
    with _input_lock:
        _input_buffer = ""
        if _input_timer is not None:
            _input_timer.cancel()
            _input_timer = None


def _start_input_timeout() -> None:
    global _input_timer
    if _input_timer is not None:
        _input_timer.cancel()
    _input_timer = threading.Timer(15.0, _on_input_timeout)
    _input_timer.daemon = True
    _input_timer.start()


def _on_input_timeout() -> None:
    global _input_buffer
    with _input_lock:
        _input_buffer = ""
    _display.show_idle()
    logger.debug("Input timeout — returned to idle")


def _on_key_press(key: str) -> None:
    global _input_buffer

    # Keypad is locked while the "CLOSE THE DOOR" alarm message is showing —
    # ignore all input until the door is physically closed.
    if _door_alarm_display_shown:
        logger.debug("Keypad ignored (door alarm active): %s", key)
        return

    code_to_submit: str | None = None

    with _input_lock:
        if key in "0123456789":
            if len(_input_buffer) >= config.CODE_LENGTH:
                # Already at the configured code length — ignore further digits.
                return
            _buzzer.beep_keypress()
            _input_buffer += key
            shown = "*" * len(_input_buffer) if config.MASK_CODE_INPUT else _input_buffer
            _display.show_input(shown)
            _start_input_timeout()

        elif key == "*":
            _buzzer.beep_keypress()
            _input_buffer = ""
            if _input_timer is not None:
                _input_timer.cancel()
            _display.show_idle()

        elif key == "#":
            code_to_submit = _input_buffer
            _input_buffer = ""
            if _input_timer is not None:
                _input_timer.cancel()

    # Submit OUTSIDE the input lock — DB validation can take time and we
    # don't want to block subsequent keypad events.
    if code_to_submit is not None:
        _submit_code(code_to_submit)


def _submit_code(code: str) -> None:
    global _failed_attempts, _lockout_until

    if not code:
        _display.show_idle()
        return

    # Check lockout
    with _failed_lock:
        now = time.time()
        if now < _lockout_until:
            remaining = int(_lockout_until - now)
            _buzzer.beep_error()
            _display.show_error(f"Locked {remaining}s", duration=2)
            logger.warning("Keypad locked out — %d seconds remaining", remaining)
            return

    result = validate_code(code)

    if result.success:
        with _failed_lock:
            _failed_attempts = 0
        _buzzer.beep_success()
        _display.show_success(result.valid_until)
        _door_relay.pulse(config.DOOR_UNLOCK_DURATION)
        _schedule_lock_recheck()

        for lid in result.light_ids:
            _light_manager.turn_on(lid, result.valid_until)

        # NOTE: log code id, never the secret code value.
        code_ref = str(result.code_id) if result.code_id is not None else None
        log_event(
            "DOOR_OPEN",
            code=code_ref,
            light_ids=json.dumps(result.light_ids),
            actor="keypad",
        )
        log_event(
            "LIGHT_ON",
            code=code_ref,
            light_ids=json.dumps(result.light_ids),
            actor="keypad",
        )
    else:
        with _failed_lock:
            _failed_attempts += 1
            if _failed_attempts >= _MAX_FAILED_ATTEMPTS:
                _lockout_until = time.time() + _LOCKOUT_SECONDS
                logger.warning(
                    "Keypad locked out for %ds after %d failed attempts",
                    _LOCKOUT_SECONDS, _failed_attempts,
                )
                _failed_attempts = 0

        _buzzer.beep_error()
        _display.show_error(result.reason)
        # NOTE: do NOT log the attempted code — it would aid brute force.
        log_event(
            "CODE_FAIL",
            actor="keypad",
            details=result.reason,
        )


def _on_exit_button() -> None:
    _buzzer.beep_success()
    _door_relay.pulse(config.DOOR_UNLOCK_DURATION)
    _schedule_lock_recheck()
    log_event("DOOR_OPEN", actor="button")
    logger.info("Exit button — door unlocked for %ds", config.DOOR_UNLOCK_DURATION)


def _on_door_sensor_change(closed: bool) -> None:
    """Handle physical door open/close transitions reported by the reed sensor."""
    if closed:
        log_event("DOOR_CLOSED", actor="sensor")
        _cancel_door_open_alarm()
    else:
        log_event("DOOR_OPENED", actor="sensor")
        _evaluate_door_alarm()


def _evaluate_door_alarm() -> None:
    """Decide whether the door-open alarm should be armed *right now*.

    The alarm only makes sense when the door is physically open AND the
    door lock is engaged. While the door relay is energized (lock released
    by a recent unlock), opening the door is legitimate, so we defer the
    check until the lock re-engages — see `_schedule_lock_recheck`.

    Called when the sensor reports OPENED, after every unlock pulse, and
    once at startup.
    """
    if not config.DOOR_OPEN_ALARM_ENABLED:
        return
    if not _door_sensor.is_available():
        return
    if _door_sensor.is_closed():
        return
    if _door_relay.is_on():
        # Lock currently disengaged — defer until it re-engages.
        logger.debug("Door open while relay unlocked — deferring alarm check")
        return
    _arm_door_open_alarm()
    _show_alarm_message()


def _show_alarm_message() -> None:
    global _door_alarm_display_shown
    _door_alarm_display_shown = True
    # Drop any in-progress keypad code — the keypad is locked while the
    # alarm message is up, and a stale buffer would surface again the
    # moment the message clears.
    _reset_input()
    _display.show_message(
        config.LANG["close_door_line1"],
        config.LANG["close_door_line2"],
    )


def _hide_alarm_message() -> None:
    global _door_alarm_display_shown
    if _door_alarm_display_shown:
        _door_alarm_display_shown = False
        _display.show_idle()


def _arm_door_open_alarm() -> None:
    """Schedule the buzzer alarm to fire if the door isn't closed in time."""
    if config.DOOR_OPEN_ALARM_SECONDS <= 0:
        return

    global _door_alarm_timer
    with _door_alarm_lock:
        if _door_alarm_timer is not None:
            _door_alarm_timer.cancel()
        _door_alarm_timer = threading.Timer(
            config.DOOR_OPEN_ALARM_SECONDS, _trigger_door_open_alarm
        )
        _door_alarm_timer.daemon = True
        _door_alarm_timer.start()
        logger.debug(
            "Door-open alarm armed (fires in %ds)", config.DOOR_OPEN_ALARM_SECONDS
        )


def _cancel_door_open_alarm() -> None:
    """Cancel a pending alarm timer and silence the buzzer + display."""
    global _door_alarm_timer, _door_alarm_active
    with _door_alarm_lock:
        if _door_alarm_timer is not None:
            _door_alarm_timer.cancel()
            _door_alarm_timer = None
        was_active = _door_alarm_active
        _door_alarm_active = False
    if was_active:
        _buzzer.stop_alarm()
        log_event("DOOR_ALARM_CLEARED", actor="sensor")
        logger.info("Door-open alarm cleared (door now closed)")
    _hide_alarm_message()


def _trigger_door_open_alarm() -> None:
    global _door_alarm_active, _door_alarm_timer
    # Re-check the actual sensor state — if the door closed in the gap
    # between the timer firing and us being scheduled, do nothing.
    if _door_sensor.is_closed():
        return
    with _door_alarm_lock:
        _door_alarm_active = True
        _door_alarm_timer = None
    _buzzer.start_alarm()
    # Defensive: ensure the message is on screen even if it was cleared
    # (e.g. by an idle return) between arming and firing.
    _show_alarm_message()
    log_event(
        "DOOR_ALARM",
        actor="sensor",
        details=f"door open >{config.DOOR_OPEN_ALARM_SECONDS}s",
    )
    logger.warning(
        "Door-open alarm triggered — door has been open for %ds",
        config.DOOR_OPEN_ALARM_SECONDS,
    )


def _schedule_lock_recheck() -> None:
    """Re-evaluate the alarm shortly after the door lock re-engages.

    Called whenever something triggers a door unlock pulse (keypad code,
    exit button, remote API). After the relay's pulse duration elapses
    plus a small grace period, we re-check the door sensor — if the door
    is still open then, the user genuinely left it open.
    """
    if not config.DOOR_OPEN_ALARM_ENABLED:
        return
    if not _door_sensor.is_available():
        return
    delay = config.DOOR_UNLOCK_DURATION + 0.5
    t = threading.Timer(delay, _evaluate_door_alarm)
    t.daemon = True
    t.start()


# ─── Shutdown ─────────────────────────────────────

_shutdown_event = threading.Event()


def _shutdown(signum=None, frame=None) -> None:
    logger.info("Shutting down...")
    _reset_input()
    _cancel_door_open_alarm()
    # Drop the relays to OFF for safety, but keep the persisted scheduler
    # jobs so an in-progress booking can be restored on the next startup.
    _light_manager.shutdown_relays_keep_jobs()
    _door_relay.off()
    _display.show_message(config.LANG["shutting_down"], duration=2)
    _scheduler.shutdown(wait=False)
    _keypad.cleanup()
    _exit_button.cleanup()
    _door_sensor.cleanup()
    _buzzer.cleanup()
    _display.shutdown()
    _shutdown_event.set()
    logger.info("Shutdown complete")
    sys.exit(0)


# ─── Main ─────────────────────────────────────────

def main() -> None:
    global _door_relay, _buzzer, _display, _light_manager, _keypad, _exit_button, _door_sensor, _scheduler

    # Refuse to start with the placeholder API key — would leave the system wide open.
    if config.API_KEY == config.DEFAULT_API_KEY_PLACEHOLDER or not config.API_KEY:
        logger.error(
            "API_KEY is missing or still set to the default placeholder. "
            "Set a strong value in .env (e.g. `openssl rand -hex 32`) before starting."
        )
        sys.exit(1)

    logger.info("Starting Padel Access Control System")

    # 1. Init GPIO relays
    _door_relay = RelayController(config.DOOR_RELAY_GPIO, config.RELAY_ACTIVE_LOW)
    light_relay_1 = RelayController(config.LIGHT_RELAY_1_GPIO, config.RELAY_ACTIVE_LOW)
    light_relay_2 = RelayController(config.LIGHT_RELAY_2_GPIO, config.RELAY_ACTIVE_LOW)
    light_relays = {1: light_relay_1, 2: light_relay_2}

    # 2. Init display
    _display = DisplayManager()

    # 3. Init database
    init_db()

    # 4. Init scheduler
    _scheduler = create_scheduler(config.DATABASE_URL)
    _scheduler.start()

    # 5. Init light manager + restore jobs
    _light_manager = LightManager(light_relays, _scheduler)
    restore_light_jobs(_scheduler, _light_manager)

    # 5b. Schedule daily cleanup
    schedule_cleanup(_scheduler)

    # 6. Init buzzer
    _buzzer = Buzzer(config.BUZZER_GPIO, config.BUZZER_ENABLED)

    # 7. Build FastAPI app
    app = FastAPI(title="Padel Access Control")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(api_router)
    app.state.door_relay = _door_relay
    app.state.light_manager = _light_manager
    app.state.buzzer = _buzzer
    app.state.scheduler = _scheduler
    app.state.schedule_lock_recheck = _schedule_lock_recheck

    # 8. Init keypad
    _keypad = KeypadManager(
        row_pins=config.KEYPAD_ROW_PINS,
        col_pins=config.KEYPAD_COL_PINS,
        on_key_callback=_on_key_press,
    )

    # 9. Init exit button
    _exit_button = ExitButton(config.EXIT_BUTTON_GPIO, _on_exit_button)

    # 9b. Init door close sensor (NO magnetic reed)
    _door_sensor = DoorSensor(
        config.DOOR_SENSOR_GPIO,
        on_change_callback=_on_door_sensor_change,
        enabled=config.DOOR_SENSOR_ENABLED,
    )
    app.state.door_sensor = _door_sensor

    # 10. Start uvicorn in daemon thread
    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={
            "host": config.APP_HOST,
            "port": config.APP_PORT,
            "log_level": config.LOG_LEVEL.lower(),
        },
        daemon=True,
    )
    server_thread.start()
    logger.info("API server started on %s:%d", config.APP_HOST, config.APP_PORT)

    # 11. Show idle screen
    _display.show_idle()

    # 11b. Evaluate door alarm — if we boot with the door already open and
    # the lock engaged, this will arm the timer and show "CLOSE THE DOOR".
    _evaluate_door_alarm()

    # 12. Register signal handlers and block
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("System ready — waiting for input")
    _shutdown_event.wait()


if __name__ == "__main__":
    main()
