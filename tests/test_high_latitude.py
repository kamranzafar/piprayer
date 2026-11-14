# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Short summer nights far from the equator: Isha after midnight, and the high-latitude rules."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipray.cli import main
from pipray.config import Config, ConfigError
from pipray.output import format_times
from pipray.schedule import next_azaan, prayer_times

LONDON = ZoneInfo("Europe/London")
LONDON_CONFIG = """\
[Default]
lat = 51.5074
lng = -0.1278
timezone = Europe/London
method = MWL
asr-time = Standard
prayers = Fajr, Dhuhr, Asr, Maghrib, Isha
fajr-azaan = 14
"""

# On Sunday 21 June 2026 in London, AlAdhan gives Maghrib 21:22, and with the middle of the night rule
# Isha and Fajr both at 01:02. Isha is after midnight, on Monday.


@pytest.fixture(autouse=True)
def no_installed_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def london(write_config, extra=""):
    return write_config(LONDON_CONFIG + extra)


def at(day, hour, minute):
    return datetime(2026, 6, day, hour, minute, tzinfo=LONDON)


def test_isha_after_midnight_is_on_the_next_day(write_config):
    times = dict(prayer_times(Config(london(write_config)), date(2026, 6, 21)))

    assert times["fajr"] == at(21, 1, 2)
    assert times["maghrib"] == at(21, 21, 22)
    assert times["isha"] == at(22, 1, 2)


def test_times_are_in_order_with_isha_last(write_config):
    prayers = [prayer for prayer, _ in prayer_times(Config(london(write_config)), date(2026, 6, 21))]
    assert prayers == ["fajr", "dhuhr", "asr", "maghrib", "isha"]


@pytest.mark.parametrize("rule, fajr, isha", [
    ("AngleBased", at(21, 2, 31), at(21, 23, 27)),
    ("OneSeventh", at(21, 3, 40), at(21, 22, 25)),
    ("anglebased", at(21, 2, 31), at(21, 23, 27)),  # any case
])
def test_high_latitude_rules(write_config, rule, fajr, isha):
    config = Config(london(write_config, "high-latitude = {}\n".format(rule)))
    times = dict(prayer_times(config, date(2026, 6, 21)))
    assert (times["fajr"], times["isha"]) == (fajr, isha)


def test_no_high_latitude_rule_leaves_out_fajr_and_isha(write_config, caplog):
    config = Config(london(write_config, "high-latitude = None\n"))
    prayers = [prayer for prayer, _ in prayer_times(config, date(2026, 6, 21))]
    assert prayers == ["dhuhr", "asr", "maghrib"]
    assert "No fajr time on 2026-06-21" in caplog.text


def test_invalid_high_latitude_rule(write_config):
    with pytest.raises(ConfigError, match="Invalid high-latitude: Midnight, use one of: NightMiddle, AngleBased"):
        Config(london(write_config, "high-latitude = Midnight\n"))


def test_next_azaan_before_midnight_is_isha_tomorrow(write_config):
    config = Config(london(write_config))
    entry = next_azaan(config, at(21, 23, 0))
    assert (entry.prayer, entry.at) == ("isha", at(22, 1, 2))


def test_after_midnight_yesterdays_isha_is_still_to_come(write_config):
    config = Config(london(write_config))
    entry = next_azaan(config, at(22, 0, 30))
    assert (entry.prayer, entry.at) == ("isha", at(22, 1, 2))


def test_table_says_isha_is_after_midnight(write_config):
    config = Config(london(write_config))
    lines = format_times(config, date(2026, 6, 21), at(21, 23, 0)).splitlines()
    assert lines[-1] == " Isha     01:02  1      100%    ◀ next · after midnight"


def test_times_next_says_tomorrow(write_config, monkeypatch, capsys, at_time):
    at_time(at(21, 23, 0))
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", london(write_config), "times", "next"])
    assert main() == 0
    assert capsys.readouterr().out.startswith(" Next azaan: Isha at 01:02 tomorrow, in 2 h 2 min")


def test_service_plays_isha_after_midnight(write_config, fake_clock):
    clock = fake_clock(datetime(2026, 6, 21, 21, 0), LONDON)
    clock.run_service(london(write_config), hours=5)

    # Fajr is due at the same minute as Isha, so it follows once Isha has played
    assert [(at.strftime("%a %H:%M"), sound) for at, sound, _ in clock.played] == [
        ("Sun 21:22", "1"), ("Mon 01:02", "1"), ("Mon 01:06", "14"),
    ]


def test_service_started_after_midnight_still_plays_isha(write_config, fake_clock):
    clock = fake_clock(datetime(2026, 6, 22, 0, 30), LONDON)
    clock.run_service(london(write_config), hours=1)
    assert [(at.strftime("%a %H:%M"), sound) for at, sound, _ in clock.played][0] == ("Mon 01:02", "1")
