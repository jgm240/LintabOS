# SPDX-License-Identifier: MIT
"""lintab-wifi-fix: undo everything in software that can stop Wi-Fi, and say what it can't fix.

It changes only things that are safe to change and easy to reverse:

* removes **rfkill** blocks on Wi-Fi (a *hardware* switch block is reported; software can't lift it);
* turns NetworkManager's networking and Wi-Fi radio **on**, and starts/unmasks NetworkManager and wpa_supplicant;
* **loads the Wi-Fi driver** for hardware that has none bound (``--reload-driver`` also unloads and reloads it);
* asks for a fresh scan.

It only *reports*, and never edits, config that deliberately stops Wi-Fi (a modprobe blacklist, NetworkManager's "unmanaged"
list, an ``/etc/network/interfaces`` entry, the ``rfkill.default_state=0`` kernel option) and missing firmware files, because
those need a decision from you. It can't help if the Wi-Fi chip isn't detected at all, and says so.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

Runner = Callable[[list[str]], tuple[int, str]]

WIFI_MODULES = ("iwlwifi", "iwlmvm", "iwldvm", "rtw88", "rtw89", "rtl8", "mt76", "mt79", "ath9k", "ath10k", "ath11k", "ath12k",
                "brcm", "cfg80211", "mac80211")
_BLACKLIST = re.compile(r"^\s*(?:blacklist\s+(\S+)|install\s+(\S+)\s+/bin/(?:false|true))", re.M)


@dataclass
class Line:
    kind: str       # "OK" | "FIXED" | "WARN" | "FAIL" | "INFO"
    text: str

    def __str__(self) -> str:
        return f"  [{self.kind:^5}] {self.text}"


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return proc.returncode, proc.stdout + proc.stderr


def parse_rfkill(text: str) -> list[dict]:
    """Entries of ``rfkill list``: [{"name", "type", "soft", "hard"}]. Only the fields we need."""
    entries, current = [], None
    for line in text.splitlines():
        header = re.match(r"^(\d+): (\S+): (.+)$", line)
        if header:
            current = {"name": header.group(2), "type": header.group(3).strip().lower(), "soft": False, "hard": False}
            entries.append(current)
        elif current is not None:
            if "Soft blocked: yes" in line:
                current["soft"] = True
            if "Hard blocked: yes" in line:
                current["hard"] = True
    return entries


def wifi_modules_from_lspci(text: str) -> tuple[bool, list[str], str]:
    """From ``lspci -nnk``: (a Wi-Fi device exists, its candidate kernel modules, the driver in use or "")."""
    found, modules, in_use, inside = False, [], "", False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            inside = bool(re.search(r"network controller|wireless|802\.11", line, re.I))
            found = found or inside
            continue
        if inside:
            m = re.match(r"\s*Kernel modules:\s*(.+)", line)
            if m:
                modules += [x.strip() for x in m.group(1).split(",")]
            m = re.match(r"\s*Kernel driver in use:\s*(\S+)", line)
            if m:
                in_use = m.group(1)
    return found, list(dict.fromkeys(modules)), in_use


def find_blockers(modprobe_dirs: list[str], nm_dirs: list[str], interfaces: str, cmdline: str) -> list[str]:
    """Config that deliberately keeps Wi-Fi off. Reported, never edited."""
    problems = []
    for directory in modprobe_dirs:
        for path in sorted(glob.glob(os.path.join(directory, "*.conf"))):
            try:
                text = open(path).read()
            except OSError:
                continue
            for match in _BLACKLIST.finditer(text):
                name = match.group(1) or match.group(2)
                if any(name.startswith(w) for w in WIFI_MODULES):
                    problems.append(f"{path} stops the Wi-Fi driver '{name}' from loading. Remove that line, then run: sudo update-initramfs -u && sudo reboot")
    for directory in nm_dirs:
        for path in [directory] if os.path.isfile(directory) else sorted(glob.glob(os.path.join(directory, "*.conf"))):
            try:
                text = open(path).read()
            except OSError:
                continue
            if re.search(r"^\s*unmanaged-devices\s*=.*(wl|type:wifi|\*)", text, re.M):
                problems.append(f"{path} tells NetworkManager to ignore Wi-Fi devices (unmanaged-devices).")
    try:
        if re.search(r"^\s*(auto|allow-hotplug|iface)\s+wl\S*", open(interfaces).read(), re.M):
            problems.append(f"{interfaces} configures a Wi-Fi interface by hand, so NetworkManager leaves it alone.")
    except OSError:
        pass
    if re.search(r"\brfkill\.default_state=0\b", cmdline):
        problems.append("The kernel option rfkill.default_state=0 starts every radio switched off (see /etc/default/grub*).")
    return problems


def firmware_problems(journal: str, firmware_dir: str = "/lib/firmware") -> list[str]:
    """Missing firmware files named in the kernel log, with whether they exist on disk."""
    out = []
    for name in dict.fromkeys(re.findall(r"(?:firmware|loading)[^\n]*?\b((?:iwlwifi|rtw|rtl_|mediatek|mt7|ath1|brcm)[\w./-]+\.(?:ucode|bin|fw))", journal)):
        if any(os.path.exists(os.path.join(firmware_dir, name + s)) for s in ("", ".zst", ".xz")):
            continue
        out.append(f"the kernel asked for firmware '{name}' and it is not in {firmware_dir}")
    if re.search(r"no suitable firmware found|failed to load firmware|Direct firmware load .* failed", journal, re.I) and not out:
        out.append("the kernel log says the Wi-Fi firmware failed to load (see: journalctl -k -b | grep -i firmware)")
    return out


def fix(run: Runner = _run, reload_driver: bool = False, root: str = "/", firmware_dir: str = "/lib/firmware",
        cmdline: Optional[str] = None) -> list[Line]:
    lines: list[Line] = []
    add = lambda kind, text: lines.append(Line(kind, text))

    # 1. rfkill
    code, listing = run(["rfkill", "list"])
    blocks = [e for e in parse_rfkill(listing) if e["type"] in ("wireless lan", "wlan")]
    if code == 127:
        add("WARN", "rfkill is not installed; skipping the block check")
    for entry in blocks:
        if entry["hard"]:
            add("FAIL", f"{entry['name']} is HARD blocked: a physical switch or the firmware has switched Wi-Fi off. "
                        "Software cannot undo that (look for a switch or an airplane-mode key; try a full power-off, not a restart).")
        elif entry["soft"]:
            run(["rfkill", "unblock", "wifi"])
            add("FIXED", f"{entry['name']} was blocked by software (rfkill); unblocked")
        else:
            add("OK", f"{entry['name']} is not blocked")
    if not blocks and code != 127:
        add("INFO", "rfkill lists no Wi-Fi radio (the driver may not have loaded; see below)")

    # 2. services and radio
    for unit in ("NetworkManager", "wpa_supplicant"):
        _c, state = run(["systemctl", "is-enabled", unit])
        if state.strip() == "masked":
            run(["systemctl", "unmask", unit])
            add("FIXED", f"{unit} was masked; unmasked")
    code, active = run(["systemctl", "is-active", "NetworkManager"])
    if active.strip() != "active":
        run(["systemctl", "start", "NetworkManager"])
        add("FIXED", "NetworkManager was not running; started it")
    else:
        add("OK", "NetworkManager is running")
    run(["nmcli", "networking", "on"])
    _c, radio = run(["nmcli", "radio", "wifi"])
    if radio.strip() == "disabled":
        run(["nmcli", "radio", "wifi", "on"])
        add("FIXED", "NetworkManager's Wi-Fi radio was off; turned it on")
    elif radio.strip() == "enabled":
        add("OK", "NetworkManager's Wi-Fi radio is on")

    # 3. hardware and driver
    _c, pci = run(["lspci", "-nnk"])
    _c, usb = run(["lsusb"])
    present, modules, in_use = wifi_modules_from_lspci(pci)
    usb_wifi = bool(re.search(r"wireless|wlan|wi-?fi|802\.11", usb, re.I))
    if not (present or usb_wifi):
        add("FAIL", "no Wi-Fi hardware is detected on PCI or USB. If it worked before, power the tablet off completely and "
                    "start it again; if it is still missing the problem is below the operating system (BIOS, hardware).")
    elif in_use and not reload_driver:
        add("OK", f"the Wi-Fi hardware is detected and the driver '{in_use}' is in use")
    else:
        for module in modules:
            if reload_driver and in_use:
                run(["modprobe", "-r", module])
            code, out = run(["modprobe", module])
            add("FIXED" if code == 0 else "WARN",
                f"{'reloaded' if reload_driver and in_use else 'loaded'} driver module '{module}'" if code == 0
                else f"could not load '{module}': {out.strip().splitlines()[-1] if out.strip() else 'unknown error'}")
        if not modules and not in_use:
            add("WARN", "Wi-Fi hardware is detected but no kernel module is listed for it; the kernel may not support it yet.")

    # 4. things we report but never change
    cmd = cmdline if cmdline is not None else _read(os.path.join(root, "proc/cmdline"))
    for problem in find_blockers([os.path.join(root, "etc/modprobe.d"), os.path.join(root, "usr/lib/modprobe.d")],
                                 [os.path.join(root, "etc/NetworkManager/NetworkManager.conf"),
                                  os.path.join(root, "etc/NetworkManager/conf.d")],
                                 os.path.join(root, "etc/network/interfaces"), cmd):
        add("WARN", problem)
    _c, journal = run(["sh", "-c", "journalctl -k -b --no-pager 2>/dev/null | grep -i -E 'firmware|iwlwifi' | tail -60"])
    for problem in firmware_problems(journal, firmware_dir):
        add("WARN", problem + ". Install it with: sudo apt install firmware-iwlwifi firmware-realtek firmware-mediatek (needs another "
                              "connection), or copy the file from another computer into /lib/firmware.")
    if os.path.exists(os.path.join(root, "etc/lintabos/school-mode")):
        add("INFO", "school mode is on; it blocks cameras only and does not affect Wi-Fi")

    # 5. rescan and final state
    run(["nmcli", "device", "wifi", "rescan"])
    _c, devices = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
    wifi = [d for d in devices.splitlines() if ":wifi:" in d]
    if wifi:
        add("OK", f"NetworkManager now has a Wi-Fi device: {wifi[0].split(':')[0]} ({wifi[0].split(':')[2]})")
    else:
        add("FAIL", "NetworkManager still has no Wi-Fi device. Run 'Hardware Report' (or lintab-hwreport) and share it so this can be fixed.")
    return lines


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def main(argv: Optional[list[str]] = None, euid: Optional[int] = None, run: Runner = _run) -> int:
    ap = argparse.ArgumentParser(prog="lintab-wifi-fix", description=__doc__.split("\n\n")[0])
    ap.add_argument("--reload-driver", action="store_true", help="also unload and reload the Wi-Fi driver (briefly drops Wi-Fi)")
    ap.add_argument("--log", help="also save the result to this file (handy if you have no other network to copy it from)")
    args = ap.parse_args(argv)
    if (os.geteuid() if euid is None else euid) != 0:
        if shutil.which("sudo") and euid is None:
            print("Wi-Fi settings need administrator rights; asking for your password…", file=sys.stderr)
            os.execvp("sudo", ["sudo", sys.executable, *sys.argv])
        print("run this as root: sudo lintab-wifi-fix", file=sys.stderr)
        return 1
    result = fix(run, reload_driver=args.reload_driver)
    text = "\n".join(str(line) for line in result)
    print(text)
    if args.log:
        with open(args.log, "w") as f:
            f.write(text + "\n")
        print(f"\n(saved to {args.log})")
    return 1 if any(line.kind == "FAIL" for line in result) else 0


if __name__ == "__main__":
    sys.exit(main())
