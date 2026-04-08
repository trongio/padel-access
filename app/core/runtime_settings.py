"""Runtime-mutable settings overlay.

The system loads its baseline configuration from `.env` into `app.config` at
import time. This module provides a JSON file at `data/runtime_settings.json`
that overrides selected behavior settings, lets the API mutate them at runtime,
and persists the changes so they survive a restart.

Hardware-shape settings (GPIO pins, port, API key, the door sensor enable
flag) are deliberately NOT in the allowed key set — they cannot be safely
hot-swapped without re-initializing GPIO event detection or the listening
socket. Operators change those by editing `.env` and rebooting.

`apply_overrides()` is called once at startup (from `main.py.main()`) before
any hardware is constructed; it only mutates `app.config` attributes.

`apply_single()` is called by the PATCH /api/settings endpoint after hardware
is up — it mutates `app.config`, performs the live-update side effect for the
given key, and persists the change to disk.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from app import config

logger = logging.getLogger(__name__)

_PATH: Path = config.DATA_DIR / "runtime_settings.json"
_lock = threading.Lock()


# key -> expected python type. Used both for validation and to surface a clear
# error when a caller passes a junk type.
ALLOWED_KEYS: dict[str, type] = {
    "door_unlock_duration": int,
    "mask_code_input": bool,
    "buzzer_enabled": bool,
    "door_open_alarm_enabled": bool,
    "door_open_alarm_seconds": int,
    "display_idle_text": str,
    "display_idle_subtext": str,
    "app_lang": str,
    "log_level": str,
    "code_length": int,
    # Persisted here so the system can come up in the same mode it was last
    # set to. Not exposed through PATCH /api/settings — see apply_single.
    "system_mode": str,
}


def _validate(key: str, value: Any) -> Any:
    """Validate a single key/value, returning the normalized value.

    Raises ValueError on bad input. Bool keys accept literal True/False
    only — JSON parsing already gives us bools, so anything else is a
    client mistake worth surfacing.
    """
    if key not in ALLOWED_KEYS:
        raise ValueError(f"unknown setting: {key}")

    expected = ALLOWED_KEYS[key]
    # bool is a subclass of int — guard explicitly so True/False is rejected
    # for int fields.
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
    elif expected is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
    elif expected is str:
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")

    if key == "door_unlock_duration":
        if not (1 <= value <= 60):
            raise ValueError("door_unlock_duration must be between 1 and 60")
    elif key == "door_open_alarm_seconds":
        if not (5 <= value <= 600):
            raise ValueError("door_open_alarm_seconds must be between 5 and 600")
    elif key == "code_length":
        if not (4 <= value <= 8):
            raise ValueError("code_length must be between 4 and 8")
    elif key == "app_lang":
        v = value.upper()
        if v not in ("EN", "KA"):
            raise ValueError("app_lang must be EN or KA")
        return v
    elif key == "log_level":
        v = value.upper()
        if v not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("invalid log_level")
        return v
    elif key == "system_mode":
        if value not in ("normal", "keypad_disabled", "free"):
            raise ValueError("invalid system_mode")
    elif key in ("display_idle_text", "display_idle_subtext"):
        if len(value) > 40:
            raise ValueError(f"{key} must be 40 characters or fewer")

    return value


def load() -> dict[str, Any]:
    """Read the overlay JSON file. Returns {} on missing or malformed file.

    Boot must never be blocked by a corrupt overlay, so any error is logged
    and treated as "no overrides" — the .env defaults still apply.
    """
    with _lock:
        if not _PATH.exists():
            return {}
        try:
            with _PATH.open("r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("runtime_settings.json is not an object — ignoring")
                return {}
            return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read runtime_settings.json: %s — ignoring", exc)
            return {}


def save_partial(updates: dict[str, Any]) -> None:
    """Merge `updates` into the on-disk overlay using an atomic replace."""
    with _lock:
        existing: dict[str, Any] = {}
        if _PATH.exists():
            try:
                with _PATH.open("r") as f:
                    parsed = json.load(f)
                if isinstance(parsed, dict):
                    existing = parsed
            except (OSError, json.JSONDecodeError):
                pass

        existing.update(updates)

        # Atomic write: tempfile in the same dir, then os.replace.
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".runtime_settings.", suffix=".json.tmp", dir=str(_PATH.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(existing, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, _PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def apply_overrides(overrides: dict[str, Any]) -> None:
    """Push every valid override into `app.config` at startup.

    Skips `system_mode` (the SystemModeController owns its own restore path)
    and any keys that fail validation, logging warnings instead of raising —
    boot must never be blocked by a bad overlay key.
    """
    for key, value in overrides.items():
        if key == "system_mode":
            continue
        try:
            normalized = _validate(key, value)
        except ValueError as exc:
            logger.warning("Ignoring invalid runtime setting %r: %s", key, exc)
            continue
        _mutate_config(key, normalized)

    # Re-derive LANG from APP_LANG since it was set at config import time.
    config.LANG = config._TRANSLATIONS.get(config.APP_LANG.upper(), config._TRANSLATIONS["EN"])


def _mutate_config(key: str, value: Any) -> None:
    """Set the upper-cased attribute on `app.config`."""
    setattr(config, key.upper(), value)


# Live-update side effects: applied by apply_single after hardware is up.
# Each handler receives the FastAPI app.state (so it can reach buzzer,
# display, etc.) and the new value. Only handlers with non-trivial work
# need to do anything — many settings are read live from `config` at
# every use site and need no propagation.

def _side_effect_buzzer_enabled(app_state, value: bool) -> None:
    buzzer = getattr(app_state, "buzzer", None)
    if buzzer is not None:
        buzzer.set_enabled(value)


def _side_effect_door_alarm_enabled(app_state, value: bool) -> None:
    if value:
        return
    cancel = getattr(app_state, "cancel_door_open_alarm", None)
    if cancel is not None:
        cancel()


def _side_effect_display_refresh(app_state, value: Any) -> None:
    display = getattr(app_state, "display", None)
    if display is not None:
        try:
            display.show_idle()
        except Exception:
            logger.exception("Failed to refresh idle display after settings change")


def _side_effect_app_lang(app_state, value: str) -> None:
    config.LANG = config._TRANSLATIONS.get(value.upper(), config._TRANSLATIONS["EN"])
    _side_effect_display_refresh(app_state, value)


def _side_effect_log_level(app_state, value: str) -> None:
    logging.getLogger().setLevel(getattr(logging, value, logging.INFO))


_SIDE_EFFECTS: dict[str, Callable[[Any, Any], None]] = {
    "buzzer_enabled": _side_effect_buzzer_enabled,
    "door_open_alarm_enabled": _side_effect_door_alarm_enabled,
    "display_idle_text": _side_effect_display_refresh,
    "display_idle_subtext": _side_effect_display_refresh,
    "app_lang": _side_effect_app_lang,
    "log_level": _side_effect_log_level,
}


def apply_single(key: str, value: Any, app_state) -> None:
    """Validate, mutate config, run live-update side effect, and persist.

    Rejects `system_mode` — it has its own dedicated endpoint and write path
    via SystemModeController.set_mode (which itself calls save_partial).
    """
    if key == "system_mode":
        raise ValueError("system_mode is set via POST /api/system/mode, not /api/settings")

    normalized = _validate(key, value)
    _mutate_config(key, normalized)

    handler = _SIDE_EFFECTS.get(key)
    if handler is not None:
        handler(app_state, normalized)

    save_partial({key: normalized})
