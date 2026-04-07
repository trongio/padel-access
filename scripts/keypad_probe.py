#!/usr/bin/env python3
"""
Auto-detect 3x4 matrix keypad row/col pin mapping and update .env.

Procedure:
  1. Stop the service so GPIO pins are free:
       sudo bash scripts/stop.sh
  2. Run the probe (must be root for GPIO access):
       sudo python3 scripts/keypad_probe.py
  3. Press the 6 keys it asks for. The script writes the detected
     KEYPAD_ROW_PINS / KEYPAD_COL_PINS to .env.
  4. Restart:
       sudo bash scripts/start.sh

By default, the 7 pins from .env are probed. To probe a different set:
       sudo python3 scripts/keypad_probe.py 5 6 13 19 12 16 20
"""
import os
import sys
import time
from pathlib import Path

# If RPi.GPIO is missing in the current interpreter, try to re-exec under
# the project venv. The service runs from /opt/padel-access/venv, but a
# clone in $HOME or anywhere else will also have ./venv or ./.venv.
#
# NOTE: do NOT compare resolved paths — venv pythons are typically symlinks
# straight to /usr/bin/python3, so .resolve() makes them look identical to
# the system interpreter. The venv is selected via pyvenv.cfg, not a separate
# binary, so we must invoke it under its own (unresolved) path.
def _reexec_in_venv() -> None:
    if os.environ.get("KEYPAD_PROBE_REEXEC"):
        return  # already re-execed once, avoid infinite loop
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "venv" / "bin" / "python",
        here / ".venv" / "bin" / "python",
        Path("/opt/padel-access/venv/bin/python"),
    ]
    for py in candidates:
        if py.exists() and str(py) != sys.executable:
            env = os.environ.copy()
            env["KEYPAD_PROBE_REEXEC"] = "1"
            os.execve(str(py), [str(py), *sys.argv], env)

try:
    import RPi.GPIO as GPIO
except ImportError:
    _reexec_in_venv()
    # If we get here, no venv with RPi.GPIO was found.
    print("ERROR: RPi.GPIO is not installed in this Python environment, and")
    print("       no project venv was found at:")
    print("         ./venv, ./.venv, or /opt/padel-access/venv")
    print("       Either activate the venv or run: pip install RPi.GPIO")
    sys.exit(1)


