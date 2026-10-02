# SPDX-License-Identifier: MIT
"""Automatic tablet mode for detachable tablets such as the IdeaPad Duet 3.

The folio keyboard is an external USB (or Bluetooth) keyboard, so "is there an external keyboard?" tells us which
mode the person is in:

* keyboard attached  -> laptop mode: the on-screen keyboard is turned off;
* keyboard detached  -> tablet mode: the on-screen keyboard is turned on.

GNOME's own screen rotation keeps working in both modes (it follows the accelerometer). Settings are changed through
gsettings for the logged-in user; the background service ``lintab-tablet-mode run`` watches udev and reapplies them
whenever a keyboard appears or disappears. ``lintab-tablet-mode tablet|laptop|auto`` overrides the automatic choice.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from typing import Callable, Iterable, Optional

CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "lintabos", "tablet-mode.conf")
SETTLE_SECONDS = 1.5  # the pogo connection flickers when the folio is attached or detached

# Names of input nodes that report ID_INPUT_KEYBOARD but are not something you type on.
_NOT_TYPING = ("consumer control", "system control", "wireless radio control", "video bus")


def is_external_keyboard(props: dict[str, str]) -> bool:
    """Is this udev input device a physical keyboard connected over USB or Bluetooth?"""
    if props.get("ID_INPUT_KEYBOARD") != "1":
        return False
    if props.get("ID_BUS") not in ("usb", "bluetooth"):
        return False
    if "/virtual/" in props.get("DEVPATH", ""):
        return False
    name = props.get("NAME", "").strip('"').lower()
    return not any(skip in name for skip in _NOT_TYPING)


def keyboard_attached(devices: Iterable[dict[str, str]]) -> bool:
    return any(is_external_keyboard(d) for d in devices)


def decide(mode: str, devices: Iterable[dict[str, str]]) -> str:
    """Return "tablet" or "laptop" for the configured mode ("auto", "tablet" or "laptop")."""
    if mode in ("tablet", "laptop"):
        return mode
    return "laptop" if keyboard_attached(devices) else "tablet"


def read_mode(path: str = CONF) -> str:
    try:
        with open(path) as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip() == "mode" and value.strip() in ("auto", "tablet", "laptop"):
                    return value.strip()
    except OSError:
        pass
    return "auto"


def write_mode(mode: str, path: str = CONF) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"# lintab-tablet-mode: auto = follow the keyboard, tablet or laptop = force a mode\nmode = {mode}\n")


def apply_state(state: str, run: Callable[[list[str]], object] = subprocess.run) -> list[list[str]]:
    """Switch GNOME's on-screen keyboard for the given state. Returns the commands issued (for tests)."""
    osk = "true" if state == "tablet" else "false"
    commands = [["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-keyboard-enabled", osk]]
    for command in commands:
        run(command)
    return commands


def current_devices() -> list[dict[str, str]]:
    """All input devices known to udev, as property dictionaries."""
    try:
        import pyudev
    except ImportError:
        return []
    context = pyudev.Context()
    return [dict(d.properties) for d in context.list_devices(subsystem="input")]


SOUND_DIR = "/usr/share/lintabos/sounds"


def event_text(attached: bool, state: str) -> tuple[str, str]:
    """(title, body) of the notification shown when the keyboard is attached or detached."""
    title = "Keyboard Attached" if attached else "Keyboard Detached"
    body = ("Laptop mode: on-screen keyboard off" if state == "laptop" else "Tablet mode: on-screen keyboard on")
    return title, body


def sound_command(attached: bool, sound_dir: str = SOUND_DIR, which: Callable[[str], Optional[str]] = shutil.which
                  ) -> Optional[list[str]]:
    """The command that plays the snap, or None when there is no sound file or no player."""
    path = os.path.join(sound_dir, "keyboard-attached.wav" if attached else "keyboard-detached.wav")
    if not os.path.exists(path):
        return None
    for player in ("pw-play", "paplay", "aplay"):
        if which(player):
            return [player, "-q", path] if player == "aplay" else [player, path]
    return None


def plan_refresh(mode: str, devices: list[dict[str, str]], last_attached: Optional[bool], last_state: Optional[str]
                 ) -> tuple[str, bool, Optional[bool], bool]:
    """Decide what to do after the devices changed.

    Returns (state, attached, event, apply): ``event`` is True/False (keyboard attached/detached) when the physical
    keyboard changed since last time and this isn't the first look, else None; ``apply`` says whether the on-screen
    keyboard setting has to be rewritten. The message follows the real keyboard even when a mode is forced.
    """
    attached = keyboard_attached(devices)
    state = decide(mode, devices)
    event = attached if (last_attached is not None and attached != last_attached) else None
    return state, attached, event, state != last_state


def announce(attached: bool, state: str) -> None:
    title, body = event_text(attached, state)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "LintabOS", "-i", "input-keyboard-symbolic", title, body],
                       capture_output=True)
    command = sound_command(attached)
    if command:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_service() -> int:
    """Apply the current state, then watch udev and reapply when keyboards come and go."""
    try:
        import pyudev
    except ImportError:
        print("python3-pyudev is required", file=sys.stderr)
        return 1
    last_attached: Optional[bool] = None
    last_state: Optional[str] = None

    def refresh() -> None:
        nonlocal last_attached, last_state
        state, attached, event, apply = plan_refresh(read_mode(), current_devices(), last_attached, last_state)
        if apply:
            apply_state(state)
        if event is not None:
            announce(event, state)
        last_attached, last_state = attached, state

    refresh()
    monitor = pyudev.Monitor.from_netlink(pyudev.Context())
    monitor.filter_by("input")
    for _device in iter(lambda: monitor.poll(timeout=None), None):
        time.sleep(SETTLE_SECONDS)
        while monitor.poll(timeout=0.2) is not None:  # drain the burst of events a (de)attach causes
            pass
        refresh()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-tablet-mode", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["auto", "tablet", "laptop", "status", "run"], nargs="?", default="status")
    args = ap.parse_args(argv)
    if args.action == "run":
        return run_service()
    if args.action in ("auto", "tablet", "laptop"):
        write_mode(args.action)
        apply_state(decide(args.action, current_devices()))
        print(f"mode: {args.action}")
        return 0
    devices = current_devices()
    mode = read_mode()
    print(f"setting: {mode}\nexternal keyboard attached: {'yes' if keyboard_attached(devices) else 'no'}\n"
          f"state: {decide(mode, devices)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
