# SPDX-License-Identifier: MIT
"""Tests for the "tablet doesn't wake up" fix: preferring deep sleep, detecting and watching the power button and lid
switch, and the configured action being dispatched without ever being able to hang or crash the guard loop."""

import os
import struct
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import sleepmode  # noqa: E402


# ------------------------------------------------------------------------- sleep mode --

@pytest.mark.parametrize("text,current,available", [
    ("[s2idle]", "s2idle", ["s2idle"]),
    ("[s2idle] deep", "s2idle", ["s2idle", "deep"]),
    ("s2idle [deep]", "deep", ["s2idle", "deep"]),
    ("  [s2idle]  deep  \n", "s2idle", ["s2idle", "deep"]),
])
def test_mem_sleep_is_parsed_from_the_kernels_bracketed_format(text, current, available):
    status = sleepmode.parse_mem_sleep(text)
    assert (status.current, status.available) == (current, available)


def test_an_empty_or_unreadable_mem_sleep_file_means_no_choice_not_a_crash(tmp_path):
    assert sleepmode.parse_mem_sleep("") is None
    assert sleepmode.read_mem_sleep(str(tmp_path / "does-not-exist")) is None


def test_deep_sleep_is_preferred_only_when_offered_and_not_already_selected(tmp_path):
    path = tmp_path / "mem_sleep"
    written = []
    path.write_text("[s2idle] deep")
    msg = sleepmode.apply_preferred_mem_sleep(str(path), write=written.append)
    assert written == ["deep"] and "Switched from s2idle to deep" in msg

    path.write_text("[deep] s2idle")
    written.clear()
    msg = sleepmode.apply_preferred_mem_sleep(str(path), write=written.append)
    assert written == [] and "already selected" in msg

    path.write_text("[s2idle]")
    written.clear()
    msg = sleepmode.apply_preferred_mem_sleep(str(path), write=written.append)
    assert written == [] and "only offers s2idle" in msg

    msg = sleepmode.apply_preferred_mem_sleep(str(tmp_path / "missing"), write=written.append)
    assert written == [] and "does not offer a choice" in msg


def test_a_failed_write_is_reported_not_raised(tmp_path):
    path = tmp_path / "mem_sleep"
    path.write_text("[s2idle] deep")

    def boom(_text):
        raise OSError("Device or resource busy")

    msg = sleepmode.apply_preferred_mem_sleep(str(path), write=boom)
    assert "Could not switch" in msg


def test_apply_main_needs_root(monkeypatch, capsys):
    monkeypatch.setattr(sleepmode.os, "geteuid", lambda: 1000)
    assert sleepmode.mem_sleep_apply_main() == 1 and "root" in capsys.readouterr().err


# ---------------------------------------------------------------------------- actions --

def test_actions_default_to_suspend_and_reject_garbage(tmp_path):
    conf = tmp_path / "sleep-mode.conf"
    assert sleepmode.read_actions(str(conf)) == {"power": "suspend", "lid": "suspend"}
    conf.write_text("power = nothing\nlid = banana\n")
    assert sleepmode.read_actions(str(conf)) == {"power": "nothing", "lid": "suspend"}


def test_actions_round_trip_and_validate(tmp_path):
    conf = tmp_path / "sleep-mode.conf"
    sleepmode.write_actions("lock", "nothing", str(conf))
    assert sleepmode.read_actions(str(conf)) == {"power": "lock", "lid": "nothing"}
    with pytest.raises(ValueError):
        sleepmode.write_actions("explode", "lock", str(conf))


def test_helper_needs_root_and_validates_its_two_arguments(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sleepmode.os, "geteuid", lambda: 1000)
    assert sleepmode.helper_main(["suspend", "lock"]) == 1
    monkeypatch.setattr(sleepmode.os, "geteuid", lambda: 0)
    assert sleepmode.helper_main(["suspend"]) == 2
    assert sleepmode.helper_main(["suspend", "explode"]) == 2
    assert "one of" in capsys.readouterr().err


