# SPDX-License-Identifier: MIT
"""Fixes, as far as software can, the tablet not turning back on after the power button is pressed or the folio is closed.

Two independent things, because the freeze can come from either direction:

1. **Prefer the more reliable sleep mode.** Where the firmware offers real "deep" (S3) sleep as well as "s2idle"
   (suspend-to-idle, the usual default on "Modern Standby" firmware), LintabOS selects "deep" at every boot
   (``lintab-sleep-mode-apply.service``). This is the single most common fix for exactly this symptom — "suspends, never
   wakes, needs a hard power-off" — on this class of Intel hardware: s2idle depends on every driver's own suspend/resume
   hooks behaving correctly, where deep/S3 instead powers most of the system off and back on, which has far fewer places to
   go wrong. Where the firmware only offers s2idle, there is nothing to switch to, and this does nothing — see
   ``lintab-sleep-mode status``.

2. **Make the power button and folio switch do something that can't get stuck.** GNOME's own power handling is partly
   hardcoded for tablets ("Tablets always suspend, ignoring all the other action options" — gnome-settings-daemon's own
   schema says so) and has no setting for the lid switch at all. Rather than fight GNOME's and systemd-logind's handling,
   LintabOS's own small background service (``lintab-sleep-guard``) takes the power button and lid switch over directly —
   the same mechanism GNOME itself uses to take them over from logind, a low-level inhibitor lock — and does whatever you
   choose in **Sleep & Power Button**: suspend (the normal choice, with the deep-sleep preference already applied), lock
   the screen without suspending (if suspend still doesn't come back reliably even with deep sleep), or do nothing (the
   screen stays on and the battery drains in the bag — only for when the other two are both unusable).
"""

from __future__ import annotations

import argparse
import os
import re
import select
import struct
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

MEM_SLEEP = "/sys/power/mem_sleep"
CONF_PATH = "/etc/lintabos/sleep-mode.conf"
SET_HELPER = "/usr/libexec/lintab/sleep-mode-set"
GUARD_SERVICE = "lintab-sleep-guard.service"
ACTIONS = ("suspend", "lock", "nothing")

KEY_POWER = 116
SW_LID = 0
EV_KEY = 1
EV_SW = 5
EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux: 24 bytes

Runner = Callable[..., "subprocess.CompletedProcess"]


# ------------------------------------------------------------------------- sleep mode --

@dataclass
class MemSleep:
    current: str
    available: list[str]

    @property
    def deep_available(self) -> bool:
        return "deep" in self.available

    @property
    def prefers_deep(self) -> bool:
        return self.deep_available and self.current != "deep"


def parse_mem_sleep(text: str) -> Optional[MemSleep]:
    text = text.strip()
    if not text:
        return None
    modes, current = [], ""
    for word in text.split():
        m = re.fullmatch(r"\[(\w+)\]", word)
        if m:
            current = m.group(1)
            modes.append(m.group(1))
        else:
            modes.append(word)
    return MemSleep(current, modes) if modes else None


def read_mem_sleep(path: str = MEM_SLEEP) -> Optional[MemSleep]:
    """None when the platform doesn't expose a choice at all — not a failure, just nothing to prefer."""
    try:
        with open(path) as f:
            return parse_mem_sleep(f.read())
    except OSError:
        return None


def apply_preferred_mem_sleep(path: str = MEM_SLEEP, write: Optional[Callable[[str], None]] = None) -> str:
    """Select "deep" if it's offered and not already chosen. Never raises; returns a one-line status for display/logging."""
    status = read_mem_sleep(path)
    if status is None:
        return "This platform does not offer a choice of sleep mode; nothing to change."
    if not status.deep_available:
        return f"This platform only offers {', '.join(status.available)} (no deep sleep to prefer); left as is."
    if not status.prefers_deep:
        return "Deep sleep is already selected."
    writer = write or (lambda text: open(path, "w").write(text))
    try:
        writer("deep")
    except OSError as exc:
        return f"Could not switch to deep sleep: {exc}"
    return "Switched from s2idle to deep sleep (the more reliable choice for waking back up on this kind of hardware)."


def mem_sleep_apply_main(path: str = MEM_SLEEP) -> int:
    if os.geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    print(apply_preferred_mem_sleep(path))
    return 0


# --------------------------------------------------------------------------- actions --

def read_actions(path: str = CONF_PATH) -> dict[str, str]:
    actions = {"power": "suspend", "lid": "suspend"}
    try:
        with open(path) as f:
            for line in f:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key in actions and value in ACTIONS:
                    actions[key] = value
    except OSError:
        pass
    return actions


def write_actions(power: str, lid: str, path: str = CONF_PATH) -> None:
    if power not in ACTIONS or lid not in ACTIONS:
        raise ValueError(f"actions must be one of {', '.join(ACTIONS)}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# LintabOS: what the power button and closing the folio do. Written by Sleep & Power Button\n"
                f"# (or `lintab-sleep-mode set`); lintab-sleep-guard picks this up the next time either is triggered.\n"
                f"power = {power}\nlid = {lid}\n")


