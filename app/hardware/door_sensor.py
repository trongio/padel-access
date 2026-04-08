import logging
from typing import Callable, Optional

import RPi.GPIO as GPIO

from app.hardware.relay import _ensure_gpio

logger = logging.getLogger(__name__)


class DoorSensor:
    """Magnetic reed door sensor (normally-open).

    Wired between the GPIO pin and GND with the internal pull-up enabled:
      - Door CLOSED → magnet near sensor → switch closes → pin reads LOW
      - Door OPEN   → magnet away         → switch open  → pin reads HIGH

    Both edges are detected so callers can react to either transition.
    The callback receives a single ``closed: bool`` argument.
    """

    def __init__(
        self,
        gpio_pin: int,
        on_change_callback: Optional[Callable[[bool], None]] = None,
        enabled: bool = True,
        bouncetime_ms: int = 200,
    ) -> None:
        self._pin = gpio_pin
        self._callback = on_change_callback
        self._available = False

        if not enabled:
            logger.info("Door sensor disabled via config")
            return

        try:
            _ensure_gpio()
            GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self._pin,
                GPIO.BOTH,
                callback=self._handle_change,
                bouncetime=bouncetime_ms,
            )
            self._available = True
            logger.info(
                "Door sensor on GPIO %d initialized (initial state: %s)",
                self._pin,
                "closed" if self.is_closed() else "open",
            )
        except Exception:
            logger.exception(
                "Door sensor init failed on GPIO %d — running without it", self._pin
            )

    def is_available(self) -> bool:
        return self._available

    def is_closed(self) -> bool:
        """Return True when the door is currently closed (sensor magnet engaged)."""
        if not self._available:
            return False
        try:
            return GPIO.input(self._pin) == GPIO.LOW
        except Exception:
            logger.exception("Door sensor read failed on GPIO %d", self._pin)
            return False

    def _handle_change(self, channel: int) -> None:
        try:
            closed = GPIO.input(self._pin) == GPIO.LOW
            logger.info("Door sensor: %s", "CLOSED" if closed else "OPENED")
            if self._callback is not None:
                self._callback(closed)
        except Exception:
            logger.exception("Door sensor callback error")

    def cleanup(self) -> None:
        if not self._available:
            return
        try:
            GPIO.remove_event_detect(self._pin)
        except Exception:
            pass