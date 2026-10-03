# SPDX-License-Identifier: MIT
"""Android mode: the whole tablet becomes an Android tablet (Waydroid, full screen) and an app inside Android takes you back.

How: Waydroid's own documentation sets up a Wayland *session* that runs ``cage waydroid show-full-ui`` (cage is a tiny
compositor that shows exactly one app, full screen). This module installs that session ("Android" in the gear menu of the
login screen) and adds what a tablet needs around it:

* **Switch**: the *Android Mode* app remembers your current login session, selects the Android one for your next login (through
  AccountsService, the same place the login screen's gear menu writes to) and logs you out.
* **Leave**: inside Android there is an app called **Computer Mode**. Opening it ends Android mode and returns you to the login
  screen, with your previous desktop selected again. (The app asks a tiny listener on Waydroid's network bridge to end the
  session; the listener answers only on that bridge, never on your Wi-Fi or LAN.) A keyboard can still use Ctrl+Alt+F3.
* **Power button**: it is Android's power button while you are in Android mode. The host's own reaction to it (power off) and to
  the folio and sleep keys is held back for the session, so the tablet can't be turned off or put to sleep by accident.
* **Fail closed**: if the Computer Mode listener can't start, the Android session ends straight away instead of leaving you with
  no way out.

Waydroid itself comes from *LintabOS Extras → Android apps*. Untested on the Duet 3.
"""

from __future__ import annotations

import argparse
import http.server
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

EXIT_HOST = "192.168.240.1"            # the host's address on Waydroid's network bridge (waydroid0)
EXIT_PORT = 8765
EXIT_PATH = "/exit"
EXIT_URL = f"http://{EXIT_HOST}:{EXIT_PORT}{EXIT_PATH}"
APK = "/usr/share/lintabos/android/ComputerMode.apk"
APP_PACKAGE = "org.lintabos.computermode"
SESSION_NAME = "lintab-android"
DEFAULT_SESSION = "gnome"
STATE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "lintabos", "android-previous-session")
INHIBIT = ["systemd-inhibit", "--what=handle-power-key:handle-suspend-key:handle-hibernate-key:handle-lid-switch",
           "--who=LintabOS Android mode", "--why=the power button belongs to Android while it runs"]

Runner = Callable[[list[str]], tuple[int, str]]


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return proc.returncode, proc.stdout + proc.stderr


# ------------------------------------------------------------- Computer Mode listener --

class _ExitHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:                      # noqa: N802 - http.server API
        if self.path.split("?")[0] == EXIT_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            self.server.on_exit()                  # type: ignore[attr-defined]
        else:
            self.send_error(404)

    def log_message(self, *args) -> None:
        pass


def start_exit_listener(on_exit: Callable[[], None], host: str = EXIT_HOST, port: int = EXIT_PORT, tries: int = 30,
                        sleep: Callable[[float], None] = time.sleep) -> Optional[http.server.HTTPServer]:
    """Serve ``GET /exit`` on the Waydroid bridge only. The bridge appears when Waydroid's container starts, so retry for a while.
    Returns the running server, or None if the address could not be bound (the caller must then not enter Android mode)."""
    for attempt in range(tries):
        try:
            server = http.server.ThreadingHTTPServer((host, port), _ExitHandler)
        except OSError:
            sleep(1.0)
            continue
        server.on_exit = on_exit                   # type: ignore[attr-defined]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    return None


def app_installed(run: Runner = _run) -> bool:
    code, out = run(["waydroid", "app", "list"])
    return code == 0 and APP_PACKAGE in out


def session_running(run: Runner = _run) -> bool:
    code, out = run(["waydroid", "status"])
    return code == 0 and "RUNNING" in out.upper() and "SESSION" in out.upper()


