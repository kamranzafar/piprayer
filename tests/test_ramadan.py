# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ramadan mode: the suhoor alarm, and the end of suhoor and iftar shown with the times."""
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipray import mqtt, player
from pipray.cli import main
from pipray.config import Config, ConfigError
from pipray.hijri import fasting
from pipray.output import format_next, format_times, times_json
from pipray.player import SUHOOR_SOUND
from pipray.schedule import IFTAR, SUHOOR, SUHOOR_ENDS, events, fast_times, next_azaan, next_fast_time
from pipray.state import LastAzaan, Playing, State, last_file, mute, skip_next, state_file
from pipray.web import status_html
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")

# 1 Ramadan 1448 is Monday 8 February 2027 by the Umm al-Qura calendar, and Eid, 1 Shawwal, Tuesday 9 March
FIRST_DAY = date(2027, 2, 8)
LAST_DAY = date(2027, 3, 8)
EID = date(2027, 3, 9)


def config_with(write_config, *lines):
    return Config(write_config(MELBOURNE_CONFIG + "".join(line + "\n" for line in lines)))


def at(day, hour, minute):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MELBOURNE)


# ------------------------------------------------------------------ settings

def test_off_unless_set(write_config):
    config = config_with(write_config)
    assert (config.ramadan, config.suhoor) == ("auto", 0)


def test_settings(write_config):
    config = config_with(write_config, "ramadan = ON", "suhoor = 30")
    assert (config.ramadan, config.suhoor) == ("on", 30)


@pytest.mark.parametrize("line, message", [
    ("ramadan = yes", "Invalid ramadan: yes, use auto or on or off"),
    ("suhoor = 181", "Invalid suhoor: 181, use a number from 0 to 180"),
    ("suhoor = half an hour", "Invalid suhoor: half an hour"),
])
def test_invalid_settings(write_config, line, message):
    with pytest.raises(ConfigError, match=message):
        config_with(write_config, line)


# ------------------------------------------------------------------ days of fasting

@pytest.mark.parametrize("day, expected", [
    (date(2027, 2, 7), False),  # 29 Shaban, though Ramadan starts at its Maghrib
    (FIRST_DAY, True),
    (LAST_DAY, True),
    (EID, False),
])
def test_days_of_ramadan(write_config, day, expected):
    assert fasting(config_with(write_config), day) == expected


def test_hijri_adjust_moves_ramadan(write_config):
    config = config_with(write_config, "hijri-adjust = -1")
    assert [fasting(config, day) for day in (FIRST_DAY, date(2027, 2, 9), EID, date(2027, 3, 10))] == \
           [False, True, True, False]


@pytest.mark.parametrize("mode, expected", [("on", [True, True]), ("off", [False, False])])
def test_turned_on_or_off_by_hand(write_config, mode, expected):
    config = config_with(write_config, "ramadan = " + mode)
    assert [fasting(config, day) for day in (date(2026, 11, 12), FIRST_DAY)] == expected


def test_times_of_the_fast(write_config):
    fast = fast_times(config_with(write_config, "suhoor = 30"), FIRST_DAY)
    assert fast.to_json() == {"suhoor_alarm": "2027-02-08T04:34:00+11:00",
                              "suhoor_ends": "2027-02-08T05:04:00+11:00", "iftar": "2027-02-08T20:22:00+11:00"}
    assert (fast.label("fajr"), fast.label("maghrib"), fast.label("asr")) == (SUHOOR_ENDS, IFTAR, None)


def test_times_of_the_fast_without_azaans_for_fajr_and_maghrib(write_config):
    config = Config(write_config(MELBOURNE_CONFIG.replace("Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19",
                                                          "Dhuhr, Asr")))
    fast = fast_times(config, FIRST_DAY)
    assert (fast.suhoor_ends.strftime("%H:%M"), fast.iftar.strftime("%H:%M")) == ("05:15", "20:45")
    assert fast.suhoor_alarm is None


def test_no_fast_outside_ramadan(write_config):
    assert fast_times(config_with(write_config, "suhoor = 30"), EID) is None


def test_next_fast_time(write_config):
    config = config_with(write_config)
    assert next_fast_time(config, at(FIRST_DAY, 0, 30)) == (SUHOOR_ENDS, at(FIRST_DAY, 5, 4))
    assert next_fast_time(config, at(FIRST_DAY, 12, 0)) == (IFTAR, at(FIRST_DAY, 20, 22))
    # after iftar, the end of tomorrow's suhoor
    assert next_fast_time(config, at(FIRST_DAY, 21, 0)) == (SUHOOR_ENDS, at(date(2027, 2, 9), 5, 6))
    # the evening before Ramadan, and none after the last iftar
    assert next_fast_time(config, at(date(2027, 2, 7), 21, 0)) == (SUHOOR_ENDS, at(FIRST_DAY, 5, 4))
    assert next_fast_time(config, at(LAST_DAY, 20, 0)) is None


# ------------------------------------------------------------------ the suhoor alarm

def test_suhoor_alarm_is_an_event_in_ramadan(write_config):
    config = config_with(write_config, "suhoor = 30")
    alarms = [event for event in events(config, FIRST_DAY) if event.suhoor]
    assert [(event.at, event.prayer, event.azaan_at) for event in alarms] == \
           [(at(FIRST_DAY, 4, 34), "fajr", at(FIRST_DAY, 5, 4))]
    assert str(alarms[0]) == "Suhoor alarm at 2027-02-08 04:34 AEDT"
    assert not any(event.suhoor for event in events(config, EID))
    assert not any(event.suhoor for event in events(config_with(write_config), FIRST_DAY))


def played(clock):
    return [(at.strftime("%a %H:%M"), sound, volume) for at, sound, volume in clock.played]


