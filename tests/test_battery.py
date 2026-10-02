# SPDX-License-Identifier: MIT
"""Battery health maths and the charge limit, against a fake /sys/class/power_supply."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import battery  # noqa: E402


def make_battery(root, name="BAT0", **files):
    path = root / name
    path.mkdir(parents=True)
    defaults = {"type": "Battery", "status": "Discharging", "capacity": "73"}
    for key, value in {**defaults, **files}.items():
        (path / key).write_text(f"{value}\n")
    return path


def test_health_comes_from_full_versus_design_capacity(tmp_path):
    make_battery(tmp_path, energy_full="34000000", energy_full_design="40000000", cycle_count="212")
    make_battery(tmp_path, "AC", type="Mains")                                    # not a battery: ignored
    [b] = battery.read_batteries(str(tmp_path))
    assert (b.name, b.capacity, b.status, b.cycles) == ("BAT0", 73, "Discharging", 212)
    assert b.health == 85.0 and not b.limit_supported


def test_charge_based_batteries_work_too_and_missing_data_is_unknown(tmp_path):
    make_battery(tmp_path, charge_full="3000", charge_full_design="4000")
    assert battery.read_batteries(str(tmp_path))[0].health == 75.0
    other = tmp_path / "x"
    other.mkdir()
    make_battery(other, capacity="50")
    assert battery.read_batteries(str(other))[0].health is None
    assert "unknown" in battery.describe_health(None)


@pytest.mark.parametrize("health,word", [(96, "like new"), (85, "good"), (70, "worn"), (40, "replacement")])
def test_health_wording(health, word):
    assert word in battery.describe_health(health)


def test_limit_values_are_validated():
    assert battery.parse_limit("80") == 80 and battery.parse_limit("OFF") is None and battery.parse_limit("100") is None
    for bad in ("49", "0", "abc", "-5", "150", "8 0"):
        with pytest.raises(ValueError):
            battery.parse_limit(bad)


def test_a_limit_is_written_only_to_batteries_that_support_one(tmp_path):
    with_limit = make_battery(tmp_path, "BAT0", energy_full="1", energy_full_design="1", charge_control_end_threshold="100")
    make_battery(tmp_path, "BAT1")                                                # no threshold file
    assert battery.apply_limit(80, str(tmp_path)) == []
    assert (with_limit / "charge_control_end_threshold").read_text() == "80"
    assert not (tmp_path / "BAT1" / "charge_control_end_threshold").exists()      # never creates the file
    assert battery.apply_limit(None, str(tmp_path)) == [] and (with_limit / "charge_control_end_threshold").read_text() == "100"


def test_a_tablet_without_support_says_so_instead_of_pretending(tmp_path):
    make_battery(tmp_path)
    assert "doesn't offer a charge limit" in battery.apply_limit(80, str(tmp_path))[0]


def test_the_limit_is_remembered_for_the_next_boot(tmp_path):
    conf = str(tmp_path / "battery.conf")
    assert battery.read_conf(conf) is None
    battery.write_conf(80, conf)
    assert battery.read_conf(conf) == 80
    battery.write_conf(None, conf)
    assert battery.read_conf(conf) is None
    open(conf, "w").write("limit = banana\n")
    assert battery.read_conf(conf) is None                                         # garbage means no limit, not a crash


def test_the_root_helper_only_accepts_its_two_commands(monkeypatch, capsys):
    monkeypatch.setattr(battery.os, "geteuid", lambda: 0)
    assert battery.helper_main(["rm", "-rf"]) == 2 and battery.helper_main(["set"]) == 2
    assert battery.helper_main(["set", "30"]) == 2 and "between 50 and 99" in capsys.readouterr().err
    monkeypatch.setattr(battery.os, "geteuid", lambda: 1000)
    assert battery.helper_main(["apply"]) == 1


def test_the_boot_service_and_policy_are_wired_to_the_helper():
    root = os.path.join(os.path.dirname(__file__), "..", "payload")
    unit = open(os.path.join(root, "usr/lib/systemd/system/lintab-battery-limit.service")).read()
    assert "ExecStart=/usr/libexec/lintab/battery-limit apply" in unit and "Type=oneshot" in unit
    policy = open(os.path.join(root, "usr/share/polkit-1/actions/org.lintabos.battery.policy")).read()
    assert "/usr/libexec/lintab/battery-limit" in policy and "auth_admin_keep" in policy