def ensure_app(run: Runner = _run, apk: str = APK, tries: int = 90, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Once Android is up, make sure the Computer Mode app is installed in it (first time only)."""
    for _ in range(tries):
        if session_running(run):
            break
        sleep(2.0)
    else:
        return False
    if app_installed(run):
        return True
    code, _out = run(["waydroid", "app", "install", apk])
    return code == 0


# -------------------------------------------------------------- login session ---

def _user_path(uid: int, run: Runner) -> Optional[str]:
    code, out = run(["busctl", "--system", "call", "org.freedesktop.Accounts", "/org/freedesktop/Accounts",
                     "org.freedesktop.Accounts", "FindUserById", "x", str(uid)])
    parts = out.split('"')
    return parts[1] if code == 0 and len(parts) >= 3 else None


def get_session(uid: int, run: Runner = _run) -> Optional[str]:
    path = _user_path(uid, run)
    if not path:
        return None
    code, out = run(["busctl", "--system", "get-property", "org.freedesktop.Accounts", path, "org.freedesktop.Accounts.User", "Session"])
    parts = out.split('"')
    return parts[1] if code == 0 and len(parts) >= 3 and parts[1] else None


def set_session(uid: int, name: str, run: Runner = _run) -> bool:
    path = _user_path(uid, run)
    if not path:
        return False
    for method in ("SetSession", "SetXSession"):
        code, _out = run(["busctl", "--system", "call", "org.freedesktop.Accounts", path, "org.freedesktop.Accounts.User", method, "s", name])
        if code == 0:
            return True
    return False


def remember_previous(uid: int, run: Runner = _run, path: str = STATE) -> str:
    """Save the login session to come back to (never the Android one itself)."""
    current = get_session(uid, run)
    previous = current if current and current != SESSION_NAME else DEFAULT_SESSION
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(previous + "\n")
    return previous


def restore_previous(uid: int, run: Runner = _run, path: str = STATE) -> str:
    try:
        with open(path) as f:
            previous = f.read().strip() or DEFAULT_SESSION
    except OSError:
        previous = DEFAULT_SESSION
    set_session(uid, previous, run)
    return previous


# ----------------------------------------------------------------------- flow ---

def waydroid_ready() -> bool:
    return bool(shutil.which("waydroid") and shutil.which("cage"))


def switch(uid: Optional[int] = None, run: Runner = _run, path: str = STATE, apk: str = APK,
           ready: Optional[bool] = None) -> str:
    """Select the Android session for the next login. Returns "" on success, or a plain-words reason it didn't."""
    uid = os.getuid() if uid is None else uid
    if not (waydroid_ready() if ready is None else ready):
        return "Android apps aren't installed yet. Open LintabOS Extras and pick “Android apps (Waydroid)” first."
    if not os.path.isfile(apk):
        return "The Computer Mode app is missing from this installation, and it is the way back from Android. Reinstall lintabos-core."
    remember_previous(uid, run, path)
    if not set_session(uid, SESSION_NAME, run):
        return "The login session could not be changed (is AccountsService running?)."
    return ""


def session_main(run: Runner = _run) -> int:
    """Runs inside the Android session (under systemd-inhibit): Waydroid in cage; the Computer Mode app is the way out."""
    uid = os.getuid()

    def leave() -> None:
        subprocess.run(["waydroid", "session", "stop"], capture_output=True)
        subprocess.run(["pkill", "-u", str(uid), "-x", "cage"], capture_output=True)

    server = start_exit_listener(leave)
    if server is None:                             # no way out would exist: don't go in
        print("lintab-android: could not open the Computer Mode listener on the Waydroid bridge; leaving Android mode.",
              file=sys.stderr)
        restore_previous(uid, run)
        return 1
    threading.Thread(target=ensure_app, args=(run,), daemon=True).start()
    try:
        code = subprocess.run(["cage", "-s", "--", "waydroid", "show-full-ui"]).returncode
    finally:
        server.shutdown()
        subprocess.run(["waydroid", "session", "stop"], capture_output=True)
        restore_previous(uid, run)                 # the next login is the normal desktop again
    return code


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-android", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["start", "back", "status", "session"], nargs="?", default="start")
    ap.add_argument("--no-logout", action="store_true", help="select the Android session but don't log out")
    args = ap.parse_args(argv)
    uid = os.getuid()
    if args.action == "session":
        return session_main()
    if args.action == "status":
        print(f"waydroid and cage installed: {'yes' if waydroid_ready() else 'no'}\n"
              f"Computer Mode app shipped: {'yes' if os.path.isfile(APK) else 'NO'}\n"
              f"next login session: {get_session(uid) or 'unknown'}")
        return 0
    if args.action == "back":
        print(f"next login session: {restore_previous(uid)}")
        return 0
    return gui(uid, logout=not args.no_logout)


def gui(uid: int, logout: bool = True) -> int:
    zenity = shutil.which("zenity")

    def tell(kind: str, text: str) -> None:
        if zenity:
            subprocess.run([zenity, kind, "--title=Android mode", "--width=460", f"--text={text}"])
        else:
            print(text)

    if zenity and subprocess.run([
            zenity, "--question", "--title=Android mode", "--width=480", "--ok-label=Switch to Android",
            "--text=The whole tablet becomes an Android tablet.\n\nThis logs you out of LintabOS (save your work first). "
            "Sign in again and you'll land in Android.\n\nTo come back: open the “Computer Mode” app inside Android. "
            "You sign in again and get the normal desktop. The power button works as Android's power button.\n\n"
            "Android mode is untested on this tablet."]).returncode != 0:
        return 0
    problem = switch(uid)
    if problem:
        tell("--error", problem)
        return 1
    if logout:
        subprocess.run(["gnome-session-quit", "--logout", "--no-prompt"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
