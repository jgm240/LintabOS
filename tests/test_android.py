# SPDX-License-Identifier: MIT
"""Android mode: switching the login session safely, the Computer Mode listener and app install, and failing closed."""

import http.client
import os
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import android  # noqa: E402


class Bus:
    """A fake AccountsService over busctl."""

    def __init__(self, session="gnome", ok=True):
        self.session, self.ok, self.calls = session, ok, []

    def __call__(self, argv):
        self.calls.append(argv)
        if not self.ok:
            return 1, "Failed to connect"
        if "FindUserById" in argv:
            return 0, 'o "/org/freedesktop/Accounts/User1000"'
        if "get-property" in argv:
            return 0, f's "{self.session}"'
        if argv[-3] in ("SetSession", "SetXSession"):
            self.session = argv[-1]
            return 0, ""
        return 1, ""


def test_the_previous_session_is_remembered_and_put_back(tmp_path):
    state, bus = str(tmp_path / "prev"), Bus("gnome-xorg")
    assert android.remember_previous(1000, bus, state) == "gnome-xorg"
    assert android.set_session(1000, android.SESSION_NAME, bus) and bus.session == "lintab-android"
    assert android.restore_previous(1000, bus, state) == "gnome-xorg" and bus.session == "gnome-xorg"


def test_it_never_remembers_android_as_the_session_to_return_to(tmp_path):
    state = str(tmp_path / "prev")
    assert android.remember_previous(1000, Bus("lintab-android"), state) == "gnome"      # fell asleep in Android last time
    assert android.remember_previous(1000, Bus(ok=False), state) == "gnome"               # can't ask: the safe default
    assert android.restore_previous(1000, Bus("lintab-android"), str(tmp_path / "none")) == "gnome"


def test_switching_selects_the_android_session_after_saving_the_old_one(tmp_path):
    apk = tmp_path / "ComputerMode.apk"
    apk.write_bytes(b"PK")
    bus, state = Bus("gnome"), tmp_path / "prev"
    assert android.switch(1000, bus, str(state), str(apk), ready=True) == ""
    assert bus.session == "lintab-android" and state.read_text().strip() == "gnome"
    failing = Bus("gnome", ok=False)
    assert "could not be changed" in android.switch(1000, failing, str(tmp_path / "p2"), str(apk), ready=True)


def test_switching_needs_the_way_back_to_exist(tmp_path):
    bus = Bus()
    reason = android.switch(1000, bus, str(tmp_path / "p"), str(tmp_path / "missing.apk"), ready=True)
    assert "Computer Mode app is missing" in reason
    assert bus.session == "gnome" and not any("SetSession" in c for c in bus.calls)        # nothing was changed
    assert "Extras" in android.switch(1000, bus, str(tmp_path / "p"), str(tmp_path / "x.apk"), ready=False)


# ------------------------------------------------------------ Computer Mode listener --

def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    return response.status, response.read()


def test_the_exit_request_ends_the_session_and_nothing_else_does():
    fired = threading.Event()
    server = android.start_exit_listener(fired.set, host="127.0.0.1", port=0, tries=1, sleep=lambda s: None)
    try:
        port = server.server_address[1]
        for path in ("/", "/exit/extra", "/status", "/favicon.ico"):
            assert get(port, path)[0] == 404
        assert not fired.is_set()
        assert get(port, "/exit") == (200, b"ok")
        assert fired.wait(5)
    finally:
        server.shutdown()


def test_the_listener_is_bound_to_the_waydroid_bridge_not_to_everything():
    assert android.EXIT_HOST == "192.168.240.1" and android.EXIT_HOST not in ("", "0.0.0.0", "::")
    assert android.EXIT_URL == "http://192.168.240.1:8765/exit"


def test_the_listener_waits_for_the_bridge_and_gives_up_cleanly():
    sleeps = []
    assert android.start_exit_listener(lambda: None, host="192.0.2.77", port=8765, tries=3, sleep=sleeps.append) is None
    assert len(sleeps) == 3                                                   # it kept trying, then returned None


