# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Jumu'ah mode: Surah Al-Kahf before Jumu'ah on Fridays, stopping before the reminder or azaan for Dhuhr."""
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipray import audio, mqtt, player
from pipray.cli import main
from pipray.config import KAHF_SOUND, Config, ConfigError, set_kahf
from pipray.output import format_next, format_times, times_json
from pipray.schedule import KAHF, events, kahf_event, next_azaan, upcoming_kahf
from pipray.state import AZAAN, SUHOOR, LastAzaan, Playing, State, last_file, mute, skip_next, state_file
from pipray.web import status_html
from conftest import MELBOURNE_CONFIG, make_command
from test_audio import SPEAKER_1, bluetooth  # noqa: F401, the bluetooth fixture
from test_settings import settings  # noqa: F401, the settings fixture

MELBOURNE = ZoneInfo("Australia/Melbourne")

# Dhuhr in Melbourne with the test configuration's offset: Friday 13 November 2026 at 13:06
FRIDAY = date(2026, 11, 13)
THURSDAY = date(2026, 11, 12)

ONLY_DHUHR = MELBOURNE_CONFIG.replace("prayers = Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19", "prayers = Dhuhr:1")


def config_with(write_config, *lines):
    return Config(write_config(MELBOURNE_CONFIG + "".join(line + "\n" for line in lines)))


def at(day, hour, minute):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MELBOURNE)


def played(clock):
    return [(at.strftime("%a %H:%M"), sound, volume) for at, sound, volume in clock.played]


# ------------------------------------------------------------------ settings

def test_off_unless_set(write_config):
    config = config_with(write_config)
    assert (config.kahf, config.kahf_file) == (0, KAHF_SOUND)
    assert kahf_event(config, FRIDAY) is None
    assert os.path.isfile(KAHF_SOUND)


@pytest.mark.parametrize("line, message", [
    ("kahf = 90", "Invalid kahf: 90, use off or 1h or 2h or 3h"),
    ("kahf = 4h", "Invalid kahf: 4h"),
    ("kahf = an hour", "Invalid kahf: an hour"),
])
def test_invalid_settings(write_config, line, message):
    with pytest.raises(ConfigError, match=message):
        config_with(write_config, line)


def test_own_recording_from_the_folder_of_the_configuration(write_config, tmp_path):
    (tmp_path / "kahf.mp3").write_bytes(b"")
    config = config_with(write_config, "kahf = 1h", "kahf-file = kahf.mp3")
    assert config.kahf_file == str(tmp_path / "kahf.mp3")
    assert config.warnings == []


def test_missing_own_recording_plays_the_one_piprayer_comes_with(write_config):
    config = config_with(write_config, "kahf = 1h", "kahf-file = /nowhere/kahf.mp3")
    assert config.kahf_file == KAHF_SOUND
    assert config.warnings == ["kahf-file /nowhere/kahf.mp3 isn't found, playing the recording of Surah Al-Kahf "
                               "PiPrayer comes with"]


@pytest.mark.parametrize("value, minutes", [("1h", 60), ("2H", 120), (" 3h ", 180), ("off", 0), ("OFF", 0)])
def test_hours(write_config, value, minutes):
    assert config_with(write_config, "kahf = " + value).kahf == minutes


def test_warns_when_it_would_start_with_the_reminder(write_config):
    config = config_with(write_config, "kahf = 1h", "reminder = 60")
    assert config.warnings == ["kahf = 1h starts Surah Al-Kahf with the reminder for Dhuhr, 60 minutes before it, "
                               "so it doesn't play. Use kahf = 2h, or a shorter reminder"]
    assert kahf_event(config, FRIDAY) is None
    assert config_with(write_config, "kahf = 1h", "reminder = 59").warnings == []


def test_no_recording_is_an_error(write_config, monkeypatch, tmp_path):
    monkeypatch.setattr("pipray.config.KAHF_SOUND", str(tmp_path / "missing.opus"))
    with pytest.raises(ConfigError, match="kahf is on, but there is no"):
        config_with(write_config, "kahf = 1h")
    config_with(write_config)  # off, so it doesn't matter


