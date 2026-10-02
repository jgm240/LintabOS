# SPDX-License-Identifier: MIT
"""Optional extra desktops (KDE Plasma, Xfce), downloaded during setup.

GNOME is always installed. KDE Plasma and Xfce are added from Debian's archive in the *installed* system, after GRUB is in
place, so a failed or interrupted download can never leave the computer unbootable (and never touches Windows). They show
up in GDM's session chooser (the gear icon on the login screen); GDM stays the login screen and GNOME stays the default.

Touch friendliness of each desktop (on-screen keyboard, bigger panel and cursor, rotation) is set up at first login by
:mod:`lintab.touchsetup` and :mod:`lintab.xfce_rotate`, which ship in ``lintabos-core``.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class Desktop:
    key: str
    label: str
    approx_mb: int            # download size, measured with `apt-get install -s` on Debian 13 from a bare system
    packages: tuple[str, ...]
    session_files: tuple[str, ...]   # at least one must exist afterwards, or the install did not take


DESKTOPS: dict[str, Desktop] = {
    "kde": Desktop(
        "kde", "KDE Plasma", 450,
        ("kde-plasma-desktop", "kwin-wayland", "plasma-nm", "plasma-pa", "powerdevil", "kscreen", "bluedevil",
         "maliit-keyboard", "konsole", "dolphin", "xdg-desktop-portal-kde"),
        ("usr/share/wayland-sessions/plasma.desktop",)),
    "xfce": Desktop(
        "xfce", "Xfce", 70,
        ("xfce4", "xfce4-terminal", "xfce4-notifyd", "xfce4-power-manager", "mate-polkit", "thunar-volman",
         "xserver-xorg-core", "xserver-xorg-input-libinput", "x11-xserver-utils", "xinput", "onboard",
         "iio-sensor-proxy", "dbus-x11", "xdg-desktop-portal-gtk"),
        ("usr/share/xsessions/xfce.desktop",)),
}


def parse_selection(keys: list[str]) -> list[Desktop]:
    """Unique, known desktops in a stable order; unknown names are an error rather than silently ignored."""
    unknown = [k for k in keys if k not in DESKTOPS]
    if unknown:
        raise ValueError(f"unknown desktop(s): {', '.join(unknown)}")
    return [DESKTOPS[k] for k in DESKTOPS if k in keys]


def download_summary(keys: list[str]) -> str:
    chosen = parse_selection(keys)
    if not chosen:
        return ""
    return ", ".join(f"{d.label} (about {d.approx_mb} MB)" for d in chosen)


def parse_apt_status(line: str) -> Optional[tuple[str, float]]:
    """Read one line of apt's ``APT::Status-Fd`` output: ("download"|"install", percent 0..100), or None."""
    kind, _, rest = line.strip().partition(":")
    if kind not in ("dlstatus", "pmstatus"):
        return None
    parts = rest.split(":")
    try:
        return ("download" if kind == "dlstatus" else "install", float(parts[1]))
    except (IndexError, ValueError):
        return None


@contextlib.contextmanager
def working_dns(target: str, host_resolv: str = "/etc/resolv.conf") -> Iterator[None]:
    """Give the target system the live session's name servers while packages download, then put its file back."""
    path = os.path.join(target, "etc/resolv.conf")
    saved_link = os.readlink(path) if os.path.islink(path) else None
    saved_text = None
    if saved_link is None and os.path.exists(path):
        with open(path) as f:
            saved_text = f.read()
    try:
        with open(os.path.realpath(host_resolv)) as f:
            wanted = f.read()
        if os.path.lexists(path):
            os.remove(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(wanted)
    except OSError:
        pass  # keep whatever is there; apt will report if it can't resolve names
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
            if saved_link is not None:
                os.symlink(saved_link, path)
            elif saved_text is not None:
                with open(path, "w") as f:
                    f.write(saved_text)


def keep_gdm_as_login_screen(target: str, chroot: Callable[[list[str]], None]) -> bool:
    """Make sure installing a desktop did not swap the login screen. Returns True when it had to put GDM back."""
    marker = os.path.join(target, "etc/X11/default-display-manager")
    try:
        with open(marker) as f:
            current = f.read().strip()
    except OSError:
        current = ""
    if current == "/usr/sbin/gdm3":
        return False
    with open(marker, "w") as f:
        f.write("/usr/sbin/gdm3\n")
    chroot(["ln", "-sf", "/usr/lib/systemd/system/gdm3.service", "/etc/systemd/system/display-manager.service"])
    return True


def install_desktops(keys: list[str], target: str, chroot: Callable[..., None],
                     stream: Callable[[list[str]], "subprocess.Popen"], progress: Progress,
                     lo: float = 0.0, hi: float = 1.0) -> list[str]:
    """Download and install the chosen desktops into ``target``. Never raises: returns warning texts instead.

    ``chroot(argv, input=None)`` runs a command in the target and raises on failure; ``stream(argv)`` starts one in the
    target and returns a Popen whose stdout yields apt's status lines. ``progress`` receives values between lo and hi.
    """
    chosen = parse_selection(keys)
    if not chosen:
        return []
    warnings: list[str] = []
    names = " and ".join(d.label for d in chosen)
    packages = sorted({p for d in chosen for p in d.packages})

    def at(fraction: float, message: str) -> None:
        progress(lo + (hi - lo) * min(max(fraction, 0.0), 1.0), message)

    try:
        at(0.0, f"Preparing to download {names}")
        chroot(["debconf-set-selections"], input="gdm3\tshared/default-x-display-manager\tselect\tgdm3\n")
        with working_dns(target):
            chroot(["apt-get", "update"])
            proc = stream(["apt-get", "install", "-y", "--no-install-recommends",
                           "-o", "APT::Status-Fd=1", "-o", "Dpkg::Progress-Fraction=1", *packages])
            tail: list[str] = []
            assert proc.stdout is not None
            for line in proc.stdout:
                parsed = parse_apt_status(line)
                if parsed is None:
                    if line.strip():
                        tail.append(line.strip())
                    continue
                phase, percent = parsed
                if phase == "download":
                    at(0.05 + 0.55 * percent / 100, f"Downloading {names}")
                else:
                    at(0.60 + 0.38 * percent / 100, f"Installing {names}")
            if proc.wait() != 0:
                raise RuntimeError("\n".join(tail[-6:]) or "apt-get failed")
        if keep_gdm_as_login_screen(target, chroot):
            warnings.append("GDM was put back as the login screen after the extra desktops changed it.")
        for desktop in chosen:
            if not any(os.path.exists(os.path.join(target, f)) for f in desktop.session_files):
                warnings.append(f"{desktop.label} was downloaded but its login session wasn't found.")
        at(1.0, f"{names} installed")
    except Exception as exc:  # noqa: BLE001 - the main install already succeeded; report, don't fail it
        warnings.append(f"Could not install {names} (is the tablet online?). LintabOS itself is installed and works; "
                        f"you can add it later with: sudo apt install {' '.join(d.packages[0] for d in chosen)}\n"
                        f"({str(exc).strip()[:300]})")
    return warnings


def network_available(host: str = "deb.debian.org", port: int = 443, timeout: float = 4.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
