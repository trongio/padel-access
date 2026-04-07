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

_FONT_DIR = config._BASE_DIR / "assets" / "fonts"
_FONT_FILE = _FONT_DIR / "NotoSansGeorgian.ttf"

_SCREEN_W = 128
_SCREEN_H = 64

# Marquee tuning for lines that don't fit on screen.
_SCROLL_SPEED_PX = 30      # pixels per second
_SCROLL_GAP_PX = 24        # blank gap between consecutive copies in the loop
_SCROLL_HOLD_S = 0.8       # pause at the start before scrolling kicks in
# Refresh cadence
_FAST_REFRESH = 0.06       # ~16 fps while a marquee is on screen
_CLOCK_REFRESH = 1.0       # 1 fps while showing the idle clock


class DisplayManager:
    """Queue-based OLED display manager. All I2C writes run on a single thread."""

    def __init__(self) -> None:
        self._available = False
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._running = True
        self._return_timer: threading.Timer | None = None
        self._tz = ZoneInfo(config.TZ)
        # Tracks the most recently rendered command so the worker thread can
        # re-render it (for marquee animation or clock ticking) between
        # incoming queue events.
        self._current_cmd: dict = {"type": "idle"}
        self._current_started: float = time.monotonic()
        self._frame_has_scroll = False  # set by _draw_line during render

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
        self._queue.put({"type": "idle"})

    def show_input(self, text: str) -> None:
        """Render the current keypad input as-is. Caller decides whether to
        pre-mask the digits (see config.MASK_CODE_INPUT)."""
        if not self._available:
            return
        self._cancel_timer()
        self._queue.put({"type": "input", "text": text})

    def show_success(self, valid_until: datetime) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._queue.put({"type": "success", "until": valid_until})
        local_until = valid_until.astimezone(self._tz)
        until_text = f"{config.LANG['until']} {local_until.strftime('%H:%M')}"
        self._schedule_return(
            self._needed_duration(3.0, config.LANG["access_granted"], until_text)
        )

    def show_error(self, message: str, duration: float = 3.0) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._queue.put({"type": "error", "message": message})
        self._schedule_return(self._needed_duration(duration, message))

    def show_message(self, line1: str, line2: str = "", duration: float | None = None) -> None:
        if not self._available:
            return
        self._cancel_timer()
        self._queue.put({"type": "message", "line1": line1, "line2": line2})
        if duration is not None:
            self._schedule_return(self._needed_duration(duration, line1, line2))

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

    # ─── Duration helper ─────────────────────────

    @staticmethod
    def _needed_duration(base: float, *texts: str) -> float:
        """If a line is wider than the screen and will scroll, give it long
        enough on screen to complete one full marquee cycle plus a small
        buffer so the user can read it."""
        # Per-character pixel estimate generous enough to cover Georgian.
        worst_chars = max((len(t) for t in texts if t), default=0)
        worst_px = worst_chars * 11
        if worst_px <= _SCREEN_W:
            return base
        cycle = _SCROLL_HOLD_S + (worst_px + _SCROLL_GAP_PX) / _SCROLL_SPEED_PX
        return max(base, cycle + 1.0)

    # ─── Internal ─────────────────────────────────

    def _run(self) -> None:
        while self._running:
            # Pick a wait timeout that matches what the current frame needs:
            # marquees need fast ticks, the idle clock needs ~1 Hz, and
            # static frames can simply block until something changes.
            if self._frame_has_scroll:
                wait: float | None = _FAST_REFRESH
            elif self._current_cmd.get("type") == "idle":
                wait = _CLOCK_REFRESH
            else:
                wait = None  # block until a new command arrives
            try:
                cmd = self._queue.get(timeout=wait)
            except queue.Empty:
                self._render_safe(self._current_cmd, time.monotonic() - self._current_started)
                continue
            if cmd is None:
                break
            # Preserve animation phase across updates that don't change what
            # the user sees structurally (e.g. typing more digits keeps the
            # "Enter Code:" label scrolling smoothly).
            if not self._is_continuous(self._current_cmd, cmd):
                self._current_started = time.monotonic()
            self._current_cmd = cmd
            self._render_safe(cmd, time.monotonic() - self._current_started)

    @staticmethod
    def _is_continuous(old: dict, new: dict) -> bool:
        if old is None or old.get("type") != new.get("type"):
            return False
        # Input and idle don't change the scrolled text between updates,
        # so the marquee phase should keep ticking.
        return new.get("type") in ("input", "idle")

    def _render_safe(self, cmd: dict, elapsed: float) -> None:
        try:
            self._render(cmd, elapsed)
        except Exception:
            logger.exception("Display render error")

    def _render(self, cmd: dict, elapsed: float) -> None:
        img = Image.new("1", (_SCREEN_W, _SCREEN_H), 0)
        draw = ImageDraw.Draw(img)
        # Reset before each frame; _draw_line sets it back to True if any
        # line on this frame had to scroll.
        self._frame_has_scroll = False

        cmd_type = cmd["type"]
        if cmd_type == "idle":
            self._draw_idle(draw, elapsed)
        elif cmd_type == "input":
            self._draw_input(draw, cmd["text"], elapsed)
        elif cmd_type == "success":
            self._draw_success(draw, cmd["until"], elapsed)
        elif cmd_type == "error":
            self._draw_error(draw, cmd["message"], elapsed)
        elif cmd_type == "message":
            self._draw_message(draw, cmd["line1"], cmd.get("line2", ""), elapsed)

        self._device.display(img)

    def _draw_line(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        y: int,
        font,
        elapsed: float,
    ) -> None:
        """Render a single line: centered if it fits, otherwise as a
        right-to-left marquee. Two copies separated by a gap are drawn so
        the loop is seamless."""
        if not text:
            return
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        if w <= _SCREEN_W:
            draw.text(((_SCREEN_W - w) // 2, y), text, fill=1, font=font)
            return
        self._frame_has_scroll = True
        scroll_t = max(0.0, elapsed - _SCROLL_HOLD_S)
        period = w + _SCROLL_GAP_PX
        offset = int(scroll_t * _SCROLL_SPEED_PX) % period
        draw.text((-offset, y), text, fill=1, font=font)
        draw.text((-offset + period, y), text, fill=1, font=font)

    def _draw_idle(self, draw: ImageDraw.ImageDraw, elapsed: float) -> None:
        now = datetime.now(self._tz)
        self._draw_line(draw, config.DISPLAY_IDLE_TEXT, 2, self._font_sm, elapsed)
        self._draw_line(draw, now.strftime("%H:%M:%S"), 18, self._font_lg, elapsed)
        self._draw_line(draw, config.format_date(now), 48, self._font_sm, elapsed)

    def _draw_input(self, draw: ImageDraw.ImageDraw, text: str, elapsed: float) -> None:
        self._draw_line(draw, config.LANG["enter_code"], 6, self._font_md, elapsed)
        # Space out the characters so the row reads cleanly on the OLED.
        spaced = " ".join(text) if text else ""
        self._draw_line(draw, spaced, 30, self._font_lg, elapsed)

    def _draw_success(self, draw: ImageDraw.ImageDraw, until: datetime, elapsed: float) -> None:
        local_until = until.astimezone(self._tz)
        self._draw_line(draw, config.LANG["access_granted"], 8, self._font_md, elapsed)
        self._draw_line(
            draw,
            f"{config.LANG['until']} {local_until.strftime('%H:%M')}",
            34,
            self._font_lg,
            elapsed,
        )

    def _draw_error(self, draw: ImageDraw.ImageDraw, message: str, elapsed: float) -> None:
        self._draw_line(draw, config.LANG["error"], 6, self._font_md, elapsed)
        self._draw_line(draw, message, 30, self._font_md, elapsed)

    def _draw_message(self, draw: ImageDraw.ImageDraw, line1: str, line2: str, elapsed: float) -> None:
        self._draw_line(draw, line1, 14, self._font_md, elapsed)
        if line2:
            self._draw_line(draw, line2, 36, self._font_md, elapsed)

    def _schedule_return(self, seconds: float) -> None:
        self._cancel_timer()
        self._return_timer = threading.Timer(seconds, self.show_idle)
        self._return_timer.daemon = True
        self._return_timer.start()

    def _cancel_timer(self) -> None:
        if self._return_timer is not None:
            self._return_timer.cancel()
            self._return_timer = None
