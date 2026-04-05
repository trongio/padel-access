import logging
import threading
import time

import RPi.GPIO as GPIO

from app.hardware.relay import _ensure_gpio

logger = logging.getLogger(__name__)


class Buzzer:
    """Active buzzer with non-blocking beep patterns. GPIO HIGH = buzzer ON."""

    def __init__(self, gpio_pin: int, enabled: bool = True) -> None:
        self._pin = gpio_pin
        self._enabled = enabled
        self._lock = threading.Lock()

        if self._enabled:
            _ensure_gpio()
            GPIO.setup(self._pin, GPIO.OUT, initial=GPIO.LOW)
            logger.info("Buzzer on GPIO %d initialized", self._pin)

    def beep(self, count: int = 1, on_ms: int = 100, off_ms: int = 100) -> None:
        if not self._enabled:
            return
        t = threading.Thread(target=self._beep_worker, args=(count, on_ms, off_ms), daemon=True)
        t.start()

    def _beep_worker(self, count: int, on_ms: int, off_ms: int) -> None:
        try:
            with self._lock:
                for i in range(count):
                    GPIO.output(self._pin, GPIO.HIGH)
                    time.sleep(on_ms / 1000.0)
                    GPIO.output(self._pin, GPIO.LOW)
                    if off_ms > 0 and i < count - 1:
                        time.sleep(off_ms / 1000.0)
        except Exception:
            logger.exception("Buzzer error on GPIO %d", self._pin)

    def beep_keypress(self) -> None:
        self.beep(count=1, on_ms=10, off_ms=0)

    def beep_success(self) -> None:
        self.beep(count=2, on_ms=100, off_ms=100)

    def beep_error(self) -> None:
        self.beep(count=1, on_ms=500, off_ms=0)

    def beep_exit(self) -> None:
        self.beep(count=1, on_ms=100, off_ms=0)

    def cleanup(self) -> None:
        if self._enabled:
            GPIO.output(self._pin, GPIO.LOW)
