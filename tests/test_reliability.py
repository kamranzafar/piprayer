# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Connecting the speaker ahead, trying again when playing fails, recording how the last azaan went, and
warning when the clock isn't set from the network."""
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pipray import clock, mqtt, player, service
from pipray.config import Config, ConfigError
from pipray.output import format_next, format_times, times_json
from pipray.player import PLAYED_LOCALLY, REMINDER_SOUND
from pipray.schedule import Event
from pipray.state import LastAzaan, State, last_file, state_file
from pipray.web import status_html
from conftest import MELBOURNE_CONFIG, FakeResult

MELBOURNE = ZoneInfo("Australia/Melbourne")
ASR = datetime(2026, 11, 12, 18, 3, tzinfo=MELBOURNE)

REAL_SYNCHRONISED = clock.synchronised


def last(path):
    return LastAzaan.load(last_file(path))


def times(entries):
    return [(at.strftime("%H:%M:%S"), what, speaker) for at, what, speaker in entries]


# ---------------------------------------------------------------- connecting ahead

def test_speaker_is_connected_before_the_azaan(write_config, fake_clock):
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.run_service(write_config(), hours=1.5)

    assert [(at.strftime("%H:%M"), sound) for at, sound, _ in clock_.played] == [("18:03", "1")]
    # 30 seconds ahead, at 18:02:30, and play-azaan.sh disconnects it after playing
    assert [(at.strftime("%H:%M"), what) for at, what, _ in clock_.bluetooth] == [("18:02", "--connect")]


def test_chosen_speaker_is_connected_ahead(write_config, fake_clock):
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.run_service(write_config(MELBOURNE_CONFIG + "speaker = aa:bb:cc:dd:ee:ff\n"), hours=1.5)
    assert [(what, speaker) for _, what, speaker in clock_.bluetooth] == [("--connect", "AA:BB:CC:DD:EE:FF")]


def test_skipped_azaan_doesnt_connect_the_speaker(write_config, fake_clock):
    path = write_config()
    State([ASR]).save(state_file(path))
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.run_service(path, hours=1.5)

    assert clock_.played == [] and clock_.bluetooth == []
    assert last(path) == LastAzaan("asr", ASR, "skipped")


def test_speaker_is_disconnected_when_skipped_after_connecting(write_config, fake_clock):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.at(datetime(2026, 11, 12, 18, 2, 45), lambda: State([ASR]).save(state_file(path)))
    clock_.run_service(path, hours=1.5)

    assert clock_.played == []
    assert [what for _, what, _ in clock_.bluetooth] == ["--connect", "--disconnect"]


def test_speaker_is_disconnected_when_the_configuration_changes(write_config, fake_clock, monkeypatch):
    # connect two minutes ahead, so the change is noticed between connecting and playing
    monkeypatch.setattr(service, "CONNECT_AHEAD_SECONDS", 120)
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)

    def remove_asr():
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("Asr:0, ", ""))
        later = clock_.now.timestamp() + 10
        os.utime(path, (later, later))

    clock_.at(datetime(2026, 11, 12, 18, 1, 30), remove_asr)
    clock_.run_service(path, hours=1.5)

    assert clock_.played == []
    assert [what for _, what, _ in clock_.bluetooth] == ["--connect", "--disconnect"]


def test_azaan_plays_when_connecting_ahead_fails(write_config, fake_clock):
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.connect_fails = True
    clock_.run_service(write_config(), hours=1.5)
    assert [(at.strftime("%H:%M"), sound) for at, sound, _ in clock_.played] == [("18:03", "1")]
    assert [what for _, what, _ in clock_.bluetooth] == ["--connect"]  # nothing to disconnect


# ---------------------------------------------------------------- trying again

def test_tries_again_until_the_speaker_connects(write_config, fake_clock, caplog):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.failures = [(2, "Can't connect to bluetooth")] * 2
    with caplog.at_level(logging.WARNING, logger="piprayer"):
        clock_.run_service(path, hours=1.5)

    assert [(at.strftime("%H:%M"), sound) for at, sound, _ in clock_.played] == [("18:04", "1")]
    assert "Can't connect to bluetooth. Trying again in 30 seconds" in caplog.text
    assert last(path) == LastAzaan("asr", ASR, "played")


