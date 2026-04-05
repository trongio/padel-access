import logging
import queue
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont

from app import config

logger = logging.getLogger(__name__)

_CLOCK_REFRESH = 1.0  # seconds between idle clock updates
_FONT_DIR = config._BASE_DIR / "assets" / "fonts"
_FONT_FILE = _FONT_DIR / "NotoSansGeorgian.ttf"


class DisplayManager:
    """Queue-based OLED display manager. All I2C writes run on a single thread."""

    def __init__(self) -> None:
        self._available = False
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._running = True
        self._return_timer: threading.Timer | None = None
        self._tz = ZoneInfo(config.TZ)
        self._idle = True  # tracks if we're showing the idle/clock screen

        try:
            serial = i2c(port=1, address=0x3C)
            self._device = ssd1306(serial)
            # Load Georgian-capable font at multiple sizes
            if _FONT_FILE.exists():
                self._font_sm = ImageFont.truetype(str(_FONT_FILE), 12)
                self._font_md = ImageFont.truetype(str(_FONT_FILE), 14)
                self._font_lg = ImageFont.truetype(str(_FONT_FILE), 22)
                logger.info("Loaded NotoSansGeorgian font")
            else:
                self._font_sm = ImageFont.load_default()
                self._font_md = ImageFont.load_default()
                self._font_lg = ImageFont.load_default()
                logger.warning("Georgian font not found, using default")
            self._available = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            logger.info("Display initialized (128x64 SSD1306)")
        except Exception:
            logger.warning("OLED display not found — running without display")

    # ─── Public API (non-blocking, enqueue only) ──

    def show_idle(self) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._idle = True
        self._queue.put({"type": "idle"})

    def show_input(self, masked: str) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._idle = False
        self._queue.put({"type": "input", "masked": masked})

    def show_success(self, valid_until: datetime) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._queue.put({"type": "success", "until": valid_until})
        self._schedule_return(3.0)

    def show_error(self, message: str, duration: float = 3.0) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._queue.put({"type": "error", "message": message})
        self._schedule_return(duration)

    def show_message(self, line1: str, line2: str = "", duration: float | None = None) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._queue.put({"type": "message", "line1": line1, "line2": line2})
        if duration is not None:
            self._schedule_return(duration)

    def shutdown(self) -> None:
        self._cancel_timer()
        self._running = False
        if not self._available:
            return
        self._queue.put(None)  # sentinel to unblock
        self._thread.join(timeout=2)
        try:
            self._device.hide()
        except Exception:
            pass

    # ─── Internal ─────────────────────────────────

    def _run(self) -> None:
        last_clock = 0.0
        while self._running:
            try:
                cmd = self._queue.get(timeout=0.2)
            except queue.Empty:
                # Auto-refresh clock when idle
                if self._idle and time.time() - last_clock >= _CLOCK_REFRESH:
                    try:
                        self._render({"type": "idle"})
                        last_clock = time.time()
                    except Exception:
                        logger.exception("Display clock refresh error")
                continue
            if cmd is None:
                break
            try:
                self._render(cmd)
                if cmd["type"] == "idle":
                    last_clock = time.time()
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

    def _center(self, draw: ImageDraw.ImageDraw, text: str, y: int, font) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text(((128 - w) // 2, y), text, fill=1, font=font)

    def _draw_idle(self, draw: ImageDraw.ImageDraw) -> None:
        now = datetime.now(self._tz)
        self._center(draw, config.DISPLAY_IDLE_TEXT, 2, self._font_sm)
        self._center(draw, now.strftime("%H:%M:%S"), 18, self._font_lg)
        self._center(draw, now.strftime("%d %b %Y, %a"), 48, self._font_sm)

    def _draw_input(self, draw: ImageDraw.ImageDraw, masked: str) -> None:
        self._center(draw, "Enter Code:", 6, self._font_md)
        dots = " ".join("*" for _ in masked) if masked else ""
        self._center(draw, dots, 30, self._font_lg)

    def _draw_success(self, draw: ImageDraw.ImageDraw, until: datetime) -> None:
        local_until = until.astimezone(self._tz)
        self._center(draw, "Access Granted", 8, self._font_md)
        self._center(draw, f"Until {local_until.strftime('%H:%M')}", 34, self._font_lg)

    def _draw_error(self, draw: ImageDraw.ImageDraw, message: str) -> None:
        self._center(draw, "Error", 6, self._font_md)
        self._center(draw, message[:20], 28, self._font_md)
        if len(message) > 20:
            self._center(draw, message[20:40], 46, self._font_sm)

    def _draw_message(self, draw: ImageDraw.ImageDraw, line1: str, line2: str) -> None:
        self._center(draw, line1, 14, self._font_md)
        if line2:
            self._center(draw, line2, 36, self._font_md)

    def _schedule_return(self, seconds: float) -> None:
        self._cancel_timer()
        self._return_timer = threading.Timer(seconds, self.show_idle)
        self._return_timer.daemon = True
        self._return_timer.start()

    def _cancel_timer(self) -> None:
        if self._return_timer is not None:
            self._return_timer.cancel()
            self._return_timer = None
