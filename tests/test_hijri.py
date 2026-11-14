# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Hijri date: the Umm al-Qura calendar, hijri-adjust, the change at Maghrib, and saving the adjustment."""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipray.config import Config, ConfigError, save_option, set_hijri_adjust
from pipray.hijri import MONTH_STARTS, HijriDate, describe_adjust, hijri_now
from conftest import MELBOURNE_CONFIG
from test_status_page import page, request  # noqa: F401

MELBOURNE = ZoneInfo("Australia/Melbourne")


# ------------------------------------------------------------------ the calendar

@pytest.mark.parametrize("day, hijri", [
    (date(1979, 11, 20), "1 Muharram 1400"),
    (date(2024, 4, 10), "1 Shawwal 1445"),  # Eid al-Fitr
    (date(2024, 7, 7), "1 Muharram 1446"),
    (date(2026, 2, 18), "1 Ramadan 1447"),
    (date(2026, 5, 27), "10 Dhu al-Hijjah 1447"),  # Eid al-Adha
    (date(2026, 6, 16), "1 Muharram 1448"),
    (date(2026, 11, 12), "2 Jumada al-Thani 1448"),
    (date(2077, 11, 16), "30 Dhu al-Hijjah 1500"),
])
def test_umm_al_qura_dates(day, hijri):
    assert str(HijriDate.from_gregorian(day)) == hijri


def test_no_date_outside_the_table():
    assert HijriDate.from_gregorian(date(1979, 11, 19)) is None
    assert HijriDate.from_gregorian(date(2077, 11, 17)) is None


def test_months_have_29_or_30_days_and_years_353_to_355():
    lengths = [b - a for a, b in zip(MONTH_STARTS, MONTH_STARTS[1:])]
    assert len(lengths) == 101 * 12
    assert set(lengths) == {29, 30}
    # Umm al-Qura follows the moon, rather than a fixed pattern of leap years
    assert {sum(lengths[i:i + 12]) for i in range(0, len(lengths), 12)} == {353, 354, 355}


def test_each_day_follows_the_one_before():
    previous = HijriDate.from_gregorian(date(1979, 11, 20))
    for number in range(date(1979, 11, 21).toordinal(), date(2077, 11, 17).toordinal()):
        hijri = HijriDate.from_gregorian(date.fromordinal(number))
        if hijri.day == 1:
            assert previous.day in (29, 30)
            assert (hijri.year, hijri.month) == (previous.year + (previous.month == 12), previous.month % 12 + 1)
        else:
            assert (hijri.year, hijri.month, hijri.day) == (previous.year, previous.month, previous.day + 1)
        previous = hijri


@pytest.mark.parametrize("adjust, text", [
    (0, "Umm al-Qura calendar"),
    (1, "1 day ahead of the Umm al-Qura calendar"),
    (-2, "2 days behind the Umm al-Qura calendar"),
])
def test_describe_adjust(adjust, text):
    assert describe_adjust(adjust) == text


# ------------------------------------------------------------------ the date now

def at(hour, minute=0):
    return datetime(2026, 11, 12, hour, minute, tzinfo=MELBOURNE)


def test_the_next_date_from_maghrib(write_config):
    # Maghrib is at 20:02 with its offset of -23 minutes
    config = Config(write_config())
    assert str(hijri_now(config, at(0, 30))) == "2 Jumada al-Thani 1448"
    assert str(hijri_now(config, at(20, 1))) == "2 Jumada al-Thani 1448"
    assert str(hijri_now(config, at(20, 2))) == "Evening of 3 Jumada al-Thani 1448"
    assert str(hijri_now(config, at(23, 59))) == "Evening of 3 Jumada al-Thani 1448"


def test_maghrib_is_used_without_its_azaan(write_config):
    # Sunset, with no offset, is at 20:25
    config = Config(write_config(MELBOURNE_CONFIG.replace("Maghrib:-23, ", "")))
    assert not hijri_now(config, at(20, 24)).evening
    assert hijri_now(config, at(20, 25)).evening


