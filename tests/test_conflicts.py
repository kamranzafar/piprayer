# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sounds due at the same time, or while another plays: the azaan comes first, and a reminder doesn't come late."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pipray import player
from pipray.config import Config
from pipray.player import REMINDER_SOUND
from pipray.schedule import AZAAN, KAHF, REMINDER, SUHOOR, Event, events, next_event, resolve
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")

# 1 Ramadan 1448, when Fajr is at 05:04 in Melbourne with the test configuration
FIRST_DAY = date(2027, 2, 8)


def at(hour, minute, day=date(2026, 11, 12)):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MELBOURNE)


def played(clock):
    return [(at.strftime("%H:%M"), sound) for at, sound, _ in clock.played]


def test_same_time_plays_by_priority():
    t = at(13, 0)
    kahf, reminder, suhoor, azaan = Event(t, "dhuhr", KAHF), Event(t, "asr", REMINDER), Event(t, "fajr", SUHOOR), \
        Event(t, "isha")
    assert [event.kind for event in resolve([kahf, reminder, suhoor, azaan])] == [AZAAN, SUHOOR, KAHF]
    assert [event.kind for event in resolve([reminder, Event(t, "fajr", REMINDER)])] == [REMINDER]
    assert resolve([Event(at(13, 1), "asr", REMINDER), azaan]) == [azaan, Event(at(13, 1), "asr", REMINDER)]


def test_reminder_at_the_time_of_the_suhoor_alarm_isnt_played(write_config):
    config = Config(write_config(MELBOURNE_CONFIG + "suhoor = 10\nreminder = 10\n"))
    alarm_time = datetime(2027, 2, 8, 4, 54, tzinfo=MELBOURNE)
    assert [(event.kind, event.prayer) for event in events(config, FIRST_DAY) if event.at == alarm_time] == \
        [(SUHOOR, "fajr")]
    assert next_event(config, alarm_time - timedelta(minutes=1)).kind == SUHOOR


ONLY_EVENING = MELBOURNE_CONFIG.replace("prayers = Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19",
                                        "prayers = Maghrib:-23, Isha:19")


def test_reminder_isnt_played_late_after_an_azaan(write_config, fake_clock):
    # Maghrib at 20:02 plays until 20:47, past the reminder for Isha at 20:43
    path = write_config(ONLY_EVENING + "reminder = 60\n")
    clock = fake_clock(datetime(2026, 11, 12, 18, 30), MELBOURNE)
    clock.play_duration = timedelta(minutes=45)
    clock.run_service(path, hours=4)
    assert played(clock) == [("19:02", REMINDER_SOUND), ("20:02", "1"), ("21:43", "1")]


def test_reminder_a_little_late_still_plays(write_config, fake_clock):
    path = write_config(ONLY_EVENING + "reminder = 60\n")
    clock = fake_clock(datetime(2026, 11, 12, 18, 30), MELBOURNE)
    clock.play_duration = timedelta(minutes=41, seconds=30)  # until 20:43:30
    clock.run_service(path, hours=4)
    assert ("20:43", REMINDER_SOUND) in played(clock)


def test_reminder_isnt_tried_again_once_late(write_config, fake_clock):
    path = write_config(ONLY_EVENING + "reminder = 10\n")
    clock = fake_clock(datetime(2026, 11, 12, 21, 20), MELBOURNE)
    clock.failures = [(player.NOT_CONNECTED, "Can't connect to bluetooth")] * 20
    clock.run_service(path, hours=1 / 3)  # until 21:40, before the azaan
    # the reminder at 21:33 is tried at 21:33, 21:33:30 and 21:34, not for 5 minutes as an azaan is
    assert 20 - len(clock.failures) == 3
