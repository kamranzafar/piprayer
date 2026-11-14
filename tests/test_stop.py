# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stopping the azaan or reminder that is playing, from the command line, the status page and Home Assistant."""
import json
import os
import stat
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray import mqtt, player, service
from pipray.cli import main
from pipray.config import Config
from pipray.output import times_json
from pipray.schedule import Event
from pipray.state import LastAzaan, Playing, State, last_file, playing_file
from pipray.web import StatusRoutes, status_html
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")
ASR = datetime(2026, 11, 12, 18, 3, tzinfo=MELBOURNE)
NOW = datetime(2026, 11, 12, 18, 4, tzinfo=MELBOURNE)


@pytest.fixture(autouse=True)
def any_process_is_the_player(monkeypatch):
    """The fake play-azaan.sh has no real process to check."""
    monkeypatch.setattr(player, "is_player", lambda pid: True)


@pytest.fixture
def terminated(monkeypatch):
    stopped = []
    monkeypatch.setattr(player, "terminate", stopped.append)
    return stopped


def playing_asr(path, pid=4242, reminder=False):
    Playing("asr", reminder, ASR, pid).save(playing_file(path))


# ---------------------------------------------------------------- the service

def test_stopping_while_the_azaan_plays(write_config, fake_clock):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    seen = []

    def stop_it():
        seen.append(Playing.load(playing_file(path)))
        player.stop(path)

    clock_.at(datetime(2026, 11, 12, 18, 4), stop_it)
    clock_.run_service(path, hours=1.5)

    assert seen == [Playing("asr", False, ASR, 4242)]
    assert clock_.terminated == [4242]
    assert LastAzaan.load(last_file(path)) == LastAzaan("asr", ASR, "stopped")
    assert not os.path.exists(playing_file(path))  # removed once done


def test_stopped_azaan_isnt_tried_again(write_config, fake_clock):
    path = write_config()
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.failures = [(2, "Can't connect to bluetooth")] * 20
    clock_.at(datetime(2026, 11, 12, 18, 3, 15), lambda: player.stop(path))
    clock_.run_service(path, hours=1.5)

    assert len(clock_.failures) == 19  # only the first attempt, at 18:03
    assert LastAzaan.load(last_file(path)).result == "stopped"


def test_stopped_reminder_doesnt_stop_the_azaan(write_config, fake_clock):
    path = write_config(MELBOURNE_CONFIG + "reminder = 10\n")
    clock_ = fake_clock(datetime(2026, 11, 12, 17, 0), MELBOURNE)
    clock_.at(datetime(2026, 11, 12, 17, 54), lambda: player.stop(path))
    clock_.run_service(path, hours=1.5)

    assert [at.strftime("%H:%M") for at, _, _ in clock_.played] == ["17:53", "18:03"]
    assert LastAzaan.load(last_file(path)) == LastAzaan("asr", ASR, "played")


def test_stop_asked_before_the_player_started_stops_it_straight_away(write_config, terminated):
    path = playing_file(write_config())
    Playing("asr", False, ASR, stopped=True).save(path)
    service.started_playing(path, 99)
    assert terminated == [99]
    assert Playing.load(path).pid == 99


def test_service_removes_a_playing_file_left_over(write_config, fake_clock):
    path = write_config()
    playing_asr(path)
    clock_ = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock_.run_service(path, hours=0.1)
    assert not os.path.exists(playing_file(path))


def test_service_says_when_playing_starts_and_ends(write_config, fake_clock):
    path = write_config()
    fake_clock(datetime(2026, 11, 12, 18, 3), MELBOURNE)
    calls = []
    service.play_with_retries(path, Config(path), path + "-state", Event(ASR, "asr"),
                              lambda: calls.append(os.path.exists(playing_file(path))))
    assert calls == [True, False]


# ---------------------------------------------------------------- stopping

def test_stop_when_nothing_plays(write_config, terminated):
    assert player.stop(write_config()) is None
    assert terminated == []


