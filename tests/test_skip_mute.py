# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Skipping the next azaan and muting for days: the commands, the times output and the service."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray.cli import main
from pipray.config import Config
from pipray.player import REMINDER_SOUND
from pipray.state import State, skip_next, state_file
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")

# Times on Thursday 12 November 2026: Fajr 04:23, Dhuhr 13:06, Asr 18:03, Maghrib 20:02, Isha 21:43.
# Friday's Fajr is at 04:22.


@pytest.fixture(autouse=True)
def no_installed_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def cli(monkeypatch, capsys, *args):
    monkeypatch.setattr("sys.argv", ["piprayer.py"] + list(args))
    code = main()
    return code, capsys.readouterr()


def melbourne(*args):
    return datetime(*args, tzinfo=MELBOURNE)


def played(clock):
    return [(at.strftime("%a %H:%M"), sound) for at, sound, _ in clock.played]


# ------------------------------------------------------------------ commands

def test_skip_skips_the_next_azaan(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))

    code, out = cli(monkeypatch, capsys, "-c", path, "skip")

    assert code == 0
    assert out.out.startswith("Skipping the azaan for Asr at 18:03 today.")
    assert State.load(state_file(path)).skip == [melbourne(2026, 11, 12, 18, 3)]


def test_skip_again_skips_the_one_after(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 21, 0))

    cli(monkeypatch, capsys, "-c", path, "skip")
    code, out = cli(monkeypatch, capsys, "-c", path, "skip")

    assert out.out.startswith("Skipping the azaan for Fajr at 04:22 tomorrow.")
    assert State.load(state_file(path)).skip == [melbourne(2026, 11, 12, 21, 43), melbourne(2026, 11, 13, 4, 22)]


def test_skip_mentions_the_reminder(write_config, monkeypatch, capsys, at_time):
    at_time(melbourne(2026, 11, 12, 14, 0))
    code, out = cli(monkeypatch, capsys, "-c", write_config(MELBOURNE_CONFIG + "reminder = 10\n"), "skip")
    assert "Asr at 18:03 today and its reminder." in out.out


def test_skip_while_muted_skips_the_first_azaan_after_the_mute(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))

    cli(monkeypatch, capsys, "-c", path, "mute")
    code, out = cli(monkeypatch, capsys, "-c", path, "skip")

    assert out.out.startswith("Skipping the azaan for Fajr at 04:22 tomorrow.")


def test_mute_today(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))

    code, out = cli(monkeypatch, capsys, "-c", path, "mute")

    assert code == 0
    assert out.out == "Muted until the end of today. Run piprayer.py resume to play again.\n"
    assert State.load(state_file(path)).mute_until == melbourne(2026, 11, 13, 0, 0)


def test_mute_for_days(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))

    code, out = cli(monkeypatch, capsys, "-c", path, "mute", "--days", "3")

    assert out.out.startswith("Muted until the end of Saturday 14 November.")
    assert State.load(state_file(path)).mute_until == melbourne(2026, 11, 15, 0, 0)


def test_mute_until_a_date(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))

    code, out = cli(monkeypatch, capsys, "-c", path, "mute", "--until", "2026-11-13")

    assert out.out.startswith("Muted until the end of tomorrow.")


@pytest.mark.parametrize("args", [["--days", "0"], ["--days", "31"], ["--days", "x"],
                                  ["--days", "2", "--until", "2026-11-13"]])
def test_mute_rejects_invalid_days(write_config, monkeypatch, capsys, args):
    with pytest.raises(SystemExit):
        cli(monkeypatch, capsys, "-c", write_config(), "mute", *args)


@pytest.mark.parametrize("until", ["2026-11-11", "2026-12-12"])
def test_mute_until_must_be_within_30_days(write_config, monkeypatch, capsys, at_time, until):
    at_time(melbourne(2026, 11, 12, 14, 0))
    code, out = cli(monkeypatch, capsys, "-c", write_config(), "mute", "--until", until)
    assert code == 1
    assert "--until must be from today to 29 days ahead" in out.err


