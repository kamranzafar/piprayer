# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Telling systemd the service is ready and alive, for its watchdog."""
import shutil
import socket
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pipray import player, service, systemd
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")

needs_unix_sockets = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")


@pytest.fixture(autouse=True)
def not_ready(monkeypatch):
    monkeypatch.setattr(systemd, "_ready", False)


@pytest.fixture
def notify_socket(monkeypatch):
    """A socket standing in for systemd's, set as NOTIFY_SOCKET. Returns a function giving the messages sent."""
    # Unix socket paths are short, so not in pytest's long temporary folder
    directory = tempfile.mkdtemp()
    path = directory + "/notify"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    sock.setblocking(False)
    monkeypatch.setenv("NOTIFY_SOCKET", path)

    def received():
        messages = []
        while True:
            try:
                messages.append(sock.recv(1024).decode())
            except BlockingIOError:
                return messages

    yield received
    sock.close()
    shutil.rmtree(directory)


@pytest.fixture
def notified(monkeypatch):
    """Record what is sent to systemd, with the simulated time, instead of sending it."""
    sent = []
    monkeypatch.setattr(systemd, "notify", lambda message: sent.append(message) or True)
    return sent


# ---------------------------------------------------------------- notify

@needs_unix_sockets
def test_notify_sends_to_the_socket(notify_socket):
    assert systemd.notify("WATCHDOG=1")
    assert notify_socket() == ["WATCHDOG=1"]


@needs_unix_sockets
def test_ready_is_sent_once(notify_socket):
    systemd.ready()
    systemd.ready()
    systemd.watchdog()
    assert notify_socket() == ["READY=1", "WATCHDOG=1"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="abstract sockets are Linux only")
def test_notify_to_an_abstract_socket(monkeypatch):
    name = "piprayer-test-{}".format(id(monkeypatch))
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.bind("\0" + name)
        monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)
        assert systemd.notify("READY=1")
        assert sock.recv(1024) == b"READY=1"


def test_notify_does_nothing_outside_systemd(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert not systemd.notify("READY=1")
    systemd.ready()
    systemd.watchdog()


def test_notify_to_a_missing_socket_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "gone"))
    assert not systemd.notify("WATCHDOG=1")


# ---------------------------------------------------------------- the service

def watchdog_gaps(sent):
    """The longest simulated time between two watchdog pings."""
    times = [at for at, message in sent if message == "WATCHDOG=1"]
    return max(b - a for a, b in zip(times, times[1:]))


@pytest.fixture
def timed_notify(monkeypatch):
    def create(clock_):
        sent = []
        monkeypatch.setattr(systemd, "notify", lambda message: sent.append((clock_.now, message)) or True)
        return sent
    return create


def test_service_is_ready_then_pings_through_the_day(write_config, fake_clock, timed_notify):
    clock_ = fake_clock(datetime(2026, 11, 12, 0, 0), MELBOURNE)
    clock_.play_duration = timedelta(seconds=10)
    sent = timed_notify(clock_)
    clock_.run_service(write_config(), hours=26)

    assert len(clock_.played) >= 5
    assert [message for _, message in sent].count("READY=1") == 1
    assert sent[0][1] == "WATCHDOG=1" and sent[1][1] == "READY=1"
    assert watchdog_gaps(sent) <= timedelta(seconds=service.MAX_SLEEP_SECONDS)


def test_service_with_broken_config_is_ready_and_pings_while_it_waits(write_config, fake_clock, timed_notify):
    clock_ = fake_clock(datetime(2026, 11, 12, 0, 0), MELBOURNE)
    sent = timed_notify(clock_)
    clock_.run_service(write_config(MELBOURNE_CONFIG.replace("Fajr:-11", "Fajir:-11")), hours=3)

    assert clock_.played == []
    assert [message for _, message in sent].count("READY=1") == 1
    assert watchdog_gaps(sent) <= timedelta(seconds=service.MAX_SLEEP_SECONDS)


def test_service_pings_while_retrying(write_config, fake_clock, timed_notify):
    clock_ = fake_clock(datetime(2026, 11, 12, 4, 0), MELBOURNE)
    clock_.play_duration = timedelta(seconds=10)  # the real player pings while it plays, see below
    clock_.failures = [(2, "Can't connect to bluetooth")] * 3
    sent = timed_notify(clock_)
    clock_.run_service(write_config(), hours=1)

    assert len(clock_.played) == 1
    assert watchdog_gaps(sent) <= timedelta(seconds=service.MAX_SLEEP_SECONDS)


def test_player_pings_while_it_plays(monkeypatch, notified):
    monkeypatch.setattr(player, "PLAYER", [sys.executable, "-c", "import time; time.sleep(0.5); print('done')"])
    monkeypatch.setattr(player, "WATCHDOG_SECONDS", 0.1)
    code, output = player.run_player([])

    assert (code, output) == (0, ["done"])
    assert notified.count("WATCHDOG=1") >= 3


def test_player_doesnt_get_the_notify_socket(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/user/1000/systemd/notify")
    monkeypatch.setattr(player, "PLAYER", [sys.executable, "-c",
                                           "import os; print(os.environ.get('NOTIFY_SOCKET', 'none'))"])
    monkeypatch.setattr(systemd, "notify", lambda message: True)
    assert player.run_player([]) == (0, ["none"])
