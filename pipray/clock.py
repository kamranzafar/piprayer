# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The current time and sleeping. Everything goes through here, so tests can run a day in milliseconds."""
import subprocess
import time
from datetime import datetime


def now(tz):
    return datetime.now(tz)


def sleep(seconds):
    time.sleep(seconds)


def monotonic():
    """Seconds from an arbitrary start, for measuring how long since something, unaffected by clock changes."""
    return time.monotonic()


def synchronised():
    """Whether the system clock is set from the network: True, False, or None when that can't be told.

    The Raspberry Pi has no hardware clock, so until it is set from the network the time can be far off.
    """
    try:
        result = subprocess.run(["timedatectl", "show", "--property=NTPSynchronized", "--value"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return {"yes": True, "no": False}.get(result.stdout.strip())


NOT_SYNCHRONISED = ("The clock isn't set from the network, so the azaan may play at the wrong time. "
                    "Check the Raspberry Pi is online, and run: timedatectl status")