@pytest.mark.parametrize("adjust, text", [
    ("-2", "30 Jumada al-Awwal 1448"),
    ("-1", "1 Jumada al-Thani 1448"),
    ("+1", "3 Jumada al-Thani 1448"),
    ("2", "4 Jumada al-Thani 1448"),
])
def test_hijri_adjust(write_config, adjust, text):
    config = Config(write_config(MELBOURNE_CONFIG + "hijri-adjust = {}\n".format(adjust)))
    hijri = hijri_now(config, at(12))
    assert str(hijri) == text
    assert hijri.adjust == int(adjust)


@pytest.mark.parametrize("value", ["3", "-3", "1.5", "one", ""])
def test_invalid_hijri_adjust(write_config, value):
    with pytest.raises(ConfigError, match="Invalid hijri-adjust"):
        Config(write_config(MELBOURNE_CONFIG + "hijri-adjust = {}\n".format(value)))


# ------------------------------------------------------------------ saving it

def test_saving_adds_the_option_at_the_end_of_default(write_config):
    path = write_config("# My settings\n[Default]\nlat = 1 ; here\n\n# MQTT, later\n")
    save_option(path, "hijri-adjust", 1)
    assert open(path).read() == "# My settings\n[Default]\nlat = 1 ; here\nhijri-adjust = 1\n\n# MQTT, later\n"


def test_saving_changes_the_line_where_it_is(write_config):
    path = write_config("[Default]\n# shift the date\nHijri-Adjust: 2\n  continued\nlat = 1\n"
                        "[Other]\nhijri-adjust = 5\n")
    save_option(path, "hijri-adjust", -1)
    assert open(path).read() == "[Default]\n# shift the date\nhijri-adjust = -1\nlat = 1\n[Other]\nhijri-adjust = 5\n"


def test_saving_to_a_file_without_a_final_newline(write_config):
    path = write_config("[Default]\nlat = 1")
    save_option(path, "hijri-adjust", 0)
    assert open(path).read() == "[Default]\nlat = 1\nhijri-adjust = 0\n"


def test_saving_keeps_who_can_read_the_file(write_config):
    path = write_config(MELBOURNE_CONFIG + "mqtt-password = secret\n")
    os.chmod(path, 0o600)
    save_option(path, "hijri-adjust", 1)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert not os.path.exists(path + ".saving")


def test_saving_needs_a_default_section(write_config):
    with pytest.raises(ConfigError, match="needs a \\[Default\\] section"):
        save_option(write_config("[Other]\nlat = 1\n"), "hijri-adjust", 1)


def test_set_hijri_adjust_checks_the_value(write_config):
    path = write_config()
    assert set_hijri_adjust(path, " +2 ") == 2
    assert Config(path).hijri_adjust == 2
    with pytest.raises(ValueError, match="Invalid hijri-adjust: 3, use a number from -2 to 2"):
        set_hijri_adjust(path, "3")
    assert Config(path).hijri_adjust == 2


# ------------------------------------------------------------------ the status page

def test_status_page_shows_the_date_with_buttons(page):  # noqa: F811
    _, _, body = request(page, "/")
    assert '<div class="hijri-date">2 Jumada al-Thani 1448</div>' in body
    assert '<div class="detail">Umm al-Qura calendar</div>' in body
    assert 'name="days" value="-1"><button aria-label="A day back"' in body
    assert 'name="days" value="1"><button aria-label="A day forward"' in body


def test_status_page_button_saves_the_adjustment(page):  # noqa: F811
    status, headers, _ = request(page, "/hijri-adjust", "POST", data=b"days=2")
    assert (status, headers["Location"]) == (303, "/")
    assert "hijri-adjust = 2\n" in open(page.config_path).read()

    _, _, body = request(page, "/")
    assert '<div class="hijri-date">4 Jumada al-Thani 1448</div>' in body
    assert "2 days ahead of the Umm al-Qura calendar" in body
    # no further forward
    assert 'value="3"><button aria-label="A day forward" title="A day forward" disabled>' in body


def test_hijri_adjust_api(page):  # noqa: F811
    status, _, body = request(page, "/api/hijri-adjust?days=-1", "POST")
    assert status == 200
    assert '"hijri_adjust": -1' in body and '"text": "1 Jumada al-Thani 1448"' in body

    status, _, body = request(page, "/api/hijri-adjust?days=9", "POST")
    assert (status, body) == (400, "Invalid hijri-adjust: 9, use a number from -2 to 2")