def perform_action(action: str, run: Runner = subprocess.run) -> None:
    """Never raises: a crash here must not leave the tablet unresponsive to its own power button."""
    try:
        if action == "suspend":
            apply_preferred_mem_sleep()         # best effort; a failure here must not stop the suspend itself
            run(["systemctl", "suspend"], capture_output=True)
        elif action == "lock":
            run(["loginctl", "lock-sessions"], capture_output=True)
        # "nothing": exactly that
    except Exception:  # noqa: BLE001 - the guard loop must survive whatever goes wrong here
        pass


def helper_main(argv: list[str], run: Runner = subprocess.run) -> int:
    if os.geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    if len(argv) != 2:
        print(f"usage: sleep-mode-set POWER LID  (each one of {', '.join(ACTIONS)})", file=sys.stderr)
        return 2
    try:
        write_actions(argv[0], argv[1])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


# ------------------------------------------------------------------ power/lid detection --

def has_bit(bitmap: str, code: int) -> bool:
    """Is ``code`` set in a /proc/bus/input/devices bitmap line (hex words, highest first, 64 bits each)?"""
    words = bitmap.split()
    index = len(words) - 1 - code // 64
    if index < 0 or index >= len(words):
        return False
    try:
        return bool(int(words[index], 16) >> (code % 64) & 1)
    except ValueError:
        return False


def _devices_with(proc_devices: str, bitmap_prefix: str, code: int) -> list[str]:
    found = []
    for block in proc_devices.strip().split("\n\n"):
        bitmap = next((l[len(bitmap_prefix):] for l in block.splitlines() if l.startswith(bitmap_prefix)), "")
        handler_line = next((l for l in block.splitlines() if l.startswith("H: Handlers=")), "")
        handlers = handler_line[len("H: Handlers="):].split()  # the first name sits right after "=", with no space
        event = next((w for w in handlers if w.startswith("event")), "")
        if event and bitmap and has_bit(bitmap, code):
            found.append(f"/dev/input/{event}")
    return found


def power_button_devices(proc_devices: str) -> list[str]:
    return _devices_with(proc_devices, "B: KEY=", KEY_POWER)


def lid_switch_devices(proc_devices: str) -> list[str]:
    return _devices_with(proc_devices, "B: SW=", SW_LID)


def read_proc_devices(path: str = "/proc/bus/input/devices") -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def decode_events(data: bytes) -> list[tuple[int, int, int]]:
    return [EVENT.unpack_from(data, o)[2:] for o in range(0, len(data) - EVENT.size + 1, EVENT.size)]


def classify_event(type_: int, code: int, value: int) -> Optional[str]:
    """"power" or "lid-close" when this event should trigger the configured action; None otherwise (including lid-open,
    key releases and repeats, and anything else)."""
    if type_ == EV_KEY and code == KEY_POWER and value == 1:
        return "power"
    if type_ == EV_SW and code == SW_LID and value == 1:
        return "lid-close"
    return None


def guard_loop(power_devices: list[str], lid_devices: list[str], read_actions_fn: Callable[[], dict[str, str]],
              perform: Callable[[str], None], stop: Optional[Callable[[], bool]] = None,
              opener: Callable[[str], int] = lambda p: os.open(p, os.O_RDONLY | os.O_NONBLOCK),
              reader: Callable[[int], bytes] = lambda fd: os.read(fd, EVENT.size * 16)) -> None:
    """Watch the given devices and dispatch "power"/"lid-close" events to the configured action. Runs until ``stop()``
    returns True (defaults to never, for the real daemon)."""
    fds: dict[int, str] = {}
    for path in power_devices:
        try:
            fds[opener(path)] = "power"
        except OSError:
            continue
    for path in lid_devices:
        try:
            fds[opener(path)] = "lid"
        except OSError:
            continue
    try:
        while not (stop and stop()):
            ready, _w, _x = select.select(list(fds), [], [], 0.5)
            for fd in ready:
                try:
                    kind = classify_event_batch(reader(fd))
                except OSError:
                    continue
                if kind:
                    perform(read_actions_fn().get("power" if kind == "power" else "lid", "suspend"))
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


def classify_event_batch(data: bytes) -> Optional[str]:
    for type_, code, value in decode_events(data):
        kind = classify_event(type_, code, value)
        if kind:
            return "power" if kind == "power" else "lid"
    return None


def guard_main() -> int:
    devices = read_proc_devices()
    power = power_button_devices(devices)
    lid = lid_switch_devices(devices)
    if not power and not lid:
        print("lintab-sleep-guard: no power button or lid switch found; nothing to watch.", file=sys.stderr)
        return 0
    guard_loop(power, lid, read_actions, perform_action)
    return 0


# --------------------------------------------------------------------------------- CLI --

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-sleep-mode", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("set", help="choose what the power button and the folio do (asks for an administrator's password)")
    s.add_argument("power", choices=ACTIONS)
    s.add_argument("lid", choices=ACTIONS)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        ms = read_mem_sleep()
        print(f"sleep mode: {ms.current if ms else 'unknown'}"
              + (f" (available: {', '.join(ms.available)})" if ms else " (this platform offers no choice)"))
        actions = read_actions()
        print(f"power button: {actions['power']}   folio close: {actions['lid']}")
        return 0
    return subprocess.run(["pkexec", SET_HELPER, args.power, args.lid]).returncode


if __name__ == "__main__":
    sys.exit(main())
