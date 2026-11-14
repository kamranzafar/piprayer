# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The optional settings: azaan, fajr-azaan, volume and reminder."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pipray import player
from pipray.cli import main
from pipray.config import Config, ConfigError
from pipray.player import REMINDER_SOUND, play_event
from pipray.schedule import Event, next_event
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")


def config_with(write_config, *lines):
    return write_config(MELBOURNE_CONFIG + "".join(line + "\n" for line in lines))


# ----------------------------------------------------------------- parsing

def test_defaults_when_options_are_not_set(write_config):
    config = Config(write_config())
    assert config.azaan is None
    assert config.fajr_azaan is None
    assert config.volume == 100
    assert config.volumes == {}
    assert config.reminder == 0


def test_all_options(write_config):
    config = Config(config_with(write_config,
                                "azaan = 5", "fajr-azaan = 3", "volume = 80, Fajr:40, isha:60", "reminder = 10"))
    assert config.azaan == 5
    assert config.fajr_azaan == 3
    assert config.volume == 80
    assert config.volumes == {"fajr": 40, "isha": 60}
    assert config.reminder == 10


def test_volume_for_one_prayer_only_keeps_default(write_config):
    config = Config(config_with(write_config, "volume = Fajr:30"))
    assert config.volume_for("fajr") == 30
    assert config.volume_for("dhuhr") == 100


@pytest.mark.parametrize("line, message", [
    ("azaan = 18", "Invalid azaan: 18, there is no media/azaan-18.opus"),
    ("azaan = 0", "Invalid azaan: 0, use a number from 1 to 99"),
    ("azaan = five", "Invalid azaan: five, use a whole number"),
    ("fajr-azaan = 20", "Invalid fajr-azaan: 20, there is no media/azaan-20.opus"),
    ("volume = 120", "Invalid volume: 120, use a number from 0 to 100"),
    ("volume = loud", "Invalid volume: loud, use a whole number"),
    ("volume = 80, Fajir:40", "Unknown prayer: Fajir"),
    ("volume = Fajr:-5", "Invalid Fajr volume: -5, use a number from 0 to 100"),
    ("volume = Fajr", "Missing Fajr volume, e.g. Fajr:50"),
    ("reminder = 90", "Invalid reminder: 90, use a number from 0 to 60"),
    ("reminder = soon", "Invalid reminder: soon, use a whole number"),
])
def test_invalid_options(write_config, line, message):
    with pytest.raises(ConfigError, match=message):
        Config(config_with(write_config, line))


# ------------------------------------------------------- choosing what to play

def test_azaan_for_uses_default_when_not_configured(write_config):
    config = Config(write_config(), default_azaan=7)
    assert config.azaan_for("dhuhr") == 7
    assert config.azaan_for("fajr") == 7


def test_azaan_option_overrides_default(write_config):
    config = Config(config_with(write_config, "azaan = 5"), default_azaan=7)
    assert config.azaan_for("dhuhr") == 5
    assert config.azaan_for("fajr") == 5


def test_fajr_azaan_only_applies_to_fajr(write_config):
    config = Config(config_with(write_config, "azaan = 5", "fajr-azaan = 3"))
    assert config.azaan_for("fajr") == 3
    assert config.azaan_for("isha") == 5


def test_fajr_azaan_without_azaan_uses_default_for_others(write_config):
    config = Config(config_with(write_config, "fajr-azaan = 3"), default_azaan=7)
    assert config.azaan_for("fajr") == 3
    assert config.azaan_for("asr") == 7


# ------------------------------------------------------------------ reminders

def test_next_event_is_reminder_before_azaan(write_config):
    config = Config(config_with(write_config, "reminder = 10"))
    event = next_event(config, datetime(2026, 11, 12, 17, 0, tzinfo=MELBOURNE))
    assert event.reminder
    assert (event.prayer, event.at.strftime("%H:%M")) == ("asr", "17:53")


def test_next_event_after_reminder_is_azaan(write_config):
    config = Config(config_with(write_config, "reminder = 10"))
    event = next_event(config, datetime(2026, 11, 12, 17, 55, tzinfo=MELBOURNE))
    assert not event.reminder
    assert (event.prayer, event.at.strftime("%H:%M")) == ("asr", "18:03")


def test_reminder_for_tomorrows_fajr(write_config):
    config = Config(config_with(write_config, "reminder = 10"))
    event = next_event(config, datetime(2026, 11, 12, 22, 0, tzinfo=MELBOURNE))
    assert event.reminder
    assert event.at.strftime("%Y-%m-%d %H:%M") == "2026-11-13 04:12"


# --------------------------------------------------------------- the service

def test_service_plays_reminders_and_azaans_with_settings(write_config, fake_clock):
    path = config_with(write_config, "azaan = 5", "fajr-azaan = 3", "volume = 80, Fajr:40", "reminder = 10")
    clock = fake_clock(datetime(2026, 11, 12, 3, 0), MELBOURNE)
    clock.run_service(path, hours=11, azaan=7)

    reminder = REMINDER_SOUND
    assert [(at.strftime("%H:%M"), sound, volume) for at, sound, volume in clock.played] == [
        ("04:13", reminder, "40"),
        ("04:23", "3", "40"),
        ("12:56", reminder, "80"),
        ("13:06", "5", "80"),
    ]