def test_service_sounds_the_suhoor_alarm(write_config, fake_clock):
    path = write_config(MELBOURNE_CONFIG + "suhoor = 30\nvolume = 80, Fajr:40\n")
    clock = fake_clock(datetime(2027, 2, 7, 21, 0), MELBOURNE)
    clock.run_service(path, hours=9)

    assert played(clock) == [("Sun 22:03", "1", "80"), ("Mon 04:34", SUHOOR_SOUND, "40"), ("Mon 05:04", "1", "40")]
    # only the azaan is recorded
    assert LastAzaan.load(last_file(path)).prayer == "fajr"


def test_suhoor_alarm_is_muted_but_not_skipped_with_fajr(write_config, fake_clock):
    path = write_config(MELBOURNE_CONFIG + "suhoor = 30\n")
    config = Config(path)
    clock = fake_clock(datetime(2027, 2, 8, 0, 30), MELBOURNE)
    assert skip_next(config, state_file(path), at(FIRST_DAY, 0, 30)).prayer == "fajr"
    clock.run_service(path, hours=5)
    assert played(clock) == [("Mon 04:34", SUHOOR_SOUND, "100")]

    # muted from now to the end of tomorrow, which has its own suhoor alarm at 04:36
    mute(config, state_file(path), clock.local_now(), date(2027, 2, 9))
    clock.played.clear()
    clock.run_service(path, hours=24)
    assert played(clock) == []


def test_playing_the_suhoor_alarm(write_config, monkeypatch):
    commands = []
    monkeypatch.setattr(player, "run_player", lambda args, on_start=None, seconds=None:
                        commands.append(args) or (0, []))
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", write_config(MELBOURNE_CONFIG + "volume = 80, Fajr:40\n"),
                                     "play", "--suhoor"])
    assert main() == 0
    assert commands == [[SUHOOR_SOUND, "40"]]
    assert os.path.isfile(SUHOOR_SOUND)


def test_stopping_the_suhoor_alarm(write_config):
    playing = Playing("fajr", SUHOOR, at(FIRST_DAY, 4, 34), 4242)
    assert playing.describe() == "the suhoor alarm"
    page = status_html(config_with(write_config, "suhoor = 30"), at(FIRST_DAY, 4, 35), State(), playing=playing)
    assert "Stop the suhoor alarm" in page


# ------------------------------------------------------------------ shown

def test_status_page_counts_down_to_iftar(write_config):
    config = config_with(write_config, "suhoor = 30")
    page = status_html(config, at(FIRST_DAY, 12, 0), State())

    assert '<h2 class="label">Ramadan</h2>' in page
    assert '<span class="hero-prayer">Iftar</span><span class="hero-time">20:22</span>' in page
    assert 'data-at="2027-02-08T20:22:00+11:00">Today, in <span class="in">8 h 22 min</span>' in page
    assert "The fast began at 05:04" in page
    assert '<span class="detail fast">Suhoor ends · alarm 04:34</span>' in page
    assert '<span class="detail fast">Iftar</span>' in page


def test_status_page_counts_down_to_the_end_of_suhoor(write_config):
    page = status_html(config_with(write_config, "suhoor = 30"), at(FIRST_DAY, 3, 0), State())
    assert '<span class="hero-prayer">Suhoor ends</span><span class="hero-time">05:04</span>' in page
    assert "Suhoor alarm at 04:34 · Iftar at 20:22" in page


def test_status_page_says_fasting_when_turned_on(write_config):
    page = status_html(config_with(write_config, "ramadan = on"), at(date(2026, 11, 12), 12, 0), State())
    assert '<h2 class="label">Fasting</h2>' in page


def test_status_page_outside_ramadan(write_config):
    page = status_html(config_with(write_config, "suhoor = 30"), at(EID, 12, 0), State())
    assert "Iftar" not in page and "Suhoor" not in page


def test_times_table(write_config):
    config = config_with(write_config, "suhoor = 30")
    table = format_times(config, FIRST_DAY, at(FIRST_DAY, 12, 0))

    lines = table.splitlines()
    assert next(line for line in lines if "Fajr" in line).endswith("suhoor ends")
    assert next(line for line in lines if "Maghrib" in line).endswith("iftar")
    assert " Ramadan: suhoor alarm 04:34 · suhoor ends 05:04 · iftar 20:22" in lines
    assert "Ramadan" not in format_times(config, EID, at(EID, 12, 0))


def test_next_azaan_says_when_iftar_is(write_config):
    config = config_with(write_config)
    now = at(FIRST_DAY, 12, 0)
    assert " Iftar at 20:22 today, in 8 h 22 min" in format_next(config, next_azaan(config, now), now).splitlines()


def test_times_json(write_config):
    config = config_with(write_config, "suhoor = 30")
    data = times_json(config, FIRST_DAY, at(FIRST_DAY, 12, 0))
    assert data["ramadan"] == {"suhoor_alarm": "2027-02-08T04:34:00+11:00",
                               "suhoor_ends": "2027-02-08T05:04:00+11:00", "iftar": "2027-02-08T20:22:00+11:00"}
    assert times_json(config, EID, at(EID, 12, 0))["ramadan"] is None


def test_home_assistant(write_config):
    config = config_with(write_config)
    data = json.loads(mqtt.state_payload(config, at(FIRST_DAY, 12, 0), State()))
    assert (data["iftar"], data["suhoor_ends"]) == ("2027-02-08T20:22:00+11:00", "2027-02-09T05:06:00+11:00")
    data = json.loads(mqtt.state_payload(config, at(LAST_DAY, 20, 0), State()))
    assert (data["iftar"], data["suhoor_ends"]) == (None, None)
