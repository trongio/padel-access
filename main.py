import json
import logging
import signal
import sys
import threading

import uvicorn
from fastapi import FastAPI

from app import config
from app.api.router import api_router
from app.core.database import init_db, log_event
from app.core.scheduler import create_scheduler, restore_light_jobs
from app.hardware.button import ExitButton
from app.hardware.buzzer import Buzzer
from app.hardware.display import DisplayManager
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

# Globals set during init
_door_relay: RelayController
_buzzer: Buzzer
_display: DisplayManager
_light_manager: LightManager
_keypad: KeypadManager
_exit_button: ExitButton


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

    with _input_lock:
        if key in "0123456789ABCD":
            _buzzer.beep_keypress()
            _input_buffer += key
            _display.show_input("*" * len(_input_buffer))
            _start_input_timeout()

        elif key == "*":
            _buzzer.beep_keypress()
            _input_buffer = ""
            if _input_timer is not None:
                _input_timer.cancel()
            _display.show_idle()

        elif key == "#":
            code = _input_buffer
            _input_buffer = ""
            if _input_timer is not None:
                _input_timer.cancel()
            # Release lock before submit (it may take time)
            _submit_code(code)


def _submit_code(code: str) -> None:
    if not code:
        _display.show_idle()
        return

    result = validate_code(code)

    if result.success:
        _buzzer.beep_success()
        _display.show_success(result.valid_until)
        _door_relay.pulse(config.DOOR_UNLOCK_DURATION)

        for lid in result.light_ids:
            _light_manager.turn_on(lid, result.valid_until)

        log_event(
            "DOOR_OPEN",
            code=code,
            light_ids=json.dumps(result.light_ids),
            actor="keypad",
        )
        log_event(
            "LIGHT_ON",
            code=code,
            light_ids=json.dumps(result.light_ids),
            actor="keypad",
        )
    else:
        _buzzer.beep_error()
        _display.show_error(result.reason)
        log_event(
            "CODE_FAIL",
            code=code,
            actor="keypad",
            details=result.reason,
        )


def _on_exit_button() -> None:
    _buzzer.beep_success()
    _door_relay.pulse(config.DOOR_UNLOCK_DURATION)
    log_event("DOOR_OPEN", actor="button")
    logger.info("Exit button — door unlocked for %ds", config.DOOR_UNLOCK_DURATION)


# ─── Shutdown ─────────────────────────────────────

_shutdown_event = threading.Event()


def _shutdown(signum=None, frame=None) -> None:
    logger.info("Shutting down...")
    _reset_input()
    _light_manager.turn_off_all()
    _door_relay.off()
    _display.show_message("Shutting down...", duration=2)
    _scheduler.shutdown(wait=False)
    _keypad.cleanup()
    _exit_button.cleanup()
    _buzzer.cleanup()
    _display.shutdown()
    _shutdown_event.set()
    logger.info("Shutdown complete")
    sys.exit(0)


# ─── Main ─────────────────────────────────────────

def main() -> None:
    global _door_relay, _buzzer, _display, _light_manager, _keypad, _exit_button, _scheduler

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

    # 6. Init buzzer
    _buzzer = Buzzer(config.BUZZER_GPIO, config.BUZZER_ENABLED)

    # 7. Build FastAPI app
    app = FastAPI(title="Padel Access Control")
    app.include_router(api_router)
    app.state.door_relay = _door_relay
    app.state.light_manager = _light_manager
    app.state.buzzer = _buzzer
    app.state.scheduler = _scheduler

    # 8. Init keypad
    _keypad = KeypadManager(
        row_pins=config.KEYPAD_ROW_PINS,
        col_pins=config.KEYPAD_COL_PINS,
        on_key_callback=_on_key_press,
    )

    # 9. Init exit button
    _exit_button = ExitButton(config.EXIT_BUTTON_GPIO, _on_exit_button)

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

    # 12. Register signal handlers and block
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("System ready — waiting for input")
    _shutdown_event.wait()


if __name__ == "__main__":
    main()