def test_tries_again_when_the_sound_doesnt_play(write_config, fake_clock):
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.failures = [(3, "paplay failed with exit code 1")]
    clock_.run_service(write_config(), hours=1.5)
    assert clock_.failures == [] and [sound for _, sound, _ in clock_.played] == ["1"]


def test_gives_up_after_five_minutes(write_config, fake_clock, caplog):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.failures = [(2, "Can't connect to bluetooth")] * 20
    clock_.run_service(path, hours=1.5)

    assert clock_.played == []
    assert len(clock_.failures) == 20 - 11  # at 18:03:00, 18:03:30, ... 18:08:00
    assert last(path) == LastAzaan("asr", ASR, "failed", "Can't connect to bluetooth")
    assert "Couldn't play Azaan for asr at 2026-11-12 18:03 AEDT: Can't connect to bluetooth" in caplog.text


def test_doesnt_try_again_when_the_sound_is_missing(write_config, fake_clock):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.failures = [(1, "Sound file not found: media/azaan-1.opus")] * 2
    clock_.run_service(path, hours=1.5)

    assert len(clock_.failures) == 1
    assert last(path).result == "failed"


def test_stops_trying_when_skipped(write_config, fake_clock):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.failures = [(2, "Can't connect to bluetooth")] * 20
    clock_.at(datetime(2026, 11, 12, 18, 4), lambda: State([ASR]).save(state_file(path)))
    clock_.run_service(path, hours=1.5)

    assert len(clock_.failures) == 20 - 2  # 18:03:00 and 18:03:30, then skipped at 18:04:00
    assert last(path) == LastAzaan("asr", ASR, "skipped")


def test_failed_reminder_isnt_recorded(write_config, fake_clock):
    path = write_config(MELBOURNE_CONFIG + "reminder = 10\n")
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.failures = [(1, "Sound file not found")]
    clock_.run_service(path, hours=0.95)  # until 17:57, after the reminder at 17:53 but before the azaan
    assert last(path) is None


# ---------------------------------------------------------------- playing on the Raspberry Pi

def test_fallback_output_option(write_config):
    assert Config(write_config()).fallback_local is False
    assert Config(write_config(MELBOURNE_CONFIG + "fallback-output = Local\n")).fallback_local is True
    assert Config(write_config(MELBOURNE_CONFIG + "fallback-output = off\n")).fallback_local is False
    with pytest.raises(ConfigError, match="Invalid fallback-output: hdmi, use local or off"):
        Config(write_config(MELBOURNE_CONFIG + "fallback-output = hdmi\n"))


def test_fallback_is_passed_to_play_azaan(write_config, monkeypatch):
    commands = []
    monkeypatch.setattr(player, "run_player", lambda args, on_start=None: commands.append(args) or (0, []))
    config = Config(write_config(MELBOURNE_CONFIG + "fallback-output = local\nspeaker = AA:BB:CC:DD:EE:FF\n"))

    player.play_event(config, Event(ASR, "asr"))
    player.play_event(config, Event(ASR, "asr", reminder=True))
    assert commands == [["--fallback", "1", "100", "AA:BB:CC:DD:EE:FF"],
                        ["--fallback", REMINDER_SOUND, "100", "AA:BB:CC:DD:EE:FF"]]


def test_playing_locally_is_recorded_as_a_problem(write_config, fake_clock):
    path = write_config(MELBOURNE_CONFIG + "fallback-output = local\n")
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.play_output = "Can't connect to bluetooth, playing on the local audio output instead\n" + PLAYED_LOCALLY
    clock_.run_service(path, hours=1.5)

    recorded = last(path)
    assert recorded.result == "played locally" and recorded.problem


def test_missed_azaan_is_recorded(write_config, fake_clock):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)

    def jump_forward():
        clock_.now += timedelta(minutes=20)

    clock_.at(datetime(2026, 11, 12, 18, 2, 45), jump_forward)
    clock_.run_service(path, hours=1.5)

    assert clock_.played == []
    assert last(path) == LastAzaan("asr", ASR, "missed", "noticed 20 minutes late, the clock may have changed")


def test_last_azaan_file(tmp_path):
    path = str(tmp_path / "last")
    assert LastAzaan.load(path) is None
    LastAzaan("isha", ASR, "failed", "Can't connect").save(path)
    assert LastAzaan.load(path) == LastAzaan("isha", ASR, "failed", "Can't connect")
    (tmp_path / "last").write_text("{nonsense")
    assert LastAzaan.load(path) is None


# ---------------------------------------------------------------- the clock

