# SPDX-License-Identifier: MIT
"""A hardware report you can paste into a GitHub issue, so "does it work on the Duet?" gets answered with facts.

Collects what matters for the tablet (Wi-Fi, Bluetooth, fingerprint reader, sensors, cameras, keyboard/folio, battery,
storage, audio) and the LintabOS state. **Nothing is sent anywhere.** The report is written to a file and shown to you first;
only if you agree is it copied to the clipboard and the issue page opened, and you paste it yourself.

Privacy: it never asks for Wi-Fi names or passwords, serial numbers or the machine id; MAC addresses, IP addresses, UUIDs and
your user and computer names are masked before anything is written.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
from typing import Callable, Optional

ISSUES_URL = "https://github.com/jgm240/LintabOS/issues/new?template=hardware-report.md&title=Hardware+report%3A+"
MAX_SECTION = 6000

Runner = Callable[[list[str]], tuple[int, str]]

DMI = ("sys_vendor", "product_name", "product_version", "bios_version", "bios_date")   # deliberately no *_serial / uuid


def _grep(pattern: str, source: list[str]) -> list[str]:
    return ["sh", "-c", f"{' '.join(source)} 2>&1 | grep -i -E '{pattern}' | tail -40"]


# (title, argv). Every command is read-only and none asks for secrets.
SECTIONS: list[tuple[str, list[str]]] = [
    ("System", ["sh", "-c", "for f in " + " ".join(DMI) + "; do printf '%s: ' $f; cat /sys/class/dmi/id/$f 2>/dev/null || echo '?'; done; "
                "uname -r; grep PRETTY_NAME /etc/os-release; cat /usr/share/lintabos/VERSION 2>/dev/null; cat /proc/cmdline"]),
    ("Wi-Fi: devices and drivers", ["sh", "-c", "lspci -nnk 2>&1 | grep -i -A3 -E 'network|wireless|802.11'; lsusb 2>&1 | grep -i -E 'wireless|wlan|wi-?fi|802.11'; "
                                                "lsmod | grep -E '^(iwl|rtw|rtl|mt7|ath|brcm|cfg80211|mac80211)'; rfkill list 2>&1"]),
    ("Wi-Fi: NetworkManager", ["sh", "-c", "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>&1 | sed 's/:[^:]*$/:<name hidden>/'; "
                                           "systemctl is-active NetworkManager wpa_supplicant 2>&1; ip -brief link 2>&1"]),
    ("Wi-Fi: kernel messages", _grep("iwlwifi|rtw[0-9]|rtl8|mt79|ath1|brcm|cnvi|cfg80211|firmware", ["journalctl", "-k", "-b", "--no-pager"])),
    ("Wi-Fi: firmware packages", ["sh", "-c", "dpkg-query -W -f='${Package} ${Version}\\n' 'firmware-*' 'wpasupplicant' 'network-manager' "
                                             "'linux-image-*' 2>/dev/null | sort; ls /lib/firmware | grep -c -E '^iwlwifi' || true"]),
    ("Bluetooth", ["sh", "-c", "lsusb 2>&1 | grep -i -E 'bluetooth|8087:'; bluetoothctl list 2>&1; lsmod | grep -E '^(bluetooth|btusb|btintel|btmtk|btrtl)'"]),
    ("Fingerprint reader", ["sh", "-c", "lsusb 2>&1 | grep -i -E '27c6|fingerprint|goodix|synaptics|elan'; systemctl is-active fprintd 2>&1; "
                                        "fprintd-list \"$USER\" 2>&1 | head -8; gsettings get org.gnome.login-screen enable-fingerprint-authentication 2>&1; "
                                        "grep -H fprintd /etc/pam.d/common-auth /etc/pam.d/gdm-fingerprint /etc/pam.d/gdm-password 2>&1; "
                                        "journalctl -u fprintd -b --no-pager 2>&1 | tail -15; dpkg-query -W -f='${Package} ${Version}\\n' fprintd libfprint-2-2 libpam-fprintd 2>&1"]),
    ("Accelerometer and light sensor", ["sh", "-c", "for d in /sys/bus/iio/devices/iio:device*; do echo \"$d: $(cat $d/name 2>/dev/null)\"; done 2>&1; "
                                                    "busctl --system get-property net.hadess.SensorProxy /net/hadess/SensorProxy net.hadess.SensorProxy "
                                                    "HasAccelerometer AccelerometerOrientation HasAmbientLight 2>&1; "
                                                    "busctl --system get-property net.hadess.SensorProxy /net/hadess/SensorProxy net.hadess.SensorProxy AccelerometerOrientation 2>&1"]),
    ("Cameras", ["sh", "-c", "cam --list 2>&1 | grep -v -E 'INFO|WARN'; v4l2-ctl --list-devices 2>&1 | head -30; "
                             "journalctl -k -b --no-pager 2>&1 | grep -i -E 'ipu6|ipu-bridge|int3472|ov[0-9]{4}|hi[0-9]{3,4}|imx[0-9]{3}' | tail -15"]),
    ("Keyboard and folio", ["sh", "-c", "lintab-tablet-mode status 2>&1; grep -E '^(N: Name|H: Handlers)' /proc/bus/input/devices 2>&1 | paste - - | cut -c1-150"]),
    ("Touchscreen", ["sh", "-c", "echo '-- devices tagged as a touchscreen by udev'; "
                                "for f in /dev/input/event*; do p=$(udevadm info -q property -n \"$f\" 2>/dev/null); "
                                "echo \"$p\" | grep -q 'ID_INPUT_TOUCHSCREEN=1' && echo \"$f: $(echo \\\"$p\\\" | grep ^NAME=)\"; done; "
                                "echo '-- what libinput (what GNOME actually uses) sees'; "
                                "libinput list-devices 2>&1 | grep -B1 -A5 -iE 'touch|elan|goodix|melfas|focaltech'; "
                                "echo '-- kernel messages about the touch controller'; "
                                "journalctl -k -b --no-pager 2>&1 | grep -iE 'i2c_hid|hid-multitouch|touchscreen|goodix|elan_i2c|elants' | tail -30"]),
    ("Battery", ["sh", "-c", "for b in /sys/class/power_supply/*; do echo \"== $b\"; for f in type status capacity energy_full energy_full_design "
                             "charge_full charge_full_design cycle_count charge_control_end_threshold technology; do "
                             "[ -r $b/$f ] && echo \"$f=$(cat $b/$f)\"; done; done"]),
    ("Sleep and resume", ["sh", "-c", "echo '-- what the kernel offers'; cat /sys/power/state /sys/power/mem_sleep /sys/power/disk 2>&1; "
                                      "echo '-- swap and resume device'; swapon --show 2>&1; grep -o 'resume=[^ ]*' /proc/cmdline; cat /proc/cmdline; "
                                      "echo '-- what the buttons do'; "
                                      "gsettings get org.gnome.settings-daemon.plugins.power power-button-action 2>&1; "
                                      "gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 2>&1; "
                                      "grep -h -E '^(Handle|HoldoffTimeoutSec)' /etc/systemd/logind.conf /etc/systemd/logind.conf.d/*.conf 2>&1; "
                                      "systemctl is-enabled sleep.target suspend.target hibernate.target 2>&1; "
                                      "echo '-- boots'; journalctl --list-boots --no-pager 2>&1 | tail -4; "
                                      "echo '-- previous boot: sleep and device messages'; "
                                      "journalctl -b -1 -k --no-pager 2>&1 | grep -i -E 'PM:|suspend|s2idle|resume|hibernat|ufs|ish|i915|i2c_hid|ipu6|iwlwifi|nvme' | tail -45; "
                                      "echo '-- previous boot: last lines before it stopped'; journalctl -b -1 --no-pager 2>&1 | tail -20"]),
    ("Storage", ["sh", "-c", "lsblk -dno NAME,SIZE,TRAN,MODEL 2>&1; [ -d /sys/firmware/efi ] && echo 'UEFI boot' || echo 'not UEFI'; "
                             "mokutil --sb-state 2>&1 | head -1"]),
    ("Audio", ["sh", "-c", "aplay -l 2>&1 | head -20"]),
    ("LintabOS", ["sh", "-c", "lintab-update status 2>&1; lintab-school-mode status 2>&1; lintab-boot-menu status 2>&1"]),
]

_MAC = re.compile(r"\b([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){2})([:-][0-9A-Fa-f]{2}){3}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")
_UUID = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")
_SERIAL = re.compile(r"(?im)^(.*\b(?:serial(?: ?number)?|iserial|machine-id|product_uuid)\b\s*[:=]?\s*)\S.*$")


def redact(text: str, user: str = "", host: str = "") -> str:
    """Mask what could identify the person or the network. Order matters: MACs first (they contain colons)."""
    text = _MAC.sub(lambda m: m.group(1) + ":**:**:**", text)
    text = _UUID.sub("<uuid>", text)
    text = _IPV4.sub(lambda m: m.group(0) if m.group(0) in ("0.0.0.0", "127.0.0.1") else "<ip>", text)
    text = _IPV6.sub("<ipv6>", text)
    text = _SERIAL.sub(lambda m: m.group(1) + "<hidden>", text)
    for word, label in ((user, "<user>"), (host, "<host>")):
        if word and len(word) > 2:
            text = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(word)}(?![A-Za-z0-9_-])", label, text)
    return text


def _default_run(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return 127, "(command not found)"
    except subprocess.TimeoutExpired:
        return 124, "(timed out)"
    return proc.returncode, (proc.stdout + proc.stderr)


def generate(run: Runner = _default_run, user: Optional[str] = None, host: Optional[str] = None,
             now: Optional[datetime.datetime] = None) -> str:
    user = user if user is not None else os.environ.get("USER", "")
    host = host if host is not None else (os.uname().nodename if hasattr(os, "uname") else "")
    now = now or datetime.datetime.now()
    out = [f"# LintabOS hardware report ({now:%Y-%m-%d %H:%M})", "",
           "_Generated by `lintab-hwreport`. MAC/IP addresses, UUIDs, serial numbers, and the user and computer names are "
           "masked; Wi-Fi names are never collected. Read it before sharing._", ""]
    for title, argv in SECTIONS:
        code, text = run(argv)
        text = text.strip() or ("(nothing found)" if code == 0 else f"(no output, exit {code})")
        if len(text) > MAX_SECTION:
            text = text[:MAX_SECTION] + "\n… (cut)"
        out += [f"## {title}", "```", redact(text, user, host), "```", ""]
    return "\n".join(out)


def default_path(now: Optional[datetime.datetime] = None) -> str:
    return os.path.join(os.path.expanduser("~"), f"lintabos-hardware-report-{(now or datetime.datetime.now()):%Y%m%d-%H%M}.md")


def copy_to_clipboard(text: str) -> bool:
    for tool in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(tool[0]):
            return subprocess.run(tool, input=text, text=True).returncode == 0
    return False


def assist() -> int:
    """The friendly flow: explain, build, show for review, and only then copy + open the issue page."""
    zenity = shutil.which("zenity")
    if not zenity:
        print("zenity is missing; run `lintab-hwreport` instead.", file=sys.stderr)
        return 1
    if subprocess.run([zenity, "--question", "--width=460", "--title=Hardware report", "--ok-label=Make report",
                       "--text=This collects what your tablet's Wi-Fi, fingerprint reader, sensors, cameras and battery "
                       "report, so problems on the Duet 3 can be fixed.\n\nNothing is sent. You'll read the report first; "
                       "if you agree, it is copied and the GitHub page opens for you to paste it."]).returncode != 0:
        return 0
    path = default_path()
    text = generate()
    with open(path, "w") as f:
        f.write(text)
    review = subprocess.run([zenity, "--text-info", "--width=760", "--height=560", "--title=Review before sharing",
                             f"--filename={path}", "--ok-label=Copy and open GitHub", "--cancel-label=Don't share"])
    if review.returncode != 0:
        subprocess.run([zenity, "--info", "--title=Hardware report", f"--text=Nothing was shared. The report is saved at:\n{path}"])
        return 0
    copied = copy_to_clipboard(text)
    subprocess.Popen(["xdg-open", ISSUES_URL])
    subprocess.run([zenity, "--info", "--width=420", "--title=Hardware report",
                    "--text=" + ("Copied. In the GitHub page, paste it into the box (Ctrl+V) and press Submit."
                                 if copied else f"Open the file {path}, copy its text, and paste it into the GitHub page.")])
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-hwreport", description=__doc__.split("\n\n")[0])
    ap.add_argument("--output", "-o", help="file to write (default: a dated file in your home folder)")
    ap.add_argument("--stdout", action="store_true", help="print the report instead of writing a file")
    ap.add_argument("--assist", action="store_true", help="guided: review, copy to clipboard and open the GitHub issue page")
    args = ap.parse_args(argv)
    if args.assist:
        return assist()
    text = generate()
    if args.stdout:
        print(text)
        return 0
    path = args.output or default_path()
    with open(path, "w") as f:
        f.write(text)
    print(f"Report written to {path}\nRead it, then paste it into a new issue: {ISSUES_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
