"""Restarting the service when PiPrayer is updated, and warnings about unknown options."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import MELBOURNE_CONFIG
from pipray import cli, service
from pipray.config import Config
from pipray.service import Updated

MELBOURNE = ZoneInfo("Australia/Melbourne")


@pytest.fixture
def code(tmp_path, monkeypatch):
    """A copy of PiPrayer's files for the service to watch, returning a function that changes one."""
    (tmp_path / "pipray" / "static").mkdir(parents=True)
    for name in ("piprayer.py", "praytimes.py", "pipray/service.py", "pipray/static/status.css"):
        (tmp_path / name).write_text("# version 1\n")
    monkeypatch.setattr(service, "SCRIPT_DIR", str(tmp_path))

    def update(name="pipray/service.py", text="# version 2\n"):
        path = tmp_path / name
        path.write_text(text)
        later = os.stat(path).st_mtime + 10  # make sure the modification time changes
        os.utime(path, (later, later))

    return update


def test_service_stops_waiting_when_updated(write_config, fake_clock, code):
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock.at(datetime(2026, 11, 12, 16, 0), code)

    with pytest.raises(Updated):
        clock.run_service(write_config(), hours=4)

    assert clock.played == []  # Asr at 18:03 is left to the new code
    assert clock.local_now() < datetime(2026, 11, 12, 16, 2, tzinfo=MELBOURNE)


def test_waits_for_the_update_to_finish(code, monkeypatch):
    started = service.code_version()
    code()  # a git pull has changed one file so far
    later_changes = [lambda: code("piprayer.py")]  # and changes another during the first wait
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if later_changes:
            later_changes.pop()()

    monkeypatch.setattr(service.clock, "sleep", sleep)
    with pytest.raises(Updated):
        service.check_for_update(started)
    # restarted only once nothing changed for a whole wait
    assert sleeps == [service.UPDATE_SETTLE_SECONDS] * 2


def test_unchanged_code_doesnt_restart(write_config, fake_clock, code):
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock.run_service(write_config(), hours=4)
    assert [sound for _, sound, _ in clock.played] == ["1"]


def test_run_command_restarts_with_the_same_arguments(monkeypatch, write_config):
    def updated(config_path, azaan):
        raise Updated

    restarts = []
    monkeypatch.setattr(cli, "run", updated)
    monkeypatch.setattr(cli, "restart", lambda: restarts.append(True))
    monkeypatch.setattr("sys.argv", ["/home/pi/piprayer/piprayer.py", "-c", write_config(), "run"])

    assert cli.main() == 0
    assert restarts == [True]


def test_restart_runs_the_same_command(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.os, "execv", lambda path, args: calls.append((path, args)))
    monkeypatch.setattr("sys.argv", ["/home/pi/piprayer/piprayer.py", "run", "--azaan", "3"])
    cli.restart()
    assert calls == [(cli.sys.executable,
                      [cli.sys.executable, "/home/pi/piprayer/piprayer.py", "run", "--azaan", "3"])]


# ------------------------------------------------------------------ unknown options

def test_unknown_options_are_warned_about(write_config):
    path = write_config(MELBOURNE_CONFIG + "status_port = 8080\nvolumes = 50\ncolour = green\n")
    assert Config(path).warnings == [
        "Unknown option status_port in {}, it is ignored. Did you mean status-port?".format(path),
        "Unknown option volumes in {}, it is ignored. Did you mean volume?".format(path),
        "Unknown option colour in {}, it is ignored".format(path),
    ]


def test_known_options_have_no_warnings(write_config):
    text = MELBOURNE_CONFIG + "azaan = 3\nstatus-port = 8080\nhigh-latitude = AngleBased\nmqtt-host = broker\n"
    assert Config(write_config(text)).warnings == []


def test_times_command_prints_warnings(write_config, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["piprayer.py", "-c", write_config(MELBOURNE_CONFIG + "status_port = 80\n"),
                                     "times", "-d", "2026-11-12"])
    assert cli.main() == 0
    assert "Did you mean status-port?" in capsys.readouterr().err


def test_service_logs_warnings(write_config, fake_clock, caplog):
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock.run_service(write_config(MELBOURNE_CONFIG + "status_port = 80\n"), hours=1)
    assert "Unknown option status_port" in caplog.text
