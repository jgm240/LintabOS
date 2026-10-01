# SPDX-License-Identifier: MIT
"""The installer's "Turn off BitLocker" step.

Shown when the Windows partition is BitLocker-encrypted (or a previous decryption was interrupted).
The partitioner can't shrink an encrypted volume, so BitLocker has to be off first. Two routes are
offered, the safe one first:

1. in Windows itself (instructions), then come back; or
2. from here with the recovery key: :mod:`lintab.bitlocker_decrypt` decrypts the partition in place,
   journaled so an interruption can be resumed, and verifies Windows' boot files afterwards.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import bitlocker, bitlocker_decrypt as bd  # noqa: E402
from .disks import GiB, Partition  # noqa: E402

WINDOWS_STEPS = (
    "1.  Start Windows and sign in.\n"
    "2.  Windows 11 Home: Settings → Privacy & security → Device encryption → Off.\n"
    "     Windows Pro: Control Panel → BitLocker Drive Encryption → Turn off BitLocker.\n"
    "     (Or, in an administrator Command Prompt:  manage-bde -off C:)\n"
    "3.  Wait until Windows says decryption is finished. It can take an hour or more.\n"
    "4.  Turn off Fast Startup (Command Prompt as administrator:  powercfg /h off).\n"
    "5.  Shut down fully (hold Shift while clicking Shut down), then start this installer again.\n"
    "\n"
    "If the installer says the drive \"needs a check\": in Windows choose Restart, then run\n"
    "chkdsk C: /f as administrator (BitLocker doesn't prevent this). From the recovery screen use\n"
    "Troubleshoot → Command Prompt:  manage-bde -unlock C: -rp <48-digit key>  and then chkdsk C: /f."
)

RISK_TEXT = (
    "This rewrites your entire Windows drive. If the power fails it can be resumed, and every step is "
    "checked, but a hardware fault in the middle could still lose data, so back up what matters first and "
    "keep the charger plugged in.\n\n"
    "Afterwards Windows should start as before: its core boot files are compared before and after. "
    "This installer can't test-boot Windows, though. Windows' own “turn off BitLocker” (above) is the "
    "safest route.\n\n"
    "If Windows turns Device encryption back on later, turn it off again in Settings; with it on, starting "
    "Windows from the LintabOS boot menu asks for the recovery key."
)


class BitLockerStep:
    def __init__(self, window, partition: Partition, esp: Optional[Partition],
                 pending: Optional[dict], on_finished: Callable[[], None]) -> None:
        self.w = window
        self.partition = partition
        self.esp = esp
        self.pending = pending
        self.on_finished = on_finished
        self.esp_mount: Optional[str] = None
        self.inhibitor: Optional[subprocess.Popen] = None
        self.preflight: Optional[bd.Preflight] = None
        self.started = 0.0

    # ----------------------------------------------------------------- pages
    def start(self) -> None:
        self.w.nav.push(self._key_page())

    def _key_page(self) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        resuming = self.pending is not None
        needs_key = not resuming or self.pending.get("phase") in ("body", "header")

        if resuming:
            intro = ("A BitLocker decryption of this drive was interrupted. Nothing is lost: it can pick up "
                     "where it stopped. Don't start Windows until it has finished.")
        else:
            intro = ("Windows is encrypted with BitLocker (Device encryption), and an encrypted drive can't be "
                     "shrunk safely. BitLocker has to be turned off before LintabOS can be installed next to Windows.")
        box.append(Gtk.Label(label=intro, wrap=True, xalign=0))

        if not resuming:
            safe = Adw.PreferencesGroup()
            expander = Adw.ExpanderRow(title="Safest way: turn it off in Windows",
                                       subtitle="Recommended. Takes longer, but Windows does it itself.")
            steps = Gtk.Label(label=WINDOWS_STEPS, wrap=True, xalign=0, selectable=True,
                              margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
            expander.add_row(steps)
            safe.add(expander)
            box.append(safe)

        group = Adw.PreferencesGroup(
            title="Resume decrypting" if resuming else "Or turn it off from here",
            description=("The recovery key is the 48-digit number from account.microsoft.com/devices/recoverykey, "
                         "your Microsoft account, or the printout/USB you saved."
                         if needs_key else "No key is needed for the remaining steps."))
        self.key_row = Adw.PasswordEntryRow(title="Recovery key (48 digits)")
        self.key_row.set_visible(needs_key)
        group.add(self.key_row)
        box.append(group)

        self.status = Gtk.Label(label="", wrap=True, xalign=0)
        box.append(self.status)

        label = "Resume" if resuming else "Check key and drive"
        footer = self.w._footer(label, self._check_clicked)
        self.key_footer = footer
        return self.w._page("Turn off BitLocker", "bitlocker", box, footer)

    # ----------------------------------------------------------- check / key
    def _check_clicked(self) -> None:
        resuming = self.pending is not None
        key = ""
        if not resuming or self.pending.get("phase") in ("body", "header"):
            try:
                key = bitlocker.normalize_recovery_key(self.key_row.get_text())
            except bitlocker.BitLockerError as exc:
                self.w._error("Check the recovery key", str(exc))
                return
        self.key = key
        self.key_footer.set_sensitive(False)
        if resuming:
            self.status.set_text("Preparing to resume…")
            self._begin_decrypt(resume=True)
            return
        self.status.set_text("Checking the key and the drive. This takes up to a minute…")

        def work() -> None:
            try:
                self.esp_mount = bd.mount_esp(self.esp.path) if self.esp else None
                if not self.esp_mount:
                    raise bd.DecryptError("The EFI partition wasn't found, so there's no safe place for the "
                                          "recovery journal.")
                pre = bd.preflight(self.partition.path, key, self.esp_mount)
                GLib.idle_add(self._checked, pre, None)
            except (bd.DecryptError, bitlocker.BitLockerError) as exc:
                GLib.idle_add(self._checked, None, exc)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self._checked, None, exc)

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, pre: Optional[bd.Preflight], error: Optional[Exception]) -> bool:
        self.key_footer.set_sensitive(True)
        self.status.set_text("")
        if error is not None or pre is None:
            self._release()
            self.w._error("Couldn't open the drive", str(error))
            return False
        if not pre.ok:
            self._release()
            self.w._error("Not ready yet", "\n\n".join(pre.problems))
            return False
        self.preflight = pre
        self.w.nav.push(self._confirm_page(pre))
        return False

    def _confirm_page(self, pre: bd.Preflight) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        facts = Adw.PreferencesGroup(title="The key works. This is what was found")
        facts.add(Adw.ActionRow(title="Windows drive",
                                subtitle=f"{pre.info.size / GiB:.0f} GB, {pre.info.used / GiB:.0f} GB in use, "
                                         + ("file system healthy" if pre.info.healthy
                                            else "flagged for a check (fine for this step)")))
        facts.add(Adw.ActionRow(title="Windows boot files found",
                                subtitle=("%d, their checksums are recorded now and compared afterwards" % pre.boot_files)
                                if pre.boot_files else "None (this looks like a data drive)"))
        facts.add(Adw.ActionRow(title="Charger", subtitle="Plugged in" if pre.ac_power else "NOT plugged in"))
        for warning in pre.warnings:
            facts.add(Adw.ActionRow(title=warning, title_lines=3))
        box.append(facts)

        box.append(Gtk.Label(label=RISK_TEXT, wrap=True, xalign=0))

        confirm = Adw.PreferencesGroup()
        self.understand = Adw.SwitchRow(title="I've backed up what matters and understand the risk")
        self.typed = Adw.EntryRow(title="Type DECRYPT to confirm")
        for row in (self.understand, self.typed):
            confirm.add(row)
        box.append(confirm)

        footer = self.w._footer("Turn off BitLocker", self._confirm_clicked, "destructive-action")
        footer.set_sensitive(False)
        self.go_button = footer

        def update(*_a) -> None:
            footer.set_sensitive(self.understand.get_active() and self.typed.get_text().strip() == "DECRYPT")

        self.understand.connect("notify::active", update)
        self.typed.connect("changed", update)
        return self.w._page("Turn off BitLocker", "bitlocker-confirm", box, footer)

    def _confirm_clicked(self) -> None:
        self._begin_decrypt(resume=False)

    # -------------------------------------------------------------- progress
    def _begin_decrypt(self, resume: bool) -> None:
        self.bar = Gtk.ProgressBar(margin_top=24)
        self.label = Gtk.Label(label="Starting…")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.append(self.bar)
        inner.append(self.label)
        status = Adw.StatusPage(
            title="Turning off BitLocker",
            description="Keep the charger plugged in and don't turn the tablet off.\n"
                        "If it is interrupted, start the installer again to resume.")
        status.set_child(inner)
        self.w.nav.push(self.w._page("Decrypting", "bitlocker-progress", status, can_pop=False))
        self.started = time.time()
        self.inhibitor = _inhibit_sleep()

        def progress(phase: str, done: int, total: int, message: str) -> None:
            GLib.idle_add(self._progress, phase, done, total, message)

        def work() -> None:
            try:
                if self.esp_mount is None:
                    self.esp_mount = bd.mount_esp(self.esp.path)
                bd.decrypt_in_place(self.partition.path, self.key, self.esp_mount, progress=progress)
                GLib.idle_add(self._finished, None)
            except Exception as exc:  # noqa: BLE001 - shown to the user, journal is kept
                GLib.idle_add(self._finished, exc)

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, phase: str, done: int, total: int, message: str) -> bool:
        fraction = done / total if total else 0.0
        self.bar.set_fraction(min(max(fraction, 0.0), 1.0))
        text = message
        if phase == "decrypt" and done:
            elapsed = time.time() - self.started
            remaining = elapsed * (total - done) / done
            text = f"{message}: {fraction * 100:.0f}%, about {max(1, round(remaining / 60))} min left"
        elif phase in ("verify",) and total > 1:
            text = f"{message}: {fraction * 100:.0f}%"
        self.label.set_text(text)
        return False

    def _finished(self, error: Optional[Exception]) -> bool:
        self._release()
        if error is not None:
            interrupted = isinstance(error, bd.DecryptInterrupted)
            body = str(error) + ("\n\nNothing is lost. Do not start Windows yet; start this installer again and "
                                 "choose to resume." if not interrupted else "")
            status = Adw.StatusPage(title="BitLocker was not fully turned off", icon_name="dialog-error-symbolic",
                                    description=body)
            self.w.nav.push(self.w._page("Failed", "bitlocker-failed", status,
                                         self.w._footer("Close", self.w.close, "destructive-action"), can_pop=False))
            return False
        status = Adw.StatusPage(title="BitLocker is off", icon_name="emblem-ok-symbolic",
                                description="Windows' files were checked and match. Continue to choose how much "
                                            "space LintabOS gets.")
        self.w.nav.push(self.w._page("Done", "bitlocker-done", status,
                                     self.w._footer("Continue", self.on_finished), can_pop=False))
        return False

    def _release(self) -> None:
        if self.inhibitor is not None:
            self.inhibitor.terminate()
            self.inhibitor = None
        bitlocker.lock_all()
        if self.esp_mount:
            bd.unmount_esp(self.esp_mount)
            self.esp_mount = None


def _inhibit_sleep() -> Optional[subprocess.Popen]:
    """Keep the tablet awake while the drive is being rewritten."""
    try:
        return subprocess.Popen(["systemd-inhibit", "--what=sleep:idle:handle-lid-switch",
                                 "--who=LintabOS installer", "--why=Turning off BitLocker on the Windows drive",
                                 "--mode=block", "sleep", "infinity"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None
