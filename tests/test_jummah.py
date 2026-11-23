# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Jumu'ah mode: Surah Al-Kahf before Jumu'ah on Fridays, stopping before the reminder or azaan for Dhuhr."""
import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from pipray import audio, mqtt, player
from pipray.cli import main
from pipray.config import KAHF_SOUND, Config, ConfigError, set_kahf
from pipray.media import duration
from pipray.player import REMINDER_SOUND
from pipray.output import format_next, format_times, times_json
from pipray.schedule import KAHF, events, jummah_time, kahf_event, next_azaan, upcoming_jummah, upcoming_kahf
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
    assert str(kahf[0]) == "Surah Al-Kahf at 2026-11-13 11:06 AEDT"  # it finishes at 11:35, well before 12:54
    assert (kahf[0].ends, kahf[0].cut_short) == (datetime(2026, 11, 13, 11, 35, 11, tzinfo=MELBOURNE), False)
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
    assert upcoming_kahf(config, at(FRIDAY, 11, 20)).at == at(FRIDAY, 11, 6)  # playing now
    assert upcoming_kahf(config, at(FRIDAY, 12, 0)).at == at(date(2026, 11, 20), 11, 8)  # finished at 11:35
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
    assert "Jumu'ah  13:06" in table and "Surah Al-Kahf at 11:06" in table and "stopping" not in table
    assert "Dhuhr" not in table
    assert "Dhuhr  " in format_times(config, THURSDAY, at(THURSDAY, 9, 0))

    entry = next_azaan(config, at(FRIDAY, 12, 0))
    assert format_next(config, entry, at(FRIDAY, 12, 0)).splitlines()[:2] == [
        " Next azaan: Jumu'ah at 13:06 today, in 1 h 6 min",
        " Azaan 1 · 100% volume · reminder at 12:56 · Surah Al-Kahf at 11:06"]

    dhuhr = times_json(config, FRIDAY, now)["prayers"][1]
    assert (dhuhr["prayer"], dhuhr["jummah"]) == ("dhuhr", True)
    assert dhuhr["kahf"] == {"start": "2026-11-13T11:06:00+11:00", "end": "2026-11-13T11:35:11+11:00",
                             "cut_short": False}


def test_status_page(write_config):
    config = config_with(write_config, "kahf = 2h")
    page = status_html(config, at(FRIDAY, 12, 0), State(),
                       playing=Playing("dhuhr", KAHF, at(FRIDAY, 11, 6), 4242))
    assert '<span class="hero-prayer">Jumu&#x27;ah</span>' in page
    assert "Surah Al-Kahf at 11:06" in page
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
    assert (data["kahf"], data["kahf_end"], data["kahf_cut_short"], data["kahf_hours"]) == \
        ("2026-11-13T11:06:00+11:00", "2026-11-13T11:35:11+11:00", False, "2h")

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


# ------------------------------------------------------------------ the time of the khutbah

@pytest.mark.parametrize("value, expected", [("13:30", time(13, 30)), ("9:05", time(9, 5)), (" 12:00 ", time(12, 0)),
                                             ("", None)])
def test_jummah_time(write_config, value, expected):
    assert config_with(write_config, "jummah-time = " + value).jummah_time == expected


@pytest.mark.parametrize("value", ["1:30pm", "25:00", "13.30", "13:5", "noon"])
def test_invalid_jummah_time(write_config, value):
    with pytest.raises(ConfigError, match="Invalid jummah-time: {}, use the time of the khutbah on the 24-hour clock"
                       .format(value)):
        config_with(write_config, "jummah-time = " + value)


def test_jummah_is_dhuhr_without_jummah_time(write_config):
    config = config_with(write_config)
    assert (jummah_time(config, FRIDAY), jummah_time(config, THURSDAY)) == (at(FRIDAY, 13, 6), None)


def test_kahf_counts_back_from_the_khutbah_and_stops_before_the_azaan(write_config):
    config = config_with(write_config, "jummah-time = 13:30", "kahf = 2h")
    assert jummah_time(config, FRIDAY) == at(FRIDAY, 13, 30)
    kahf = kahf_event(config, FRIDAY)
    # the azaan for Dhuhr is still at 13:06, so Al-Kahf stops before it
    assert (kahf.at, kahf.until) == (at(FRIDAY, 11, 30), at(FRIDAY, 13, 4))
    assert [(event.kind, event.at) for event in events(config, FRIDAY) if event.prayer == "dhuhr"] == \
        [(KAHF, at(FRIDAY, 11, 30)), ("azaan", at(FRIDAY, 13, 6))]


