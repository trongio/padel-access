import logging
from typing import Callable

import RPi.GPIO as GPIO

from app.hardware.relay import _ensure_gpio

logger = logging.getLogger(__name__)


class ExitButton:
    """Exit button using internal pull-up and falling-edge detection."""

    def __init__(self, gpio_pin: int, on_press_callback: Callable[[], None]) -> None:
        _ensure_gpio()
        self._pin = gpio_pin
        self._callback = on_press_callback

        GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(
            self._pin,
            GPIO.FALLING,
            callback=self._handle_press,
            bouncetime=300,
        )
        logger.info("Exit button on GPIO %d initialized", self._pin)

    def _handle_press(self, channel: int) -> None:
        try:
            logger.info("Exit button pressed")
            self._callback()
        except Exception:
            logger.exception("Exit button callback error")

    def cleanup(self) -> None:
        try:
            GPIO.remove_event_detect(self._pin)
        except Exception:
            pass