@pytest.mark.parametrize("output, expected", [("yes\n", True), ("no\n", False), ("", None)])
def test_clock_synchronised(monkeypatch, output, expected):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeResult(0, output)

    monkeypatch.setattr(clock.subprocess, "run", fake_run)
    assert REAL_SYNCHRONISED() is expected
    assert calls == [["timedatectl", "show", "--property=NTPSynchronized", "--value"]]


def test_clock_synchronised_without_timedatectl(monkeypatch):
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(clock.subprocess, "run", missing)
    assert REAL_SYNCHRONISED() is None


def test_clock_watch_logs_changes_once(monkeypatch, caplog):
    answers = iter([True, False, False, True, None])
    monkeypatch.setattr(clock, "synchronised", lambda: next(answers))
    watch = service.ClockWatch()
    with caplog.at_level(logging.INFO, logger="piprayer"):
        for _ in range(5):
            watch.check()

    messages = [record.getMessage() for record in caplog.records]
    assert messages == [clock.NOT_SYNCHRONISED, "The clock is set from the network again"]


def test_service_warns_when_the_clock_isnt_synchronised(write_config, fake_clock, monkeypatch, caplog):
    monkeypatch.setattr(clock, "synchronised", lambda: False)
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    with caplog.at_level(logging.WARNING, logger="piprayer"):
        clock_.run_service(write_config(), hours=1.5)
    assert caplog.text.count("The clock isn't set from the network") == 1
    assert len(clock_.played) == 1  # it still plays


# ---------------------------------------------------------------- showing it

NOW = datetime(2026, 11, 12, 19, 0, tzinfo=MELBOURNE)


def test_times_shows_the_last_azaan_and_clock_warning(write_config):
    config = Config(write_config())
    text = format_times(config, NOW.date(), NOW, State(), LastAzaan("asr", ASR, "played"), False)
    assert "Last azaan: Asr at 18:03 today played" in text
    assert "Warning: The clock isn't set from the network" in text

    text = format_times(config, NOW.date(), NOW, State(), LastAzaan("asr", ASR, "failed", "Can't connect"), True)
    assert "Last azaan: Asr at 18:03 today didn't play: Can't connect" in text
    assert "Warning" not in text

    # another day's times don't say how today's last azaan went
    assert "Last azaan" not in format_times(config, NOW.date() + timedelta(days=1), NOW, State(),
                                            LastAzaan("asr", ASR, "played"))


def test_next_shows_clock_warning(write_config):
    config = Config(write_config())
    assert "Warning: The clock" in format_next(config, None, NOW, State(), False)
    assert "Warning" not in format_next(config, None, NOW, State(), None)


def test_times_json_has_the_last_azaan(write_config):
    config = Config(write_config())
    data = times_json(config, NOW.date(), NOW, State(), LastAzaan("asr", ASR, "failed", "Can't connect"), False)
    assert data["last"] == {"prayer": "asr", "time": "2026-11-12T18:03:00+11:00", "result": "failed",
                            "error": "Can't connect"}
    assert data["clock_synchronised"] is False


def test_status_page_shows_problems(write_config):
    config = Config(write_config())
    page = status_html(config, NOW, State(), LastAzaan("asr", ASR, "failed", "Can't connect to bluetooth"), False)
    assert '<div class="notice">Asr at 18:03 today didn&#x27;t play: Can&#x27;t connect to bluetooth.</div>' in page
    assert '<div class="notice">The clock isn&#x27;t set from the network' in page

    page = status_html(config, NOW, State(), LastAzaan("asr", ASR, "played"), True)
    assert '<div class="notice">' not in page
    assert "Last azaan: Asr at 18:03 today played" in page


def test_mqtt_state_has_the_last_azaan_and_problems(write_config):
    config = Config(write_config())
    data = json.loads(mqtt.state_payload(config, NOW, State(), LastAzaan("asr", ASR, "played"), True))
    assert (data["last_result"], data["last_prayer"], data["last_time"]) == \
        ("played", "Asr", "2026-11-12T18:03:00+11:00")
    assert data["problem"] is None

    data = json.loads(mqtt.state_payload(config, NOW, State(), LastAzaan("asr", ASR, "failed", "No speaker"), False))
    assert data["problem"] == "Asr at 18:03 today didn't play: No speaker. " + clock.NOT_SYNCHRONISED
    assert data["clock_synchronised"] is False