def test_perform_action_dispatches_the_right_systemd_call_and_never_raises():
    calls = []
    run = lambda argv, **k: calls.append(argv)
    sleepmode.perform_action("suspend", run=run)
    assert ["systemctl", "suspend"] in calls
    calls.clear()
    sleepmode.perform_action("lock", run=run)
    assert calls == [["loginctl", "lock-sessions"]]
    calls.clear()
    sleepmode.perform_action("nothing", run=run)
    assert calls == []

    def explode(argv, **k):
        raise OSError("no systemctl")
    sleepmode.perform_action("suspend", run=explode)       # must not raise, whatever goes wrong underneath


# ------------------------------------------------------------- power button / lid detection --

PROC_DEVICES = """I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
H: Handlers=kbd event0
B: PROP=0
B: EV=3
B: KEY=10000000000000 0

I: Bus=0019 Vendor=0000 Product=0005 Version=0000
N: Name="Lid Switch"
H: Handlers=event1
B: EV=21
B: SW=1

I: Bus=0003 Vendor=17ef Product=6099 Version=0111
N: Name="Lenovo Duet 3 Keyboard"
H: Handlers=sysrq kbd event5 leds
B: EV=120013
B: KEY=1000000000007 ff800000000007ff feaeffdfffefffff fffffffffffffffe

I: Bus=0018 Vendor=04f3 Product=2c01 Version=0100
N: Name="ELAN Touchscreen"
H: Handlers=mouse2 event4
B: EV=b
B: KEY=400 0 0 0 0 0
"""


def test_the_power_button_bit_is_read_the_way_the_kernel_prints_it():
    assert sleepmode.has_bit("10000000000000 0", 116)     # bit 52 of word 1 (KEY_POWER)
    assert not sleepmode.has_bit("10000000000000 0", 115)
    assert not sleepmode.has_bit("", 116) and not sleepmode.has_bit("zz", 0)
    assert not sleepmode.has_bit("ff", 116)                # bitmap too short to reach it


def test_only_the_real_power_button_and_lid_switch_devices_are_found():
    assert sleepmode.power_button_devices(PROC_DEVICES) == ["/dev/input/event0"]
    assert sleepmode.lid_switch_devices(PROC_DEVICES) == ["/dev/input/event1"]


# ----------------------------------------------------------------------- event decode --

def event(type_, code, value):
    return struct.pack("llHHi", 0, 0, type_, code, value)


def test_only_a_power_press_or_a_lid_close_are_classified():
    assert sleepmode.classify_event(sleepmode.EV_KEY, sleepmode.KEY_POWER, 1) == "power"
    assert sleepmode.classify_event(sleepmode.EV_KEY, sleepmode.KEY_POWER, 0) is None   # release
    assert sleepmode.classify_event(sleepmode.EV_KEY, sleepmode.KEY_POWER, 2) is None   # auto-repeat
    assert sleepmode.classify_event(sleepmode.EV_SW, sleepmode.SW_LID, 1) == "lid-close"
    assert sleepmode.classify_event(sleepmode.EV_SW, sleepmode.SW_LID, 0) is None       # lid OPEN: not an action trigger
    assert sleepmode.classify_event(sleepmode.EV_KEY, 30, 1) is None                    # some other key
    assert sleepmode.classify_event_batch(event(0, 0, 0) + event(sleepmode.EV_KEY, sleepmode.KEY_POWER, 1)) == "power"
    assert sleepmode.classify_event_batch(event(sleepmode.EV_KEY, sleepmode.KEY_POWER, 1)[:10]) is None  # partial read


# --------------------------------------------------------------------------- the loop --

