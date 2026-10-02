# SPDX-License-Identifier: MIT
"""Battery health, and a charge limit where the tablet's firmware allows one.

*Health* is how much charge the battery holds now compared with when it was new (``energy_full`` / ``energy_full_design``
in sysfs). A *charge limit* (stop at 80 % to wear the battery less) only exists if the kernel exposes
``charge_control_end_threshold`` for this battery; many tablets don't, and then the tool says so instead of pretending.
Setting a limit needs an administrator (polkit) and is re-applied at every boot by ``lintab-battery-limit.service``.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

SYSFS = "/sys/class/power_supply"
CONF = "/etc/lintabos/battery.conf"
HELPER = "/usr/libexec/lintab/battery-limit"
MIN_LIMIT = 50


@dataclass
class Battery:
    name: str
    path: str
    status: str = ""
    capacity: Optional[int] = None
    full: Optional[int] = None          # energy_full or charge_full
    design: Optional[int] = None        # the same, when new
    cycles: Optional[int] = None
    threshold: Optional[int] = None     # current charge limit, if the kernel offers one
    technology: str = ""

    @property
    def health(self) -> Optional[float]:
        if self.full and self.design:
            return round(100.0 * self.full / self.design, 1)
        return None

    @property
    def limit_supported(self) -> bool:
        return self.threshold is not None


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _int(path: str) -> Optional[int]:
    value = _read(path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def read_batteries(root: str = SYSFS) -> list[Battery]:
    found = []
    for path in sorted(glob.glob(os.path.join(root, "*"))):
        if _read(os.path.join(path, "type")) != "Battery":
            continue
        full = _int(os.path.join(path, "energy_full"))
        design = _int(os.path.join(path, "energy_full_design"))
        if full is None or design is None:
            full, design = _int(os.path.join(path, "charge_full")), _int(os.path.join(path, "charge_full_design"))
        found.append(Battery(
            name=os.path.basename(path), path=path, status=_read(os.path.join(path, "status")) or "",
            capacity=_int(os.path.join(path, "capacity")), full=full, design=design,
            cycles=_int(os.path.join(path, "cycle_count")),
            threshold=_int(os.path.join(path, "charge_control_end_threshold")),
            technology=_read(os.path.join(path, "technology")) or ""))
    return found


def describe_health(health: Optional[float]) -> str:
    if health is None:
        return "unknown (the battery doesn't report its original capacity)"
    if health >= 90:
        verdict = "like new"
    elif health >= 80:
        verdict = "good"
    elif health >= 60:
        verdict = "worn: it holds noticeably less than when new"
    else:
        verdict = "heavily worn: consider a replacement"
    return f"{health:.0f} % of its original capacity ({verdict})"


def parse_limit(text: str) -> Optional[int]:
    """``80`` -> 80, ``off`` -> None (no limit, i.e. 100). Anything else is an error."""
    text = text.strip().lower()
    if text in ("off", "none", "100"):
        return None
    if not text.isdigit() or not (MIN_LIMIT <= int(text) <= 99):
        raise ValueError(f"the limit must be between {MIN_LIMIT} and 99, or 'off'")
    return int(text)


def read_conf(path: str = CONF) -> Optional[int]:
    try:
        with open(path) as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip() == "limit":
                    return parse_limit(value)
    except (OSError, ValueError):
        pass
    return None


def write_conf(limit: Optional[int], path: str = CONF) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"# lintab-battery: stop charging at this percent (remove the line or use 'off' for no limit)\n"
                f"limit = {limit if limit else 'off'}\n")


def apply_limit(limit: Optional[int], root: str = SYSFS) -> list[str]:
    """Write the limit to every battery that supports one. Returns problems (empty = all good)."""
    problems = []
    supported = [b for b in read_batteries(root) if b.limit_supported]
    if not supported:
        return ["This tablet's firmware doesn't offer a charge limit, so there is nothing to set."]
    for battery in supported:
        try:
            with open(os.path.join(battery.path, "charge_control_end_threshold"), "w") as f:
                f.write(str(limit or 100))
        except OSError as exc:
            problems.append(f"{battery.name}: {exc.strerror or exc}")
    return problems


def helper_main(argv: list[str]) -> int:
    """Root helper: ``battery-limit set N|off`` stores and applies a limit; ``battery-limit apply`` re-applies at boot."""
    if os.geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    try:
        if argv[:1] == ["set"] and len(argv) == 2:
            limit = parse_limit(argv[1])
            problems = apply_limit(limit)
            if not problems:
                write_conf(limit)
        elif argv == ["apply"]:
            limit = read_conf()
            problems = apply_limit(limit) if limit else []
        else:
            print("usage: battery-limit set N|off | apply", file=sys.stderr)
            return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-battery", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="show battery health and the charge limit (default)")
    lim = sub.add_parser("limit", help="set the charge limit (asks for an administrator's password)")
    lim.add_argument("value", help=f"{MIN_LIMIT}-99, or 'off'")
    args = ap.parse_args(argv)
    if args.cmd == "limit":
        try:
            parse_limit(args.value)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return subprocess.run(["pkexec", HELPER, "set", args.value]).returncode
    batteries = read_batteries()
    if not batteries:
        print("No battery found.")
        return 1
    for b in batteries:
        print(f"{b.name}: {b.capacity if b.capacity is not None else '?'} % ({b.status or 'unknown'})")
        print(f"  health: {describe_health(b.health)}")
        if b.cycles is not None:
            print(f"  charge cycles: {b.cycles}")
        print("  charge limit: " + (f"{b.threshold} %" if b.limit_supported else "not offered by this tablet's firmware"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
