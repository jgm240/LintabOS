# SPDX-License-Identifier: MIT
"""Command-line front end: ``lintab-partitioner``.

The GUI installer uses the same library; this CLI exists so the partitioner can
be reviewed and scripted (``plan`` never writes anything).
"""

from __future__ import annotations

import argparse
import sys

from . import disks, plan as planmod
from .disks import GiB


def _find_disk(path: str) -> disks.Disk:
    return disks.read_disk(path)


def cmd_list(args: argparse.Namespace) -> int:
    found = disks.list_disks(hide="" if args.all else None)
    if not found:
        print("No suitable disks found (need >= 16 GB, not the live USB).")
        return 1
    for d in found:
        print(f"{d.display_name}  table={d.label or 'none'}  sector={d.sector_size}")
        win = disks.find_windows(d) if d.label == "gpt" else None
        for p in d.partitions:
            tag = " <- Windows" if win and p.path == win.path else (" <- EFI" if p.is_esp else "")
            print(f"    {p.path:<16} {p.size / GiB:8.1f} GB  {p.fstype or '-':<10} {p.name}{tag}")
        for r in d.free_regions():
            print(f"    free             {r.size / GiB:8.1f} GB  at {r.start / GiB:.1f} GB")
        if win:
            try:
                info = planmod.probe_ntfs(win)
                print(f"    Windows can give up to {planmod.max_linux_size(win, info) / GiB:.1f} GB")
            except planmod.PlanError as exc:
                print("    Windows can't be shrunk yet:\n      " + str(exc).replace("\n", "\n      "))
    return 0


def _build_plan(args: argparse.Namespace) -> planmod.Plan:
    disk = _find_disk(args.disk)
    if args.mode == "wipe":
        return planmod.plan_wipe(disk)
    if args.mode == "dualboot":
        return planmod.plan_dualboot(disk, int(args.size_gb * GiB))
    regions = disk.free_regions()
    if not regions:
        raise planmod.PlanError("No free space on this disk.")
    region = max(regions, key=lambda r: r.size)
    return planmod.plan_free_space(disk, region, int(args.size_gb * GiB) if args.size_gb else None)


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        print(_build_plan(args).describe())
    except planmod.PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        plan = _build_plan(args)
        print(plan.describe())
        if not args.dry_run:
            if args.yes != "I-UNDERSTAND":
                print("\nRefusing to write: pass --yes I-UNDERSTAND to confirm.", file=sys.stderr)
                return 3
        planmod.apply_plan(plan, dry_run=args.dry_run,
                           progress=lambda i, n, d: print(f"[{i}/{n}] {d}", flush=True))
        if not args.dry_run:
            print(f"\nroot partition: {planmod.resolve_partuuid(plan.root_partuuid, plan.disk)}")
            print(f"efi partition:  {planmod.resolve_partuuid(plan.esp_partuuid, plan.disk)}")
    except planmod.PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lintab-partitioner", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="show disks, Windows installs and shrinkable space")
    ls.add_argument("--all", action="store_true", help="include the live USB")
    ls.set_defaults(fn=cmd_list)

    for name, fn in (("plan", cmd_plan), ("apply", cmd_apply)):
        sp = sub.add_parser(name, help=f"{name} a layout" + (" (writes to disk!)" if name == "apply" else ""))
        sp.add_argument("disk")
        sp.add_argument("--mode", choices=["dualboot", "wipe", "free"], required=True)
        sp.add_argument("--size-gb", type=float, help="size for LintabOS (dualboot: taken from Windows)")
        if name == "apply":
            sp.add_argument("--dry-run", action="store_true")
            sp.add_argument("--yes", default="", help="pass I-UNDERSTAND to allow writing")
        sp.set_defaults(fn=fn)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
