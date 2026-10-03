# SPDX-License-Identifier: MIT
"""LintabOS Extras: optional downloads you can add and remove, so the ISO stays small and you only keep what you want.

* **Microsoft 365 web apps**: Word, Excel, PowerPoint, OneDrive and Teams as web apps in their own windows (Chromium), plus a
  shortcut to connect OneDrive to Files. They are Microsoft's web versions and need your Microsoft account. Added for you only.
* **Drawing and notes**: Rnote and Xournal++ (Flathub). They take pen, finger or mouse.
* **Office pack**: LibreOffice and the unofficial Teams for Linux (Flathub).
* **Android apps**: Waydroid, a container that runs Android, and Android Mode (Debian backports; needs an administrator's password and
  a download of about a gigabyte for the Android image).
* **Windows apps**: Bottles, which manages Wine so some Windows programs run on Linux (Flathub). Many programs work, some don't.

Flathub apps and the Microsoft shortcuts are installed for you alone, without a password. Removing keeps your own documents and notes
(``--delete-data`` also deletes the apps' settings and saved data). Nothing here runs unless you ask for it and the tablet is online.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"
WAYDROID_HELPER = "/usr/libexec/lintab/install-waydroid"
WAYDROID_REMOVE_HELPER = "/usr/libexec/lintab/remove-waydroid"
CHROMIUM_HELPER = "/usr/libexec/lintab/install-chromium"
LAUNCHER_SOURCES = {"microsoft": "/usr/share/lintabos/extras/microsoft"}

Runner = Callable[[list[str]], int]


@dataclass(frozen=True)
class Extra:
    key: str
    label: str
    description: str
    size: str            # a rough figure for the dialog; not measured on the tablet
    kind: str            # "flatpak" | "waydroid" | "launchers"
    refs: tuple[str, ...] = ()
    check: str = ""      # flatpak ref, package or launcher file whose presence means "installed"
    removal_note: str = ""   # what removing it deletes, shown before the person confirms


CATALOG: tuple[Extra, ...] = (
    Extra("microsoft", "Microsoft 365 web apps", "Word, Excel, PowerPoint, OneDrive and Teams as web apps in their own windows, plus a "
          "shortcut to connect OneDrive to Files. The web versions, not desktop apps; they need your Microsoft account.",
          "a few MB, plus about 110 MB if Chromium isn't installed yet", "launchers", (), "lintab-word.desktop",
          "Removes the shortcuts and forgets your Microsoft sign-in in these windows. Chromium itself stays."),
    Extra("drawing", "Drawing and notes", "Rnote and Xournal++ for handwriting, sketching and marking up PDFs.",
          "about 400 MB", "flatpak", ("com.github.flxzt.rnote", "com.github.xournalpp.xournalpp"), "com.github.flxzt.rnote",
          "Removes the apps. Your notes and drawings are files in your folders and stay."),
    Extra("office", "Office pack", "LibreOffice (opens and edits Word, Excel, PowerPoint files) and the unofficial Teams for Linux.",
          "about 800 MB", "flatpak", ("org.libreoffice.LibreOffice", "com.github.IsmaelMartinez.teams_for_linux"),
          "org.libreoffice.LibreOffice", "Removes the apps. Your documents stay."),
    Extra("android", "Android apps (Waydroid)", "Runs Android apps, and adds “Android Mode”, which turns the whole tablet into a full-screen "
          "Android tablet. Needs an administrator's password and downloads an Android image. Untested on this tablet.",
          "about 1 GB", "waydroid", (), "waydroid",
          "DELETES the Android image and everything inside Android: its apps, accounts and files. Needs an administrator's password."),
    Extra("windows", "Windows apps (Bottles)", "Runs some Windows programs through Wine. Many work, some don't.",
          "about 500 MB, more on first use", "flatpak", ("com.usebottles.bottles",), "com.usebottles.bottles",
          "Removes Bottles. The Windows programs inside its bottles stay until you delete its data."),
)


def by_key(keys: list[str]) -> list[Extra]:
    known = {e.key: e for e in CATALOG}
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise ValueError(f"unknown extra(s): {', '.join(unknown)}")
    return [known[k] for k in dict.fromkeys(keys)]


# ------------------------------------------------------- Microsoft shortcuts (per user) --

def user_applications_dir() -> str:
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "applications")


def webapp_profile_dir() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "lintabos", "webapps")


def add_launchers(key: str, src: Optional[str] = None, dest: Optional[str] = None) -> list[str]:
    """Copy the shortcuts of one launcher extra into the user's own applications folder."""
    src = src or LAUNCHER_SOURCES[key]
    dest = dest or user_applications_dir()
    os.makedirs(dest, exist_ok=True)
    added = []
    for path in sorted(glob.glob(os.path.join(src, "*.desktop"))):
        target = os.path.join(dest, os.path.basename(path))
        shutil.copyfile(path, target)
        os.chmod(target, 0o644)
        added.append(os.path.basename(path))
    return added