def test_no_listener_means_the_session_ends_at_once_instead_of_trapping_you(monkeypatch):
    called = []
    monkeypatch.setattr(android, "start_exit_listener", lambda *a, **k: None)
    monkeypatch.setattr(android, "restore_previous", lambda uid, run=None: called.append("restored"))
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **k: pytest.fail("must not start cage without a way out"))
    assert android.session_main(run=lambda argv: (0, "")) == 1 and called == ["restored"]


class Waydroid:
    def __init__(self, running=True, installed=False, install_ok=True):
        self.running, self.installed, self.install_ok, self.calls = running, installed, install_ok, []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:2] == ["waydroid", "status"]:
            return 0, "Session:\tRUNNING\nContainer:\tRUNNING\n" if self.running else "Session:\tSTOPPED\n"
        if argv[:3] == ["waydroid", "app", "list"]:
            return 0, f"Name: Computer Mode\npackageName: {android.APP_PACKAGE}\n" if self.installed else "Name: Settings\n"
        if argv[:3] == ["waydroid", "app", "install"]:
            self.installed = self.install_ok
            return (0 if self.install_ok else 1), ""
        return 1, ""

    def ran(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.calls)


def test_the_computer_mode_app_is_installed_once_android_is_up():
    wd = Waydroid()
    assert android.ensure_app(wd, "/x/ComputerMode.apk", tries=2, sleep=lambda s: None) is True
    assert wd.ran("waydroid", "app", "install", "/x/ComputerMode.apk")
    wd2 = Waydroid(installed=True)
    assert android.ensure_app(wd2, "/x/ComputerMode.apk", tries=2, sleep=lambda s: None) is True
    assert not wd2.ran("waydroid", "app", "install")                         # already there: left alone


def test_installing_the_app_waits_for_android_and_reports_failure():
    sleeps = []
    assert android.ensure_app(Waydroid(running=False), tries=3, sleep=sleeps.append) is False and len(sleeps) == 3
    assert android.ensure_app(Waydroid(install_ok=False), "/x.apk", tries=1, sleep=lambda s: None) is False


# ------------------------------------------------------------------- wiring ----

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_session_files_are_wired_correctly():
    session = open(os.path.join(ROOT, "payload/usr/share/wayland-sessions/lintab-android.desktop")).read()
    assert "Exec=/usr/bin/lintab-android-session" in session and "Type=Application" in session
    path = os.path.join(ROOT, "installer/bin/lintab-android-session")
    script = open(path).read()
    assert subprocess.run(["sh", "-n", path]).returncode == 0
    for held in ("handle-power-key", "handle-suspend-key", "handle-lid-switch"):
        assert held in script                                                # host must not power off or sleep the tablet
    assert "session_main" in script
    code = open(os.path.join(ROOT, "installer/lintab/android.py")).read()
    assert '"cage", "-s", "--", "waydroid", "show-full-ui"' in code
    assert "KEY_POWER" not in code and "watch_power" not in code            # the power button is Android's, not an exit
    helper = open(os.path.join(ROOT, "payload/usr/libexec/lintab/install-waydroid")).read()
    assert "install -y --no-install-recommends cage" in helper.replace("apt-get ", "")


def test_the_android_app_source_asks_for_the_same_address_the_host_listens_on():
    manifest = open(os.path.join(ROOT, "android/computermode/AndroidManifest.xml")).read()
    assert 'package="org.lintabos.computermode"' in manifest and "android.permission.INTERNET" in manifest
    assert android.APP_PACKAGE == "org.lintabos.computermode"
    source = open(os.path.join(ROOT, "android/computermode/src/org/lintabos/computermode/MainActivity.java")).read()
    assert '"@EXIT_URL@"' in source                                         # filled in from android.EXIT_URL at build time
    assert "Computer Mode" in open(os.path.join(ROOT, "android/computermode/res/values/strings.xml")).read()


def test_the_built_apk_is_shipped_and_signed():
    apk = os.path.join(ROOT, "payload/usr/share/lintabos/android/ComputerMode.apk")
    assert os.path.isfile(apk) and os.path.getsize(apk) < 200_000
    import zipfile
    names = zipfile.ZipFile(apk).namelist()
    assert "classes.dex" in names and "AndroidManifest.xml" in names and any(n.startswith("META-INF/") for n in names)
    dex = zipfile.ZipFile(apk).read("classes.dex")
    assert android.EXIT_URL.encode() in dex                                 # the host's address really is inside the app
