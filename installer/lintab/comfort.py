# SPDX-License-Identifier: MIT
"""Reading mode: a warm, easy-on-the-eyes screen that stays on until you turn it off (GNOME).

* **On**: GNOME's Night Light is held on all day at a warm 2700 K. Your own Night Light settings are saved first and put
  back when you turn reading mode off.
* **--grey (experimental)**: also drains the colour out of the screen, using the accessibility magnifier at 1x with zero
  saturation. It is a trick, not a feature GNOME advertises, so it may misbehave on some setups.

Auto-brightness from the ambient light sensor is a GNOME default that LintabOS switches on (see the dconf defaults); this
tool doesn't change it. KDE Plasma and Xfce have their own night-colour settings and are not touched here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Callable, Optional

COLOR = "org.gnome.settings-daemon.plugins.color"
MAGNIFIER = "org.gnome.desktop.a11y.magnifier"
APPS = "org.gnome.desktop.a11y.applications"

WARM_KELVIN = 2700
STATE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "lintabos", "reading-mode.json")

# (schema, key, value while reading mode is on)
NIGHT_LIGHT = [
    (COLOR, "night-light-enabled", "true"),
    (COLOR, "night-light-schedule-automatic", "false"),
    (COLOR, "night-light-schedule-from", "0.0"),
    (COLOR, "night-light-schedule-to", "24.0"),
    (COLOR, "night-light-temperature", f"uint32 {WARM_KELVIN}"),
]
GREYSCALE = [
    (MAGNIFIER, "mag-factor", "1.0"),
    (MAGNIFIER, "color-saturation", "0.0"),
    (MAGNIFIER, "mouse-tracking", "'none'"),
    (APPS, "screen-magnifier-enabled", "true"),
]

Getter = Callable[[str, str], str]
Setter = Callable[[str, str, str], None]


def _get(schema: str, key: str) -> str:
    return subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True).stdout.strip()


def _set(schema: str, key: str, value: str) -> None:
    subprocess.run(["gsettings", "set", schema, key, value], capture_output=True)


def is_on(path: str = STATE) -> bool:
    return os.path.exists(path)


def turn_on(grey: bool = False, get: Getter = _get, set_: Setter = _set, path: str = STATE) -> None:
    wanted = NIGHT_LIGHT + (GREYSCALE if grey else [])
    if not is_on(path):                         # remember the person's own values, but only the first time
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({f"{s}|{k}": get(s, k) for s, k, _v in NIGHT_LIGHT + GREYSCALE}, f)
    for schema, key, value in wanted:
        set_(schema, key, value)


def turn_off(set_: Setter = _set, path: str = STATE) -> None:
    try:
        with open(path) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    for composite, value in saved.items():
        schema, key = composite.split("|", 1)
        if value:
            set_(schema, key, value)
    try:
        os.remove(path)
    except OSError:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-reading-mode", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["on", "off", "toggle", "status"], nargs="?", default="toggle")
    ap.add_argument("--grey", action="store_true", help="also remove colour (experimental)")
    args = ap.parse_args(argv)
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "KDE" in desktop or "XFCE" in desktop:
        print("Reading mode is for GNOME. Use the night-colour setting of your desktop's own settings app.", file=sys.stderr)
        return 1
    action = args.action
    if action == "toggle":
        action = "off" if is_on() else "on"
    if action == "status":
        print("reading mode: " + ("on" if is_on() else "off"))
        return 0
    if action == "on":
        turn_on(args.grey)
        print("reading mode: on" + (" (greyscale)" if args.grey else ""))
    else:
        turn_off()
        print("reading mode: off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
