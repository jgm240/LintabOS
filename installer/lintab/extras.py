# SPDX-License-Identifier: MIT
"""LintabOS Extras: optional downloads, so the ISO stays small and you only get what you want.

* **Drawing and notes**: Rnote and Xournal++ (Flathub). They take pen, finger or mouse.
* **Office pack**: LibreOffice and the unofficial Teams for Linux (Flathub).
* **Android apps**: Waydroid, a container that runs Android (Debian backports; needs an administrator's password and a
  download of about a gigabyte for the Android image).
* **Windows apps**: Bottles, which manages Wine so some Windows programs run on Linux (Flathub). Many programs work, some
  don't; Wine is not Windows.

Flathub apps install for you alone, without a password. Nothing here runs unless you pick it and the tablet is online.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"
WAYDROID_HELPER = "/usr/libexec/lintab/install-waydroid"

Runner = Callable[[list[str]], int]


@dataclass(frozen=True)
class Extra:
    key: str
    label: str
    description: str
    size: str            # a rough figure for the dialog; not measured on the tablet
    kind: str            # "flatpak" | "waydroid"
    refs: tuple[str, ...] = ()
    check: str = ""      # flatpak ref (or package) whose presence means "installed"


CATALOG: tuple[Extra, ...] = (
    Extra("drawing", "Drawing and notes", "Rnote and Xournal++ for handwriting, sketching and marking up PDFs.",
          "about 400 MB", "flatpak", ("com.github.flxzt.rnote", "com.github.xournalpp.xournalpp"), "com.github.flxzt.rnote"),
    Extra("office", "Office pack", "LibreOffice (opens and edits Word, Excel, PowerPoint files) and the unofficial Teams for Linux.",
          "about 800 MB", "flatpak", ("org.libreoffice.LibreOffice", "com.github.IsmaelMartinez.teams_for_linux"),
          "org.libreoffice.LibreOffice"),
    Extra("android", "Android apps (Waydroid)", "Runs Android apps in a window. Needs an administrator's password and downloads an "
          "Android image. Untested on this tablet.", "about 1 GB", "waydroid", (), "waydroid"),
    Extra("windows", "Windows apps (Bottles)", "Runs some Windows programs through Wine. Many work, some don't.",
          "about 500 MB, more on first use", "flatpak", ("com.usebottles.bottles",), "com.usebottles.bottles"),
)


def by_key(keys: list[str]) -> list[Extra]:
    known = {e.key: e for e in CATALOG}
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise ValueError(f"unknown extra(s): {', '.join(unknown)}")
    return [known[k] for k in dict.fromkeys(keys)]


def plan(keys: list[str]) -> list[tuple[str, list[str]]]:
    """(description, argv) steps for the chosen extras, in order. Flathub is added once, for this user only."""
    chosen = by_key(keys)
    steps: list[tuple[str, list[str]]] = []
    if any(e.kind == "flatpak" for e in chosen):
        steps.append(("Adding Flathub", ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB]))
    for extra in chosen:
        if extra.kind == "flatpak":
            steps.append((f"Downloading {extra.label}",
                          ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", *extra.refs]))
        else:
            steps.append((f"Installing {extra.label}", ["pkexec", WAYDROID_HELPER]))
    return steps


def _run(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, capture_output=True, text=True).returncode
    except FileNotFoundError:
        return 127


def is_installed(extra: Extra, run: Runner = _run) -> bool:
    if extra.kind == "flatpak":
        return run(["flatpak", "info", "--user", extra.check]) == 0
    return run(["dpkg-query", "-W", "-f=${Status}", extra.check]) == 0 and shutil.which("waydroid") is not None


def install(keys: list[str], run: Runner = _run, log: Callable[[str], None] = print) -> dict[str, bool]:
    """Run the steps; returns {extra key: worked}. A failure in one extra doesn't stop the others."""
    results: dict[str, bool] = {}
    chosen = by_key(keys)
    steps = plan(keys)
    flathub_ok = True
    if chosen and any(e.kind == "flatpak" for e in chosen):
        description, argv = steps.pop(0)
        log(description)
        flathub_ok = run(argv) == 0
    for extra, (description, argv) in zip(chosen, steps):
        log(description)
        if extra.kind == "flatpak" and not flathub_ok:
            results[extra.key] = False
            continue
        results[extra.key] = run(argv) == 0
    return results


def gui() -> int:
    zenity = shutil.which("zenity")
    if not zenity:
        print("zenity is missing; use `lintab-extras install KEY…`", file=sys.stderr)
        return 1
    rows: list[str] = []
    for extra in CATALOG:
        rows += ["FALSE", extra.key, extra.label, extra.size, extra.description]
    picked = subprocess.run([zenity, "--list", "--checklist", "--title=LintabOS Extras", "--width=760", "--height=420",
                             "--text=Optional downloads. Pick what you want; the tablet must be online.",
                             "--column=Get", "--column=key", "--column=Name", "--column=Size", "--column=What it is",
                             "--hide-column=2", "--separator= ", *rows], capture_output=True, text=True)
    keys = picked.stdout.split()
    if picked.returncode != 0 or not keys:
        return 0
    working = subprocess.Popen([zenity, "--progress", "--pulsate", "--no-cancel", "--auto-close", "--title=LintabOS Extras",
                                "--text=Downloading… this can take a while."], stdin=subprocess.PIPE, text=True)
    results = install(keys, log=lambda m: None)
    if working.stdin:
        working.stdin.close()
    working.wait()
    done = [e.label for e in by_key(keys) if results.get(e.key)]
    failed = [e.label for e in by_key(keys) if not results.get(e.key)]
    text = ("Installed:\n  " + "\n  ".join(done) if done else "") + ("\n\nDidn't work (check your internet connection):\n  "
                                                                       + "\n  ".join(failed) if failed else "")
    subprocess.run([zenity, "--error" if failed and not done else "--info", "--title=LintabOS Extras", "--width=420",
                    f"--text={text.strip()}"])
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-extras", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="show the extras and whether they are installed")
    i = sub.add_parser("install", help="download and install extras")
    i.add_argument("keys", nargs="+")
    sub.add_parser("gui", help="pick extras in a window (the default)")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        for extra in CATALOG:
            print(f"{extra.key:9} {'installed' if is_installed(extra, run=_run) else '-':10} {extra.label} ({extra.size})")
        return 0
    if args.cmd == "install":
        try:
            results = install(args.keys, run=_run)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for key, ok in results.items():
            print(f"{key}: {'installed' if ok else 'FAILED'}")
        return 0 if all(results.values()) else 1
    return gui()


if __name__ == "__main__":
    sys.exit(main())