def remove_launchers(key: str, src: Optional[str] = None, dest: Optional[str] = None, profile: Optional[str] = None,
                     delete_profile: bool = True) -> list[str]:
    """Delete exactly the shortcuts this extra added (matched by file name) and, normally, the shared sign-in profile."""
    src = src or LAUNCHER_SOURCES[key]
    dest = dest or user_applications_dir()
    removed = []
    for path in sorted(glob.glob(os.path.join(src, "*.desktop"))):
        target = os.path.join(dest, os.path.basename(path))
        if os.path.isfile(target):
            os.remove(target)
            removed.append(os.path.basename(path))
    if delete_profile:
        shutil.rmtree(profile or webapp_profile_dir(), ignore_errors=True)
    return removed


# --------------------------------------------------------------------------- planning --

def _run(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, capture_output=True, text=True).returncode
    except FileNotFoundError:
        return 127


def _have_chromium() -> bool:
    return any(shutil.which(b) for b in ("chromium", "chromium-browser", "google-chrome-stable"))


def _install_steps(extra: Extra, have_chromium: bool) -> list[tuple[str, list[str]]]:
    if extra.kind == "flatpak":
        return [(f"Downloading {extra.label}", ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", *extra.refs])]
    if extra.kind == "waydroid":
        return [(f"Installing {extra.label}", ["pkexec", WAYDROID_HELPER])]
    steps = [] if have_chromium else [("Installing Chromium (the browser engine for these apps)", ["pkexec", CHROMIUM_HELPER])]
    return steps + [(f"Adding {extra.label}", ["lintab-extras", "add-launchers", extra.key])]


def plan(keys: list[str], have_chromium: Optional[bool] = None) -> list[tuple[str, list[str]]]:
    """(description, argv) steps for the chosen extras, in order. Flathub is added once, for this user only."""
    chosen = by_key(keys)
    have = _have_chromium() if have_chromium is None else have_chromium
    steps: list[tuple[str, list[str]]] = []
    if any(e.kind == "flatpak" for e in chosen):
        steps.append(("Adding Flathub", ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB]))
    for extra in chosen:
        steps += _install_steps(extra, have)
    return steps


def plan_remove(keys: list[str], delete_data: bool = False) -> list[tuple[str, list[str]]]:
    steps: list[tuple[str, list[str]]] = []
    for extra in by_key(keys):
        if extra.kind == "flatpak":
            steps.append((f"Removing {extra.label}", ["flatpak", "uninstall", "--user", "-y", "--noninteractive",
                                                       *(["--delete-data"] if delete_data else []), *extra.refs]))
        elif extra.kind == "waydroid":
            steps.append(("Leaving Android mode", ["lintab-android", "back"]))
            steps.append((f"Removing {extra.label}", ["pkexec", WAYDROID_REMOVE_HELPER]))
        else:
            steps.append((f"Removing {extra.label}", ["lintab-extras", "remove-launchers", extra.key]))
    return steps


def is_installed(extra: Extra, run: Runner = _run) -> bool:
    if extra.kind == "flatpak":
        return run(["flatpak", "info", "--user", extra.check]) == 0
    if extra.kind == "launchers":
        return os.path.isfile(os.path.join(user_applications_dir(), extra.check))
    return run(["dpkg-query", "-W", "-f=${Status}", extra.check]) == 0 and shutil.which("waydroid") is not None


def install(keys: list[str], run: Runner = _run, log: Callable[[str], None] = print,
            have_chromium: Optional[bool] = None) -> dict[str, bool]:
    """Run the steps; returns {extra key: worked}. A failure in one extra doesn't stop the others."""
    chosen = by_key(keys)
    have = _have_chromium() if have_chromium is None else have_chromium
    results: dict[str, bool] = {}
    flathub_ok = True
    if any(e.kind == "flatpak" for e in chosen):
        log("Adding Flathub")
        flathub_ok = run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB]) == 0
    for extra in chosen:
        if extra.kind == "flatpak" and not flathub_ok:
            results[extra.key] = False
            continue
        ok = True
        for description, argv in _install_steps(extra, have):
            log(description)
            if run(argv) != 0 and argv != ["pkexec", CHROMIUM_HELPER]:   # no Chromium isn't fatal: the shortcuts open in the default browser
                ok = False
                break
        results[extra.key] = ok
    return results