def test_a_power_press_and_a_lid_close_each_trigger_their_own_configured_action(tmp_path):
    performed = []
    actions = {"power": "suspend", "lid": "lock"}
    stop = threading.Event()

    power_r, power_w = os.pipe()
    lid_r, lid_w = os.pipe()
    os.set_blocking(power_r, False)
    os.set_blocking(lid_r, False)

    def opener(path):
        return {"power-dev": power_r, "lid-dev": lid_r}[path]

    def reader(fd):
        return os.read(fd, 4096)

    thread = threading.Thread(target=sleepmode.guard_loop, args=(["power-dev"], ["lid-dev"], lambda: actions,
                                                                  performed.append, stop.is_set, opener, reader))
    thread.start()
    os.write(power_w, event(sleepmode.EV_KEY, sleepmode.KEY_POWER, 0))     # a release first: must be ignored
    os.write(lid_w, event(sleepmode.EV_SW, sleepmode.SW_LID, 0))          # lid opening: must be ignored
    os.write(power_w, event(sleepmode.EV_KEY, sleepmode.KEY_POWER, 1))
    os.write(lid_w, event(sleepmode.EV_SW, sleepmode.SW_LID, 1))
    import time
    for _ in range(50):
        if len(performed) >= 2:
            break
        time.sleep(0.1)
    stop.set()
    thread.join(5)
    os.close(power_w); os.close(lid_w)
    assert sorted(performed) == ["lock", "suspend"]
    assert not thread.is_alive()


def test_the_loop_survives_a_device_it_cannot_open_and_closes_what_it_did_open():
    opened = []

    def open_or_fail(path):
        if path == "bad":
            raise OSError("no such device")
        fd = os.open("/dev/null", os.O_RDONLY)
        opened.append(fd)
        return fd

    stop_now = iter([False, True])
    sleepmode.guard_loop(["bad"], ["also-bad-but-ok"], lambda: {"power": "nothing", "lid": "nothing"}, lambda a: None,
                         stop=lambda: next(stop_now, True), opener=open_or_fail, reader=lambda fd: b"")
    assert opened                                                  # the good device really was opened
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)                                          # closed: using it now raises


def test_guard_main_does_nothing_gracefully_when_no_relevant_device_exists(monkeypatch, capsys):
    monkeypatch.setattr(sleepmode, "read_proc_devices", lambda *a, **k: "")
    assert sleepmode.guard_main() == 0
    assert "nothing to watch" in capsys.readouterr().err


# ---------------------------------------------------------------------------- the CLI --

def test_status_reports_both_sleep_mode_and_the_configured_actions(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sleepmode, "read_mem_sleep", lambda: sleepmode.MemSleep("deep", ["s2idle", "deep"]))
    monkeypatch.setattr(sleepmode, "read_actions", lambda *a, **k: {"power": "suspend", "lid": "lock"})
    assert sleepmode.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "sleep mode: deep" in out and "power button: suspend" in out and "folio close: lock" in out


def test_set_goes_through_pkexec_to_the_one_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(sleepmode.subprocess, "run", lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
    assert sleepmode.main(["set", "lock", "nothing"]) == 0
    assert calls == [["pkexec", sleepmode.SET_HELPER, "lock", "nothing"]]


# ------------------------------------------------------------------------- wiring ----

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_the_guard_service_holds_the_inhibitor_for_its_whole_lifetime_and_is_root():
    unit = open(os.path.join(ROOT, "payload/usr/lib/systemd/system/lintab-sleep-guard.service")).read()
    assert "systemd-inhibit" in unit and "--mode=block" in unit
    assert "handle-power-key" in unit and "handle-lid-switch" in unit
    assert "User=" not in unit                              # runs as root: needed for raw /dev/input access
    apply_unit = open(os.path.join(ROOT, "payload/usr/lib/systemd/system/lintab-sleep-mode-apply.service")).read()
    assert "sleep-mode-apply" in apply_unit and "Type=oneshot" in apply_unit


def test_the_privileged_helpers_and_polkit_policy_are_wired_together():
    for helper, needle in (("sleep-mode-set", "helper_main"), ("sleep-mode-apply", "mem_sleep_apply_main"),
                           ("sleep-guard-run", "guard_main")):
        path = os.path.join(ROOT, "payload/usr/libexec/lintab", helper)
        assert needle in open(path).read()
        assert subprocess.run(["python3", "-c", f"compile(open('{path}').read(), '{path}', 'exec')"]).returncode == 0
    policy = open(os.path.join(ROOT, "payload/usr/share/polkit-1/actions/org.lintabos.sleep-mode.policy")).read()
    assert "/usr/libexec/lintab/sleep-mode-set" in policy and "auth_admin" in policy