def test_resume_clears_skip_and_mute(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))
    cli(monkeypatch, capsys, "-c", path, "skip")
    cli(monkeypatch, capsys, "-c", path, "mute")

    code, out = cli(monkeypatch, capsys, "-c", path, "resume")

    assert out.out == "Skip and mute cleared. Next azaan: Asr at 18:03 today.\n"
    assert not os.path.exists(state_file(path))


def test_unreadable_state_is_ignored(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    with open(state_file(path), "w") as f:
        f.write("not json")
    at_time(melbourne(2026, 11, 12, 14, 0))

    code, out = cli(monkeypatch, capsys, "-c", path, "times", "next", "-f", "plain")

    assert out.out == "asr-18:03\n"


# ------------------------------------------------------------------ times output

def test_table_marks_skipped_and_muted(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))
    cli(monkeypatch, capsys, "-c", path, "skip")

    code, out = cli(monkeypatch, capsys, "-c", path, "times")
    assert " Asr      18:03  1      100%    ◀ next · skipped" in out.out.splitlines()

    cli(monkeypatch, capsys, "-c", path, "mute")
    code, out = cli(monkeypatch, capsys, "-c", path, "times")
    lines = out.out.splitlines()
    assert " Maghrib  20:02  1      100%    muted" in lines
    assert lines[-1] == " Muted until the end of today. Run piprayer.py resume to play again."


def test_next_says_when_skipped(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))
    cli(monkeypatch, capsys, "-c", path, "skip")

    code, out = cli(monkeypatch, capsys, "-c", path, "times", "next")

    assert out.out.splitlines()[-1] == " Skipped. Run piprayer.py resume to play it."


def test_json_shows_skipped_and_muted(write_config, monkeypatch, capsys, at_time):
    path = write_config()
    at_time(melbourne(2026, 11, 12, 14, 0))
    cli(monkeypatch, capsys, "-c", path, "skip")

    code, out = cli(monkeypatch, capsys, "-c", path, "times", "next", "-f", "json")
    data = json.loads(out.out)
    assert (data["prayer"], data["skipped"], data["muted"], data["muted_until"]) == ("asr", True, False, None)

    cli(monkeypatch, capsys, "-c", path, "mute", "-d", "2")
    code, out = cli(monkeypatch, capsys, "-c", path, "times", "-f", "json")
    data = json.loads(out.out)
    assert data["muted_until"] == "2026-11-14T00:00:00+11:00"
    assert all(p["muted"] for p in data["prayers"])


# ------------------------------------------------------------------ service

def test_service_doesnt_play_a_skipped_azaan_or_its_reminder(write_config, fake_clock):
    path = write_config(MELBOURNE_CONFIG + "reminder = 10\n")
    State([melbourne(2026, 11, 12, 18, 3)]).save(state_file(path))
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)

    clock.run_service(path, hours=5.5)

    assert played(clock) == [("Thu 19:52", REMINDER_SOUND), ("Thu 20:02", "1")]
    assert not os.path.exists(state_file(path))  # the skip is forgotten once it is over


def test_service_mute_lasts_until_the_end_of_the_day(write_config, fake_clock):
    path = write_config()
    State(mute_until=melbourne(2026, 11, 13, 0, 0)).save(state_file(path))
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)

    clock.run_service(path, hours=14)

    assert played(clock) == [("Fri 04:22", "1")]
    assert not os.path.exists(state_file(path))


def test_service_applies_a_skip_made_while_it_waits(write_config, fake_clock):
    path = write_config()
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock.at(datetime(2026, 11, 12, 17, 0),
             lambda: skip_next(Config(path), state_file(path), clock.local_now()))

    clock.run_service(path, hours=7)

    assert played(clock) == [("Thu 20:02", "1"), ("Thu 21:43", "1")]