# ------------------------------------------------------------------ when it plays

def test_plays_on_fridays_before_jummah_and_stops_before_the_reminder(write_config):
    config = config_with(write_config, "kahf = 2h", "reminder = 10")
    kahf = [event for event in events(config, FRIDAY) if event.kind == KAHF]
    assert [(event.at, event.until, event.azaan_at, event.prayer) for event in kahf] == \
        [(at(FRIDAY, 11, 6), at(FRIDAY, 12, 54), at(FRIDAY, 13, 6), "dhuhr")]
    assert str(kahf[0]) == "Surah Al-Kahf at 2026-11-13 11:06 AEDT, until 12:54"
    assert not any(event.kind == KAHF for event in events(config, THURSDAY))


def test_stops_before_jummah_without_a_reminder(write_config):
    kahf = kahf_event(config_with(write_config, "kahf = 1h"), FRIDAY)
    assert (kahf.at, kahf.until) == (at(FRIDAY, 12, 6), at(FRIDAY, 13, 4))


def test_stops_before_jummah_when_no_azaan_plays_for_dhuhr(write_config):
    config = Config(write_config(MELBOURNE_CONFIG.replace("Dhuhr:1, ", "") + "kahf = 1h\n"))
    kahf = kahf_event(config, FRIDAY)
    # Jumu'ah is at the time of Dhuhr without its offset, as Dhuhr isn't in prayers
    assert (kahf.at, kahf.until) == (at(FRIDAY, 12, 5), at(FRIDAY, 13, 3))


def test_upcoming_kahf(write_config):
    config = config_with(write_config, "kahf = 2h")
    assert upcoming_kahf(config, at(THURSDAY, 9, 0)).at == at(FRIDAY, 11, 6)
    assert upcoming_kahf(config, at(FRIDAY, 12, 0)).at == at(FRIDAY, 11, 6)  # playing now
    assert upcoming_kahf(config, at(FRIDAY, 13, 30)).at == at(date(2026, 11, 20), 11, 8)  # the next Friday
    assert upcoming_kahf(config_with(write_config), at(THURSDAY, 9, 0)) is None


def test_service_plays_it_and_the_azaan_on_time(write_config, fake_clock):
    path = write_config(ONLY_DHUHR + "kahf = 2h\nreminder = 10\nvolume = 80, Dhuhr:60\n")
    clock = fake_clock(datetime(2026, 11, 13, 11, 0), MELBOURNE)
    # The recitation runs longer than the time it has, and the reminder and azaan as usual
    usual = clock.play_duration
    clock.play_duration = usual * 30
    clock.at(datetime(2026, 11, 13, 12, 0), lambda: setattr(clock, "play_duration", usual))
    clock.run_service(path, hours=3)

    assert played(clock) == [("Fri 11:06", KAHF_SOUND, "60"), ("Fri 12:56", player.REMINDER_SOUND, "60"),
                             ("Fri 13:06", "1", "60")]
    assert LastAzaan.load(last_file(path)).prayer == "dhuhr"  # Al-Kahf isn't recorded


def test_service_gives_the_player_its_time(write_config, fake_clock, monkeypatch):
    path = write_config(MELBOURNE_CONFIG + "kahf = 2h\nreminder = 10\n")
    clock = fake_clock(datetime(2026, 11, 13, 11, 0), MELBOURNE)
    calls = []
    fake = clock.run_player
    monkeypatch.setattr(player, "run_player",
                        lambda args, on_start=None, seconds=None: calls.append((args, seconds)) or
                        fake(args, on_start, seconds))
    clock.run_service(path, hours=1)

    args, seconds = next((args, seconds) for args, seconds in calls if KAHF_SOUND in args)
    assert args[:3] == ["--for", str(seconds), "--once"]
    assert 108 * 60 - 60 <= seconds <= 108 * 60  # from 11:06 to 12:54, less the seconds spent connecting


