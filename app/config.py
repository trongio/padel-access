import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BASE_DIR / ".env")


def _bool(val: str, default: bool = False) -> bool:
    if not val:
        return default
    return val.strip().lower() in ("true", "1", "yes")


def _int_list(val: str) -> list[int]:
    if not val:
        return []
    return [int(x.strip()) for x in val.split(",")]


# ─── App ──────────────────────────────────────────
APP_HOST: str = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
DEFAULT_API_KEY_PLACEHOLDER = "change_me_strong_secret"
API_KEY: str = os.getenv("API_KEY", DEFAULT_API_KEY_PLACEHOLDER)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
TZ: str = os.getenv("TZ", "UTC")

# ─── Language & Display ───────────────────────────
APP_LANG: str = os.getenv("APP_LANG", "EN")  # EN or KA
CODE_LENGTH: int = int(os.getenv("CODE_LENGTH", "6"))  # 4-8 digits
# true: mask digits as ***  false: show entered digits as-is on the display
MASK_CODE_INPUT: bool = _bool(os.getenv("MASK_CODE_INPUT", "true"), default=True)

_TRANSLATIONS = {
    "EN": {
        "enter_code": "Enter Code:",
        "access_granted": "Welcome",
        "error": "Error",
        "invalid_code": "Invalid code",
        "code_expired": "Code expired",
        "code_not_valid": "Code not yet valid",
        "code_used": "Code already used",
        "shutting_down": "Shutting down...",
        "until": "Until",
    },
    "KA": {
        "enter_code": "შეიყვანეთ კოდი:",
        "access_granted": "მობრძანდით",
        "error": "შეცდომა",
        "invalid_code": "არასწორი კოდი",
        "code_expired": "კოდი ვადაგასულია",
        "code_not_valid": "კოდი ჯერ არ მოქმედებს",
        "code_used": "კოდი უკვე გამოყენებულია",
        "shutting_down": "გამორთვა...",
        "until": "სანამ",
    },
}

LANG: dict[str, str] = _TRANSLATIONS.get(APP_LANG.upper(), _TRANSLATIONS["EN"])

_KA_MONTHS = ["იან", "თებ", "მარ", "აპრ", "მაი", "ივნ", "ივლ", "აგვ", "სექ", "ოქტ", "ნოე", "დეკ"]
_KA_DAYS = ["ორშ", "სამ", "ოთხ", "ხუთ", "პარ", "შაბ", "კვი"]


def format_date(dt) -> str:
    """Format date for display in the configured language."""
    if APP_LANG.upper() == "KA":
        return f"{dt.day} {_KA_MONTHS[dt.month - 1]} {dt.year}, {_KA_DAYS[dt.weekday()]}"
    return dt.strftime("%d %b %Y, %a")


DISPLAY_IDLE_TEXT: str = os.getenv("DISPLAY_IDLE_TEXT", "AllDigital")
DISPLAY_IDLE_SUBTEXT: str = os.getenv("DISPLAY_IDLE_SUBTEXT", "")

# ─── Hardware ─────────────────────────────────────
RELAY_ACTIVE_LOW: bool = _bool(os.getenv("RELAY_ACTIVE_LOW", "true"), default=True)
DOOR_RELAY_GPIO: int = int(os.getenv("DOOR_RELAY_GPIO", "17"))
DOOR_UNLOCK_DURATION: int = int(os.getenv("DOOR_UNLOCK_DURATION", "5"))

EXIT_BUTTON_GPIO: int = int(os.getenv("EXIT_BUTTON_GPIO", "26"))

BUZZER_GPIO: int = int(os.getenv("BUZZER_GPIO", "24"))
BUZZER_ENABLED: bool = _bool(os.getenv("BUZZER_ENABLED", "true"), default=True)

LIGHT_RELAY_1_GPIO: int = int(os.getenv("LIGHT_RELAY_1_GPIO", "27"))
LIGHT_RELAY_2_GPIO: int = int(os.getenv("LIGHT_RELAY_2_GPIO", "22"))

KEYPAD_ROW_PINS: list[int] = _int_list(os.getenv("KEYPAD_ROW_PINS", "5,6,13,19"))
KEYPAD_COL_PINS: list[int] = _int_list(os.getenv("KEYPAD_COL_PINS", "12,16,20"))

# Light relay mapping: light_id -> GPIO pin
LIGHT_RELAYS: dict[int, int] = {
    1: LIGHT_RELAY_1_GPIO,
    2: LIGHT_RELAY_2_GPIO,
}

# ─── Cloudflare Tunnel ────────────────────────────
CF_TUNNEL_TOKEN: str = os.getenv("CF_TUNNEL_TOKEN", "")
CF_TUNNEL_NAME: str = os.getenv("CF_TUNNEL_NAME", "padel-access")

# ─── Database ─────────────────────────────────────
DATA_DIR = _BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DATABASE_URL: str = f"sqlite:///{DATA_DIR / 'padel_access.db'}"
