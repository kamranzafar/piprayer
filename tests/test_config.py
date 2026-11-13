"""Reading and validating the .piprayer configuration."""
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pipray.cli import main
from pipray.config import Config, ConfigError, parse_prayers


def test_parse_prayers_with_offsets():
    prayers, offsets = parse_prayers("Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19")
    assert prayers == ["fajr", "dhuhr", "asr", "maghrib", "isha"]
    assert offsets == {"fajr": -11, "dhuhr": 1, "asr": 0, "maghrib": -23, "isha": 19}


def test_parse_prayers_offsets_are_optional():
    prayers, offsets = parse_prayers("Fajr, Dhuhr:5, Asr")
    assert prayers == ["fajr", "dhuhr", "asr"]
    assert offsets == {"fajr": 0, "dhuhr": 5, "asr": 0}


def test_parse_prayers_ignores_spaces_case_and_trailing_comma():
    prayers, offsets = parse_prayers("  fAJr : -5 ,ISHA,")
    assert prayers == ["fajr", "isha"]
    assert offsets == {"fajr": -5, "isha": 0}


def test_parse_prayers_unknown_prayer():
    with pytest.raises(ConfigError, match="Unknown prayer: Fajir"):
        parse_prayers("Fajir:0")


def test_parse_prayers_invalid_offset():
    with pytest.raises(ConfigError, match="Invalid offset for Fajr: abc"):
        parse_prayers("Fajr:abc")


def test_config_with_timezone(write_config):
    config = Config(write_config())
    assert config.lat == -37.909499
    assert config.lng == 144.751876
    assert config.method == "Jafari"
    assert config.asr_time == "Hanafi"
    assert config.tz == ZoneInfo("Australia/Melbourne")


def test_config_legacy_gmt_offset_and_dst(write_config):
    text = piprayer_config(timezone_line="gmt-offset = 10\ndst = 1")
    config = Config(write_config(text))
    assert config.tz == timezone(timedelta(hours=11))


def test_config_legacy_fractional_offset(write_config):
    text = piprayer_config(timezone_line="gmt-offset = 5.5\ndst = 0")
    assert Config(write_config(text)).tz == timezone(timedelta(hours=5, minutes=30))


def test_config_unknown_timezone(write_config):
    with pytest.raises(ConfigError, match="Unknown timezone: Mars/Base"):
        Config(write_config(piprayer_config(timezone_line="timezone = Mars/Base")))


def test_config_missing_timezone(write_config):
    with pytest.raises(ConfigError, match="Missing configuration option: timezone"):
        Config(write_config(piprayer_config(timezone_line="")))


def test_config_missing_option(write_config):
    text = piprayer_config().replace("lat = -37.909499\n", "")
    with pytest.raises(ConfigError, match="Missing configuration option: lat"):
        Config(write_config(text))


def test_config_invalid_number(write_config):
    text = piprayer_config().replace("lat = -37.909499", "lat = north")
    with pytest.raises(ConfigError, match="Invalid configuration value"):
        Config(write_config(text))


def test_config_missing_section(write_config):
    with pytest.raises(ConfigError, match="Prayer configuration is missing"):
        Config(write_config("[Other]\nlat = 1\n"))


def test_config_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="File not found"):
        Config(str(tmp_path / "missing.ini"))


def test_main_reports_config_errors(write_config, monkeypatch, capsys):
    path = write_config(piprayer_config().replace("Fajr,", "Fajir,"))
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", path, "times"])
    assert main() == 1
    assert "Unknown prayer: Fajir" in capsys.readouterr().err


def piprayer_config(timezone_line="timezone = Australia/Melbourne"):
    return """\
[Default]
lat = -37.909499
lng = 144.751876
{}
method = MWL
asr-time = Standard
prayers = Fajr, Dhuhr, Asr, Maghrib, Isha
""".format(timezone_line)
