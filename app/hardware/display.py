import logging
import queue
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont

from app import config

logger = logging.getLogger(__name__)


class DisplayManager:
    """Queue-based OLED display manager. All I2C writes run on a single thread."""

    def __init__(self) -> None:
        serial = i2c(port=1, address=0x3C)
        self._device = ssd1306(serial)
        self._font = ImageFont.load_default()
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._running = True
        self._return_timer: threading.Timer | None = None
        self._tz = ZoneInfo(config.TZ)

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Display initialized (128x64 SSD1306)")

    # ─── Public API (non-blocking, enqueue only) ──

    def show_idle(self) -> None:
        self._cancel_timer()
        self._queue.put({"type": "idle"})

    def show_input(self, masked: str) -> None:
        self._cancel_timer()
        self._queue.put({"type": "input", "masked": masked})

    def show_success(self, valid_until: datetime) -> None:
        self._cancel_timer()
        self._queue.put({"type": "success", "until": valid_until})
        self._schedule_return(3.0)

    def show_error(self, message: str, duration: float = 3.0) -> None:
        self._cancel_timer()
        self._queue.put({"type": "error", "message": message})
        self._schedule_return(duration)

    def show_message(self, line1: str, line2: str = "", duration: float | None = None) -> None:
        self._cancel_timer()
        self._queue.put({"type": "message", "line1": line1, "line2": line2})
        if duration is not None:
            self._schedule_return(duration)

    def shutdown(self) -> None:
        self._cancel_timer()
        self._running = False
        self._queue.put(None)  # sentinel to unblock
        self._thread.join(timeout=2)
        try:
            self._device.hide()
        except Exception:
            pass

    # ─── Internal ─────────────────────────────────

    def _run(self) -> None:
        while self._running:
            try:
                cmd = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if cmd is None:
                break
            try:
                self._render(cmd)
            except Exception:
                logger.exception("Display render error")

    def _render(self, cmd: dict) -> None:
        img = Image.new("1", (128, 64), 0)
        draw = ImageDraw.Draw(img)

        cmd_type = cmd["type"]
        if cmd_type == "idle":
            self._draw_idle(draw)
        elif cmd_type == "input":
            self._draw_input(draw, cmd["masked"])
        elif cmd_type == "success":
            self._draw_success(draw, cmd["until"])
        elif cmd_type == "error":
            self._draw_error(draw, cmd["message"])
        elif cmd_type == "message":
            self._draw_message(draw, cmd["line1"], cmd.get("line2", ""))

        self._device.display(img)

    def _draw_idle(self, draw: ImageDraw.ImageDraw) -> None:
        text = config.DISPLAY_IDLE_TEXT
        subtext = config.DISPLAY_IDLE_SUBTEXT
        # Center main text
        bbox = draw.textbbox((0, 0), text, font=self._font)
        w = bbox[2] - bbox[0]
        x = (128 - w) // 2
        draw.text((x, 16), text, fill=1, font=self._font)
        # Center subtext
        if subtext:
            bbox = draw.textbbox((0, 0), subtext, font=self._font)
            w = bbox[2] - bbox[0]
            x = (128 - w) // 2
            draw.text((x, 36), subtext, fill=1, font=self._font)

    def _draw_input(self, draw: ImageDraw.ImageDraw, masked: str) -> None:
        draw.text((10, 8), "Enter Code:", fill=1, font=self._font)
        # Show dots for each digit
        dots = " ".join("*" for _ in masked) if masked else ""
        bbox = draw.textbbox((0, 0), dots, font=self._font)
        w = bbox[2] - bbox[0]
        x = (128 - w) // 2
        draw.text((x, 34), dots, fill=1, font=self._font)

    def _draw_success(self, draw: ImageDraw.ImageDraw, until: datetime) -> None:
        draw.text((10, 8), "Access Granted", fill=1, font=self._font)
        # Convert UTC to local time for display
        local_until = until.astimezone(self._tz)
        time_str = f"Until {local_until.strftime('%H:%M')}"
        bbox = draw.textbbox((0, 0), time_str, font=self._font)
        w = bbox[2] - bbox[0]
        x = (128 - w) // 2
        draw.text((x, 36), time_str, fill=1, font=self._font)

    def _draw_error(self, draw: ImageDraw.ImageDraw, message: str) -> None:
        draw.text((10, 8), "Error", fill=1, font=self._font)
        # Word-wrap message if needed
        draw.text((10, 30), message[:20], fill=1, font=self._font)
        if len(message) > 20:
            draw.text((10, 44), message[20:40], fill=1, font=self._font)

    def _draw_message(self, draw: ImageDraw.ImageDraw, line1: str, line2: str) -> None:
        bbox = draw.textbbox((0, 0), line1, font=self._font)
        w = bbox[2] - bbox[0]
        x = (128 - w) // 2
        draw.text((x, 16), line1, fill=1, font=self._font)
        if line2:
            bbox = draw.textbbox((0, 0), line2, font=self._font)
            w = bbox[2] - bbox[0]
            x = (128 - w) // 2
            draw.text((x, 36), line2, fill=1, font=self._font)

    def _schedule_return(self, seconds: float) -> None:
        self._cancel_timer()
        self._return_timer = threading.Timer(seconds, self.show_idle)
        self._return_timer.daemon = True
        self._return_timer.start()

    def _cancel_timer(self) -> None:
        if self._return_timer is not None:
            self._return_timer.cancel()
            self._return_timer = None
