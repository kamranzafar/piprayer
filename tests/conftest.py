# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
from datetime import timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipray import clock, player, service  # noqa: E402

MELBOURNE_CONFIG = """\
[Default]
lat = -37.909499
lng = 144.751876
timezone = Australia/Melbourne
method = Jafari
asr-time = Hanafi
prayers = Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19
"""


@pytest.fixture
def write_config(tmp_path):
    """Write a configuration file and return its path."""

    def write(text=MELBOURNE_CONFIG, name="piprayer.ini"):
        path = tmp_path / name
        path.write_text(text)
        return str(path)

    return write


class FakeResult:
    """What subprocess.run returns."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def clock_synchronised(monkeypatch):
    """The clock is set from the network, unless a test says otherwise, so results don't depend on this computer."""
    monkeypatch.setattr(clock, "synchronised", lambda: True)


class Stop(Exception):
    """Raised by the fake clock to end a simulated service run."""


class FakeClock:
    """Replaces time in piprayer: sleeping advances the clock instantly and playing is recorded."""

    def __init__(self, start, tz):
        self.now = start.replace(tzinfo=tz).astimezone(timezone.utc)
        self.tz = tz
        self.played = []
        self.bluetooth = []  # (local time, "--connect" or "--disconnect", speaker or None)
        self.failures = []  # (exit code, message) for the next plays to fail with
        self.connect_fails = False
        self.play_output = "Played on speaker AA:AA:AA:AA:AA:01"
        self.pid = 4242  # play-azaan.sh's process
        self.stopped_while_playing = False  # set by terminate(), as if play-azaan.sh was stopped
        self.terminated = []
        self.events = []  # (utc datetime, callback)
        self.stop_at = None
        self.play_duration = timedelta(minutes=4)

    def at(self, local_time, callback):
        """Run callback once the simulated clock reaches local_time."""
        self.events.append((local_time.replace(tzinfo=self.tz).astimezone(timezone.utc), callback))

    def local_now(self):
        return self.now.astimezone(self.tz)

    def sleep(self, seconds):
        self.advance(timedelta(seconds=seconds))
        if self.stop_at and self.now >= self.stop_at:
            raise Stop

    def advance(self, delta):
        """Move the clock on, running the callbacks due by then."""
        self.now += delta
        for event in [e for e in self.events if self.now >= e[0]]:
            self.events.remove(event)
            event[1]()

    def run_player(self, args, on_start=None):
        """Replaces player.run_player: returns play-azaan.sh's exit code and the lines it printed."""
        minute = self.local_now().replace(second=0, microsecond=0)
        if args[0] in ("--connect", "--disconnect"):
            self.bluetooth.append((minute, args[0], args[1] if len(args) > 1 else None))
            if args[0] == "--connect" and self.connect_fails:
                return 2, ["Can't connect to bluetooth"]
            return 0, [args[1] if len(args) > 1 else "AA:AA:AA:AA:AA:01"]
        if args[0] == "--fallback":
            args = args[1:]
        if on_start:
            on_start(self.pid)
        if self.failures:
            code, message = self.failures.pop(0)
            return code, [message]
        # (local time, azaan number or sound file, volume)
        self.played.append((minute, args[0], args[1]))
        self.advance(self.play_duration)
        if self.stopped_while_playing:
            self.stopped_while_playing = False
            return 4, ["Stopped"]
        return 0, self.play_output.splitlines()

    def terminate(self, pid):
        self.terminated.append(pid)
        self.stopped_while_playing = True

    def run_service(self, config_path, hours, azaan=1):
        self.stop_at = self.now + timedelta(hours=hours)
        try:
            service.run(config_path, azaan)
        except Stop:
            pass
        return self.played


@pytest.fixture
def fake_clock(monkeypatch):
    def create(start, tz):
        fake = FakeClock(start, tz)

        monkeypatch.setattr(clock, "now", lambda tz: fake.now.astimezone(tz))
        monkeypatch.setattr(clock, "sleep", fake.sleep)
        monkeypatch.setattr(player, "run_player", fake.run_player)
        monkeypatch.setattr(player, "terminate", fake.terminate)
        return fake

    return create


@pytest.fixture
def at_time(monkeypatch):
    """Fix the current time seen by piprayer."""

    def set_now(now):
        monkeypatch.setattr(clock, "now", lambda tz: now.astimezone(tz))

    return set_now
