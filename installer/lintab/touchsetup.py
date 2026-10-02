# SPDX-License-Identifier: MIT
"""First-login touch settings for the optional KDE Plasma and Xfce desktops.

GNOME is touch-ready out of the box (and tuned by dconf defaults). For the other two desktops this runs once per user, at
the first login into that desktop, and sets:

* **Xfce**: a 48 px panel, a bigger cursor and a 144 dpi interface (the Duet's 2000x1200 11" panel is dense), single-click
  in the file manager, and Onboard (on-screen keyboard) set to appear whenever you tap a text field.
* **KDE Plasma**: Maliit as the on-screen keyboard and a 56 px panel. (Plasma's window manager already rotates the screen
  from the accelerometer and scales for the panel's density by itself.)

Everything is a plain per-user setting: delete ``~/.config/lintabos/touch-setup-*.done`` to run it again, or just change the
settings in the desktop's own settings app. ``lintab-tablet-mode`` later switches the on-screen keyboard with the folio.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Callable, NamedTuple, Optional

VERSION = "1"
MALIIT_DESKTOP = "/usr/share/applications/com.github.maliit.keyboard.desktop"
KDE_PANEL_HEIGHT = 56
XFCE_PANEL_SIZE = 48

Runner = Callable[[list[str]], int]


class XfconfSetting(NamedTuple):
    channel: str
    prop: str
    kind: str       # xfconf-query -t value
    value: str


def xfce_settings() -> list[XfconfSetting]:
    return [
        XfconfSetting("xsettings", "/Xft/DPI", "int", "144"),
        XfconfSetting("xsettings", "/Gtk/CursorThemeSize", "int", "32"),
        XfconfSetting("xfce4-panel", "/panels/panel-1/size", "int", str(XFCE_PANEL_SIZE)),
        XfconfSetting("thunar", "/misc-single-click", "bool", "true"),
    ]


def xfce_commands() -> list[list[str]]:
    commands = [["xfconf-query", "-c", s.channel, "-p", s.prop, "-n", "-t", s.kind, "-s", s.value] for s in xfce_settings()]
    commands += [
        ["gsettings", "set", "org.onboard.auto-show", "enabled", "true"],      # appear when a text field is tapped
        ["gsettings", "set", "org.onboard.window", "docking-enabled", "true"],  # dock to the screen edge, not float
    ]
    return commands


def kde_commands() -> list[list[str]]:
    def write(file: str, group: str, key: str, value: str) -> list[str]:
        return ["kwriteconfig6", "--file", file, "--group", group, "--key", key, value]
    return [
        write("kwinrc", "Wayland", "InputMethod", MALIIT_DESKTOP),
        write("kwinrc", "Wayland", "VirtualKeyboardEnabled", "true"),
        write("kdeglobals", "KDE", "SingleClick", "true"),
    ]


def kde_panel_script(height: int = KDE_PANEL_HEIGHT) -> str:
    return f"panels().forEach(function (p) {{ p.height = {height}; }});"


def kde_panel_command(height: int = KDE_PANEL_HEIGHT) -> list[str]:
    return ["dbus-send", "--session", "--print-reply", "--dest=org.kde.plasmashell", "/PlasmaShell",
            "org.kde.PlasmaShell.evaluateScript", f"string:{kde_panel_script(height)}"]


def marker_path(desktop: str, config_home: Optional[str] = None) -> str:
    base = config_home or os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "lintabos", f"touch-setup-{desktop}.done")


def _run(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, capture_output=True, text=True).returncode
    except FileNotFoundError:
        return 127


def plasmashell_running() -> bool:
    got = subprocess.run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus.NameHasOwner", "string:org.kde.plasmashell"], capture_output=True, text=True)
    return "boolean true" in got.stdout


def wait_for(check: Callable[[], bool], timeout: float = 90.0, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> bool:
    """Poll ``check`` until it is true or the timeout passes (Plasma's shell starts a moment after the login)."""
    deadline = clock() + timeout
    while not check():
        if clock() >= deadline:
            return False
        sleep(2.0)
    return True


def apply(desktop: str, run: Runner = _run, wait: Callable[[], bool] = lambda: wait_for(plasmashell_running)) -> bool:
    """Run the settings for one desktop. True only if every step worked (so a partial run is retried next login)."""
    commands = xfce_commands() if desktop == "xfce" else kde_commands()
    ok = all(run(c) == 0 for c in commands)
    if desktop == "kde":
        ok = (wait() and run(kde_panel_command()) == 0) and ok
    return ok


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-touch-setup", description=__doc__.split("\n\n")[0])
    ap.add_argument("desktop", choices=["xfce", "kde"])
    ap.add_argument("--force", action="store_true", help="run again even if it already ran")
    args = ap.parse_args(argv)
    marker = marker_path(args.desktop)
    try:
        with open(marker) as f:
            if f.read().strip() == VERSION and not args.force:
                return 0
    except OSError:
        pass
    if not apply(args.desktop):
        print("some touch settings could not be applied; will try again at the next login", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w") as f:
        f.write(VERSION + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
