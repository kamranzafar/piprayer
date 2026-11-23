# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The dua after the azaan, played straight after it with the dua option."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray import config as config_module, player
from pipray.cli import main
from pipray.config import Config, ConfigError
from pipray.output import format_next, format_times, times_json
from pipray.player import REMINDER_SOUND, SUHOOR_SOUND, play_event
from pipray.schedule import REMINDER, SUHOOR, Event, next_azaan
from pipray.state import State, skip_next, state_file
from pipray.web import status_html
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")
NOW = datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE)


@pytest.fixture(autouse=True)
def dua_file(tmp_path, monkeypatch):
    """A recording of the dua, so the tests don't depend on the one in media."""
    path = tmp_path / "dua.opus"
    path.write_bytes(b"dua")
    monkeypatch.setattr(config_module, "DUA_FILE", str(path))
    monkeypatch.setattr(player, "DUA_FILE", str(path))
    return str(path)


def config_with(write_config, *lines):
    return Config(write_config(MELBOURNE_CONFIG + "".join(line + "\n" for line in lines)))


# ------------------------------------------------------------------ settings

@pytest.mark.parametrize("value, expected", [
    (None, [False] * 5),
    ("on", [True] * 5),
    ("ON", [True] * 5),
    ("off", [False] * 5),
    ("on, Fajr:off", [False, True, True, True, True]),
    ("Maghrib:on, Isha:on", [False, False, False, True, True]),
])
def test_which_prayers(write_config, value, expected):
    config = config_with(write_config, *(["dua = " + value] if value else []))
    assert [config.dua_for(prayer) for prayer in ("fajr", "dhuhr", "asr", "maghrib", "isha")] == expected
    assert config.plays_dua == any(expected)


@pytest.mark.parametrize("value, message", [
    ("yes", "Invalid dua: yes, use on or off, e.g. on, Fajr:off"),
    ("Fajr:loud", "Invalid Fajr dua: loud, use on or off"),
    ("Fajr", "Missing Fajr dua, e.g. Fajr:on"),
    ("on, off", "Two values without a prayer"),
])
def test_invalid_settings(write_config, value, message):
    with pytest.raises(ConfigError, match=message):
        config_with(write_config, "dua = " + value)


def test_needs_a_recording_when_on(write_config, monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DUA_FILE", str(tmp_path / "missing.opus"))
    with pytest.raises(ConfigError, match="dua is on, but there is no .*missing.opus. Add a recording of the dua"):
        config_with(write_config, "dua = Fajr:on")
    assert not config_with(write_config, "dua = off").plays_dua


# ------------------------------------------------------------------ playing

@pytest.fixture
def played_commands(monkeypatch):
    commands = []
    monkeypatch.setattr(player, "run_player", lambda args, on_start=None, seconds=None:
                        commands.append(args) or (0, []))
    return commands


def test_played_after_the_azaan_only(write_config, played_commands, dua_file):
    config = config_with(write_config, "dua = on, Fajr:off", "volume = 80")
    at = datetime(2026, 11, 13, 5, 0, tzinfo=MELBOURNE)
    play_event(config, Event(at, "isha"))
    play_event(config, Event(at, "fajr"))
    play_event(config, Event(at, "isha", REMINDER))
    play_event(config, Event(at, "fajr", SUHOOR))
    assert played_commands == [["--then", dua_file, "1", "80"], ["1", "80"], [REMINDER_SOUND, "80"],
                               [SUHOOR_SOUND, "80"]]


def test_service_plays_it_after_the_azaan(write_config, fake_clock, dua_file):
    path = write_config(MELBOURNE_CONFIG + "dua = Maghrib:on\n")
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock.run_service(path, hours=6)

    assert [(at.strftime("%H:%M"), sound) for at, sound, _ in clock.played] == [("18:03", "1"), ("20:02", "1")]
    assert [(at.strftime("%H:%M"), sound) for at, sound in clock.then] == [("20:02", dua_file)]


def test_skipped_with_the_azaan(write_config, fake_clock):
    path = write_config(MELBOURNE_CONFIG + "dua = on\n")
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    skip_next(Config(path), state_file(path), clock.local_now())
    clock.run_service(path, hours=4)
    assert (clock.played, clock.then) == ([], [])


@pytest.mark.parametrize("args, expected", [
    (["--dua"], ["DUA", "80"]),
    (["-p", "maghrib"], ["--then", "DUA", "1", "80"]),
    (["-p", "fajr"], ["1", "80"]),
])
def test_play_command(write_config, monkeypatch, played_commands, dua_file, args, expected):
    path = write_config(MELBOURNE_CONFIG + "dua = Maghrib:on\nvolume = 80\n")
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", path, "play"] + args)
    assert main() == 0
    assert played_commands == [[dua_file if arg == "DUA" else arg for arg in expected]]


# ------------------------------------------------------------------ shown

def test_times_table_has_a_dua_column_when_on(write_config):
    config = config_with(write_config, "dua = Maghrib:on")
    lines = format_times(config, NOW.date(), NOW).splitlines()
    assert lines[3].split() == ["Prayer", "Time", "Azaan", "Volume", "Dua"]
    assert next(line for line in lines if "Maghrib" in line).split()[4] == "yes"
    assert next(line for line in lines if "Isha" in line).split()[4] == "-"
    assert "Dua" not in format_times(config_with(write_config), NOW.date(), NOW)


def test_next_azaan_says_the_dua_plays(write_config):
    config = config_with(write_config, "dua = on")
    assert " Azaan 1 · 100% volume · then the dua" in format_next(config, next_azaan(config, NOW), NOW).splitlines()


def test_json_and_status_page(write_config):
    config = config_with(write_config, "dua = Maghrib:on")
    data = times_json(config, NOW.date(), NOW)
    assert [prayer["dua"] for prayer in data["prayers"]] == [False, False, False, True, False]
    page = status_html(config, NOW, State())
    assert page.count("· dua</span>") == 1
