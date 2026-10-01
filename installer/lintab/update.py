# SPDX-License-Identifier: MIT
"""LintabOS updater: pulls signed releases of ``lintabos-core`` from the project's GitHub repository.

What it updates: LintabOS's own parts (tablet tuning, installer, BitLocker tools, boot menu entry, branding),
shipped as the Debian package ``lintabos-core``. Debian's own packages (kernel, GNOME, security fixes…) are
updated the normal way through apt / GNOME Software.

How it stays safe even if the GitHub account, the network or a mirror is hostile:

* every package must carry a valid **minisign signature** from the key shipped in the OS
  (``/usr/share/lintabos/update-key.pub``); GitHub is only the delivery truck;
* the package's own name and version must match what the release announced (no swapping packages);
* **downgrades and re-installs are refused**, so an old signed release can't be replayed;
* downloads are accepted only from GitHub's hosts (every redirect is checked), over HTTPS, with size limits;
* checking is unprivileged and read-only; installing needs the user's password (polkit) and an explicit action.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

REPO = "jgm240/LintabOS"
PACKAGE = "lintabos-core"
API_ROOT = "https://api.github.com"
KEY_PATH = "/usr/share/lintabos/update-key.pub"
CONF_PATH = "/etc/lintabos/update.conf"
CACHE_DIR = "/var/cache/lintabos-update"
USER_AGENT = "lintabos-updater"

MAX_JSON = 4 * 1024 * 1024
MAX_DEB = 96 * 1024 * 1024
MAX_SIG = 8 * 1024
TIMEOUT = 20

ALLOWED_HOSTS = {"api.github.com", "github.com"}
ALLOWED_SUFFIXES = (".githubusercontent.com",)

VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+~-]{0,63}$")


class UpdateError(RuntimeError):
    """Something went wrong that the user should be told about, in plain words."""


# --------------------------------------------------------------- testing hooks --
# Only honoured with LINTAB_UPDATE_TESTING=1, so a stray environment variable can't redirect a real update.

def _testing() -> bool:
    return os.environ.get("LINTAB_UPDATE_TESTING") == "1"


def _api_root() -> str:
    return os.environ.get("LINTAB_UPDATE_API", API_ROOT) if _testing() else API_ROOT


def _key_path() -> str:
    return os.environ.get("LINTAB_UPDATE_KEY", KEY_PATH) if _testing() else KEY_PATH


def _cache_dir() -> str:
    return os.environ.get("LINTAB_UPDATE_CACHE", CACHE_DIR) if _testing() else CACHE_DIR


def _conf_path() -> str:
    return os.environ.get("LINTAB_UPDATE_CONF", CONF_PATH) if _testing() else CONF_PATH


def _allowed_host(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if _testing() and host in ("127.0.0.1", "localhost"):
        return True
    if parts.scheme != "https":
        return False
    return host in ALLOWED_HOSTS or host.endswith(ALLOWED_SUFFIXES)


# ------------------------------------------------------------------ versions ---

def parse_version(tag: str) -> str:
    version = tag.strip()
    if version[:1] in ("v", "V"):
        version = version[1:]
    if not VERSION_RE.match(version):
        raise UpdateError(f"Unrecognized release version {tag!r}.")
    return version


def vercmp(a: str, b: str) -> int:
    """-1, 0 or 1 using Debian's own comparison rules."""
    if a == b:
        return 0
    for op, result in (("lt", -1), ("gt", 1)):
        if subprocess.run(["dpkg", "--compare-versions", a, op, b]).returncode == 0:
            return result
    return 0


def installed_version(package: str = PACKAGE) -> Optional[str]:
    proc = subprocess.run(["dpkg-query", "-W", "-f=${Status}\t${Version}", package], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    status, _, version = proc.stdout.partition("\t")
    return version if "installed" in status and "not-installed" not in status else None


# -------------------------------------------------------------------- config ---

def load_config(path: Optional[str] = None) -> dict[str, str]:
    conf = {"channel": "any", "auto_check": "true"}
    try:
        with open(path or _conf_path()) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    key, _, value = line.partition("=")
                    conf[key.strip().lower()] = value.strip().lower()
    except OSError:
        pass
    if conf["channel"] not in ("any", "stable"):
        conf["channel"] = "any"
    return conf


# ------------------------------------------------------------------- network ---

class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_host(newurl):
            raise UpdateError(f"Refusing to follow a redirect to an untrusted address ({urllib.parse.urlsplit(newurl).hostname}).")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, max_bytes: int, accept: str = "*/*") -> bytes:
    if not _allowed_host(url):
        raise UpdateError(f"Refusing to download from an untrusted address ({urllib.parse.urlsplit(url).hostname}).")
    opener = urllib.request.build_opener(_CheckedRedirects())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise UpdateError("The download is larger than expected; refusing it.")
            data = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError("GitHub is rate-limiting requests right now. Try again in a while.") from exc
        raise UpdateError(f"GitHub answered with an error ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Couldn't reach GitHub. Check the internet connection.") from exc
    if len(data) > max_bytes:
        raise UpdateError("The download is larger than expected; refusing it.")
    return data


