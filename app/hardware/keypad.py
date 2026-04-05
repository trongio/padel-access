import logging
from typing import Callable

from pad4pi.rpi_gpio import KeypadFactory

logger = logging.getLogger(__name__)

KEYPAD_LAYOUT = [
    ["1", "2", "3", "A"],
    ["4", "5", "6", "B"],
    ["7", "8", "9", "C"],
    ["*", "0", "#", "D"],
]


class KeypadManager:
    """4x4 matrix keypad scanner using pad4pi."""

    def __init__(
        self,
        row_pins: list[int],
        col_pins: list[int],
        on_key_callback: Callable[[str], None],
    ) -> None:
        self._callback = on_key_callback
        self._keypad = None

        try:
            factory = KeypadFactory()
            self._keypad = factory.create_keypad(
                keypad=KEYPAD_LAYOUT,
                row_pins=row_pins,
                col_pins=col_pins,
                key_delay=200,
            )
            self._keypad.registerKeyPressHandler(self._handle_key)
            logger.info("Keypad initialized (rows=%s, cols=%s)", row_pins, col_pins)
        except Exception:
            logger.warning("Keypad init failed — running without keypad")

    def _handle_key(self, key: str) -> None:
        try:
            logger.debug("Key pressed: %s", key)
            self._callback(key)
        except Exception:
            logger.exception("Keypad callback error for key %s", key)

    def cleanup(self) -> None:
        if self._keypad is None:
            return
        try:
            self._keypad.cleanup()
        except Exception:
            pass
