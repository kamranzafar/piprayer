# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fading the azaan in, per prayer, with the fade-in option."""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray import player
from pipray.cli import main
from pipray.config import Config, ConfigError
from pipray.output import format_times
from pipray.player import REMINDER_SOUND
from pipray.schedule import Event, schedule
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")
FAJR = datetime(2026, 11, 12, 4, 23, tzinfo=MELBOURNE)


def config_with(write_config, *lines):
    return Config(write_config(MELBOURNE_CONFIG + "".join(line + "\n" for line in lines)))


@pytest.fixture
def commands(monkeypatch):
    """The arguments play-azaan.sh is run with."""
    run = []
    monkeypatch.setattr(player, "run_player", lambda args, on_start=None: run.append(args) or (0, []))
    return run


# ---------------------------------------------------------------- the option

def test_fade_in_is_off_by_default(write_config):
    config = Config(write_config())
    assert [config.fade_in_for(p) for p in ("fajr", "isha")] == [0, 0]
    assert not config.fades_in


@pytest.mark.parametrize("value, fajr, isha", [
    ("5", 5, 5),
    ("5, Fajr:15", 15, 5),
    ("Fajr:20", 20, 0),
    ("8, Fajr:0", 0, 8),
    ("fajr:30, Isha:10", 30, 10),
])
def test_fade_in_per_prayer(write_config, value, fajr, isha):
    config = config_with(write_config, "fade-in = " + value)
    assert (config.fade_in_for("fajr"), config.fade_in_for("isha")) == (fajr, isha)
    assert config.fades_in


@pytest.mark.parametrize("value, message", [
    ("31", "Invalid fade-in: 31, use a number from 0 to 30"),
    ("Fajr:-1", "Invalid Fajr fade-in: -1, use a number from 0 to 30"),
    ("Fajr:", "Missing Fajr fade-in, e.g. Fajr:10"),
    ("slow", "Invalid fade-in: slow, use a whole number from 0 to 30"),
    ("Fajir:5", "Unknown prayer: Fajir"),
])
def test_invalid_fade_in(write_config, value, message):
    with pytest.raises(ConfigError, match=message):
        config_with(write_config, "fade-in = " + value)


# ---------------------------------------------------------------- playing

def test_azaan_fades_in_but_not_the_reminder(write_config, commands):
    config = config_with(write_config, "fade-in = 5, Fajr:15", "volume = 80, Fajr:40")
    player.play_event(config, Event(FAJR, "fajr"))
    player.play_event(config, Event(FAJR, "fajr", reminder=True))
    player.play_event(config, Event(FAJR, "isha"))
    assert commands == [["--fade-in", "15", "1", "40"], [REMINDER_SOUND, "40"], ["--fade-in", "5", "1", "80"]]


def test_no_fade_in_argument_without_the_option(write_config, commands):
    player.play_event(Config(write_config()), Event(FAJR, "fajr"))
    assert commands == [["1", "100"]]


def test_fade_in_comes_after_fallback(write_config, commands):
    config = config_with(write_config, "fade-in = 5", "fallback-output = local")
    player.play_event(config, Event(FAJR, "fajr"))
    assert commands == [["--fallback", "--fade-in", "5", "1", "100"]]


def test_play_command_uses_the_prayers_fade_in(write_config, commands, monkeypatch):
    path = write_config(MELBOURNE_CONFIG + "fade-in = Fajr:15\n")
    for args in (["play", "-p", "fajr"], ["play"], ["play", "-r", "-p", "fajr"]):
        monkeypatch.setattr(sys, "argv", ["piprayer.py", "-c", path] + args)
        assert main() == 0
    assert commands == [["--fade-in", "15", "1", "100"], ["1", "100"], [REMINDER_SOUND, "100"]]


def test_service_plays_with_fade_in(write_config, fake_clock, monkeypatch):
    path = write_config(MELBOURNE_CONFIG + "fade-in = Fajr:15\n")
    clock_ = fake_clock(datetime(2026, 11, 12, 3, 0), MELBOURNE)
    calls = []

    def run_player(args, on_start=None):
        calls.append(args)
        return clock_.run_player(args, on_start)

    monkeypatch.setattr(player, "run_player", run_player)
    clock_.run_service(path, hours=11)
    assert [args for args in calls if args[0] != "--connect"] == [
        ["--fade-in", "15", "1", "100"],  # Fajr
        ["1", "100"],  # Dhuhr
    ]


# ---------------------------------------------------------------- showing it

def test_times_shows_fade_only_when_set(write_config):
    now = datetime(2026, 11, 12, 3, 0, tzinfo=MELBOURNE)
    config = config_with(write_config, "fade-in = Fajr:15", "reminder = 10")
    lines = format_times(config, now.date(), now).splitlines()
    assert lines[3].split() == ["Prayer", "Time", "Azaan", "Volume", "Fade", "Reminder"]
    assert lines[5].split()[:6] == ["Fajr", "04:23", "1", "100%", "15", "s"]
    assert lines[6].split()[:5] == ["Dhuhr", "13:06", "1", "100%", "-"]

    assert "Fade" not in format_times(Config(write_config()), now.date(), now)


def test_json_has_each_prayers_fade_in(write_config):
    config = config_with(write_config, "fade-in = 5, Fajr:15")
    assert [entry.to_json()["fade_in"] for entry in schedule(config, FAJR.date())] == [15, 5, 5, 5, 5]