# ------------------------------------------------------------------ releases ---

@dataclasses.dataclass
class Release:
    tag: str
    version: str
    name: str
    notes: str
    prerelease: bool
    url: str  # human-readable release page
    deb_url: str
    sig_url: str
    deb_name: str


def _deb_name(version: str) -> str:
    return f"{PACKAGE}_{version}_all.deb"


def parse_releases(payload: list) -> list[Release]:
    """Keep only well-formed, non-draft releases that carry both the package and its signature."""
    releases = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        try:
            version = parse_version(str(item.get("tag_name", "")))
        except UpdateError:
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in item.get("assets", []) if isinstance(a, dict)}
        deb = _deb_name(version)
        if not assets.get(deb) or not assets.get(deb + ".minisig"):
            continue
        releases.append(Release(
            tag=str(item["tag_name"]), version=version, name=str(item.get("name") or item["tag_name"]),
            notes=str(item.get("body") or ""), prerelease=bool(item.get("prerelease")),
            url=str(item.get("html_url") or f"https://github.com/{REPO}/releases"),
            deb_url=str(assets[deb]), sig_url=str(assets[deb + ".minisig"]), deb_name=deb))
    return releases


def pick_release(releases: list[Release], channel: str) -> Optional[Release]:
    candidates = [r for r in releases if channel == "any" or not r.prerelease]
    best: Optional[Release] = None
    for release in candidates:
        if best is None or vercmp(release.version, best.version) > 0:
            best = release
    return best


def fetch_releases() -> list[Release]:
    url = f"{_api_root()}/repos/{REPO}/releases?per_page=30"
    try:
        payload = json.loads(http_get(url, MAX_JSON, "application/vnd.github+json"))
    except ValueError as exc:
        raise UpdateError("GitHub sent an answer that could not be understood.") from exc
    if not isinstance(payload, list):
        raise UpdateError("GitHub sent an unexpected answer.")
    return parse_releases(payload)


@dataclasses.dataclass
class CheckResult:
    installed: Optional[str]
    latest: Optional[Release]
    available: bool


def check(channel: Optional[str] = None) -> CheckResult:
    channel = channel or load_config()["channel"]
    current = installed_version()
    latest = pick_release(fetch_releases(), channel)
    available = bool(latest and (current is None or vercmp(latest.version, current) > 0))
    return CheckResult(current, latest, available)


# --------------------------------------------------------------- verification ---

def verify_package(deb: str, sig: str, release: Release) -> None:
    """Raise UpdateError unless this file is the genuine, newer lintabos-core announced by the release."""
    key = _key_path()
    if not os.path.exists(key):
        raise UpdateError("The update signing key is missing from this system; refusing to install anything.")
    if not shutil.which("minisign"):
        raise UpdateError("minisign is not installed, so updates can't be verified.")
    proc = subprocess.run(["minisign", "-V", "-q", "-p", key, "-m", deb, "-x", sig], capture_output=True, text=True)
    if proc.returncode != 0:
        raise UpdateError("The update's signature is NOT valid. It was not installed. "
                          "(Corrupted download, or not published by the LintabOS maintainer.)")
    fields = {}
    for field in ("Package", "Version", "Architecture"):
        out = subprocess.run(["dpkg-deb", "-f", deb, field], capture_output=True, text=True)
        if out.returncode != 0:
            raise UpdateError("The downloaded file is not a valid package.")
        fields[field] = out.stdout.strip()
    if fields["Package"] != PACKAGE:
        raise UpdateError(f"The package is called {fields['Package']!r}, not {PACKAGE!r}; refusing it.")
    if fields["Version"] != release.version:
        raise UpdateError("The package version doesn't match the release it was published with; refusing it.")
    current = installed_version()
    if current is not None and vercmp(fields["Version"], current) <= 0:
        raise UpdateError(f"Version {fields['Version']} is not newer than the installed {current}; "
                          "refusing to downgrade or reinstall.")