def test_muted_but_not_skipped_with_dhuhr(write_config, fake_clock):
    path = write_config(ONLY_DHUHR + "kahf = 2h\n")
    config = Config(path)
    clock = fake_clock(datetime(2026, 11, 13, 11, 0), MELBOURNE)
    assert skip_next(config, state_file(path), at(FRIDAY, 11, 0)).prayer == "dhuhr"
    clock.run_service(path, hours=3)
    assert played(clock) == [("Fri 11:06", KAHF_SOUND, "100")]

    # muted from Friday morning to the end of the next Friday
    next_friday = date(2026, 11, 20)
    mute(config, state_file(path), at(next_friday, 9, 0), next_friday)
    clock.played.clear()
    clock.now = at(next_friday, 11, 0)
    clock.run_service(path, hours=3)
    assert played(clock) == []


def test_not_played_when_its_time_is_up(monkeypatch):
    monkeypatch.setattr(player, "run_player", lambda *args, **kwargs: pytest.fail("played"))
    monkeypatch.setattr("pipray.clock.now", lambda tz: at(FRIDAY, 12, 55))
    result = player.play(KAHF_SOUND, until=at(FRIDAY, 12, 54))
    assert (result.ok, result.time_up) == (True, True)


def test_playing_it_from_the_command_line(write_config, monkeypatch):
    commands = []
    monkeypatch.setattr(player, "run_player", lambda args, on_start=None, seconds=None:
                        commands.append(args) or (0, []))
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", write_config(MELBOURNE_CONFIG + "volume = 80, Dhuhr:60\n"),
                                     "play", "--kahf"])
    assert main() == 0
    assert commands == [["--once", KAHF_SOUND, "60"]]


# ------------------------------------------------------------------ the player

def test_player_stops_when_its_time_is_up(bluetooth):
    bluetooth.paplay_blocks()
    result = bluetooth.play("--for", "1", "3")
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip().splitlines()[-2:] == [audio.TIME_UP, "Played on speaker " + SPEAKER_1]
    assert bluetooth.log()[-1] == "disconnect " + SPEAKER_1


def test_player_fades_out_before_its_time_is_up(bluetooth, monkeypatch):
    monkeypatch.setattr(audio, "FADE_OUT_SECONDS", 1)
    bluetooth.fading(steps=1000)  # for pactl, which lists paplay's stream and records volume changes
    make_command(bluetooth.bin_dir, "paplay", """\
        if [ "$1" = --raw ]; then cat > /dev/null; exit 0; fi
        echo "play $*" >> {state}/log
        echo $$ > {state}/paplay
        while true; do /bin/sleep 0.05; done
        """.format(state=bluetooth.state))
    result = bluetooth.play("--for", "4", "3", "50")
    assert result.returncode == 0, result.stdout
    volumes = [int(line.split()[2]) for line in bluetooth.log() if line.startswith("volume 7 ")]
    assert volumes and volumes == sorted(volumes, reverse=True) and volumes[-1] == 0
    assert all(volume < 32768 for volume in volumes)


def test_player_doesnt_start_over_with_once(bluetooth):
    from test_audio import drops_once
    drops_once(bluetooth)
    result = bluetooth.play("--once", "3")
    assert result.returncode == 3
    assert "playing from the start" not in result.stdout


@pytest.mark.parametrize("seconds", ["0", "soon", ""])
def test_player_needs_a_time(bluetooth, seconds):
    result = bluetooth.play("--for", seconds, "3")
    assert result.returncode == 1
    assert "Invalid --for" in result.stdout


# ------------------------------------------------------------------ shown

