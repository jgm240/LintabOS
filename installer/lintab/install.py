# SPDX-License-Identifier: MIT
"""Copy the live system to disk and make it bootable.

Runs as root from the live session, after :mod:`lintab.plan` has created and
formatted the target partitions. Everything user-visible is reported through the
``progress(fraction, message)`` callback so the GUI and the CLI share one path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from . import desktops as desktopsmod
from .disks import DiskError
from .plan import Plan, resolve_partuuid

TARGET = "/target"
LIVE_ROOTFS_CANDIDATES = (
    "/run/live/rootfs/filesystem.squashfs",
    "/lib/live/mount/rootfs/filesystem.squashfs",
)
# Packages that only make sense in the live session.
LIVE_ONLY_PACKAGES = ["live-boot", "live-boot-initramfs-tools", "live-config", "live-config-systemd",
                      "live-tools"]

Progress = Callable[[float, str], None]


class InstallError(RuntimeError):
    pass


@dataclass
class InstallConfig:
    plan: Plan
    fullname: str
    username: str
    password: str
    hostname: str = "lintab"
    timezone: str = "UTC"
    locale: str = "en_US.UTF-8"
    keyboard_layout: str = "us"
    autologin: bool = False
    desktops: tuple[str, ...] = ()      # extra desktops to download: "kde", "xfce"

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.username):
            raise InstallError("User names must start with a lowercase letter and use only a-z, 0-9, - and _.")
        if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?", self.hostname):
            raise InstallError("That computer name isn't valid (letters, digits and dashes only).")
        if len(self.password) < 1:
            raise InstallError("Please choose a password.")
        if not re.fullmatch(r"[A-Za-z0-9_+./-]+", self.timezone) or not os.path.exists(f"/usr/share/zoneinfo/{self.timezone}"):
            raise InstallError(f"Unknown time zone {self.timezone!r}.")
        if ":" in self.fullname or "\n" in self.fullname:
            raise InstallError("The full name can't contain ':' or line breaks.")
        try:
            desktopsmod.parse_selection(list(self.desktops))
        except ValueError as exc:
            raise InstallError(str(exc)) from None


def _sh(argv: list[str], **kw) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        raise InstallError(f"{' '.join(argv)} failed:\n{(proc.stderr or proc.stdout).strip()}")
    return proc


def _chroot(argv: list[str], input: Optional[str] = None, env: Optional[dict] = None) -> None:
    full_env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C.UTF-8", **(env or {})}
    _sh(["chroot", TARGET, *argv], input=input, env=full_env)


def _chroot_stream(argv: list[str]) -> subprocess.Popen:
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C.UTF-8"}
    return subprocess.Popen(["chroot", TARGET, *argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            bufsize=1, env=env)


def find_live_rootfs() -> str:
    for path in LIVE_ROOTFS_CANDIDATES:
        if os.path.isdir(path):
            return path
    raise InstallError("Can't find the live system image. Run the installer from the LintabOS live USB.")


def _uuid(dev: str) -> str:
    return _sh(["blkid", "-s", "UUID", "-o", "value", dev]).stdout.strip()


def install(cfg: InstallConfig, progress: Progress) -> list[str]:
    """Install LintabOS. Returns warnings (things that didn't work but don't affect the base system)."""
    cfg.validate()
    warnings: list[str] = []
    if not os.path.isdir("/sys/firmware/efi"):
        raise InstallError("This computer is not booted in UEFI mode. LintabOS installs in UEFI mode only; "
                           "restart and boot the USB stick from its UEFI entry.")
    source = find_live_rootfs()
    root_dev = resolve_partuuid(cfg.plan.root_partuuid, cfg.plan.disk)
    esp_dev = resolve_partuuid(cfg.plan.esp_partuuid, cfg.plan.disk)

    mounted: list[str] = []
    try:
        progress(0.0, "Mounting the new system")
        os.makedirs(TARGET, exist_ok=True)
        _sh(["mount", root_dev, TARGET]); mounted.append(TARGET)
        os.makedirs(f"{TARGET}/boot/efi", exist_ok=True)
        _sh(["mount", esp_dev, f"{TARGET}/boot/efi"]); mounted.append(f"{TARGET}/boot/efi")

        _copy_system(source, progress)

        progress(0.80, "Preparing the system")
        for src, dst, opts in (("/dev", "dev", ["--rbind"]), ("/proc", "proc", ["--rbind"]),
                               ("/sys", "sys", ["--rbind"]), ("/run", "run", ["--bind"])):
            _sh(["mount", *opts, src, f"{TARGET}/{dst}"])
            mounted.insert(0, f"{TARGET}/{dst}")
            if opts == ["--rbind"]:
                _sh(["mount", "--make-rslave", f"{TARGET}/{dst}"])

        _write_fstab(root_dev, esp_dev)
        _configure_identity(cfg)
        _create_user(cfg)
        _show_windows_files(cfg)
        _remove_live_bits(cfg)

        progress(0.88, "Building the boot image")
        _chroot(["update-initramfs", "-u", "-k", "all"])

        progress(0.93, "Installing the GRUB bootloader")
        _install_grub(cfg)

        if cfg.desktops:
            # After GRUB: the base system already boots, so a failed download can only cost the extra desktops.
            warnings += desktopsmod.install_desktops(
                list(cfg.desktops), TARGET, _chroot, _chroot_stream, progress, lo=0.94, hi=0.985)

        progress(0.99, "Saving a copy of your old partition table")
        if cfg.plan.backup_path and os.path.exists(cfg.plan.backup_path):
            os.makedirs(f"{TARGET}/var/lib/lintab", exist_ok=True)
            shutil.copy(cfg.plan.backup_path, f"{TARGET}/var/lib/lintab/partition-table-before-install.sfdisk")
        progress(1.0, "Finishing up")
        return warnings
    finally:
        subprocess.run(["sync"])
        for mnt in mounted:
            subprocess.run(["umount", "-R", "-l", mnt], capture_output=True)


def _copy_system(source: str, progress: Progress) -> None:
    proc = subprocess.Popen(
        ["rsync", "-aHAXx", "--numeric-ids", "--info=progress2", "--no-inc-recursive",
         "--exclude=/etc/fstab", f"{source}/", f"{TARGET}/"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    buf = ""
    tail: list[str] = []
    while True:
        chunk = proc.stdout.read(256)
        if not chunk:
            break
        buf += chunk
        *lines, buf = re.split(r"[\r\n]", buf)
        for line in lines:
            m = re.search(r"(\d+)%", line)
            if m:
                progress(0.05 + 0.75 * int(m.group(1)) / 100, "Copying files")
            elif line.strip():
                tail.append(line)
    if proc.wait() != 0:
        raise InstallError("Copying the system failed:\n" + "\n".join(tail[-8:]))


def _write_fstab(root_dev: str, esp_dev: str) -> None:
    with open(f"{TARGET}/etc/fstab", "w") as f:
        f.write("# /etc/fstab: written by the LintabOS installer\n")
        f.write(f"UUID={_uuid(root_dev)}  /          ext4  defaults,noatime,errors=remount-ro  0 1\n")
        f.write(f"UUID={_uuid(esp_dev)}  /boot/efi  vfat  umask=0077  0 2\n")


def _configure_identity(cfg: InstallConfig) -> None:
    with open(f"{TARGET}/etc/hostname", "w") as f:
        f.write(cfg.hostname + "\n")
    with open(f"{TARGET}/etc/hosts", "w") as f:
        f.write(f"127.0.0.1\tlocalhost\n127.0.1.1\t{cfg.hostname}\n::1\tlocalhost ip6-localhost ip6-loopback\n")
    tz = f"{TARGET}/etc/localtime"
    if os.path.lexists(tz):
        os.remove(tz)
    os.symlink(f"/usr/share/zoneinfo/{cfg.timezone}", tz)
    with open(f"{TARGET}/etc/timezone", "w") as f:
        f.write(cfg.timezone + "\n")
    with open(f"{TARGET}/etc/default/locale", "w") as f:
        f.write(f"LANG={cfg.locale}\n")
    with open(f"{TARGET}/etc/default/keyboard", "w") as f:
        f.write(f'XKBMODEL="pc105"\nXKBLAYOUT="{cfg.keyboard_layout}"\nXKBVARIANT=""\nXKBOPTIONS=""\nBACKSPACE="guess"\n')
    if cfg.locale != "C.UTF-8":
        with open(f"{TARGET}/etc/locale.gen", "a") as f:
            f.write(f"{cfg.locale} UTF-8\n")
        _chroot(["locale-gen"])


def _create_user(cfg: InstallConfig) -> None:
    groups = "sudo,audio,video,render,input,plugdev,netdev,cdrom,dip,bluetooth,scanner,lpadmin"
    existing = {line.split(":")[0] for line in open(f"{TARGET}/etc/group")}
    groups = ",".join(g for g in groups.split(",") if g in existing)
    _chroot(["useradd", "--create-home", "--shell", "/bin/bash", "--comment", cfg.fullname,
             "--groups", groups, cfg.username])
    _chroot(["chpasswd"], input=f"{cfg.username}:{cfg.password}\n")
    _chroot(["passwd", "--lock", "root"])


def _show_windows_files(cfg: InstallConfig) -> None:
    """Dual-boot only: list the Windows drive (read-only) in the Files sidebar. Never fatal to the install."""
    if not cfg.plan.windows_present:
        return
    try:
        from . import winfiles
        for line in open(f"{TARGET}/etc/passwd"):
            fields = line.split(":")
            if fields[0] == cfg.username:
                winfiles.add_to_fstab(f"{TARGET}/etc/fstab", int(fields[2]), int(fields[3]), root=TARGET)
                break
    except Exception:
        pass  # an unreadable Windows partition (BitLocker, say) just means no sidebar entry


def _remove_live_bits(cfg: InstallConfig) -> None:
    _chroot(["sh", "-c", "apt-get -y purge " + " ".join(LIVE_ONLY_PACKAGES) + " || true"])
    _chroot(["sh", "-c", "apt-get -y autoremove --purge || true"])
    for path in ("/etc/sudoers.d/live", "/etc/live", "/etc/systemd/system/getty@tty1.service.d/live-config.conf",
                 "/etc/xdg/autostart/lintab-live-installer.desktop", "/usr/share/applications/lintab-installer.desktop",
                 "/usr/share/applications/lintab-uninstall.desktop"):
        full = TARGET + path
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        elif os.path.exists(full):
            os.remove(full)
    # Fresh GDM config: the live session's auto-login must not survive.
    daemon = ["[daemon]", "WaylandEnable=true"]
    if cfg.autologin:
        daemon += ["AutomaticLoginEnable=true", f"AutomaticLogin={cfg.username}"]
    with open(f"{TARGET}/etc/gdm3/daemon.conf", "w") as f:
        f.write("\n".join(daemon) + "\n\n[security]\n\n[xdmcp]\n\n[chooser]\n\n[debug]\n")


def _install_grub(cfg: InstallConfig) -> None:
    args = ["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
            "--bootloader-id=LintabOS", "--uefi-secure-boot", "--recheck"]
    if cfg.plan.mode == "wipe":
        # Nothing else lives on this disk; also provide the fallback path some
        # firmware insists on. Never done for dual-boot (leave Windows' ESP alone).
        args.append("--force-extra-removable")
    _chroot(args)
    _chroot(["update-grub"])
    _chroot(["grub-set-default", "0"])

    if cfg.plan.windows_present:
        _verify_windows_still_bootable()

    cfgfile = f"{TARGET}/boot/grub/grub.cfg"
    with open(cfgfile) as f:
        text = f.read()
    if "--id 'lintab-windows'" in text or "$menuentry_id_option 'lintab-windows'" in text:
        windows_entry = True
    else:
        windows_entry = "Boot into Windows" in text
    if cfg.plan.windows_present and not windows_entry:
        raise InstallError(
            "GRUB was installed but the 'Boot into Windows' entry could not be created. "
            "Windows is still intact; LintabOS can be started from the firmware boot menu.")


def _verify_windows_still_bootable() -> None:
    """After GRUB is in place, check that nothing we did removed Windows' way to boot.

    Two independent facts: the Windows Boot Manager file is still on the EFI partition, and the GRUB
    menu offers it. (A missing firmware boot entry is not fatal: GRUB chainloads the file directly.)
    """
    problems = []
    bootmgr = [os.path.join(dp, f) for dp, _dirs, files in os.walk(f"{TARGET}/boot/efi/EFI/Microsoft")
               for f in files if f.lower() == "bootmgfw.efi"]
    if not bootmgr:
        problems.append("Windows' boot manager file is missing from the EFI partition")
    try:
        with open(f"{TARGET}/boot/grub/grub.cfg") as f:
            if "lintab-windows" not in f.read():
                problems.append("the GRUB menu has no “Boot into Windows” entry")
    except OSError:
        problems.append("the GRUB configuration could not be read")
    if problems:
        raise InstallError("Windows may not be bootable: " + "; ".join(problems) + ". "
                           "Don't restart into the new system yet; Windows' files were not modified, "
                           "and its boot entry can be re-created from Windows' recovery tools.")
