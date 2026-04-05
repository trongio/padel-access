import atexit
import logging
import threading
import time

import RPi.GPIO as GPIO

logger = logging.getLogger(__name__)

_gpio_initialized = False
_gpio_lock = threading.Lock()


def _ensure_gpio() -> None:
    global _gpio_initialized
    with _gpio_lock:
        if not _gpio_initialized:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            atexit.register(GPIO.cleanup)
            _gpio_initialized = True


class RelayController:
    """Thread-safe relay controller with active-LOW support."""

    def __init__(self, gpio_pin: int, active_low: bool = True) -> None:
        _ensure_gpio()
        self._pin = gpio_pin
        self._active_low = active_low
        self._lock = threading.Lock()
        self._is_on = False

        # Initialize to OFF state
        off_level = GPIO.HIGH if active_low else GPIO.LOW
        GPIO.setup(self._pin, GPIO.OUT, initial=off_level)
        logger.info("Relay on GPIO %d initialized (active_low=%s)", self._pin, active_low)

    def on(self) -> None:
        with self._lock:
            level = GPIO.LOW if self._active_low else GPIO.HIGH
            GPIO.output(self._pin, level)
            self._is_on = True

    def off(self) -> None:
        with self._lock:
            level = GPIO.HIGH if self._active_low else GPIO.LOW
            GPIO.output(self._pin, level)
            self._is_on = False

    def pulse(self, duration: float) -> None:
        """Activate relay for `duration` seconds in a background thread."""
        t = threading.Thread(target=self._pulse_worker, args=(duration,), daemon=True)
        t.start()

    def _pulse_worker(self, duration: float) -> None:
        try:
            self.on()
            time.sleep(duration)
            self.off()
        except Exception:
            logger.exception("Relay pulse error on GPIO %d", self._pin)
            self.off()

    def is_on(self) -> bool:
        return self._is_on

    def cleanup(self) -> None:
        self.off()
