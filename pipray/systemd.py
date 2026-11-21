# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Telling systemd the service is ready, and that it is still making progress, for its watchdog.

The unit has Type=notify and WatchdogSec, so systemd restarts the service when it hangs, e.g. on a bluetoothctl call
that never returns. Run outside systemd, without NOTIFY_SOCKET, these do nothing.
"""
import logging
import os
import socket

log = logging.getLogger("piprayer")

_ready = False


def notify(message):
    """Send a message, e.g. READY=1, to systemd. Returns whether it was sent."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):  # an abstract socket
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | getattr(socket, "SOCK_CLOEXEC", 0)) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
        return True
    except OSError as e:
        log.debug("Can't notify systemd: %s", e)
        return False


def ready():
    """Tell systemd the service has started, once."""
    global _ready
    if not _ready:
        _ready = notify("READY=1")


def watchdog():
    """Tell systemd the service is still making progress. Needed at least every WatchdogSec."""
    notify("WATCHDOG=1")