def test_khutbah_before_the_calculated_dhuhr(write_config):
    kahf = kahf_event(config_with(write_config, "jummah-time = 12:30", "kahf = 1h"), FRIDAY)
    assert (kahf.at, kahf.until) == (at(FRIDAY, 11, 30), at(FRIDAY, 12, 28))


def test_khutbah_time_stays_the_same_across_daylight_saving(write_config):
    config = config_with(write_config, "jummah-time = 13:30")
    before, after = jummah_time(config, date(2026, 10, 2)), jummah_time(config, date(2026, 10, 9))  # AEST, then AEDT
    assert (before.strftime("%H:%M"), after.strftime("%H:%M")) == ("13:30", "13:30")
    assert (before.utcoffset(), after.utcoffset()) == (timedelta(hours=10), timedelta(hours=11))


def test_upcoming_jummah(write_config):
    config = config_with(write_config, "jummah-time = 13:30")
    assert upcoming_jummah(config, at(THURSDAY, 9, 0)) == at(FRIDAY, 13, 30)
    assert upcoming_jummah(config, at(FRIDAY, 13, 30)) == at(date(2026, 11, 20), 13, 30)


def test_khutbah_is_shown(write_config):
    config = config_with(write_config, "jummah-time = 13:30", "kahf = 2h")
    now = at(FRIDAY, 9, 0)
    table = format_times(config, FRIDAY, now)
    assert "Jumu'ah  13:06" in table and "khutbah 13:30" in table
    assert "khutbah at 13:30" in format_next(config, next_azaan(config, now), now)
    dhuhr = times_json(config, FRIDAY, now)["prayers"][1]
    assert (dhuhr["time"], dhuhr["khutbah"]) == ("2026-11-13T13:06:00+11:00", "2026-11-13T13:30:00+11:00")
    assert "Khutbah 13:30" in status_html(config, now, State())
    assert times_json(config_with(write_config), FRIDAY, now)["prayers"][1]["khutbah"] is None
    data = json.loads(mqtt.state_payload(config, now, State()))
    assert data["jummah"] == "2026-11-13T13:30:00+11:00"


def test_settings_page_saves_the_khutbah_time(settings):
    s = settings()
    s.login()
    assert s.post("jummah", jummah_time="13:30", kahf="off", kahf_file="").location.startswith("/settings?saved")
    assert "jummah-time = 13:30\n" in s.text()
    assert "Invalid jummah-time: 1:30pm" in s.post("jummah", jummah_time="1:30pm", kahf="off", kahf_file="").body


# ------------------------------------------------------------------ the azaan for Dhuhr on Fridays

@pytest.mark.parametrize("value, expected", [("on", None), ("ON", None), ("off", 0), ("Off", 0), ("5", 5)])
def test_jummah_azaan(write_config, value, expected):
    assert config_with(write_config, "jummah-azaan = " + value).jummah_azaan == expected


@pytest.mark.parametrize("value, message", [
    ("maybe", "Invalid jummah-azaan: maybe, use on, off or an azaan from 1 to 99"),
    ("0", "Invalid jummah-azaan: 0, use a number from 1 to 99"),
    ("98", "Invalid jummah-azaan: 98, there is no media/azaan-98.opus"),
])
def test_invalid_jummah_azaan(write_config, value, message):
    with pytest.raises(ConfigError, match=message):
        config_with(write_config, "jummah-azaan = " + value)


def test_off_leaves_out_the_azaan_and_reminder_on_fridays_only(write_config):
    config = config_with(write_config, "jummah-azaan = off", "reminder = 10")
    assert not any(event.prayer == "dhuhr" for event in events(config, FRIDAY))
    assert [event.kind for event in events(config, THURSDAY) if event.prayer == "dhuhr"] == ["reminder", "azaan"]


def test_off_leaves_al_kahf_until_the_khutbah(write_config):
    config = config_with(write_config, "jummah-azaan = off", "jummah-time = 13:30", "kahf = 1h", "reminder = 10")
    kahf = kahf_event(config, FRIDAY)
    assert (kahf.at, kahf.until) == (at(FRIDAY, 12, 30), at(FRIDAY, 13, 28))


def test_service_plays_another_azaan_on_fridays(write_config, fake_clock):
    path = write_config(ONLY_DHUHR + "jummah-azaan = 5\n")
    clock = fake_clock(datetime(2026, 11, 12, 12, 0), MELBOURNE)
    clock.run_service(path, hours=26)
    assert played(clock) == [("Thu 13:06", "1", "100"), ("Fri 13:06", "5", "100")]