def download_and_verify(release: Release, workdir: str) -> str:
    deb = os.path.join(workdir, release.deb_name)
    sig = deb + ".minisig"
    with open(deb, "wb") as f:
        f.write(http_get(release.deb_url, MAX_DEB))
    with open(sig, "wb") as f:
        f.write(http_get(release.sig_url, MAX_SIG))
    verify_package(deb, sig, release)
    return deb


# ------------------------------------------------------------------- install ---

def apply(release: Release, log=print) -> None:
    if os.geteuid() != 0:
        raise UpdateError("Installing an update needs administrator rights.")
    os.makedirs(_cache_dir(), mode=0o755, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="dl-", dir=_cache_dir())
    try:
        log(f"Downloading LintabOS {release.version}…")
        deb = download_and_verify(release, workdir)
        log("Signature and package checked. Installing…")
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C.UTF-8"}
        proc = subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", deb], env=env,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            tail = " ".join((proc.stderr or proc.stdout).strip().splitlines()[-3:])
            raise UpdateError(f"Installing the update failed: {tail}")
        log(f"LintabOS core is now version {installed_version()}.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------- notification ---

def notify(release: Release) -> None:
    """Desktop notification, once per version."""
    state_dir = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "lintabos")
    state = os.path.join(state_dir, "update-notified")
    try:
        if open(state).read().strip() == release.version:
            return
    except OSError:
        pass
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "LintabOS", "-i", "software-update-available-symbolic",
                        "-h", "string:desktop-entry:lintab-update", "LintabOS update available",
                        f"Version {release.version} is ready. Open LintabOS Updates to install it."],
                       capture_output=True)
    os.makedirs(state_dir, exist_ok=True)
    with open(state, "w") as f:
        f.write(release.version)


# ----------------------------------------------------------------------- CLI ---

def _result_json(result: CheckResult, error: str = "") -> dict:
    latest = result.latest
    return {"installed": result.installed, "latest": latest.version if latest else None,
            "available": result.available, "prerelease": bool(latest and latest.prerelease),
            "notes": latest.notes if latest else "", "page": latest.url if latest else "", "error": error}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lintab-update", description="Update LintabOS from its GitHub releases.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="ask GitHub whether a newer release exists (changes nothing)")
    c.add_argument("--json", action="store_true")
    c.add_argument("--notify", action="store_true", help="show a desktop notification if there is one")
    c.add_argument("--quiet", action="store_true", help="no output; never fail (for the background timer)")
    c.add_argument("--channel", choices=["any", "stable"])
    a = sub.add_parser("apply", help="download, verify and install the newest release (needs root)")
    a.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    a.add_argument("--channel", choices=["any", "stable"])
    sub.add_parser("status", help="show the installed version and settings")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        conf = load_config()
        print(f"installed: {installed_version() or 'not installed'}\nchannel: {conf['channel']}\n"
              f"auto_check: {conf['auto_check']}\nrepository: https://github.com/{REPO}")
        return 0

    if args.cmd == "check":
        if args.quiet and args.notify and load_config()["auto_check"] != "true":
            return 0
        try:
            result = check(args.channel)
        except UpdateError as exc:
            if args.json:
                print(json.dumps(_result_json(CheckResult(installed_version(), None, False), str(exc))))
            elif not args.quiet:
                print(f"error: {exc}", file=sys.stderr)
            return 0 if args.quiet else 2
        if args.json:
            print(json.dumps(_result_json(result)))
        elif not args.quiet:
            if result.available and result.latest:
                print(f"Update available: {result.installed or '(none)'} -> {result.latest.version}\n{result.latest.url}")
            else:
                print(f"LintabOS core is up to date ({result.installed}).")
        if args.notify and result.available and result.latest:
            notify(result.latest)
        return 0

    # apply
    try:
        result = check(args.channel)
        if not (result.available and result.latest):
            print(f"Already up to date ({result.installed}).")
            return 0
        if not args.yes:
            answer = input(f"Install LintabOS {result.latest.version} (currently {result.installed})? [y/N] ")
            if answer.strip().lower() != "y":
                return 1
        apply(result.latest)
    except UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