def test_stop_marks_it_stopped_and_stops_the_player(write_config, terminated):
    path = write_config()
    playing_asr(path)
    assert player.stop(path).describe() == "the azaan for Asr"
    assert Playing.load(playing_file(path)).stopped
    assert terminated == [4242]


def test_stop_between_attempts_doesnt_stop_another_process(write_config, terminated, monkeypatch):
    monkeypatch.setattr(player, "is_player", lambda pid: False)  # play-azaan.sh has finished
    path = write_config()
    playing_asr(path)
    assert player.stop(path) is not None
    assert terminated == []
    assert Playing.load(playing_file(path)).stopped


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs /proc")
def test_is_player_checks_the_process(monkeypatch):
    monkeypatch.undo()
    assert not player.is_player(os.getpid())  # pytest isn't play-azaan.sh
    assert not player.is_player(2 ** 22 + 1)  # no such process


def test_terminate_ignores_a_finished_process():
    player.terminate(2 ** 22 + 1)


def test_run_player_gives_the_process_id_and_output(tmp_path, monkeypatch):
    script = tmp_path / "play-azaan.sh"
    script.write_text("#!/bin/sh\necho connecting\necho \"Played $*\"\nexit 3\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(player, "PLAYER", str(script))
    started = []

    assert player.run_player(["3", "100"], started.append) == (3, ["connecting", "Played 3 100"])
    assert len(started) == 1 and started[0] > 0


# ---------------------------------------------------------------- command line, status page, Home Assistant

def run_main(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["piprayer.py"] + list(args))
    code = main()
    return code, capsys.readouterr().out


def test_stop_command(write_config, monkeypatch, capsys, terminated):
    path = write_config()
    assert run_main(monkeypatch, capsys, "-c", path, "stop") == (1, "Nothing is playing\n")

    playing_asr(path, reminder=True)
    assert run_main(monkeypatch, capsys, "-c", path, "stop") == (0, "Stopped the reminder for Asr.\n")
    assert terminated == [4242]


def test_status_page_has_a_stop_button_while_playing(write_config):
    config = Config(write_config())
    page = status_html(config, NOW, State(), playing=Playing("asr", False, ASR, 4242))
    assert '<div class="notice" data-playing>Playing the azaan for Asr</div>' in page
    assert '<form method="post" action="/stop" class="wide"><button class="primary">Stop the azaan</button>' in page
    # the skip button is no longer the main one
    assert '<form method="post" action="/skip" class="wide"><button class="">Skip Maghrib' in page

    page = status_html(config, NOW, State())
    assert 'action="/stop"' not in page and '<div class="notice" data-playing' not in page
    assert '<button class="primary">Skip Maghrib' in page


def test_status_page_stop(write_config, at_time, terminated):
    path = write_config()
    at_time(NOW)
    routes = StatusRoutes(path, 1)
    assert json.loads(routes.post("/api/stop", {}).body) == {"stopped": None}

    playing_asr(path)
    assert json.loads(routes.post("/api/stop", {}).body) == {
        "stopped": {"prayer": "asr", "reminder": False, "time": "2026-11-12T18:03:00+11:00"}}
    assert terminated == [4242]
    playing_asr(path)
    assert routes.post("/stop", {}).status == 303  # back to the page


def test_times_json_says_what_is_playing(write_config):
    config = Config(write_config())
    data = times_json(config, NOW.date(), NOW, State(), playing=Playing("asr", True, ASR, 4242))
    assert data["playing"] == {"prayer": "asr", "reminder": True, "time": "2026-11-12T18:03:00+11:00"}


def test_mqtt_stop_command_and_state(write_config, at_time, terminated):
    path = write_config()
    at_time(NOW)
    assert mqtt.handle_command("stop", path, 1) == "MQTT: nothing is playing to stop"
    playing_asr(path)
    assert mqtt.handle_command("stop", path, 1) == "MQTT: stopping the azaan for Asr"
    assert terminated == [4242]

    data = json.loads(mqtt.state_payload(Config(path), NOW, State(), playing=Playing("asr", True, ASR)))
    assert (data["playing"], data["playing_reminder"]) == ("Asr", True)
