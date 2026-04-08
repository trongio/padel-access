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
        self._alarm_stop: threading.Event | None = None
        self._alarm_thread: threading.Thread | None = None

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

    def start_alarm(self, on_ms: int = 400, off_ms: int = 400) -> None:
        """Start a continuous beep pattern that runs until stop_alarm() is called.

        Safe to call repeatedly — a no-op if the alarm is already running.
        """
        if not self._enabled:
            return
        if self._alarm_thread is not None and self._alarm_thread.is_alive():
            return
        self._alarm_stop = threading.Event()
        self._alarm_thread = threading.Thread(
            target=self._alarm_worker,
            args=(self._alarm_stop, on_ms, off_ms),
            daemon=True,
        )
        self._alarm_thread.start()
        logger.info("Buzzer alarm started")

    def stop_alarm(self) -> None:
        """Stop the continuous alarm pattern (no-op if not running)."""
        if not self._enabled:
            return
        if self._alarm_stop is not None:
            self._alarm_stop.set()
        self._alarm_thread = None
        self._alarm_stop = None
        try:
            GPIO.output(self._pin, GPIO.LOW)
        except Exception:
            pass
        logger.info("Buzzer alarm stopped")

    def _alarm_worker(self, stop: threading.Event, on_ms: int, off_ms: int) -> None:
        try:
            while not stop.is_set():
                GPIO.output(self._pin, GPIO.HIGH)
                if stop.wait(on_ms / 1000.0):
                    break
                GPIO.output(self._pin, GPIO.LOW)
                if stop.wait(off_ms / 1000.0):
                    break
        except Exception:
            logger.exception("Buzzer alarm error on GPIO %d", self._pin)
        finally:
            try:
                GPIO.output(self._pin, GPIO.LOW)
            except Exception:
                pass

    def cleanup(self) -> None:
        if not self._enabled:
            return
        self.stop_alarm()
        # Best-effort: by the time shutdown reaches us, other hardware
        # modules (e.g. pad4pi via _keypad.cleanup) may have already
        # released the GPIO chip. Don't let a final pin write crash exit.
        try:
            GPIO.output(self._pin, GPIO.LOW)
        except Exception:
            pass
