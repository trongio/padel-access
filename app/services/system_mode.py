"""System operating mode controller.

Encapsulates the three operating modes the API can put the device in:

- ``normal``: keypad active, exit button works, remote unlock pulses the
  door relay for ``DOOR_UNLOCK_DURATION`` seconds.
- ``keypad_disabled``: the keypad ignores all input. The exit button and the
  remote unlock route still pulse the door normally — useful when staff want
  to physically gate-keep entry without disabling everything.
- ``free``: the door relay is held energized indefinitely (door stays open),
  every known light relay is energized indefinitely, the keypad is ignored,
  the buzzer success/error chirps are suppressed, and the door-open alarm is
  silenced (the door is *supposed* to be open).

This module owns the single ``unlock_door()`` funnel that the keypad,
exit button and ``POST /api/control/door`` all route through, so mode-aware
suppression happens in exactly one place.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from app import config
from app.core.database import log_event

logger = logging.getLogger(__name__)


VALID_MODES = ("normal", "keypad_disabled", "free")


class SystemModeController:
    def __init__(
        self,
        door_relay,
        light_manager,
        buzzer,
        display,
        persist_fn: Callable[[str], None],
        cancel_door_alarm_fn: Callable[[], None],
    ) -> None:
        self._lock = threading.RLock()
        self._mode = "normal"
        self._door_relay = door_relay
        self._lights = light_manager
        self._buzzer = buzzer
        self._display = display
        self._persist = persist_fn
        self._cancel_alarm = cancel_door_alarm_fn

    # ─── Read-only state ─────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def is_free(self) -> bool:
        return self._mode == "free"

    def is_keypad_active(self) -> bool:
        return self._mode == "normal"

    # ─── Transitions ────────────────────────────────

    def set_mode(self, new: str, actor: str) -> None:
        if new not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}")
        with self._lock:
            old = self._mode
            if new == old:
                return
            self._leave(old)
            self._enter(new)
            self._mode = new
            try:
                self._persist(new)
            except Exception:
                logger.exception("Failed to persist system mode")
        log_event("MODE_CHANGE", actor=actor, details=f"{old}->{new}")
        logger.info("System mode changed: %s -> %s (actor=%s)", old, new, actor)

    def restore(self, persisted: str) -> None:
        """Boot-time restore. Call once after hardware is up.

        Does not write to the audit log — the previous ``MODE_CHANGE`` entry
        from when the mode was originally set already records the intent.
        """
        if persisted not in VALID_MODES:
            persisted = "normal"
        with self._lock:
            if persisted != "normal":
                self._enter(persisted)
            self._mode = persisted
        if persisted != "normal":
            logger.info("System mode restored from disk: %s", persisted)

    # ─── Door unlock funnel ─────────────────────────

    def unlock_door(self, actor: str, duration: Optional[int] = None) -> bool:
        """Single funnel for every door-unlock path.

        Returns True if a relay pulse was issued, False if the request was
        suppressed by the current mode (free mode keeps the door open
        already, so a pulse would be a no-op or worse — see the relay's
        ``_pulse_worker`` which would call ``off()`` after the pulse).
        """
        if self._mode == "free":
            logger.debug("Door unlock requested by %s — suppressed (free mode)", actor)
            return False
        d = duration if duration is not None else config.DOOR_UNLOCK_DURATION
        self._door_relay.pulse(d)
        return True

    # ─── Internal: enter/leave per-mode work ────────

    def _enter(self, mode: str) -> None:
        if mode == "free":
            # Hold the door lock disengaged. The relay's existing on() is
            # idempotent — repeating it is fine.
            self._door_relay.on()
            # Energize every light without scheduling a turn-off job.
            self._lights.turn_on_indefinite_all()
            # Silence any pending door-open alarm — the door is supposed to
            # be open in this mode.
            try:
                self._cancel_alarm()
            except Exception:
                logger.exception("Failed to cancel door-open alarm on free-mode entry")
            try:
                self._display.show_message("FREE MODE", "door open")
            except Exception:
                logger.exception("Failed to update display on free-mode entry")
        elif mode == "keypad_disabled":
            try:
                self._display.show_message("KEYPAD", "disabled", duration=3)
            except Exception:
                logger.exception("Failed to update display on keypad-disabled entry")

    def _leave(self, mode: str) -> None:
        if mode == "free":
            # turn_off_all also clears any (nonexistent) scheduler jobs.
            self._lights.turn_off_all()
            self._door_relay.off()
            try:
                self._display.show_idle()
            except Exception:
                logger.exception("Failed to refresh display on free-mode exit")