def test_service_plays_nothing_for_jummah_when_off(write_config, fake_clock):
    path = write_config(ONLY_DHUHR + "jummah-azaan = off\nreminder = 10\n")
    clock = fake_clock(datetime(2026, 11, 12, 12, 0), MELBOURNE)
    clock.run_service(path, hours=26)
    assert played(clock) == [("Thu 12:56", player.REMINDER_SOUND, "100"), ("Thu 13:06", "1", "100")]


def test_jummah_without_an_azaan_is_shown(write_config):
    config = config_with(write_config, "jummah-azaan = off", "reminder = 10")
    now = at(FRIDAY, 9, 0)
    table = format_times(config, FRIDAY, now)
    assert "Jumu'ah  13:06  -      -       -         no azaan" in table
    assert next(line for line in table.splitlines() if "◀ next" in line).split()[0] == "Asr"
    assert next_azaan(config, now).prayer == "asr"
    assert skip_next(config, state_file(write_config()), now).prayer == "asr"
    dhuhr = times_json(config, FRIDAY, now)["prayers"][1]
    assert (dhuhr["azaan"], dhuhr["reminder"]) == (None, None)
    assert "No azaan" in status_html(config, now, State())


def test_jummah_with_another_azaan_is_shown(write_config):
    config = config_with(write_config, "jummah-azaan = 5")
    assert "Jumu'ah  13:06  5" in format_times(config, FRIDAY, at(FRIDAY, 9, 0))
    assert "Dhuhr    13:06  1" in format_times(config, THURSDAY, at(THURSDAY, 9, 0))


def test_settings_page_saves_the_jummah_azaan(settings):
    s = settings()
    s.login()
    form = dict(jummah_time="", kahf="off", kahf_file="")
    assert s.post("jummah", jummah_azaan="off", **form).location.startswith("/settings?saved")
    assert "jummah-azaan = off\n" in s.text()
    page = s.get().body
    assert 'href="/media/azaan-off.opus"' not in page
    s.post("jummah", jummah_azaan="5", **form)
    assert "jummah-azaan = 5\n" in s.text()
    assert 'href="/media/azaan-5.opus"' in s.get().body


# ------------------------------------------------------------------ how long it plays

def test_length_of_the_recording(tmp_path):
    assert duration(KAHF_SOUND) == 1750.5  # 29 min 10.5 s
    assert duration(REMINDER_SOUND) == 4.0
    (tmp_path / "kahf.mp3").write_bytes(b"ID3\x03\x00" + bytes(100))
    assert duration(str(tmp_path / "kahf.mp3")) is None  # can't be told without decoding it
    assert duration(str(tmp_path / "missing.opus")) is None


def test_length_of_a_flac_recording(tmp_path):
    # STREAMINFO: 44100 Hz, 2 channels, 16 bits, 441000 samples
    info = bytes(10) + bytes([0x0A, 0xC4, 0x42, 0xF0, 0x00, 0x06, 0xBA, 0xA8]) + bytes(16)
    (tmp_path / "kahf.flac").write_bytes(b"fLaC" + bytes([0x80, 0, 0, 34]) + info)
    assert duration(str(tmp_path / "kahf.flac")) == 10.0


def test_shown_with_the_time_it_stops_when_cut_short(write_config):
    config = config_with(write_config, "jummah-time = 13:30", "kahf = 1h", "reminder = 10")
    kahf = kahf_event(config, FRIDAY)
    assert (kahf.cut_short, kahf.end) == (True, at(FRIDAY, 12, 54))  # it would finish at 12:59
    assert "Surah Al-Kahf at 12:30, stopping early at 12:54" in format_times(config, FRIDAY, at(FRIDAY, 9, 0))
    assert str(kahf) == "Surah Al-Kahf at 2026-11-13 12:30 AEDT, stopping early at 12:54"
    assert times_json(config, FRIDAY, at(FRIDAY, 9, 0))["prayers"][1]["kahf"]["cut_short"] is True


def test_shown_with_its_start_only_when_its_length_is_unknown(write_config, tmp_path):
    (tmp_path / "kahf.mp3").write_bytes(b"ID3\x03\x00" + bytes(100))
    config = config_with(write_config, "kahf = 1h", "kahf-file = kahf.mp3")
    kahf = kahf_event(config, FRIDAY)
    assert (config.kahf_length, kahf.ends, kahf.end, kahf.cut_short) == (None, None, None, False)
    assert "Surah Al-Kahf at 12:06" in format_times(config, FRIDAY, at(FRIDAY, 9, 0))
    assert str(kahf) == "Surah Al-Kahf at 2026-11-13 12:06 AEDT, stopping at 13:04 at the latest"
    assert upcoming_kahf(config, at(FRIDAY, 12, 30)) == kahf  # may still be playing
