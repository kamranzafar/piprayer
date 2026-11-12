"""The table printed by 'piprayer.py times'."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import piprayer
from conftest import MELBOURNE_CONFIG
from piprayer import Config, format_times

MELBOURNE = ZoneInfo("Australia/Melbourne")
DAY = date(2026, 11, 12)


def table(config, now=datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE), default_azaan=1, day=DAY):
    return format_times(config, day, now, default_azaan)


def test_table(write_config):
    assert table(Config(write_config())) == "\n".join([
        " Thursday 12 November 2026",
        " Australia/Melbourne (AEDT) · Jafari method · Hanafi Asr",
        "",
        " Prayer   Time   Azaan  Volume",
        " ─────────────────────────────",
        " Fajr     04:23  1      100%",
        " Dhuhr    13:06  1      100%",
        " Asr      18:03  1      100%    ◀ next",
        " Maghrib  20:02  1      100%",
        " Isha     21:43  1      100%",
    ])


def test_table_with_options(write_config):
    config = Config(write_config(MELBOURNE_CONFIG + "azaan = 5\nfajr-azaan = 3\nvolume = 80, Fajr:40\nreminder = 10\n"))
    lines = table(config, default_azaan=7).splitlines()
    assert lines[3] == " Prayer   Time   Azaan  Volume  Reminder"
    assert lines[5] == " Fajr     04:23  3      40%     04:13"
    assert lines[6] == " Dhuhr    13:06  5      80%     12:56"


def test_table_shows_default_azaan(write_config):
    lines = table(Config(write_config()), default_azaan=7).splitlines()
    assert lines[5] == " Fajr     04:23  7      100%"


def test_no_next_marker_after_isha(write_config):
    output = table(Config(write_config()), now=datetime(2026, 11, 12, 23, 0, tzinfo=MELBOURNE))
    assert "next" not in output


def test_no_next_marker_on_another_day(write_config):
    output = table(Config(write_config()), day=date(2026, 12, 25))
    assert output.startswith(" Friday 25 December 2026")
    assert "next" not in output


def test_legacy_timezone_label(write_config):
    text = MELBOURNE_CONFIG.replace("timezone = Australia/Melbourne", "gmt-offset = 10\ndst = 1")
    output = table(Config(write_config(text)))
    assert output.splitlines()[1] == " UTC+11:00 · Jafari method · Hanafi Asr"


def test_installed_azaan_is_read_from_service(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "piprayer.service").write_text(
        '[Service]\nExecStart="/usr/bin/python3" "/home/pi/piprayer/piprayer.py" -c "/x/.piprayer" run --azaan 9\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert piprayer.installed_azaan() == 9


def test_installed_azaan_defaults_without_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert piprayer.installed_azaan() == 1


def test_times_command_with_date_and_azaan(write_config, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", write_config(), "times", "-d", "2026-12-25", "-a", "4"])
    assert piprayer.main() == 0
    output = capsys.readouterr().out
    assert output.startswith(" Friday 25 December 2026")
    assert " Fajr     04:06  4      100%" in output


def test_times_command_rejects_bad_date(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["piprayer.py", "times", "-d", "25/12/2026"])
    with pytest.raises(SystemExit):
        piprayer.main()
    assert "use the format YYYY-MM-DD" in capsys.readouterr().err
