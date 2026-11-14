# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Working out the day's azaan times and the next azaan."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pipray.cli import main
from pipray.config import Config
from pipray.schedule import next_event, prayer_times

MELBOURNE = ZoneInfo("Australia/Melbourne")


def schedule(config, day):
    return [(prayer, at.strftime("%H:%M")) for prayer, at in prayer_times(config, day)]


def test_melbourne_schedule(write_config):
    # Guards the times of the configuration in use, which were checked on the Raspberry Pi
    config = Config(write_config())
    assert schedule(config, date(2026, 11, 12)) == [
        ("fajr", "04:23"), ("dhuhr", "13:06"), ("asr", "18:03"), ("maghrib", "20:02"), ("isha", "21:43"),
    ]


def test_times_are_timezone_aware(write_config):
    config = Config(write_config())
    for _, at in prayer_times(config, date(2026, 11, 12)):
        assert at.tzinfo == MELBOURNE


def test_times_are_sorted_whatever_the_config_order(write_config):
    text = open(write_config()).read().replace(
        "prayers = Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19", "prayers = Isha, Fajr, Asr")
    config = Config(write_config(text))
    assert [p for p, _ in prayer_times(config, date(2026, 11, 12))] == ["fajr", "asr", "isha"]


def test_daylight_saving_start_moves_times_an_hour_later(write_config):
    # Melbourne clocks go forward on Sunday 4 October 2026
    config = Config(write_config())
    before = dict(prayer_times(config, date(2026, 10, 3)))
    after = dict(prayer_times(config, date(2026, 10, 4)))

    for prayer in before:
        shift = (after[prayer].hour * 60 + after[prayer].minute) - (before[prayer].hour * 60 + before[prayer].minute)
        # an hour, give or take the normal change of a couple of minutes from one day to the next
        assert 55 <= shift <= 65, prayer


def test_daylight_saving_matches_legacy_dst_flag(write_config):
    with_tz = Config(write_config())
    legacy_text = open(write_config()).read().replace(
        "timezone = Australia/Melbourne", "gmt-offset = 10\ndst = 1")
    legacy = Config(write_config(legacy_text, name="legacy.ini"))

    day = date(2026, 11, 12)
    assert schedule(with_tz, day) == schedule(legacy, day)


def test_next_prayer_later_today(write_config):
    config = Config(write_config())
    event = next_event(config, datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))
    assert (event.prayer, event.at.strftime("%Y-%m-%d %H:%M")) == ("asr", "2026-11-12 18:03")
    assert not event.reminder


def test_next_prayer_is_tomorrow_after_isha(write_config):
    config = Config(write_config())
    event = next_event(config, datetime(2026, 11, 12, 22, 0, tzinfo=MELBOURNE))
    assert (event.prayer, event.at.strftime("%Y-%m-%d %H:%M")) == ("fajr", "2026-11-13 04:22")


def test_next_prayer_skips_one_starting_now(write_config):
    config = Config(write_config())
    event = next_event(config, datetime(2026, 11, 12, 18, 3, tzinfo=MELBOURNE))
    assert event.prayer == "maghrib"


def test_times_command_plain_output(write_config, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", write_config(), "times", "-f", "plain", "-d", "2026-11-12"])
    assert main() == 0
    assert capsys.readouterr().out.split() == [
        "fajr-04:23", "dhuhr-13:06", "asr-18:03", "maghrib-20:02", "isha-21:43"]
