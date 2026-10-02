# SPDX-License-Identifier: MIT
"""Reading mode: turns on warm light, and puts the person's own Night Light settings back when it is turned off."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "installer"))

from lintab import comfort  # noqa: E402


class FakeSettings:
    """A tiny gsettings: a dict of (schema, key) -> value string."""

    def __init__(self, **initial):
        self.values = {("org.gnome.settings-daemon.plugins.color", "night-light-enabled"): "false",
                       ("org.gnome.settings-daemon.plugins.color", "night-light-schedule-automatic"): "true",
                       ("org.gnome.settings-daemon.plugins.color", "night-light-schedule-from"): "20.0",
                       ("org.gnome.settings-daemon.plugins.color", "night-light-schedule-to"): "6.0",
                       ("org.gnome.settings-daemon.plugins.color", "night-light-temperature"): "uint32 3700",
                       ("org.gnome.desktop.a11y.magnifier", "mag-factor"): "2.0",
                       ("org.gnome.desktop.a11y.magnifier", "color-saturation"): "1.0",
                       ("org.gnome.desktop.a11y.magnifier", "mouse-tracking"): "'proportional'",
                       ("org.gnome.desktop.a11y.applications", "screen-magnifier-enabled"): "false"}

    def get(self, schema, key):
        return self.values[(schema, key)]

    def set(self, schema, key, value):
        self.values[(schema, key)] = value


def test_on_holds_a_warm_screen_all_day_and_off_restores_exactly_what_was_there(tmp_path):
    s, state = FakeSettings(), str(tmp_path / "state.json")
    before = dict(s.values)
    comfort.turn_on(False, s.get, s.set, state)
    c = "org.gnome.settings-daemon.plugins.color"
    assert s.values[(c, "night-light-enabled")] == "true" and s.values[(c, "night-light-schedule-automatic")] == "false"
    assert (s.values[(c, "night-light-schedule-from")], s.values[(c, "night-light-schedule-to")]) == ("0.0", "24.0")
    assert s.values[(c, "night-light-temperature")] == "uint32 2700"
    assert s.values[("org.gnome.desktop.a11y.magnifier", "color-saturation")] == "1.0"        # no greyscale unless asked
    assert comfort.is_on(state)
    comfort.turn_off(s.set, state)
    assert s.values == before and not comfort.is_on(state)


def test_turning_it_on_twice_keeps_the_original_settings_not_the_reading_mode_ones(tmp_path):
    s, state = FakeSettings(), str(tmp_path / "state.json")
    before = dict(s.values)
    comfort.turn_on(False, s.get, s.set, state)
    comfort.turn_on(True, s.get, s.set, state)                  # e.g. switching to greyscale while already on
    assert s.values[("org.gnome.desktop.a11y.magnifier", "color-saturation")] == "0.0"
    comfort.turn_off(s.set, state)
    assert s.values == before                                    # still the person's own values


def test_greyscale_is_the_magnifier_at_1x_with_no_saturation(tmp_path):
    s, state = FakeSettings(), str(tmp_path / "state.json")
    comfort.turn_on(True, s.get, s.set, state)
    m = "org.gnome.desktop.a11y.magnifier"
    assert (s.values[(m, "mag-factor")], s.values[(m, "color-saturation")], s.values[(m, "mouse-tracking")]) == ("1.0", "0.0", "'none'")
    assert s.values[("org.gnome.desktop.a11y.applications", "screen-magnifier-enabled")] == "true"


def test_off_without_a_saved_state_changes_nothing(tmp_path):
    changed = []
    comfort.turn_off(lambda *a: changed.append(a), str(tmp_path / "missing.json"))
    assert changed == []


def test_other_desktops_are_told_to_use_their_own_setting(monkeypatch, capsys):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert comfort.main(["on"]) == 1 and "GNOME" in capsys.readouterr().err


def test_gnome_defaults_turn_on_auto_brightness_and_an_evening_warm_up():
    text = open(os.path.join(os.path.dirname(__file__), "..", "payload/etc/dconf/db/local.d/00-lintabos")).read()
    assert "ambient-enabled=true" in text
    assert "night-light-enabled=true" in text and "night-light-schedule-automatic=false" in text