def test_fridays_show_jummah_with_al_kahf(write_config):
    config = config_with(write_config, "kahf = 2h", "reminder = 10")
    now = at(FRIDAY, 9, 0)
    table = format_times(config, FRIDAY, now)
    assert "Jumu'ah  13:06" in table and "Surah Al-Kahf 11:06-12:54" in table
    assert "Dhuhr" not in table
    assert "Dhuhr  " in format_times(config, THURSDAY, at(THURSDAY, 9, 0))

    entry = next_azaan(config, at(FRIDAY, 12, 0))
    assert format_next(config, entry, at(FRIDAY, 12, 0)).splitlines()[:2] == [
        " Next azaan: Jumu'ah at 13:06 today, in 1 h 6 min",
        " Azaan 1 · 100% volume · reminder at 12:56 · Surah Al-Kahf 11:06-12:54"]

    dhuhr = times_json(config, FRIDAY, now)["prayers"][1]
    assert (dhuhr["prayer"], dhuhr["jummah"]) == ("dhuhr", True)
    assert dhuhr["kahf"] == {"start": "2026-11-13T11:06:00+11:00", "until": "2026-11-13T12:54:00+11:00"}


def test_status_page(write_config):
    config = config_with(write_config, "kahf = 2h")
    page = status_html(config, at(FRIDAY, 12, 0), State(),
                       playing=Playing("dhuhr", KAHF, at(FRIDAY, 11, 6), 4242))
    assert '<span class="hero-prayer">Jumu&#x27;ah</span>' in page
    assert "Surah Al-Kahf 11:06 to 13:04" in page
    assert "Playing Surah Al-Kahf" in page and "Stop Surah Al-Kahf" in page


def test_playing_file_from_before_kinds(tmp_path):
    path = str(tmp_path / "playing")
    with open(path, "w") as f:
        json.dump({"prayer": "fajr", "reminder": False, "suhoor": True, "time": "2027-02-08T04:34:00+11:00"}, f)
    assert Playing.load(path).kind == SUHOOR
    with open(path, "w") as f:
        json.dump({"prayer": "asr", "reminder": False, "time": "2027-02-08T18:03:00+11:00"}, f)
    assert Playing.load(path).kind == AZAAN


# ------------------------------------------------------------------ Home Assistant and the settings page

def test_mqtt_state_and_command(write_config, at_time):
    path = write_config(MELBOURNE_CONFIG + "kahf = 2h\n")
    at_time(at(THURSDAY, 9, 0))
    data = json.loads(mqtt.state_payload(Config(path), at(THURSDAY, 9, 0), State()))
    assert (data["kahf"], data["kahf_until"], data["kahf_hours"]) == \
        ("2026-11-13T11:06:00+11:00", "2026-11-13T13:04:00+11:00", "2h")

    assert mqtt.handle_command("kahf 1h", path, 1) == "MQTT: Surah Al-Kahf 1h before Jumu'ah"
    assert (Config(path).kahf, "kahf = 1h\n" in open(path).read()) == (60, True)
    assert mqtt.handle_command("kahf off", path, 1) == "MQTT: Surah Al-Kahf turned off"
    assert mqtt.handle_command("kahf 90", path, 1).startswith("Ignoring MQTT command 'kahf 90': Invalid kahf")
    assert Config(path).kahf == 0


def test_set_kahf_checks_the_configuration(write_config, monkeypatch, tmp_path):
    path = write_config()
    monkeypatch.setattr("pipray.config.KAHF_SOUND", str(tmp_path / "missing.opus"))
    with pytest.raises(ValueError, match="kahf is on, but there is no"):
        set_kahf(path, "1h")
    assert "kahf" not in open(path).read()


def test_settings_page_saves_the_jummah_section(settings):
    s = settings()
    s.login()
    assert s.post("jummah", kahf="1h", kahf_file="").location.startswith("/settings?saved=jummah")
    assert "kahf = 1h\n" in s.text()
    page = s.post("jummah", kahf="1h", kahf_file="/nowhere.mp3").body
    assert "There is no file /nowhere.mp3 on the Raspberry Pi" in page
    assert "kahf-file" not in s.text()
    response = s.get("/media/kahf.opus")
    assert (response.status, response.content_type) == (200, "audio/ogg")