PROBE_SETTLE_S = 0.001       # let the line settle after switching direction
SCAN_INTERVAL_S = 0.02       # poll period while waiting for a press
DEBOUNCE_REPEATS = 3         # require N consecutive identical scans to confirm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def detect_pair(pins: list[int]) -> tuple[int, int] | None:
    """
    Drive each pin LOW in turn and read the others. If exactly one pair of
    pins is shorted (i.e. a single key is pressed), return that pair sorted
    ascending. Return None if no key — or more than one — is detected.
    """
    found: set[frozenset[int]] = set()
    for driver in pins:
        GPIO.setup(driver, GPIO.OUT, initial=GPIO.LOW)
        time.sleep(PROBE_SETTLE_S)
        for other in pins:
            if other == driver:
                continue
            if GPIO.input(other) == GPIO.LOW:
                found.add(frozenset({driver, other}))
        GPIO.setup(driver, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        time.sleep(PROBE_SETTLE_S)

    if len(found) != 1:
        return None
    a, b = sorted(next(iter(found)))
    return (a, b)


def wait_for_press(pins: list[int], key_label: str) -> tuple[int, int]:
    """Block until a single, debounced key press is detected, then wait for release."""
    print(f"  → Press and HOLD key  [ {key_label} ]  ... ", end="", flush=True)
    last_pair: tuple[int, int] | None = None
    stable = 0
    while True:
        pair = detect_pair(pins)
        if pair is not None and pair == last_pair:
            stable += 1
            if stable >= DEBOUNCE_REPEATS:
                print(f"detected GPIO{pair[0]} ↔ GPIO{pair[1]}")
                # wait for release so the next prompt doesn't see this press
                while detect_pair(pins) is not None:
                    time.sleep(0.05)
                return pair
        else:
            stable = 0
            last_pair = pair
        time.sleep(SCAN_INTERVAL_S)


def load_default_pins() -> list[int]:
    """Read the 7 pins currently configured in .env (rows + cols)."""
    rows: list[int] = []
    cols: list[int] = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("KEYPAD_ROW_PINS="):
                rows = [int(x) for x in line.split("=", 1)[1].split(",") if x.strip()]
            elif line.startswith("KEYPAD_COL_PINS="):
                cols = [int(x) for x in line.split("=", 1)[1].split(",") if x.strip()]
    if not rows or not cols:
        rows = [5, 6, 13, 19]
        cols = [12, 16, 20]
        print(f"NOTE: .env had no keypad pins — using defaults {rows + cols}")
    return rows + cols


def update_env(row_pins: list[int], col_pins: list[int]) -> None:
    row_line = f"KEYPAD_ROW_PINS={','.join(map(str, row_pins))}"
    col_line = f"KEYPAD_COL_PINS={','.join(map(str, col_pins))}"

    if not ENV_PATH.exists():
        print(f"\nWARNING: {ENV_PATH} does not exist. Add these lines manually:")
        print(f"  {row_line}")
        print(f"  {col_line}")
        return

    lines = ENV_PATH.read_text().splitlines()
    new_lines: list[str] = []
    saw_rows = saw_cols = False
    for line in lines:
        if line.startswith("KEYPAD_ROW_PINS="):
            new_lines.append(row_line)
            saw_rows = True
        elif line.startswith("KEYPAD_COL_PINS="):
            new_lines.append(col_line)
            saw_cols = True
        else:
            new_lines.append(line)
    if not saw_rows:
        new_lines.append(row_line)
    if not saw_cols:
        new_lines.append(col_line)
    ENV_PATH.write_text("\n".join(new_lines) + "\n")
    print(f"\n✓ Updated {ENV_PATH}")
    print(f"    {row_line}")
    print(f"    {col_line}")


def main() -> int:
    if len(sys.argv) > 1:
        if len(sys.argv) != 8:
            print("Usage: keypad_probe.py [pin1 pin2 pin3 pin4 pin5 pin6 pin7]")
            print("       (omit args to probe the 7 pins from .env)")
            return 1
        try:
            pins = [int(x) for x in sys.argv[1:]]
        except ValueError:
            print("ERROR: all pin arguments must be integers (BCM GPIO numbers)")
            return 1
    else:
        pins = load_default_pins()

    if len(set(pins)) != 7:
        print(f"ERROR: need 7 distinct GPIO pins, got {pins}")
        return 1

    print(f"Probing 7 GPIO pins (BCM): {pins}")
    print("Make sure padel-access is stopped:  sudo bash scripts/stop.sh")
    print()

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for p in pins:
        GPIO.setup(p, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    try:
        print("Press the keys below in order. Hold each one until 'detected'.\n")

        # Key 1 → row0 + col0 (unknown which is which yet)
        pair_1 = wait_for_press(pins, "1")
        # Key 2 → row0 + col1; the pin shared with key 1 is row0
        pair_2 = wait_for_press(pins, "2")
        common = set(pair_1) & set(pair_2)
        if len(common) != 1:
            print("ERROR: keys 1 and 2 don't share a row pin. Try again.")
            return 1
        row_0 = next(iter(common))
        col_0 = next(iter(set(pair_1) - {row_0}))
        col_1 = next(iter(set(pair_2) - {row_0}))

        # Key 3 → row0 + col2
        pair_3 = wait_for_press(pins, "3")
        if row_0 not in pair_3:
            print("ERROR: key 3 didn't share row 0 with key 1. Try again.")
            return 1
        col_2 = next(iter(set(pair_3) - {row_0}))

        # Key 4 → row1 + col0
        pair_4 = wait_for_press(pins, "4")
        if col_0 not in pair_4:
            print("ERROR: key 4 didn't share col 0 with key 1. Try again.")
            return 1
        row_1 = next(iter(set(pair_4) - {col_0}))

        # Key 7 → row2 + col0
        pair_7 = wait_for_press(pins, "7")
        if col_0 not in pair_7:
            print("ERROR: key 7 didn't share col 0 with key 1. Try again.")
            return 1
        row_2 = next(iter(set(pair_7) - {col_0}))

        # Key * → row3 + col0
        pair_star = wait_for_press(pins, "*")
        if col_0 not in pair_star:
            print("ERROR: key * didn't share col 0 with key 1. Try again.")
            return 1
        row_3 = next(iter(set(pair_star) - {col_0}))

        row_pins = [row_0, row_1, row_2, row_3]
        col_pins = [col_0, col_1, col_2]

        if len(set(row_pins + col_pins)) != 7:
            print("ERROR: detected pins are not all distinct — wiring inconsistency.")
            print(f"       rows={row_pins}, cols={col_pins}")
            return 1

        print()
        print("=" * 52)
        print(f"  ROW pins (rows of keypad layout): {row_pins}")
        print(f"  COL pins (cols of keypad layout): {col_pins}")
        print("=" * 52)

        update_env(row_pins, col_pins)
        print("\nNow restart the service:  sudo bash scripts/start.sh")
        return 0
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    sys.exit(main())