# SPDX-License-Identifier: MIT
"""The hardware report: what it masks, what it refuses to collect, and that one broken command can't sink it."""

import datetime
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import hwreport  # noqa: E402


def test_addresses_and_identifiers_are_masked():
    text = ("wlan0: 3c:e9:f7:12:ab:cd  state UP\nip 192.168.1.23 and fe80::1c2b:3dff:fe4a:5b6c\n"
            "uuid 123e4567-e89b-12d3-a456-426614174000\nSerial Number: ABC12345\niSerial 3 XYZ99\nmachine-id: 0123456789abcdef")
    out = hwreport.redact(text)
    assert "3c:e9:f7:**:**:**" in out and "12:ab:cd" not in out        # the vendor part stays (it names the chip), the rest goes
    assert "192.168.1.23" not in out and "<ip>" in out and "<ipv6>" in out
    assert "123e4567" not in out and "<uuid>" in out
    for secret in ("ABC12345", "XYZ99", "0123456789abcdef"):
        assert secret not in out
    assert "state UP" in out                                           # useful facts are kept
    assert hwreport.redact("inet 127.0.0.1") == "inet 127.0.0.1"


def test_user_and_computer_names_are_masked_without_eating_other_words():
    out = hwreport.redact("/home/ada/file ada logged in on ducky; adapter fine; ducky2", user="ada", host="ducky")
    assert "/home/<user>/file" in out and "<user> logged in on <host>" in out
    assert "adapter fine" in out and "ducky2" in out                   # part of a longer word: left alone
    assert hwreport.redact("x", user="", host="") == "x"


def test_report_has_every_section_and_survives_a_broken_command():
    calls = []

    def run(argv):
        calls.append(argv)
        if "fprintd" in " ".join(argv):
            return 127, "(command not found)"
        return 0, "Network controller [0280]: Intel AX201 [8086:a0f0]\nmac 3c:e9:f7:12:ab:cd"
    report = hwreport.generate(run, user="ada", host="ducky", now=datetime.datetime(2026, 10, 2, 12, 0))
    for title in ("System", "Wi-Fi: devices and drivers", "Wi-Fi: kernel messages", "Bluetooth", "Fingerprint reader",
                  "Accelerometer and light sensor", "Sleep and resume", "Cameras", "Keyboard and folio", "Touchscreen",
                  "Battery", "LintabOS"):
        assert f"## {title}" in report
    assert "2026-10-02 12:00" in report and "3c:e9:f7:**:**:**" in report and "12:ab:cd" not in report
    assert "(command not found)" in report                              # the failure is shown, not fatal
    assert len(calls) == len(hwreport.SECTIONS)


def test_a_huge_output_is_cut_and_an_empty_one_is_explained():
    big = hwreport.generate(lambda argv: (0, "x" * 20000))
    assert "… (cut)" in big and len(big) < len(hwreport.SECTIONS) * 7000
    assert "(nothing found)" in hwreport.generate(lambda argv: (0, ""))
    assert "(no output, exit 3)" in hwreport.generate(lambda argv: (3, ""))


def test_it_never_asks_for_secrets():
    """Static check of every command: no serial numbers, no Wi-Fi names/passwords, nothing that writes."""
    joined = " ".join(" ".join(argv) for _title, argv in hwreport.SECTIONS).lower()
    joined = joined.replace("/etc/pam.d/gdm-password", "")             # a PAM service file, only grepped for the word fprintd
    for forbidden in ("wifi list", "show-secrets", "psk", "password", "dmidecode", "product_serial", "board_serial",
                      "chassis_serial", "product_uuid", "machine-id", "sudo", "pkexec", " tee ", " rm ", "curl", "wget"):
        assert forbidden not in joined, forbidden
    assert "<name hidden>" in joined                                    # NetworkManager connection names are replaced
    assert not any(re.search(r"\s>\s*/", " ".join(argv)) for _t, argv in hwreport.SECTIONS)   # nothing redirects into files


def test_the_report_goes_to_a_file_and_nothing_else(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hwreport, "generate", lambda *a, **k: "# report\n")
    monkeypatch.setattr(hwreport.subprocess, "Popen", lambda *a, **k: pytest.fail("must not open anything on its own"))
    target = tmp_path / "r.md"
    assert hwreport.main(["--output", str(target)]) == 0
    assert target.read_text() == "# report\n"
    assert "new issue" in capsys.readouterr().out


def test_the_sleep_section_asks_for_what_diagnoses_a_failed_resume():
    command = dict(hwreport.SECTIONS)["Sleep and resume"][-1]
    for needed in ("/sys/power/mem_sleep", "swapon", "power-button-action", "logind", "journalctl -b -1 -k", "s2idle", "ufs", "tail -20"):
        assert needed in command
