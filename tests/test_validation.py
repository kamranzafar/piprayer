# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every option is checked, so a mistake in .piprayer is reported clearly instead of causing problems later, and the
service keeps playing with the previous settings until it is fixed."""
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray.config import Config, ConfigError
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")


def with_option(option, value):
    """The Melbourne configuration with an option changed, added, or removed (value None)."""
    lines = [line for line in MELBOURNE_CONFIG.splitlines() if not line.startswith(option + " =")]
    if value is not None:
        lines.append("{} = {}".format(option, value))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("option, value, message", [
    ("lat", "200", "Invalid lat: 200, use the latitude in decimal degrees from -90 to 90"),
    ("lat", "nan", "Invalid lat: nan"),
    ("lat", "", r"Invalid lat: \(empty\)"),
    ("lat", "-37,9", "Invalid lat: -37,9"),
    ("lng", "inf", "Invalid lng: inf, use the longitude in decimal degrees from -180 to 180"),
    ("lng", "-181", "Invalid lng: -181"),
    ("method", "MWLX", "Unknown method: MWLX, use one of: MWL, ISNA, .* Did you mean MWL?"),
    ("method", "", r"Unknown method: \(empty\)"),
    ("asr-time", "Hanafy", "Invalid asr-time: Hanafy, use Standard or Hanafi"),
    ("asr-time", "", r"Invalid asr-time: \(empty\)"),
    ("prayers", "", "No prayers in prayers"),
    ("prayers", "Fajr, Sunrise", "Unknown prayer: Sunrise, use Fajr, Dhuhr, Asr, Maghrib or Isha"),
    ("prayers", "Midnight", "Unknown prayer: Midnight"),
    ("prayers", "Fajr, Dhuhr, fajr:5", "Fajr is listed twice"),
    ("prayers", "Fajr:99999", "Invalid offset for Fajr: 99999, use a number from -180 to 180"),
    ("prayers", "Fajr:-181", "Invalid offset for Fajr: -181"),
    ("prayers", "Fajr:1.5", "Invalid offset for Fajr: 1.5, use a whole number"),
    ("prayers", "Fajr;Dhuhr", "Unknown prayer: Fajr;Dhuhr"),
    ("timezone", "Mars/Base", "Unknown timezone: Mars/Base, use a name from the tz database"),
    ("timezone", "", r"Unknown timezone: \(empty\)"),
    ("volume", "80, 90", "Two values without a prayer in '80, 90'"),
    ("volume", "Fajr:40, Fajr:50", "Fajr is listed twice"),
    ("volume", "Sunrise:40", "Unknown prayer: Sunrise"),
    ("volume", "80%%", "Invalid volume: 80%, use a whole number from 0 to 100"),
    ("fade-in", "5, Isha:3, isha:4", "Isha is listed twice"),
])
def test_invalid_values_are_reported(write_config, option, value, message):
    with pytest.raises(ConfigError, match=message):
        Config(write_config(with_option(option, value)))


@pytest.mark.parametrize("option, value, attribute, expected", [
    ("method", "mwl", "method", "MWL"),
    ("method", " makkah ", "method", "Makkah"),
    ("asr-time", "hanafi", "asr_time", "Hanafi"),
    ("asr-time", "STANDARD", "asr_time", "Standard"),
    ("volume", "80%", "volume", 80),
    ("lat", "90", "lat", 90.0),
])
def test_values_in_any_case_are_accepted(write_config, option, value, attribute, expected):
    assert getattr(Config(write_config(with_option(option, value))), attribute) == expected


def test_lowercase_hanafi_gives_hanafi_times(write_config):
    from pipray.schedule import prayer_times
    day = datetime(2026, 11, 12).date()
    times = [dict(prayer_times(Config(write_config(with_option("asr-time", asr), name=asr + ".ini")), day))["asr"]
             for asr in ("Hanafi", "hanafi", "Standard")]
    assert times[0] == times[1] != times[2]


def test_percent_signs_are_read_as_written(write_config):
    config = Config(write_config(MELBOURNE_CONFIG + "mqtt-host = broker\nmqtt-password = 100%sure\n"))
    assert config.mqtt.password == "100%sure"


@pytest.mark.parametrize("lines, message", [
    (["timezone = ", "gmt-offset = 99"], "Invalid gmt-offset: 99, use the hours from GMT from -12 to 14"),
    (["gmt-offset = nan"], "Invalid gmt-offset: nan"),
    (["gmt-offset = 10", "dst = yes"], "Invalid dst: yes, use 1 during daylight saving, otherwise 0"),
    (["gmt-offset = 10", "dst = 2"], "Invalid dst: 2"),
])
def test_invalid_legacy_offsets(write_config, lines, message):
    text = with_option("timezone", None) + "".join(line + "\n" for line in lines if not line.startswith("timezone"))
    with pytest.raises(ConfigError, match=message):
        Config(write_config(text))


def test_legacy_offset_with_daylight_saving(write_config):
    config = Config(write_config(with_option("timezone", None) + "gmt-offset = 5.5\ndst = 1\n"))
    assert config.tz.utcoffset(None).total_seconds() == 6.5 * 3600


@pytest.mark.parametrize("text, message", [
    (MELBOURNE_CONFIG + "lat = 1\n", r"lat is set twice in .*, on line 8. Remove one of them"),
    (MELBOURNE_CONFIG + "[Default]\nazaan = 2\n", r"\[Default\] appears twice in .*, on line 8"),
    ("lat = 1\n" + MELBOURNE_CONFIG, r"must start with \[Default\], found on line 1: lat = 1"),
    (MELBOURNE_CONFIG + "azaan 5\n", r"use option = value on each line. Check line 8: azaan 5"),
    ("[default]\nlat = 1\n", r"needs a \[Default\] section"),
    ("", r"needs a \[Default\] section"),
])
def test_unreadable_files_are_reported(write_config, text, message):
    with pytest.raises(ConfigError, match=message):
        Config(write_config(text))


def test_file_that_isnt_utf8_is_reported(tmp_path):
    path = tmp_path / "piprayer.ini"
    path.write_bytes(b"[Default]\nlat = \xff\xfe\n")
    with pytest.raises(ConfigError, match="it isn't a text file in UTF-8"):
        Config(str(path))


# ---------------------------------------------------------------- the running service

def break_config(path, clock_, text="lat = north\n"):
    """Change the configuration file, as an edit while the service runs would."""
    def edit():
        with open(path, "a") as f:
            f.write(text)
        later = clock_.now.timestamp() + 10
        os.utime(path, (later, later))
    return edit


def test_service_keeps_playing_with_the_previous_settings(write_config, fake_clock, caplog):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 12, 0), MELBOURNE)
    clock_.at(datetime(2026, 11, 12, 12, 30), break_config(path, clock_, "lat = 1\n"))  # set twice
    with caplog.at_level(logging.ERROR, logger="piprayer"):
        clock_.run_service(path, hours=7)

    assert [at.strftime("%H:%M") for at, _, _ in clock_.played] == ["13:06", "18:03"]
    assert "Can't use the new settings in {}, keeping the previous ones until it is fixed: lat is set twice" \
        .format(path) in caplog.text


def test_service_uses_the_settings_once_fixed(write_config, fake_clock):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 12, 0), MELBOURNE)
    clock_.at(datetime(2026, 11, 12, 12, 30), break_config(path, clock_, "azaan = loud\n"))

    def fix():
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("azaan = loud", "azaan = 9"))
        later = clock_.now.timestamp() + 20
        os.utime(path, (later, later))

    clock_.at(datetime(2026, 11, 12, 14, 0), fix)
    clock_.run_service(path, hours=7)
    assert [(at.strftime("%H:%M"), sound) for at, sound, _ in clock_.played] == [("13:06", "1"), ("18:03", "9")]


def test_service_waits_for_an_invalid_configuration_to_be_fixed(write_config, fake_clock, caplog):
    path = write_config(MELBOURNE_CONFIG.replace("method = Jafari", "method = Jafri"))
    clock_ = fake_clock(datetime(2026, 11, 12, 12, 0), MELBOURNE)

    def fix():
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("Jafri", "Jafari"))
        later = clock_.now.timestamp() + 10
        os.utime(path, (later, later))

    clock_.at(datetime(2026, 11, 12, 15, 0), fix)
    with caplog.at_level(logging.ERROR, logger="piprayer"):
        clock_.run_service(path, hours=7)

    assert [at.strftime("%H:%M") for at, _, _ in clock_.played] == ["18:03"]  # not 13:06, while it was invalid
    assert caplog.text.count("Can't start: Unknown method: Jafri") == 1