def test_service_uses_setup_azaan_when_config_has_none(write_config, fake_clock):
    clock = fake_clock(datetime(2026, 11, 12, 3, 0), MELBOURNE)
    clock.run_service(write_config(), hours=2, azaan=7)
    assert [(sound, volume) for _, sound, volume in clock.played] == [("7", "100")]


def test_service_picks_up_new_azaan_from_config(write_config, fake_clock):
    path = write_config()
    clock = fake_clock(datetime(2026, 11, 12, 12, 0), MELBOURNE)

    def choose_azaan():
        with open(path, "a") as f:
            f.write("azaan = 9\n")
        later = clock.now.timestamp() + 10
        os.utime(path, (later, later))

    clock.at(datetime(2026, 11, 12, 14, 0), choose_azaan)
    clock.run_service(path, hours=7, azaan=1)

    assert [(at.strftime("%H:%M"), sound) for at, sound, _ in clock.played] == [("13:06", "1"), ("18:03", "9")]


def test_late_reminder_is_skipped_but_azaan_plays(write_config, fake_clock):
    path = config_with(write_config, "reminder = 10")
    clock = fake_clock(datetime(2026, 11, 12, 4, 0), MELBOURNE)

    def jump_forward():
        clock.now += timedelta(minutes=19)  # 04:20: the 04:13 reminder is 7 minutes late

    clock.at(datetime(2026, 11, 12, 4, 1), jump_forward)
    clock.run_service(path, hours=1)

    assert [(at.strftime("%H:%M"), sound) for at, sound, _ in clock.played] == [("04:23", "1")]


# ----------------------------------------------------------- play command

@pytest.fixture
def played_commands(monkeypatch):
    commands = []

    def fake_run(args, on_start=None):
        commands.append(args)
        return 0, []

    monkeypatch.setattr(player, "run_player", fake_run)
    return commands


def run_main(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["piprayer.py"] + list(args))
    return main()


@pytest.mark.parametrize("args, expected", [
    ([], ["5", "80"]),
    (["--prayer", "Fajr"], ["3", "40"]),
    (["--prayer", "isha"], ["5", "80"]),
    (["-a", "11"], ["11", "80"]),
    (["-a", "11", "-p", "fajr"], ["11", "40"]),
    (["--reminder", "-p", "fajr"], [REMINDER_SOUND, "40"]),
])
def test_play_command_uses_config(write_config, monkeypatch, played_commands, args, expected):
    path = config_with(write_config, "azaan = 5", "fajr-azaan = 3", "volume = 80, Fajr:40")
    assert run_main(monkeypatch, "-c", path, "play", *args) == 0
    assert played_commands == [expected]


def test_play_command_works_without_config(tmp_path, monkeypatch, played_commands):
    assert run_main(monkeypatch, "-c", str(tmp_path / "missing.ini"), "play") == 0
    assert played_commands == [["1", "100"]]


def test_play_command_rejects_unknown_azaan(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "play", "-a", "18")
    assert "no azaan 18" in capsys.readouterr().err


# ---------------------------------------------------------------- speaker


def test_speaker_is_optional(write_config):
    assert Config(write_config()).speaker is None


def test_speaker_address_is_upper_case(write_config):
    assert Config(config_with(write_config, "speaker = aa:bb:cc:dd:ee:0f")).speaker == "AA:BB:CC:DD:EE:0F"


@pytest.mark.parametrize("value", ["kitchen", "AA:BB:CC:DD:EE", "AA-BB-CC-DD-EE-FF", "GG:BB:CC:DD:EE:FF"])
def test_rejects_invalid_speaker(write_config, value):
    with pytest.raises(ConfigError, match="Invalid speaker"):
        Config(config_with(write_config, "speaker = " + value))


@pytest.mark.parametrize("args, expected", [
    ([], ["1", "100", "AA:BB:CC:DD:EE:FF"]),
    (["-r"], [REMINDER_SOUND, "100", "AA:BB:CC:DD:EE:FF"]),
])
def test_play_command_uses_configured_speaker(write_config, monkeypatch, played_commands, args, expected):
    path = config_with(write_config, "speaker = AA:BB:CC:DD:EE:FF")
    assert run_main(monkeypatch, "-c", path, "play", *args) == 0
    assert played_commands == [expected]


def test_service_plays_on_configured_speaker(write_config, played_commands):
    config = Config(config_with(write_config, "speaker = AA:BB:CC:DD:EE:FF", "reminder = 5"))
    at = datetime(2026, 11, 13, 5, 0, tzinfo=config.tz)
    play_event(config, Event(at, "fajr"))
    play_event(config, Event(at, "fajr", reminder=True))
    assert played_commands == [["1", "100", "AA:BB:CC:DD:EE:FF"],
                               [REMINDER_SOUND, "100", "AA:BB:CC:DD:EE:FF"]]
