# SPDX-License-Identifier: MIT
"""lintab-uninstall: remove LintabOS and give the space back to Windows (run from the live USB). See lintab.uninstall."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import disks, uninstall
from .plan import PlanError


def _candidates() -> list[disks.Disk]:
    return [d for d in disks.list_disks() if d.label == "gpt" and uninstall.find_lintabos(d)]


def cmd_list(_args: argparse.Namespace) -> int:
    found = _candidates()
    if not found:
        print("No LintabOS installation found.")
        return 1
    for disk in found:
        root = uninstall.find_lintabos(disk)[0]
        print(f"{disk.display_name}: LintabOS on {root.path} ({root.size / disks.GiB:.1f} GB)")
    return 0


def _plan(args: argparse.Namespace) -> uninstall.UninstallPlan:
    disk = args.disk or (_candidates() or [None])[0]
    if disk is None:
        raise PlanError("No LintabOS installation found.")
    return uninstall.plan_uninstall(disks.read_disk(disk) if isinstance(disk, str) else disk)


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        print(_plan(args).describe())
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        plan = _plan(args)
        print(plan.describe())
        if not args.dry_run and args.yes != uninstall.CONFIRM_PHRASE:
            print(f"\nRefusing to write: pass --yes {uninstall.CONFIRM_PHRASE} to confirm.", file=sys.stderr)
            return 3
        warnings = uninstall.apply_uninstall(plan, dry_run=args.dry_run,
                                             progress=lambda i, n, d: print(f"[{i}/{n}] {d}", flush=True))
        for w in warnings:
            print(f"note: {w}")
        if not args.dry_run:
            print(f"\nLintabOS is removed. A copy of the old partition table is at {plan.backup_path} (this USB's RAM; "
                  "copy it somewhere if you want to keep it).")
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="lintab-uninstall", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show disks that have LintabOS on them").set_defaults(fn=cmd_list)
    for name, fn in (("plan", cmd_plan), ("apply", cmd_apply)):
        sp = sub.add_parser(name, help=f"{name} the removal" + (" (writes to disk!)" if name == "apply" else ""))
        sp.add_argument("disk", nargs="?", help="whole-disk device (default: the one disk that has LintabOS)")
        if name == "apply":
            sp.add_argument("--dry-run", action="store_true")
            sp.add_argument("--yes", default="", help=f"pass {uninstall.CONFIRM_PHRASE} to allow writing")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except disks.DiskError as exc:
        print(f"error: could not read the disks: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
