import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import piprayer  # noqa: E402

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


class Stop(Exception):
    """Raised by the fake clock to end a simulated service run."""


class FakeClock:
    """Replaces time in piprayer: sleeping advances the clock instantly and playing is recorded."""

    def __init__(self, start, tz):
        self.now = start.replace(tzinfo=tz).astimezone(timezone.utc)
        self.tz = tz
        self.played = []
        self.events = []  # (utc datetime, callback)
        self.stop_at = None
        self.play_duration = timedelta(minutes=4)

    def at(self, local_time, callback):
        """Run callback once the simulated clock reaches local_time."""
        self.events.append((local_time.replace(tzinfo=self.tz).astimezone(timezone.utc), callback))

    def local_now(self):
        return self.now.astimezone(self.tz)

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)
        for event in [e for e in self.events if self.now >= e[0]]:
            self.events.remove(event)
            event[1]()
        if self.stop_at and self.now >= self.stop_at:
            raise Stop

    def run_player(self, cmd):
        self.played.append((self.local_now().replace(second=0, microsecond=0), cmd[1]))
        self.now += self.play_duration

        class Result:
            returncode = 0

        return Result()

    def run_service(self, config_path, hours, azaan=1):
        self.stop_at = self.now + timedelta(hours=hours)
        try:
            piprayer.run(config_path, azaan)
        except Stop:
            pass
        return self.played


@pytest.fixture
def fake_clock(monkeypatch):
    def create(start, tz):
        clock = FakeClock(start, tz)

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now.astimezone(tz)

        monkeypatch.setattr(piprayer, "datetime", FakeDatetime)
        monkeypatch.setattr(piprayer.systime, "sleep", clock.sleep)
        monkeypatch.setattr(piprayer.subprocess, "run", clock.run_player)
        return clock

    return create
