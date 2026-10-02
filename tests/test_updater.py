# SPDX-License-Identifier: MIT
"""The updater against a local fake of GitHub's release API, with real minisign signatures and real .deb files.

Covers the happy path (an update is found, verified, installed) and the attacks it exists to stop: a wrong
signing key, a tampered package, a swapped package name or version, a downgrade/replay, and downloads from
untrusted hosts. Needs root, dpkg/apt and minisign: run via scripts/test-updater.sh.
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import update  # noqa: E402

needs_env = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("minisign") or not shutil.which("dpkg-deb") or not shutil.which("apt-get"),
    reason="needs root, minisign and dpkg/apt",
)


def sh(*argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kw)


def make_deb(directory, version, package="lintabos-core"):
    root = os.path.join(directory, f"pkg-{package}-{version}")
    os.makedirs(os.path.join(root, "DEBIAN"))
    os.makedirs(os.path.join(root, "usr/share/lintabos-test"))
    with open(os.path.join(root, "usr/share/lintabos-test", "marker"), "w") as f:
        f.write(version)
    with open(os.path.join(root, "DEBIAN/control"), "w") as f:
        f.write(f"Package: {package}\nVersion: {version}\nArchitecture: all\nMaintainer: test <t@example.org>\n"
                f"Description: test build {version}\n")
    deb = os.path.join(directory, f"{package}_{version}_all.deb")
    sh("dpkg-deb", "--root-owner-group", "-Zgzip", "--build", root, deb)
    return deb


def sign(deb, secret):
    sh("minisign", "-S", "-s", secret, "-m", deb, "-x", deb + ".minisig")
    return deb + ".minisig"


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    out = {}
    for name in ("good", "evil"):
        pub, sec = str(d / f"{name}.pub"), str(d / f"{name}.sec")
        sh("minisign", "-G", "-W", "-f", "-p", pub, "-s", sec)
        out[name] = (pub, sec)
    return out


class FakeGitHub:
    """Serves /repos/<repo>/releases and the asset files from a directory."""

    def __init__(self, directory):
        self.dir = directory
        self.releases = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/repos/") and "/releases" in self.path:
                    body = json.dumps(outer.releases).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                path = os.path.join(outer.dir, os.path.basename(self.path))
                if os.path.isfile(path):
                    data = open(path, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, version, secret, prerelease=False, draft=False, signed=True, deb=None, name=None):
        deb = deb or make_deb(self.dir, version)
        assets = [{"name": name or os.path.basename(deb), "browser_download_url": f"{self.url}/{os.path.basename(deb)}"}]
        if signed:
            sig = sign(deb, secret)
            assets.append({"name": os.path.basename(sig), "browser_download_url": f"{self.url}/{os.path.basename(sig)}"})
        self.releases.append({"tag_name": f"v{version}", "name": f"LintabOS {version}", "body": f"notes for {version}",
                              "draft": draft, "prerelease": prerelease, "html_url": f"https://github.com/x/y/releases/tag/v{version}",
                              "assets": assets})


@pytest.fixture
def env(tmp_path, monkeypatch, keys):
    monkeypatch.setenv("LINTAB_UPDATE_TESTING", "1")
    monkeypatch.setenv("LINTAB_UPDATE_KEY", keys["good"][0])
    monkeypatch.setenv("LINTAB_UPDATE_CACHE", str(tmp_path / "cache"))
    conf = tmp_path / "update.conf"
    conf.write_text("channel = any\nauto_check = true\n")
    monkeypatch.setenv("LINTAB_UPDATE_CONF", str(conf))
    monkeypatch.setenv("LINTAB_UPDATE_ROLLBACK", str(tmp_path / "rollback"))
    monkeypatch.setattr(update, "HEALTH_COMMANDS", [])           # the fake test packages ship no LintabOS tools to check
    web = FakeGitHub(str(tmp_path))
    monkeypatch.setenv("LINTAB_UPDATE_API", web.url)
    subprocess.run(["dpkg", "--purge", "lintabos-core"], capture_output=True)
    yield web
    subprocess.run(["dpkg", "--purge", "lintabos-core"], capture_output=True)


def install_baseline(tmp_path, version="0.1.0"):
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), version) if os.makedirs(tmp_path / "base", exist_ok=True) is None else "")


# ------------------------------------------------------------------ pure logic --

def test_version_parsing_and_ordering():
    assert update.parse_version("v0.1.0") == "0.1.0"
    assert update.parse_version("1.2.3~rc1") == "1.2.3~rc1"
    for bad in ("", "latest", "v1;rm -rf /", "../1.0"):
        with pytest.raises(update.UpdateError):
            update.parse_version(bad)
    assert update.vercmp("0.1.1", "0.1.0") == 1
    assert update.vercmp("0.1.0", "0.1.0") == 0
    assert update.vercmp("0.1.0", "0.10.0") == -1          # numeric, not alphabetical
    assert update.vercmp("1.0~rc1", "1.0") == -1            # pre-release sorts first (Debian rules)


def test_only_github_hosts_are_trusted(monkeypatch):
    monkeypatch.delenv("LINTAB_UPDATE_TESTING", raising=False)
    assert update._allowed_host("https://api.github.com/repos/jgm240/LintabOS/releases")
    assert update._allowed_host("https://objects.githubusercontent.com/x")
    assert not update._allowed_host("http://api.github.com/x")                   # no plain HTTP
    assert not update._allowed_host("https://evil.example/github.com")
    assert not update._allowed_host("https://github.com.evil.example/x")
    assert not update._allowed_host("https://127.0.0.1:8000/x")                  # loopback only in tests


def test_release_selection_rules():
    def rel(tag, **kw):
        base = {"tag_name": tag, "draft": False, "prerelease": False, "assets": [
            {"name": f"lintabos-core_{tag.lstrip('v')}_all.deb", "browser_download_url": "https://github.com/a"},
            {"name": f"lintabos-core_{tag.lstrip('v')}_all.deb.minisig", "browser_download_url": "https://github.com/b"}]}
        base.update(kw)
        return base
    releases = update.parse_releases([
        rel("v0.2.0", draft=True), rel("v0.1.5", prerelease=True), rel("v0.1.1"), rel("v0.1.0"),
        {"tag_name": "v9.9.9", "draft": False, "assets": []},                   # no package attached: ignored
        {"tag_name": "nonsense", "assets": []}])
    assert [r.version for r in releases] == ["0.1.5", "0.1.1", "0.1.0"]
    assert update.pick_release(releases, "any").version == "0.1.5"
    assert update.pick_release(releases, "stable").version == "0.1.1"


# --------------------------------------------------------------- end to end ----

@needs_env
def test_update_is_found_verified_and_installed(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    env.publish("0.1.0", keys["good"][1])
    env.publish("0.1.1", keys["good"][1])

    result = update.check()
    assert result.installed == "0.1.0" and result.available and result.latest.version == "0.1.1"

    update.apply(result.latest, log=lambda *_: None)
    assert update.installed_version() == "0.1.1"
    assert open("/usr/share/lintabos-test/marker").read() == "0.1.1"
    assert update.check().available is False                                      # now up to date
    assert os.listdir(tmp_path / "cache") == []                                   # downloads cleaned up


@needs_env
def test_up_to_date_when_nothing_newer(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.1"))
    env.publish("0.1.0", keys["good"][1])
    env.publish("0.1.1", keys["good"][1])
    assert update.check().available is False


@needs_env
def test_package_signed_with_the_wrong_key_is_rejected(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    env.publish("0.1.1", keys["evil"][1])                                         # attacker's own key
    result = update.check()
    with pytest.raises(update.UpdateError, match="signature"):
        update.apply(result.latest, log=lambda *_: None)
    assert update.installed_version() == "0.1.0"


@needs_env
def test_tampered_package_is_rejected(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    deb = make_deb(str(tmp_path), "0.1.1")
    sign(deb, keys["good"][1])                                                    # genuine signature…
    with open(deb, "ab") as f:
        f.write(b"tampered")                                                      # …then the file is modified
    env.publish("0.1.1", keys["good"][1], deb=deb, signed=False)
    env.releases[-1]["assets"].append({"name": os.path.basename(deb) + ".minisig",
                                       "browser_download_url": f"{env.url}/{os.path.basename(deb)}.minisig"})
    with pytest.raises(update.UpdateError, match="signature"):
        update.apply(update.check().latest, log=lambda *_: None)
    assert update.installed_version() == "0.1.0"


@needs_env
def test_missing_signature_means_the_release_is_ignored(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    env.publish("0.1.1", keys["good"][1], signed=False)
    assert update.check().available is False


@needs_env
def test_a_different_package_under_the_right_name_is_rejected(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    imposter = make_deb(str(tmp_path / "base"), "0.1.1", package="something-else")
    renamed = os.path.join(str(tmp_path), "lintabos-core_0.1.1_all.deb")
    shutil.copy(imposter, renamed)
    env.publish("0.1.1", keys["good"][1], deb=renamed)                            # validly signed, wrong contents
    with pytest.raises(update.UpdateError, match="not 'lintabos-core'|called"):
        update.apply(update.check().latest, log=lambda *_: None)


@needs_env
def test_version_mismatch_between_release_and_package_is_rejected(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    wrong = make_deb(str(tmp_path / "base"), "0.1.2")
    renamed = os.path.join(str(tmp_path), "lintabos-core_0.1.1_all.deb")
    shutil.copy(wrong, renamed)
    env.publish("0.1.1", keys["good"][1], deb=renamed)
    with pytest.raises(update.UpdateError, match="doesn't match"):
        update.apply(update.check().latest, log=lambda *_: None)


@needs_env
def test_replay_of_an_older_signed_release_cannot_downgrade(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.2.0"))
    env.publish("0.1.0", keys["good"][1])                                         # old but genuinely signed
    release = update.pick_release(update.fetch_releases(), "any")
    with pytest.raises(update.UpdateError, match="not newer"):
        update.apply(release, log=lambda *_: None)
    assert update.installed_version() == "0.2.0"


@needs_env
def test_redirect_to_an_untrusted_host_is_refused(monkeypatch):
    monkeypatch.delenv("LINTAB_UPDATE_TESTING", raising=False)
    handler = update._CheckedRedirects()
    with pytest.raises(update.UpdateError, match="untrusted"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/lintabos-core.deb")


@needs_env
def test_apply_needs_root_and_check_does_not(env, keys, tmp_path, monkeypatch):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    env.publish("0.1.1", keys["good"][1])
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert update.check().available                                               # checking is unprivileged
    with pytest.raises(update.UpdateError, match="administrator"):
        update.apply(update.check().latest, log=lambda *_: None)


@needs_env
def test_no_network_gives_a_plain_message_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setenv("LINTAB_UPDATE_TESTING", "1")
    monkeypatch.setenv("LINTAB_UPDATE_API", "http://127.0.0.1:9")                 # nothing listens here
    monkeypatch.setenv("LINTAB_UPDATE_CONF", str(tmp_path / "none.conf"))
    with pytest.raises(update.UpdateError, match="Couldn't reach GitHub"):
        update.check()
    # the background timer variant never fails, so systemd doesn't mark the unit as broken
    assert update.main(["check", "--quiet", "--notify"]) == 0
    assert update.main(["check"]) == 2


@needs_env
def test_auto_check_can_be_turned_off(env, keys, tmp_path, monkeypatch):
    conf = tmp_path / "update.conf"
    conf.write_text("auto_check = false\n")
    called = []
    monkeypatch.setattr(update, "check", lambda *a, **k: called.append(1))
    assert update.main(["check", "--quiet", "--notify"]) == 0
    assert not called, "the background check must not contact GitHub when auto_check is false"


# ------------------------------------------------------------------- rollback --

@needs_env
def test_an_update_keeps_a_verified_copy_of_the_old_version_and_rollback_restores_it(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    env.publish("0.1.0", keys["good"][1])
    env.publish("0.1.1", keys["good"][1])
    assert update.rollback_info() is None

    update.apply(update.check().latest, log=lambda *_: None)
    assert update.installed_version() == "0.1.1"
    info = update.rollback_info()
    assert info and info["version"] == "0.1.0"

    assert update.rollback(log=lambda *_: None) == "0.1.0"
    assert update.installed_version() == "0.1.0"
    assert open("/usr/share/lintabos-test/marker").read() == "0.1.0"
    assert update.rollback_info() is None                              # the saved copy is the installed one now
    with pytest.raises(update.UpdateError, match="nothing to roll back"):
        update.rollback(log=lambda *_: None)


@needs_env
def test_a_broken_update_is_rolled_back_by_itself_and_not_announced_again(env, keys, tmp_path, monkeypatch):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    env.publish("0.1.0", keys["good"][1])
    env.publish("0.1.1", keys["good"][1])
    monkeypatch.setattr(update, "HEALTH_COMMANDS", [["sh", "-c", "grep -q 0.1.1 /usr/share/lintabos-test/marker && "
                                                    "echo 'tablet tools crashed' >&2 && exit 1 || exit 0"]])
    with pytest.raises(update.UpdateError, match="rolled back to 0.1.0") as err:
        update.apply(update.check().latest, log=lambda *_: None)
    assert "tablet tools crashed" in str(err.value)
    assert update.installed_version() == "0.1.0"
    assert update.skipped_version() == "0.1.1"                         # the background check won't nag about it


@needs_env
def test_a_failed_health_check_without_a_saved_copy_says_so(env, keys, tmp_path, monkeypatch):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.0.9"))         # no release on GitHub for the installed version
    env.publish("0.1.1", keys["good"][1])
    monkeypatch.setattr(update, "HEALTH_COMMANDS", [["false"]])
    with pytest.raises(update.UpdateError, match="no earlier version was saved"):
        update.apply(update.check().latest, log=lambda *_: None)
    assert update.installed_version() == "0.1.1"


@needs_env
def test_rollback_refuses_a_tampered_saved_copy(env, keys, tmp_path):
    os.makedirs(tmp_path / "base")
    sh("dpkg", "-i", make_deb(str(tmp_path / "base"), "0.1.0"))
    env.publish("0.1.0", keys["good"][1])
    env.publish("0.1.1", keys["good"][1])
    update.apply(update.check().latest, log=lambda *_: None)
    with open(update.rollback_info()["deb"], "ab") as f:                # someone edits the file on disk
        f.write(b"tamper")
    with pytest.raises(update.UpdateError, match="signature is NOT valid"):
        update.rollback(log=lambda *_: None)
    assert update.installed_version() == "0.1.1"


def test_health_check_reports_each_failing_command_and_a_broken_package_state(monkeypatch):
    class Done:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def fake_run(argv, **kw):
        if argv[0] == "dpkg-query":
            return Done(0, "install ok installed")
        return Done(1, "", "boom: it crashed\nmore") if argv[0] == "bad" else Done(0)
    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update.health_check([["good"], ["bad", "x"]]) == ["bad x: more"]
    monkeypatch.setattr(update.subprocess, "run",
                        lambda argv, **kw: Done(0, "deinstall ok half-configured") if argv[0] == "dpkg-query" else Done(0))
    assert "not fully installed" in update.health_check([])[0]
