# SPDX-License-Identifier: MIT
"""Screen rotation for Xfce (which, unlike GNOME and KDE, has none of its own).

Listens to iio-sensor-proxy's ``monitor-sensor --accel`` and, when the tablet is turned, rotates the screen with xrandr and
turns every touch device's coordinates the same way with xinput, so a tap still lands where the finger is. Only used inside
Xfce sessions (X11).
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from typing import Iterable, Optional

# iio-sensor-proxy orientation -> (xrandr --rotate, 3x3 "Coordinate Transformation Matrix" for touch devices)
ROTATIONS = {
    "normal": ("normal", "1 0 0 0 1 0 0 0 1"),
    "bottom-up": ("inverted", "-1 0 1 0 -1 1 0 0 1"),
    "right-up": ("left", "0 -1 1 1 0 0 0 0 1"),
    "left-up": ("right", "0 1 0 -1 0 1 0 0 1"),
}
_TOUCH_WORDS = ("touch", "elan", "goodix", "wacom", "stylus", "finger", "pen ")
_ORIENTATION = re.compile(r"orientation[^:]*:\s*\(?\s*(normal|bottom-up|left-up|right-up)")


def parse_orientation(line: str) -> Optional[str]:
    """The orientation named on one line of ``monitor-sensor --accel`` output (None for other lines, e.g. "undefined")."""
    match = _ORIENTATION.search(line)
    return match.group(1) if match else None


def primary_output(xrandr_query: str) -> Optional[str]:
    """Name of the primary connected output (or the first connected one)."""
    connected = [line.split()[0] for line in xrandr_query.splitlines() if " connected" in line]
    for line in xrandr_query.splitlines():
        if " connected primary" in line:
            return line.split()[0]
    return connected[0] if connected else None


def touch_device_ids(xinput_list: str) -> list[str]:
    """Ids of touch screens and pens in ``xinput list`` output (touchpads are left alone)."""
    ids = []
    for line in xinput_list.splitlines():
        if "slave" not in line or "pointer" not in line:
            continue
        match = re.search(r"id=(\d+)", line)
        name = line.split("id=")[0].lower().replace("↳", "").strip(" ⎜⎡⎣\t")
        if match and any(w in name + " " for w in _TOUCH_WORDS) and "touchpad" not in name:
            ids.append(match.group(1))
    return ids


def commands(orientation: str, output: str, touch_ids: Iterable[str]) -> list[list[str]]:
    rotate, matrix = ROTATIONS[orientation]
    cmds = [["xrandr", "--output", output, "--rotate", rotate]]
    for device in touch_ids:
        cmds.append(["xinput", "set-prop", device, "Coordinate Transformation Matrix", *matrix.split()])
    return cmds


def _out(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True).stdout


def apply(orientation: str) -> None:
    output = primary_output(_out(["xrandr", "--query"]))
    if not output:
        return
    for command in commands(orientation, output, touch_device_ids(_out(["xinput", "list"]))):
        subprocess.run(command, capture_output=True)


def main() -> int:
    last: Optional[str] = None
    for _attempt in range(5):
        try:
            proc = subprocess.Popen(["monitor-sensor", "--accel"], stdout=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError:
            print("monitor-sensor (iio-sensor-proxy) is not installed", file=sys.stderr)
            return 1
        assert proc.stdout is not None
        for line in proc.stdout:
            orientation = parse_orientation(line)
            if orientation and orientation != last:
                apply(orientation)
                last = orientation
        time.sleep(5)       # the sensor service restarted; look again
    return 0


if __name__ == "__main__":
    sys.exit(main())
