"""The table printed by 'piprayer.py times'."""
import json
from datetime import date, datetime, timedelta
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


# ----------------------------------------------------------------- times next

def run_times(monkeypatch, capsys, *args):
    monkeypatch.setattr("sys.argv", ["piprayer.py"] + list(args))
    code = piprayer.main()
    return code, capsys.readouterr()


@pytest.fixture
def at_time(monkeypatch):
    """Fix the current time seen by piprayer."""

    def set_now(now):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz)

        monkeypatch.setattr(piprayer, "datetime", FixedDatetime)

    return set_now


def test_next_later_today(write_config):
    config = Config(write_config(MELBOURNE_CONFIG + "azaan = 5\nvolume = 80\nreminder = 10\n"))
    now = datetime(2026, 11, 12, 14, 50, 30, tzinfo=MELBOURNE)
    assert piprayer.format_next(config, piprayer.next_azaan(config, now, 1), now) == "\n".join([
        " Next azaan: Asr at 18:03 today, in 3 h 13 min",
        " Azaan 5 · 80% volume · reminder at 17:53",
        " Australia/Melbourne (AEDT)",
    ])


def test_next_is_tomorrow_after_isha(write_config):
    config = Config(write_config())
    now = datetime(2026, 11, 12, 22, 0, tzinfo=MELBOURNE)
    output = piprayer.format_next(config, piprayer.next_azaan(config, now, 1), now)
    assert output.splitlines()[0] == " Next azaan: Fajr at 04:22 tomorrow, in 6 h 22 min"


def test_next_ignores_reminders(write_config):
    config = Config(write_config(MELBOURNE_CONFIG + "reminder = 10\n"))
    entry = piprayer.next_azaan(config, datetime(2026, 11, 12, 17, 55, tzinfo=MELBOURNE), 1)
    assert entry.prayer == "asr"


@pytest.mark.parametrize("minutes, expected", [
    (0.5, "1 min"), (1, "1 min"), (45, "45 min"), (60, "1 h"), (61, "1 h 1 min"), (193, "3 h 13 min"),
])
def test_format_duration(minutes, expected):
    assert piprayer.format_duration(timedelta(minutes=minutes)) == expected


def test_times_next_command(write_config, monkeypatch, capsys, at_time):
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))
    code, out = run_times(monkeypatch, capsys, "-c", write_config(), "times", "next")
    assert code == 0
    assert out.out.startswith(" Next azaan: Asr at 18:03 today, in 4 h 3 min")


def test_times_next_plain(write_config, monkeypatch, capsys, at_time):
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))
    code, out = run_times(monkeypatch, capsys, "-c", write_config(), "times", "next", "-f", "plain")
    assert out.out == "asr-18:03\n"


def test_times_next_with_date_is_an_error(write_config, monkeypatch, capsys):
    code, out = run_times(monkeypatch, capsys, "-c", write_config(), "times", "next", "-d", "2026-12-25")
    assert code == 1
    assert "--date can't be used with next" in out.err


def test_no_command_is_times(write_config, monkeypatch, capsys, at_time):
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))
    code, out = run_times(monkeypatch, capsys, "-c", write_config())
    assert code == 0
    assert out.out.startswith(" Thursday 12 November 2026")


# ------------------------------------------------------------------------- JSON

def test_times_json(write_config, monkeypatch, capsys, at_time):
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))
    path = write_config(MELBOURNE_CONFIG + "fajr-azaan = 3\nvolume = 80, Fajr:40\nreminder = 10\n")
    code, out = run_times(monkeypatch, capsys, "-c", path, "times", "-f", "json", "-a", "5")

    assert code == 0
    data = json.loads(out.out)
    assert {k: v for k, v in data.items() if k != "prayers"} == {
        "date": "2026-11-12", "timezone": "Australia/Melbourne", "method": "Jafari", "asr": "Hanafi", "next": "asr",
    }
    assert data["prayers"][0] == {
        "prayer": "fajr", "time": "2026-11-12T04:23:00+11:00", "azaan": 3, "volume": 40,
        "reminder": "2026-11-12T04:13:00+11:00",
    }
    assert [(p["prayer"], p["time"][11:16], p["azaan"], p["volume"]) for p in data["prayers"][1:]] == [
        ("dhuhr", "13:06", 5, 80), ("asr", "18:03", 5, 80), ("maghrib", "20:02", 5, 80), ("isha", "21:43", 5, 80),
    ]


def test_times_json_for_another_day_has_no_next(write_config, monkeypatch, capsys):
    code, out = run_times(monkeypatch, capsys, "-c", write_config(), "times", "-f", "json", "-d", "2026-04-05")
    data = json.loads(out.out)
    assert data["next"] is None
    assert data["prayers"][0]["time"] == "2026-04-05T05:10:00+10:00"  # after daylight saving ends


def test_times_json_legacy_timezone(write_config, monkeypatch, capsys):
    text = MELBOURNE_CONFIG.replace("timezone = Australia/Melbourne", "gmt-offset = 10\ndst = 1")
    code, out = run_times(monkeypatch, capsys, "-c", write_config(text), "times", "-f", "json", "-d", "2026-11-12")
    assert json.loads(out.out)["timezone"] == "UTC+11:00"


def test_times_next_json(write_config, monkeypatch, capsys, at_time):
    at_time(datetime(2026, 11, 12, 22, 0, tzinfo=MELBOURNE))
    code, out = run_times(monkeypatch, capsys, "-c", write_config(), "times", "next", "--format", "json")
    assert json.loads(out.out) == {
        "prayer": "fajr", "time": "2026-11-13T04:22:00+11:00", "azaan": 1, "volume": 100, "reminder": None,
        "timezone": "Australia/Melbourne", "seconds_until": 22920,
    }