def remove(keys: list[str], run: Runner = _run, log: Callable[[str], None] = print, delete_data: bool = False) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for extra in by_key(keys):
        ok = True
        for description, argv in plan_remove([extra.key], delete_data):
            log(description)
            code = run(argv)
            if code != 0 and argv[0] != "lintab-android":      # leaving Android mode is best effort
                ok = False
                break
        results[extra.key] = ok
    return results


# ------------------------------------------------------------------------------- GUI --

def gui(remove_mode: bool = False) -> int:
    zenity = shutil.which("zenity")
    if not zenity:
        print("zenity is missing; use `lintab-extras install|remove KEY…`", file=sys.stderr)
        return 1
    pool = [e for e in CATALOG if (is_installed(e) if remove_mode else True)]
    title = "Remove extras" if remove_mode else "LintabOS Extras"
    if not pool:
        subprocess.run([zenity, "--info", "--title=" + title, "--text=No extras are installed."])
        return 0
    rows: list[str] = []
    for extra in pool:
        rows += ["FALSE", extra.key, extra.label, "installed" if remove_mode else extra.size, extra.removal_note if remove_mode else extra.description]
    picked = subprocess.run([zenity, "--list", "--checklist", f"--title={title}", "--width=800", "--height=440",
                             "--text=" + ("Pick what to remove." if remove_mode else "Optional downloads. Pick what you want; the tablet must be online."),
                             "--column=Pick", "--column=key", "--column=Name", "--column=" + ("State" if remove_mode else "Size"),
                             "--column=" + ("What removing does" if remove_mode else "What it is"),
                             "--hide-column=2", "--separator= ", *rows], capture_output=True, text=True)
    keys = picked.stdout.split()
    if picked.returncode != 0 or not keys:
        return 0
    if remove_mode:
        notes = "\n\n".join(f"{e.label}: {e.removal_note}" for e in by_key(keys))
        if subprocess.run([zenity, "--question", f"--title={title}", "--width=480", "--ok-label=Remove", f"--text={notes}\n\nRemove?"]
                          ).returncode != 0:
            return 0
    working = subprocess.Popen([zenity, "--progress", "--pulsate", "--no-cancel", "--auto-close", f"--title={title}",
                                "--text=" + ("Removing…" if remove_mode else "Downloading… this can take a while.")],
                               stdin=subprocess.PIPE, text=True)
    results = (remove if remove_mode else install)(keys, log=lambda m: None)
    if working.stdin:
        working.stdin.close()
    working.wait()
    done = [e.label for e in by_key(keys) if results.get(e.key)]
    failed = [e.label for e in by_key(keys) if not results.get(e.key)]
    verb = "Removed" if remove_mode else "Installed"
    text = (f"{verb}:\n  " + "\n  ".join(done) if done else "") + (
        ("\n\nDidn't work" + ("" if remove_mode else " (check your internet connection)") + ":\n  " + "\n  ".join(failed)) if failed else "")
    subprocess.run([zenity, "--error" if failed and not done else "--info", f"--title={title}", "--width=420", f"--text={text.strip()}"])
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-extras", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="show the extras and whether they are installed")
    i = sub.add_parser("install", help="download and install extras")
    i.add_argument("keys", nargs="+")
    r = sub.add_parser("remove", help="uninstall extras")
    r.add_argument("keys", nargs="+")
    r.add_argument("--delete-data", action="store_true", help="also delete the apps' own settings and saved data")
    g = sub.add_parser("gui", help="pick extras in a window (the default)")
    g.add_argument("--remove", action="store_true", help="pick extras to remove instead")
    al = sub.add_parser("add-launchers", help=argparse.SUPPRESS)
    al.add_argument("key")
    rl = sub.add_parser("remove-launchers", help=argparse.SUPPRESS)
    rl.add_argument("key")
    rl.add_argument("--keep-profile", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        for extra in CATALOG:
            print(f"{extra.key:10} {'installed' if is_installed(extra, run=_run) else '-':10} {extra.label} ({extra.size})")
        return 0
    if args.cmd in ("install", "remove"):
        try:
            results = (install(args.keys, run=_run) if args.cmd == "install"
                       else remove(args.keys, run=_run, delete_data=args.delete_data))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for key, ok in results.items():
            print(f"{key}: {('installed' if args.cmd == 'install' else 'removed') if ok else 'FAILED'}")
        return 0 if all(results.values()) else 1
    if args.cmd == "add-launchers":
        return 0 if add_launchers(args.key) else 1
    if args.cmd == "remove-launchers":
        remove_launchers(args.key, delete_profile=not args.keep_profile)
        return 0
    return gui(remove_mode=getattr(args, "remove", False))


if __name__ == "__main__":
    sys.exit(main())
